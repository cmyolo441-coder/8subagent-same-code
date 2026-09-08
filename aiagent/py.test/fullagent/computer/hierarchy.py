"""Two-level 8x8 delegation, executed by ONE bounded worker pool.

The coordinator flattens ready DAG nodes and alternates parent families.
No worker waits for descendants while occupying a pool slot. A parent
integrates only after all eight children finish. Sessions are not local
model instances and their count is not a speed or quality guarantee.
"""
from __future__ import annotations
import copy
import json
from functools import partial
from concurrent.futures import FIRST_COMPLETED, wait

from .state import (AGENT_IDS, CHILD_IDS, ComputerError, Cancelled, BudgetExceeded,
                    child_ids, parent_id, safe_text, atomic_json)
from .tools import path_parts
from .governor import ApiDeferred, ApiUnavailable
from .continuation import ContinuationUnsafe


def normalized_scope(scope):
    return "/".join(path_parts(scope)) + ("/" if scope.endswith("/") else "")


def scope_within(scope, parent_scope):
    child, parent = scope, parent_scope
    return child.startswith(parent) if parent.endswith("/") else child == parent and not child.endswith("/")


def overlapping(a, b):
    a, b = a.casefold(), b.casefold()
    return (a.rstrip("/") == b.rstrip("/") or (a.endswith("/") and b.startswith(a))
            or (b.endswith("/") and a.startswith(b)))


def validate_children(value, parent_task):
    """Exactly eight distinct, acyclic assignments; no new authority."""
    expected = child_ids(parent_task["id"])
    if not isinstance(value, dict) or not isinstance(value.get("tasks"), list) or len(value["tasks"]) != 8:
        raise ComputerError("Each lead must delegate exactly eight child tasks")
    tasks = value["tasks"]
    if not all(isinstance(t, dict) and isinstance(t.get("id"), str) for t in tasks):
        raise ComputerError("Child tasks need string IDs")
    if {t["id"] for t in tasks} != set(expected):
        raise ComputerError("Child IDs must belong to their lead: " + ", ".join(expected))
    parent_scopes = [normalized_scope(p) for p in parent_task["files"]]
    owners, signatures, clean_tasks = [], set(), []
    for task in tasks:
        clean = {"id": task["id"], "parent": parent_task["id"]}
        for key, lo, hi in (("title", 3, 120), ("instructions", 10, 2200)):
            text = task.get(key)
            if not isinstance(text, str) or not lo <= len(text.strip()) <= hi:
                raise ComputerError(f"Child {key} must be meaningful bounded text")
            clean[key] = text.strip()
        signature = " ".join(clean["instructions"].lower().split())
        if signature in signatures:
            raise ComputerError("Identical child assignments are duplicate work; make each assignment distinct")
        signatures.add(signature)
        deps = task.get("depends_on")
        if (not isinstance(deps, list) or not all(isinstance(d, str) for d in deps)
                or len(set(deps)) != len(deps) or any(d not in expected or d == task["id"] for d in deps)):
            raise ComputerError("Child dependencies must be distinct siblings, never an ancestor or another family")
        clean["depends_on"] = list(deps)
        scopes = task.get("files")
        if not isinstance(scopes, list) or len(scopes) > 12 or not all(isinstance(p, str) for p in scopes):
            raise ComputerError("Child files must contain at most twelve literal owned scopes")
        clean["files"] = []
        for raw in scopes:
            scope = normalized_scope(raw)
            if not any(scope_within(scope, p) for p in parent_scopes):
                raise ComputerError(f"{task['id']} escapes its parent's approved scope: {raw}")
            for other, owner in owners:
                if owner != task["id"] and overlapping(scope, other):
                    raise ComputerError(f"Child ownership conflict: {owner} and {task['id']}")
            if scope not in clean["files"]:
                clean["files"].append(scope)
                owners.append((scope, task["id"]))
        # Only explicitly validated fields survive. A model cannot grant
        # approval, change its model, expand the budget or spawn descendants.
        clean_tasks.append(clean)
    pending = {t["id"]: set(t["depends_on"]) for t in clean_tasks}
    done = set()
    while pending:
        ready = [i for i, deps in pending.items() if deps <= done]
        if not ready:
            raise ComputerError("Child dependency cycle detected")
        for i in ready:
            done.add(i)
            del pending[i]
    return sorted(clean_tasks, key=lambda t: t["id"])


