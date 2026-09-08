"""Pure, bounded terminal dashboard rendering; no fabricated progress.

The same rendering is used by the live TUI and width/visual regression tests.
Values show actual checkpoints and events, not decorative simulations.
"""
from __future__ import annotations

import unicodedata
import time
from .state import ROLES, safe_text, child_ids, CHILD_IDS, AGENT_IDS, parent_id

BLUE = "#5E9FE8"
GREEN = "#72BC8F"
ORANGE = "#DE9255"
RED = "#E97366"
DIM = "#999999"
FG = "#EAEAEA"


def cells(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def fit(value, width, pad=False):
    text = safe_text(value, 16000).replace("\n", " ").replace("\t", " ")
    width = max(0, width)
    if cells(text) > width:
        out, used = [], 0
        for c in text:
            size = cells(c)
            if used + size > max(0, width-1):
                break
            out.append(c)
            used += size
        text = "".join(out) + ("…" if width else "")
    if pad:
        text += " " * max(0, width-cells(text))
    return text


def tone(status):
    if status in ("done", "completed", "pass"):
        return GREEN
    if status in ("error", "needs_attention", "blocked", "budget_exhausted"):
        return RED
    if status in ("waiting", "api_wait", "paused", "planned", "cancelling", "cancelled"):
        return ORANGE
    if status == "idle":
        return DIM
    return BLUE


def api_summary(snapshot):
    api = snapshot.get("api") or {}
    settings = snapshot.get("settings", {})
    cap = api.get("max_parallel_per_domain", min(settings.get("max_parallel", 8), settings.get("api_max_parallel", 2)))
    if not api.get("enabled"):
        start = 1 if settings.get("api_adaptive", True) else cap
        return f" API idle · start {start}/domain · API cap {cap} · /computer api"
    return (f" API {api.get('active', 0)} in flight · {api.get('queued', 0)} queued · cap {cap}/domain"
            f" · {api.get('recovering', 0)} recovering · {api.get('blocked', 0)} blocked")


def api_text(snapshot):
    api = snapshot.get("api") or {}
    settings = snapshot.get("settings", {})
    out = ["API CAPACITY / RECOVERY", api_summary(snapshot).strip(),
           f"Local worker slots: {settings.get('max_parallel', 8)}; separate from API concurrency.",
           "Queued workers are parked, not failed and not secretly running model calls."]
    if not api.get("domains"):
        cap = min(settings.get("max_parallel", 8), settings.get("api_max_parallel", 2))
        adaptive = settings.get("api_adaptive", True)
        ceiling = settings.get("requests_per_minute", 0) or (60 if adaptive else 0)
        if adaptive:
            policy = f"adaptive initial window 1, cap {cap}/domain; warmup {min(12, ceiling):g} RPM, ceiling {ceiling:g}"
        else:
            pacing = f"{ceiling:g} RPM" if ceiling else "no fixed RPM pacing"
            policy = f"fixed window {cap}/domain; {pacing} (server limits still apply)"
        out += ["No model requests yet. Configured admission: " + policy + ".",
                "Estimated token-rate budget is a local policy, not a promised provider quota."]
    for row in api.get("domains", []):
        mode = {"closed": "ready", "open": "cooldown", "half_open": "one recovery probe", "blocked": "needs correction"}.get(row.get("mode"), "unknown")
        delay = max(0, row.get("not_before_epoch", 0)-time.time())
        pacing = f"{row.get('rpm', 0):g} RPM" if row.get('rpm', 0) else "no fixed RPM pacing"
        token_policy = f"{row.get('tpm', 0):g}/min" if row.get('tpm', 0) else "local token pacing disabled"
        out += ["", f"{safe_text(row.get('label', 'provider'), 100)} — {mode}",
                f"  In flight {row.get('active', 0)} / current window {row.get('cap', 1)}; queued {row.get('queued', 0)}",
                f"  Effective pacing {pacing}; estimated token-rate policy {token_policy}",
                f"  Attempts {row.get('attempts', 0)}; successful {row.get('successes', 0)}; recovery probes {row.get('probes', 0)}",
                f"  Rate faults {row.get('congestions', 0)}; availability faults {row.get('outages', 0)}; cooldown {delay:.1f}s"]
        if row.get("last_reason"):
            out.append("  Last signal: "+safe_text(row["last_reason"], 350))
        if row.get("blocked"):
            out.append("  Action: correct credentials/billing locally and explicitly resume; no automatic key/provider rotation.")
    out += ["", "Transient failures preserve conversations/tool receipts and use one shared recovery probe.",
            "Unknown usage remains conservatively estimated. Existing token/time/approval limits still apply.",
            "During a total provider outage, API-dependent thinking waits; no offline/fake progress is claimed.",
            "Scope: this running Computer, not a distributed or cross-process API gateway."]
    return "\n".join(out)


def dashboard_lines(snapshot, width=100, height=19):
    """Return (style, text) lines, each within terminal display-cell width."""
    width, height = max(12, int(width)), max(1, int(height))
    settings = snapshot.get("settings", {})
    status = snapshot.get("status", "ready")
    phase = snapshot.get("phase", "ready")
    elapsed = int(snapshot.get("elapsed_seconds", 0))
    title = f" COMPUTER  /on  ·  {status.upper()}  ·  {phase}  ·  {elapsed//60:02d}:{elapsed%60:02d}"
    out = [(f"bold {tone(status)}", title)]
    cap = settings.get("max_parallel", 8)
    hierarchical = snapshot.get("hierarchy", {}).get("enabled", False)
    topology = "8 leads + 64 workers" if hierarchical else "8 agents"
    out.append((DIM, f" {topology} · active cap {cap} · {snapshot.get('root', '.')}"))
    budget = settings.get("token_budget", 0)
    used, reserved = snapshot.get("charged_tokens", 0), snapshot.get("reserved_tokens", 0)
    measured, estimated = snapshot.get("reported_tokens", 0), snapshot.get("estimated_tokens", 0)
    if height >= 13:
        out.append((FG, f" tokens {measured:,} reported + {estimated:,} est. | reserved {reserved:,} | cap {budget:,}"))
    if height >= 13:
        api = snapshot.get("api") or {}
        out.append((ORANGE if api.get("recovering") or api.get("queued") else DIM, api_summary(snapshot)))
    if height >= 16:
        out.append((DIM, "─" * width))
    agents = snapshot.get("agents", {})
    tasks = snapshot.get("tasks", {})
    for agent_id, name, role, focus in ROLES:
        a = agents.get(agent_id, {})
        state = a.get("status", "idle")
        family = [agents.get(i, {}) for i in child_ids(agent_id)] if hierarchical else []
        active = [(i, agents.get(i, {})) for i in child_ids(agent_id)
                  if agents.get(i, {}).get("status") in ("running", "waiting", "queued", "paused")] if hierarchical else []
        child_tasks = snapshot.get("hierarchy", {}).get("tasks", {})
        done_children = sum(child_tasks.get(i, {}).get("status") == "done" for i in child_ids(agent_id)) if hierarchical else 0
        running_children = [pair for pair in active if pair[1].get("status") == "running"]
        waiting_children = [pair for pair in active if pair[1].get("status") != "running"]
        if active and state not in ("error", "blocked", "running"):
            state = "running" if running_children else "waiting"
        tools = int(a.get("tools", 0)) + sum(int(c.get("tools", 0)) for c in family)
        charged = int(a.get("charged_tokens", a.get("tokens_in", 0)+a.get("tokens_out", 0))) + sum(int(c.get("charged_tokens", 0)) for c in family)
        activity = a.get("error") or a.get("activity", focus)
        if tasks.get(agent_id, {}).get("status") == "blocked":
            state = "blocked"
            activity = "Implementation blocked; /computer inspect " + agent_id
        if active:
            who, working = (running_children or waiting_children)[0]
            activity = who + " · " + (working.get("error") or working.get("activity", "working"))
        head = f" {agent_id} {name:<6} {state:<8}"
        if hierarchical and width >= 80:
            head += f" {len(running_children)}run {len(waiting_children)}wait {done_children}/8 {tools:>3}t "
            if width >= 120:
                head += f"{charged:>7} tok "
        elif width >= 100:
            head += f" {role:<12} {tools:>3} tools {charged:>7} tok "
        elif width >= 70:
            head += f" {tools:>3} tools {charged:>6} tok "
        else:
            head += f" {tools:>2}t "
        out.append((tone(state), head + fit(activity, max(0, width-cells(head)))))
    checks = snapshot.get("checks", [])
    passed = sum(bool(c.get("ok")) for c in checks)
    done = sum(t.get("status") == "done" for t in tasks.values())
    child_done = sum(t.get("status") == "done" for t in snapshot.get("hierarchy", {}).get("tasks", {}).values())
    task_label = f" leads {done}/8 · workers {child_done}/64" if hierarchical else f" tasks {done}/8 reported done"
    out.append((DIM, f"{task_label} · checks {passed}/{len(checks)} passed · sources {len(snapshot.get('sources', []))}"))
    available = height - len(out) - 1
    events = snapshot.get("recent", [])
    if available > 0:
        if events:
            for event in events[-min(available, 5):]:
                message = event.get("message", "").replace("\n", " ⏎ ")
                actor = event.get("agent", "system")
                kind = event.get("kind", "event")
                out.append((ORANGE if "error" in kind else DIM, f" {actor} · {kind}: {message}"))
        else:
            out.append((DIM, " Type your project goal. Research uses your selected API model; no model call until you submit."))
    footer = " /computer tree a1 · pause|resume|cancel|report · Ctrl+C cancel · /off"
    if width < 80:
        footer = " /computer help · Ctrl+C cancel · /off"
    out.append((BLUE, footer))
    # Extremely short terminals show a compact status rather than overflow.
    if len(out) > height:
        out = out[:max(0, height-1)] + [out[-1]]
    return [(style, fit(text, width)) for style, text in out]


def fragments(snapshot, width=100, height=19):
    result = []
    for index, (style, line) in enumerate(dashboard_lines(snapshot, width, height)):
        if index:
            result.append(("", "\n"))
        result.append((style, line))
    return result


def status_text(snapshot):
    return "\n".join(line for _, line in dashboard_lines(snapshot, 120, 20))


def family_lines(snapshot, lead, width=100, height=19):
    if lead not in AGENT_IDS:
        raise ValueError("Choose a lead a1 through a8")
    width, height = max(12, int(width)), max(1, int(height))
    agents = snapshot.get("agents", {})
    states = snapshot.get("hierarchy", {}).get("tasks", {})
    root = agents.get(lead, {})
    out = [("bold " + BLUE, f" FAMILY {lead} · {root.get('name', lead)} · {snapshot.get('phase', 'ready')}"),
           (DIM, api_summary(snapshot)),
           (DIM, "─" * width)]
    header = f" {'ID':<5} {'ROLE':<9} {'SESSION':<8} {'TASK':<10} "
    if width >= 110:
        header += f"{'TOKENS':>6}     {'TOOL':>4} "
    out.append((DIM, header + "ACTIVITY"))
    for who in child_ids(lead):
        row, state = agents.get(who, {}), states.get(who, {})
        status = row.get("status", "idle")
        task = state.get("status", "unassigned")
        head = f" {who:<5} {row.get('role', 'worker'):<9} {status:<8} {task:<10} "
        activity = row.get("error") or row.get("activity", "Not started")
        if width >= 110:
            head += f"{row.get('charged_tokens', 0):>6} tok {row.get('tools', 0):>3}t "
        color_state = "blocked" if task == "blocked" else ("idle" if task in ("pending", "unassigned") and status == "done" else status)
        out.append((tone(color_state), head + fit(activity, width-cells(head))))
    total = sum(agents.get(i, {}).get("charged_tokens", 0) for i in (lead, *child_ids(lead)))
    requests = sum(agents.get(i, {}).get("requests", 0) for i in (lead, *child_ids(lead)))
    out += [(DIM, "─" * width), (FG, f" Family total: {total:,} charged tokens (incl. estimates) · {requests} requests"),
            (DIM, " Parent integration starts after all eight child assignments finish."),
            (BLUE, f" /computer inspect {lead}.1 · /computer tree overview · Ctrl+C cancel")]
    if len(out) > height:
        out = out[:max(0, height-1)] + [out[-1]]
    return [(style, fit(text, width)) for style, text in out]


def family_fragments(snapshot, lead, width=100, height=19):
    out = []
    for index, (style, text) in enumerate(family_lines(snapshot, lead, width, height)):
        if index:
            out.append(("", "\n"))
        out.append((style, text))
    return out


def tree_text(snapshot):
    if not snapshot.get("hierarchy", {}).get("enabled"):
        return "Eight-lead mode. Enable /computer swarm on while idle for 8 x 8 delegation."
    agents, states = snapshot.get("agents", {}), snapshot.get("hierarchy", {}).get("tasks", {})
    out = ["8 LEADS -> 64 CHILD WORKERS (72 logical sessions; bounded execution)"]
    for lead, name, role, _ in ROLES:
        out.append(f"{lead} {name} ({role}) — session={agents.get(lead, {}).get('status', 'idle')} task={snapshot.get('tasks', {}).get(lead, {}).get('status', 'unassigned')}")
        for child in child_ids(lead):
            row, state = agents.get(child, {}), states.get(child, {})
            out.append(f"  {child} {row.get('role', 'worker'):<9} session={row.get('status', 'idle'):<8} task={state.get('status', 'unassigned'):<10} tokens={row.get('charged_tokens', 0)}")
    return "\n".join(out)


def inspect_text(snapshot, agent_id):
    import json
    if agent_id not in snapshot.get("agents", {}):
        raise ValueError("Unknown/inactive agent ID; use /computer tree")
    lead = parent_id(agent_id)
    tasks = snapshot.get("hierarchy", {}).get("tasks", {}) if lead else snapshot.get("tasks", {})
    plans = snapshot.get("hierarchy", {}).get("plans", {}).get(lead, []) if lead else (snapshot.get("plan") or {}).get("tasks", [])
    assignment = next((t for t in plans if t["id"] == agent_id), None)
    value = {"agent": snapshot["agents"][agent_id], "assignment": assignment,
             "task": tasks.get(agent_id, {}), "api_wait": snapshot.get("api_waits", {}).get(agent_id),
             "continuations": {k: v for k, v in snapshot.get("continuations", {}).items() if v.get("owner") == agent_id}, "notice": "Actual actions/reports only; no private chain-of-thought is recorded"}
    return safe_text(json.dumps(value, ensure_ascii=False, indent=2), 18000)
