"""Durable, bounded state for the eight-agent computer mode.

No model weights, subprocess pools, or database servers are loaded here.
Checkpoint files are recovery records, not a claim that a task was verified.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

ROLES = (
    ("a1", "Atlas", "architect", "Architecture, interfaces, integration and acceptance criteria"),
    ("a2", "Scout", "researcher", "Primary sources, alternatives, licenses and evidence gaps"),
    ("a3", "Forge", "implementer", "Core implementation and end-to-end functionality"),
    ("a4", "Link", "integrator", "APIs, adapters, interoperability and failure handling"),
    ("a5", "Probe", "tester", "Regression, edge cases and meaningful automated tests"),
    ("a6", "Shield", "security", "Threat model, permissions, secrets and dependency risks"),
    ("a7", "Pulse", "performance", "Memory, latency, resource bounds and observability"),
    ("a8", "Guide", "delivery", "Documentation, packaging, reproducibility and operation"),
)
AGENT_IDS = tuple(row[0] for row in ROLES)  # compatibility: the eight leads
CHILD_ROLES = (
    ("scout", "Find primary-source facts and constraints"),
    ("designer", "Design interfaces and edge cases"),
    ("builder", "Implement an assigned, exclusive file scope"),
    ("connector", "Check integrations and compatibility"),
    ("tester", "Create or inspect meaningful tests"),
    ("auditor", "Audit correctness, security and permissions"),
    ("optimizer", "Inspect performance and resource use"),
    ("finisher", "Document, package and check completion gaps"),
)


def child_ids(lead_id):
    if lead_id not in AGENT_IDS:
        raise ValueError("Lead ID must be a1 through a8")
    return tuple(f"{lead_id}.{i}" for i in range(1, 9))


CHILD_IDS = tuple(child for lead in AGENT_IDS for child in child_ids(lead))
ALL_AGENT_IDS = AGENT_IDS + CHILD_IDS


def parent_id(agent_id):
    if agent_id in AGENT_IDS:
        return None
    if agent_id not in CHILD_IDS:
        raise ValueError("Agent ID must be a1–a8 or a1.1–a8.8; deeper recursion is not allowed")
    return agent_id.split(".", 1)[0]


def agent_specs(hierarchy_enabled=True):
    specs = list(ROLES)
    if hierarchy_enabled:
        for lead, lead_name, lead_role, lead_focus in ROLES:
            for index, (role, focus) in enumerate(CHILD_ROLES, 1):
                specs.append((f"{lead}.{index}", f"{lead_name}/{index}", role,
                              f"For {lead_name}'s {lead_role} workstream ({lead_focus}): {focus}"))
    return tuple(specs)


def agent_row(agent_id, name, role, focus):
    return {"id": agent_id, "name": name, "role": role, "parent": parent_id(agent_id),
            "status": "idle", "activity": focus, "steps": 0, "tools": 0,
            "tokens_in": 0, "tokens_out": 0, "estimated_tokens": 0,
            "charged_tokens": 0, "reserved_tokens": 0, "requests": 0,
            "model": "", "error": ""}


def inherited_model(agent_id, overrides, default):
    parent = parent_id(agent_id)
    return overrides.get(agent_id, overrides.get(parent, default) if parent else default)

TERMINAL_STATES = {"completed", "needs_attention", "planned", "cancelled", "budget_exhausted", "error"}


class ComputerError(RuntimeError):
    pass


class Cancelled(ComputerError):
    pass


class BudgetExceeded(ComputerError):
    pass


class WorkerBudgetExceeded(ComputerError):
    """A single child has exhausted its own allowance, not the entire mission."""
    pass


@dataclass(frozen=True)
class Settings:
    max_parallel: int = 8
    plan_rounds: int = 2
    research_steps: int = 5
    work_steps: int = 24
    review_steps: int = 5
    repair_rounds: int = 2
    token_budget: int = 400_000
    max_output_tokens: int = 4096
    max_context_chars: int = 64_000
    max_result_chars: int = 8_000
    max_file_bytes: int = 524_288
    wall_minutes: int = 60
    request_timeout: int = 45
    command_timeout: int = 120
    network: bool = True
    plan_only: bool = False
    hierarchy_enabled: bool = True
    child_research_steps: int = 3
    child_work_steps: int = 12
    child_review_steps: int = 3
    child_output_tokens: int = 2048
    child_context_chars: int = 32000
    child_token_budget: int = 50000
    requests_per_minute: int = 0  # 0 = adaptive ceiling (60 RPM), or no fixed pacing if adaptive is off
    api_max_parallel: int = 2     # independent of the local worker-pool cap
    api_adaptive: bool = True    # one initial API lane, 12 RPM slow-start
    api_tokens_per_minute: int = 60000  # conservative estimated traffic budget, not provider entitlement
    api_cooldown_seconds: int = 5

    def __post_init__(self):
        bounds = {
            "max_parallel": (1, 64), "plan_rounds": (1, 4),
            "research_steps": (1, 15), "work_steps": (2, 100),
            "review_steps": (1, 15), "repair_rounds": (0, 5),
            "token_budget": (1000, 10_000_000), "max_output_tokens": (512, 16384),
            "max_context_chars": (8000, 128_000), "max_result_chars": (1000, 16000),
            "max_file_bytes": (4096, 2_000_000), "wall_minutes": (1, 480),
            "request_timeout": (5, 180), "command_timeout": (1, 600),
            "child_research_steps": (1, 10), "child_work_steps": (2, 50),
            "child_review_steps": (1, 10), "child_output_tokens": (512, 8192),
            "child_context_chars": (8000, 64000), "child_token_budget": (1000, 1000000),
            "requests_per_minute": (0, 6000), "api_max_parallel": (1, 64),
            "api_tokens_per_minute": (0, 10_000_000), "api_cooldown_seconds": (1, 300),
        }
        for key, (lo, hi) in bounds.items():
            value = getattr(self, key)
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError(f"{key} must be an integer between {lo} and {hi}")
        for key in ("network", "plan_only", "hierarchy_enabled", "api_adaptive"):
            if type(getattr(self, key)) is not bool:
                raise ValueError(f"{key} must be a boolean")

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        valid = {f.name for f in fields(cls)}
        unknown = set(data) - valid
        if unknown:
            raise ValueError("Unknown computer settings: " + ", ".join(sorted(unknown)))
        return cls(**data)


def utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_text(value: Any, limit: int = 2000) -> str:
    """Remove terminal control sequences and redact obvious credentials.

    This is defence in depth, not a general secret-detection guarantee.
    Tool arguments containing secrets are never deliberately logged.
    """
    text = str(value)
    text = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = "".join(c for c in text if c in "\n\t" or (c.isprintable() and c != "\x7f"))
    text = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,}|AKIA[A-Z0-9]{16})\b", "[REDACTED]", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*[\"']?)[^\s\"'&,}]{8,}", r"\1[REDACTED]", text)
    return text if len(text) <= limit else text[:limit] + "\n[truncated]"


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("x", encoding="utf-8") as f:
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def mission_dir(store: Path, mission_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{12}", str(mission_id)):
        raise ValueError("Invalid mission ID; use /computer list")
    path = store / mission_id
    if path.is_symlink():
        raise ComputerError("Refusing a symlinked mission directory")
    return path


def load_checkpoint(store: Path, mission_id: str) -> dict:
    path = mission_dir(store, mission_id) / "state.json"
    if path.is_symlink() or path.stat().st_size > 16_000_000:
        raise ComputerError("Unsafe or oversized checkpoint")
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("version") not in (1, 2, 3)
            or data.get("id") != mission_id or not isinstance(data.get("goal"), str)
            or not isinstance(data.get("root"), str)):
        raise ComputerError("Unsupported or invalid checkpoint")
    return data


def list_missions(store: Path, root: Path | None = None) -> list[dict]:
    if not store.exists():
        return []
    result = []
    paths = sorted((p for p in store.iterdir() if re.fullmatch(r"[a-f0-9]{12}", p.name)),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths[:100]:
        try:
            d = load_checkpoint(store, path.name)
            if root is None or d["root"] == str(root.resolve()):
                result.append({k: d.get(k) for k in ("id", "goal", "status", "phase", "updated_at", "root")})
        except (OSError, ValueError, ComputerError):
            continue
    return result


class WorkspaceLease:
    """Cross-process cooperative lock, released by the OS on a crash.

    This prevents *computer-mode* runs from sharing a workspace. It cannot
    stop editors, legacy tools, or unrelated processes from changing files.
    """
    def __init__(self, store: Path, root: Path):
        folder = store / "locks"
        folder.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(str(root.resolve()).encode()).hexdigest()
        self.path = folder / (key + ".lock")
        self.handle = None

    def acquire(self):
        if self.path.is_symlink():
            raise ComputerError("Refusing a symlinked workspace lock")
        self.handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.write(b"\0")
                self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, ImportError) as exc:
            self.handle.close()
            self.handle = None
            raise ComputerError("Another computer-mode run owns this workspace (or locking is unavailable)") from exc

    def release(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None


class Board:
    def __init__(self, root: Path, store: Path, goal: str, settings: Settings,
                 previous: dict | None = None, listener=None):
        self.lock = threading.RLock()
        self.settings = settings
        self.listener = listener
        self.recent = deque(maxlen=80)
        self.started = time.monotonic()
        self.prior_elapsed = float((previous or {}).get("elapsed_seconds", 0))
        self.data = copy.deepcopy(previous) if previous else {
            "version": 3, "id": uuid.uuid4().hex[:12], "root": str(root.resolve()),
            "goal": goal, "created_at": utc_now(), "status": "ready", "phase": "ready",
            "settings": asdict(settings), "plan": None, "reports": {}, "tasks": {},
            "checks": [], "sources": [], "completed_phases": [], "repair_round": 0,
            "agents": {i: agent_row(i, name, role, focus)
                       for i, name, role, focus in agent_specs(settings.hierarchy_enabled)},
            "hierarchy": {"enabled": settings.hierarchy_enabled, "plans": {}, "tasks": {}},
            "inflight": {},
            "charged_tokens": 0, "reported_tokens": 0, "estimated_tokens": 0,
            "reserved_tokens": 0, "requests": 0, "sequence": 0, "files": [],
            "error": "", "elapsed_seconds": 0,
        }
        self.path = mission_dir(store, self.data["id"])
        self.path.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path, 0o700)
        except OSError:
            pass
        # Version-1 checkpoints are eight-lead mode; the controller checks
        # mode compatibility before calling Board. Never silently create
        # extra billed workers during recovery.
        self.data["version"] = 3
        self.data.setdefault("continuations", {})
        self.data.setdefault("job_generations", {})
        self.data.setdefault("api", {})
        self.data["api_waits"] = {}
        self.data.setdefault("hierarchy", {"enabled": settings.hierarchy_enabled, "plans": {}, "tasks": {}})
        for i, name, role, focus in agent_specs(settings.hierarchy_enabled):
            row = self.data["agents"].setdefault(i, agent_row(i, name, role, focus))
            row.setdefault("parent", parent_id(i))
            row.setdefault("charged_tokens", row.get("tokens_in", 0) + row.get("tokens_out", 0) + row.get("estimated_tokens", 0))
            row.setdefault("reserved_tokens", 0)
            row.setdefault("requests", 0)
        pending = int(self.data.get("reserved_tokens", 0))
        if previous and pending:
            self.data["charged_tokens"] += pending
            self.data["estimated_tokens"] += pending
            # Attribute interrupted reservations to their workers when the
            # version-2 ledger has that information. No double accounting.
            for entry in self.data.get("inflight", {}).values():
                who, amount = entry.get("agent"), int(entry.get("amount", 0))
                if who in self.data["agents"]:
                    row = self.data["agents"][who]
                    row["charged_tokens"] += amount
                    row["estimated_tokens"] += amount
        for row in self.data["agents"].values():
            row["reserved_tokens"] = 0
        self.data["reserved_tokens"] = 0
        self.data["inflight"] = {}
        self.data["settings"] = asdict(settings)
        self._reservations = {}
        self._last_save = 0.0
        self.frozen_elapsed = None
        self.save()

    def _elapsed(self):
        return self.frozen_elapsed if self.frozen_elapsed is not None else self.prior_elapsed + time.monotonic() - self.started

    def snapshot(self) -> dict:
        with self.lock:
            snap = copy.deepcopy(self.data)
            snap["elapsed_seconds"] = round(self._elapsed(), 1)
            snap["recent"] = list(self.recent)
            snap["state_dir"] = str(self.path)
            return snap

    def save(self, force=True):
        with self.lock:
            if not force and time.monotonic() - self._last_save < 0.5:
                return
            self.data["elapsed_seconds"] = round(self._elapsed(), 1)
            self.data["updated_at"] = utc_now()
            atomic_json(self.path / "state.json", self.data)
            self._last_save = time.monotonic()

    def event(self, kind: str, agent: str = "system", message: str = "", **details):
        with self.lock:
            self.data["sequence"] += 1
            item = {"seq": self.data["sequence"], "at": utc_now(), "kind": kind,
                    "agent": agent, "message": safe_text(message, 2000), **details}
            self.recent.append(item)
            logpath = self.path / "events.jsonl"
            # On-disk event log is bounded too; previous segment retained.
            if logpath.exists() and logpath.stat().st_size > 8_000_000:
                os.replace(logpath, self.path / "events.previous.jsonl")
            with logpath.open("a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
            self.save(force=kind not in ("activity", "output"))
        if self.listener:
            try:
                self.listener(item)
            except Exception:
                pass  # UI listeners cannot invalidate tool execution
        return item

    def agent(self, agent_id: str, **changes):
        with self.lock:
            self.data["agents"][agent_id].update(changes)
        self.save(force=False)

    def reserve(self, amount: int, agent_id: str | None = None) -> str:
        if type(amount) is not int or amount <= 0:
            raise ValueError("Reservation must be a positive integer")
        with self.lock:
            used = self.data["charged_tokens"] + self.data["reserved_tokens"]
            if used + amount > self.settings.token_budget:
                raise BudgetExceeded("Global token reservation limit reached; extend token_budget explicitly before resuming")
            row = self.data["agents"].get(agent_id) if agent_id else None
            if agent_id is not None and row is None:
                raise ValueError("Unknown agent for reservation")
            if row and parent_id(agent_id) is not None:
                if row["charged_tokens"] + row["reserved_tokens"] + amount > self.settings.child_token_budget:
                    raise WorkerBudgetExceeded(f"{agent_id} reached child_token_budget; other branches can continue")
            ticket = uuid.uuid4().hex
            entry = {"amount": amount, "agent": agent_id}
            self._reservations[ticket] = entry
            self.data["inflight"][ticket] = entry
            self.data["reserved_tokens"] += amount
            self.data["requests"] += 1
            if row is not None:
                row["reserved_tokens"] += amount
                row["requests"] += 1
            self.save()
            return ticket

    def release_unsent(self, ticket: str, agent_id: str):
        """Release ONLY a request known not to have entered the API client."""
        with self.lock:
            entry = self._reservations[ticket]
            if entry["agent"] != agent_id:
                raise ValueError("Reservation belongs to a different agent")
            self._reservations.pop(ticket)
            self.data["inflight"].pop(ticket, None)
            self.data["reserved_tokens"] -= entry["amount"]
            self.data["requests"] -= 1
            row = self.data["agents"][agent_id]
            row["reserved_tokens"] -= entry["amount"]
            row["requests"] -= 1
            self.save()

    def settle(self, ticket: str, agent_id: str, usage: dict | None, output_estimate: int = 0):
        with self.lock:
            entry = self._reservations[ticket]
            if entry["agent"] is not None and entry["agent"] != agent_id:
                raise ValueError("Reservation belongs to a different agent")
            reserved = entry["amount"]
            self._reservations.pop(ticket)
            self.data["inflight"].pop(ticket, None)
            self.data["reserved_tokens"] -= reserved
            agent = self.data["agents"][agent_id]
            if entry["agent"] is not None:
                agent["reserved_tokens"] -= reserved
            valid = (isinstance(usage, dict) and
                     type(usage.get("prompt_tokens")) is int and usage["prompt_tokens"] >= 0 and
                     type(usage.get("completion_tokens")) is int and usage["completion_tokens"] >= 0)
            if valid:
                tin, tout = usage["prompt_tokens"], usage["completion_tokens"]
                reported_total = usage.get("total_tokens", 0)
                total = max(tin + tout, reported_total if type(reported_total) is int else 0)
                self.data["reported_tokens"] += total
                agent["tokens_in"] += tin
                agent["tokens_out"] += tout
            else:
                total = reserved
                self.data["estimated_tokens"] += total
                agent["estimated_tokens"] += total
            agent["charged_tokens"] += total
            self.data["charged_tokens"] += total
            self.save()

    def dashboard_snapshot(self):
        """Small render projection; never deep-copy all 72 conversations/reports."""
        with self.lock:
            keys = ("id", "root", "goal", "status", "phase", "settings", "charged_tokens",
                    "reported_tokens", "estimated_tokens", "reserved_tokens", "requests", "error", "api", "api_waits")
            d = {k: copy.deepcopy(self.data.get(k)) for k in keys}
            d["agents"] = copy.deepcopy(self.data["agents"])
            d["tasks"] = {i: {"title": t.get("title", ""), "status": t.get("status", "pending")}
                          for i, t in self.data["tasks"].items()}
            hierarchy = self.data.get("hierarchy", {})
            d["hierarchy"] = {"enabled": hierarchy.get("enabled", False),
                              "tasks": {i: {"title": t.get("title", ""), "status": t.get("status", "pending"), "parent": t.get("parent")}
                                        for i, t in hierarchy.get("tasks", {}).items()}}
            d["checks"] = [{"ok": c.get("ok", False), "description": c.get("description", "")}
                           for c in self.data["checks"]]
            d["sources"] = [{"source": s.get("source")} for s in self.data["sources"]]
            d["recent"] = list(self.recent)[-12:]
            d["elapsed_seconds"] = round(self._elapsed(), 1)
            return d

    def finish(self, status: str, error: str = ""):
        with self.lock:
            self.data["status"] = status
            self.data["error"] = safe_text(error, 3000)
            self.frozen_elapsed = self._elapsed()
            for a in self.data["agents"].values():
                if a["status"] in ("running", "waiting", "queued", "paused"):
                    a["status"] = "stopped"
            self.save()
        self.event("mission.finished", message=status + (": " + error if error else ""))