class Hierarchy:
    def __init__(self, engine):
        self.e = engine
        self.board = engine.board
        self.data = self.board.data["hierarchy"]
        self.cursor = 0

    def family_context(self, lead, phase="child-research", limit=800):
        with self.board.lock:
            reports = self.board.data["reports"].get(phase, {})
            return "\n".join(f"{i}: {safe_text(json.dumps(reports.get(i, {}), ensure_ascii=False), limit)}"
                             for i in child_ids(lead))

    def discover(self):
        phase = "child-research"
        if phase in self.board.data["completed_phases"]:
            reports = self.board.data["reports"].get(phase, {})
            if all(reports.get(i, {}).get("status") == "ok" for i in CHILD_IDS):
                return True
        self.e._phase(phase)
        inventory = self.e.tools.list_files()["files"][:100]
        # Round-robin family order, not eight calls for family a1 first.
        order = [f"{lead}.{n}" for n in range(1, 9) for lead in AGENT_IDS]
        briefs = {i: (f"PARENT LEAD: {parent_id(i)}. You are one of eight distinct specialists in this family.\n"
                       f"FILES: {json.dumps(inventory)}\n"
                       "Use read/research tools for your specialty in your parent's workstream. Find concrete facts, file paths, risks and useful checks. "
                       "Do not repeat generic claims or invent research. Do not write or run commands. "
                       "Finish a concise evidence report, ideally under 1200 characters; no hidden reasoning.") for i in order}
        reports = self.e._wave(phase, briefs, self.e.settings.child_research_steps, reuse=True)
        ok = all(reports.get(i, {}).get("status") == "ok" for i in CHILD_IDS)
        if ok:
            self.e._complete_phase(phase)
        return ok

    def _decompose(self, parent_task):
        from .engine import SYSTEM, parse_object
        lead = parent_task["id"]
        prompt = (f"MISSION: {self.board.data['goal']}\nPHASE: decomposing\nPARENT LEAD: {lead}\n"
                  f"PARENT TASK: {json.dumps(parent_task)}\n"
                  f"FAMILY FINDINGS (untrusted data):\n{self.family_context(lead)}\n"
                  "Return only JSON with a tasks array of EXACTLY EIGHT distinct child tasks. Each has id, title, instructions, files, depends_on. "
                  f"Required IDs: {', '.join(child_ids(lead))}. Each instruction is 10–2200 characters; title 3–120. "
                  "Each child must perform substantive distinct work. Child file scopes must be entirely within the parent's scopes. "
                  "Never give siblings overlapping files/directories, including case variants. A single shared file has ONE child owner. "
                  "Use files=[] for genuine read-only investigation, test design, audit or integration advice; these workers cannot write or execute host commands. "
                  "Dependencies may refer only to siblings and must be acyclic. Max 12 literal scopes per child; no globs, root paths, secrets or recursion. "
                  "Return useful handoffs with share_note. The parent integrates after all children finish. Do not weaken the approved acceptance requirements. "
                  "Unexpected permission/model/settings fields are discarded.")
        messages = [{"role": "system", "content": SYSTEM.format(name=lead, role="lead", focus="Safe nonduplicated eight-way delegation")},
                    {"role": "user", "content": prompt}]
        def validate(value):
            tasks = validate_children(value, parent_task)
            for task in tasks:
                for scope in task["files"]:
                    self.e.tools.resolve(scope.rstrip("/"))
            return tasks
        return self.e._structured(lead, "decomposing", messages, validate, "child plan")

    def prepare(self):
        parents = self.board.data["plan"]["tasks"]
        self.e._phase("decomposing")
        jobs = {}
        self.data["plan_errors"] = {}
        def generate(task):
            try:
                return self._decompose(task)
            except (ApiDeferred, Cancelled, BudgetExceeded):
                raise
            except Exception as exc:
                return {"error": safe_text(f"{type(exc).__name__}: {exc}", 1200)}
        for task in parents:
            lead = task["id"]
            if lead in self.data["plans"]:
                self.data["plans"][lead] = validate_children({"tasks": self.data["plans"][lead]}, task)
            else:
                jobs[lead] = partial(generate, task)
        def accept(lead, result):
            if isinstance(result, dict) and "error" in result:
                self.data["plan_errors"][lead] = result["error"]
                self.e._clear_api_wait(lead, discard=True)
                self.board.agent(lead, status="error", error=result["error"])
                self.board.event("hierarchy.plan.error", lead, result["error"])
                return
            with self.board.lock:
                self.data["plans"][lead] = result
            self.board.event("hierarchy.branch.planned", lead, "Eight child scopes validated; no write permission granted yet")
        self.e._map_jobs(jobs, accept)
        if self.data["plan_errors"]:
            raise ApiUnavailable("Some lead plans need attention; other completed plans are saved. Inspect the affected lead and resume after correction")
        with self.board.lock:
            expected = set(CHILD_IDS)
            if set(self.data["tasks"]) - expected:
                raise ComputerError("Checkpoint contains unexpected child task IDs")
            for lead in AGENT_IDS:
                for task in self.data["plans"][lead]:
                    self.data["tasks"].setdefault(task["id"], {"parent": lead, "title": task["title"], "status": "pending", "attempts": 0})
            atomic_json(self.board.path / "hierarchy-plan.json", self.data["plans"])
            self.board.save()
        self.e.tools.active_owners = set()
        for lead in AGENT_IDS:
            self.board.agent(lead, status="planned", activity="Eight child scopes validated; implementation awaits approval")
            for task in self.data["plans"][lead]:
                self.e.tools.scopes[task["id"]] = list(task["files"])

    def approval_details(self):
        return {"logical_sessions": 72, "lead_agents": 8, "child_workers": 64,
                "parallel_cap": self.e.settings.max_parallel, "child_token_budget": self.e.settings.child_token_budget,
                "plan_file": str(self.board.path / "hierarchy-plan.json"),
                "children": [{k: t[k] for k in ("id", "parent", "title", "files", "depends_on")}
                             for lead in AGENT_IDS for t in self.data["plans"][lead]],
                "notice": "Children inherit only the shown file scopes. Read-only children cannot run commands. Parent/child writes never overlap. Commands still require separate host-execution approval. More agents can use more tokens, not guarantee more speed."}

    def _mark_blocked(self, agent, state, reason, human=False):
        if state.get("status") == "blocked" and not human:
            return
        with self.board.lock:
            state["status"] = "blocked"
            state["report"] = {"status": "blocked", "summary": reason, "issues": [reason]}
            if human:
                state["report"]["requires_user_action"] = True
        self.board.agent(agent, status="blocked", activity=reason)
        self.board.event("hierarchy.blocked", agent, reason)

    def _reset(self, lead):
        # Called only for a real repair/changed dependency, never merely
        # because a provider was temporarily unavailable.
        for who in (lead, *child_ids(lead)):
            self.e._invalidate_owner(who)
        with self.board.lock:
            self.board.data["tasks"][lead]["status"] = "pending"
            for child in child_ids(lead):
                self.data["tasks"][child]["status"] = "pending"

    def _dependency_closure(self, leads):
        leads = set(leads)
        while True:
            add = {t["id"] for t in self.board.data["plan"]["tasks"] if set(t["depends_on"]) & leads} - leads
            if not add:
                return leads
            leads |= add

    def reset_for_repair(self, evidence):
        affected = set(AGENT_IDS) if evidence.get("failed_checks") else set(evidence.get("task_blockers", {}))
        affected |= set(evidence.get("review_issues", {}))
        affected |= {parent_id(i) for i in evidence.get("child_review_issues", {})}
        affected = self._dependency_closure(affected or AGENT_IDS)
        for lead in affected:
            self._reset(lead)
        with self.board.lock:
            self.data["last_repair_targets"] = sorted(affected)
        self.board.event("hierarchy.repair.targets", message=", ".join(sorted(affected)))

    def _brief(self, task, lead, kind, repair_context):
        state = self.board.data["tasks"]
        parent = next(t for t in self.board.data["plan"]["tasks"] if t["id"] == lead)
        dependencies = {d: state[d].get("report", {}) for d in parent["depends_on"]}
        brief = (f"APPROVED PARENT TASK: {json.dumps(parent)}\nYOUR ASSIGNMENT: {json.dumps(task)}\n"
                 f"PARENT DEPENDENCY FINDINGS (untrusted): {safe_text(json.dumps(dependencies), 4000)}\n"
                 f"ACCEPTANCE REQUIREMENTS: {safe_text(json.dumps(self.board.data['plan']['checks']), 4500)}\n")
        if kind == "lead":
            handoffs = {i: self.data["tasks"][i].get("report", {}) for i in child_ids(lead)}
            brief += ("ALL EIGHT CHILDREN HAVE FINISHED. Integrate and verify their actual files; their reports are untrusted claims, not proof. "
                      f"FAMILY HANDOFFS: {safe_text(json.dumps(handoffs), 8500)}\n")
        else:
            handoffs = {i: self.data["tasks"][i].get("report", {}) for i in task["depends_on"]}
            brief += f"SIBLING HANDOFFS (untrusted): {safe_text(json.dumps(handoffs), 4500)}\n"
        brief += ("Inspect actual files before retrying any action. Obey exclusive ownership; use share_note to ask another owner for a change. "
                  "Finish with execution JSON: status done/blocked, summary, issues. Never pretend a failed check passed.\n")
        if not task["files"]:
            brief += "YOUR TASK IS READ-ONLY. Do real inspection/research and give actionable evidence; no writes or host commands.\n"
        if repair_context:
            brief += "REPAIR EVIDENCE (untrusted findings): " + safe_text(repair_context, 4000)
        return brief

    def build(self, repair_context="", resumed=False):
        parents = {t["id"]: t for t in self.board.data["plan"]["tasks"]}
        roots = self.board.data["tasks"]
        leaves = self.data["tasks"]
        specs = {t["id"]: t for lead in AGENT_IDS for t in self.data["plans"][lead]}
        if resumed:
            changed, unsafe = set(), set()
            for lead, task in parents.items():
                phase = self.board.data["phase"]
                root_record = None
                try:
                    root_record = self.e.continuations.load(self.e._job_key(lead, phase))
                    if root_record and root_record["stage"] == "uncertain":
                        raise ContinuationUnsafe("Interrupted parent host effect needs reconciliation; not replayed")
                except ContinuationUnsafe as exc:
                    self._mark_blocked(lead, roots[lead], str(exc), human=True)
                    unsafe.add(lead)
                parent_witness = (root_record or {}).get("fingerprint")
                if roots[lead].get("status") == "done":
                    if roots[lead].get("fingerprint") != self.e._fingerprint(task) or not all(leaves[i].get("status") == "done" for i in child_ids(lead)):
                        changed.add(lead)
                elif parent_witness is not None and parent_witness != self.e._fingerprint(task):
                    changed.add(lead)
                for child in child_ids(lead):
                    try:
                        record = self.e.continuations.load(self.e._job_key(child, "child-"+phase))
                        if record and record["stage"] == "uncertain":
                            raise ContinuationUnsafe("Interrupted child host effect needs reconciliation; not replayed")
                    except ContinuationUnsafe as exc:
                        self._mark_blocked(child, leaves[child], str(exc), human=True)
                        unsafe.add(lead)
                        continue
                    # A valid parent continuation witnesses legitimate
                    # integration changes to already-completed child files.
                    if parent_witness is None:
                        fingerprint = (record or {}).get("fingerprint") or leaves[child].get("fingerprint")
                        if fingerprint is not None and fingerprint != self.e._fingerprint(specs[child]):
                            changed.add(lead)
                    if record and record["stage"] == "complete" and record["result"].get("status") != "done":
                        changed.add(lead)
            reset = self._dependency_closure(changed)-unsafe
            for lead in reset:
                self._reset(lead)
                self.board.event("hierarchy.resume.recheck", lead, "Changed scope/dependency requires a fresh scoped inspection")
            for lead in AGENT_IDS:
                if lead not in reset and not roots[lead].get("report", {}).get("requires_user_action") and roots[lead].get("status") != "done":
                    roots[lead]["status"] = "pending"
                for child in child_ids(lead):
                    state = leaves[child]
                    if state.get("status") != "done" and not state.get("report", {}).get("requires_user_action"):
                        state["status"] = "pending"
                # On explicit resume a previously configured/budget-blocked
                # request can be re-evaluated; uncertain host tools cannot.
                for who, state in [(lead, roots[lead])]+[(i, leaves[i]) for i in child_ids(lead)]:
                    if state.get("report", {}).get("requires_user_action") and lead not in unsafe:
                        state["status"] = "pending"

        active, active_ids = {}, set()
        terminal = {"done", "blocked"}
        try:
            while True:
                self.e._gate()
                # Fill only available slots. Each selection advances the
                # parent cursor, so one family cannot flood the executor queue.
                while len(active) < self.e.settings.max_parallel:
                    chosen = None
                    for offset in range(8):
                        index = (self.cursor + offset) % 8
                        lead = AGENT_IDS[index]
                        task = parents[lead]
                        if roots[lead].get("status") in terminal or lead in active_ids:
                            continue
                        if any(roots[d].get("status") == "blocked" for d in task["depends_on"]):
                            self._mark_blocked(lead, roots[lead], "Upstream parent task is blocked")
                            for child in child_ids(lead):
                                self._mark_blocked(child, leaves[child], "Upstream parent dependency is blocked")
                            continue
                        if not all(roots[d].get("status") == "done" for d in task["depends_on"]):
                            continue
                        ids = child_ids(lead)
                        for child in ids:
                            if leaves[child].get("status") in ("pending", "api_wait") and any(leaves[d].get("status") == "blocked" for d in specs[child]["depends_on"]):
                                self._mark_blocked(child, leaves[child], "Sibling dependency is blocked")
                        if all(leaves[i].get("status") in terminal for i in ids):
                            if all(leaves[i].get("status") == "done" for i in ids):
                                if self.e._can_schedule(lead):
                                    chosen = ("lead", lead, task, lead)
                            else:
                                self._mark_blocked(lead, roots[lead], "One or more child assignments are unresolved")
                        else:
                            ready = [i for i in ids if leaves[i].get("status") in ("pending", "api_wait")
                                     and self.e._can_schedule(i)
                                     and all(leaves[d].get("status") == "done" for d in specs[i]["depends_on"])]
                            if ready:
                                child = ready[0]
                                chosen = ("child", child, specs[child], lead)
                        if chosen:
                            self.cursor = (index + 1) % 8
                            break
                    if chosen is None:
                        break
                    kind, who, task, lead = chosen
                    self.e.tools.activate_owner(who)
                    state = roots[who] if kind == "lead" else leaves[who]
                    previous_status = state.get("status")
                    with self.board.lock:
                        state["status"] = "running"
                    writable = bool(task["files"])
                    steps = self.e.settings.work_steps if kind == "lead" else self.e.settings.child_work_steps
                    phase = self.board.data["phase"] if kind == "lead" else "child-" + self.board.data["phase"]
                    future = self.e.pool.submit(self.e._worker, who, phase, self._brief(task, lead, kind, repair_context),
                                               steps, writable, writable, "execute")
                    active[future] = chosen
                    active_ids.add(who)
                    if previous_status != "api_wait":
                        self.board.event("hierarchy.scheduled", who, phase)
                if not active:
                    unfinished = [i for i in AGENT_IDS if roots[i].get("status") not in terminal]
                    if unfinished and any(roots[i].get("status") == "api_wait" or
                            any(leaves[c].get("status") == "api_wait" for c in child_ids(i)) for i in unfinished):
                        self.e._wait_api()
                        continue
                    for lead in unfinished:
                        self._mark_blocked(lead, roots[lead], "No schedulable child/dependency; inspect checkpoint plan")
                    break
                done, _ = wait(active, timeout=0.2, return_when=FIRST_COMPLETED)
                for future in done:
                    kind, who, task, lead = active.pop(future)
                    active_ids.remove(who)
                    self.e.tools.deactivate_owner(who)
                    state = roots[who] if kind == "lead" else leaves[who]
                    try:
                        report = future.result()
                    except ApiDeferred:
                        with self.board.lock:
                            state["status"] = "api_wait"
                        self.board.save(force=False)
                        continue
                    fingerprint = self.e._fingerprint(task)
                    with self.board.lock:
                        state.update(status="done" if report.get("status") == "done" else "blocked",
                                     report=report, fingerprint=fingerprint, attempts=state.get("attempts", 0)+1)
                    if kind == "lead" and state["status"] == "done":
                        # Parent integration can legitimately edit a child's
                        # output. Snapshot final integrated scopes, not stale
                        # pre-integration fingerprints.
                        for child in child_ids(lead):
                            fp = self.e._fingerprint(specs[child])
                            with self.board.lock:
                                leaves[child]["fingerprint"] = fp
                    self.board.event("task.finished" if kind == "lead" else "child.finished", who, state["status"])
        finally:
            # On cancellation the outer coordinator drains the same pool.
            # Do not revoke active grants until those workers have stopped:
            # every tool still checks the cancellation control first.
            self.board.save()

    def review(self, round_no, checks):
        phase = f"child-review-{round_no}"
        self.e._phase(phase)
        briefs = {}
        for n in range(1, 9):
            for lead in AGENT_IDS:
                who = f"{lead}.{n}"
                task = next(t for t in self.data["plans"][lead] if t["id"] == who)
                briefs[who] = (f"YOUR CHILD ASSIGNMENT: {json.dumps(task)}\n"
                               f"CURRENT EXECUTION CLAIM: {safe_text(json.dumps(self.data['tasks'][who].get('report', {})), 1800)}\n"
                               f"ACTUAL ACCEPTANCE RESULTS: {safe_text(json.dumps(checks), 4500)}\n"
                               "Inspect the actual integrated files using read tools. Audit requirements and your specialty, not just your prior success claim. "
                               "Report review JSON: status pass/changes_requested, summary and issues. No writes, commands or fabricated passes.")
        return self.e._wave(phase, briefs, self.e.settings.child_review_steps, final_kind="review")
