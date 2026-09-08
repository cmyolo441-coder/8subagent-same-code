"""Thin integration with FullAgent's existing persistent prompt_toolkit UI."""
from __future__ import annotations

import json
import shlex
import threading
from dataclasses import asdict, replace
from pathlib import Path

from .. import config
from ..config import PROVIDERS, effort_by_key, model_by_id
from ..tools import Tool
from .engine import Computer
from .state import ComputerError, Settings, list_missions, safe_text, ALL_AGENT_IDS, AGENT_IDS, inherited_model
from .transport import APIClient
from .view import fragments, status_text, family_fragments, tree_text, inspect_text, api_text

HELP = """COMPUTER MODE — 8 leads x 8 children = 72 real API-backed logical sessions
/on [directory]                 open the live workspace dashboard
<your project goal>             research → refine → approved plan → build → check/repair
/off                           cancel/drain the mission and return to normal chat
/computer status               current agents, usage and checks
/computer api                  API capacity, queues, quotas and recovery status
/computer swarm on|off         64-child hierarchy or eight-lead mode (idle only)
/computer tree                 print all eight families and 64 children
/computer tree a1              live family view (a1–a8); overview restores leads
/computer inspect a1.3         actual assignment, result, model and usage
/computer pause                stop scheduling new actions (in-flight work may finish)
/computer resume               unpause the current mission
/computer cancel               stop the mission; preserve checkpoints and completed writes
/computer list                 saved missions for this workspace
/computer resume <id>           recover a saved mission (asks for plan approval again)
/computer report               write/show the current report path
/computer set <key> <value>     settings for the next run, only while idle
/computer model <agent> <id>   lead or child override (e.g. a3 or a3.2), next run
/computer models               show overrides; use /model to change the default
/computer help                 this help

Useful settings: max_parallel 1–64 (default 8), child_token_budget 1000–1000000,
api_max_parallel 1–64 (default 2/API domain; separate from local worker slots),
api_adaptive true|false (default true: one initial lane, adaptive 12 RPM warmup),
requests_per_minute 0–6000 (0 = automatic ceiling 60 in adaptive mode),
api_tokens_per_minute 0–10000000 (default 60000 estimated traffic tokens/minute),
api_cooldown_seconds 1–300 (default 5; longer server Retry-After is honored), token_budget 1000–10000000,
wall_minutes 1–480, plan_rounds 1–4, repair_rounds 0–5, work_steps 2–100,
network true|false, plan_only true|false, max_output_tokens 512–16384.

Approval keys: y = this action; n = deny. At a COMMAND prompt only, a grants
that exact argv + working directory for this mission (including reruns after
code changes). It NEVER enables global /approve. Inspect untrusted code first.
Cloud/API usage can cost money. File scopes are enforced, but approved commands
run on the host: this mode is NOT a VM or an OS-level security sandbox.
"""


class ComputerBridge:
    def __init__(self, ui):
        self.ui = ui
        self.enabled = False
        self.exit_pending = False
        self.engine = None
        self._frozen_models = {}
        self.focus_lead = None
        self._frozen_effort = None
        self._approval_done = None

    @property
    def running(self):
        return bool(self.engine and self.engine.running)

    def _settings(self):
        return Settings.from_dict(self.ui.cfg.extra.get("computer_settings", {}))

    def _client(self, agent_id):
        m = model_by_id(self._frozen_models[agent_id])
        if m is None:
            raise ComputerError("Unknown selected model; choose an existing model with /model")
        return APIClient(PROVIDERS[m.provider], m, self._frozen_effort,
                         self.engine.settings.request_timeout, session_pool=self.engine.session_pool)

    def _freeze_models(self):
        overrides = self.ui.cfg.extra.get("computer_models", {})
        self._frozen_models = {i: inherited_model(i, overrides, self.ui.cfg.model_id) for i in ALL_AGENT_IDS}
        self._frozen_effort = effort_by_key(self.ui.cfg.effort)

    def _event(self, event):
        # Only real mission/phase events enter the legacy kernel; token and
        # terminal chunks stay in the bounded computer-mode event stream.
        if event["kind"] in ("mission.started", "mission.finished", "phase"):
            try:
                self.ui.agent.log.append("computer." + event["kind"],
                                         {"message": event["message"], "mission": self.engine.board.data["id"]}, actor="computer")
            except Exception:
                pass
        if event["kind"] == "mission.finished" and self.exit_pending:
            self.enabled = False
            self.exit_pending = False
        try:
            self.ui.app.invalidate()
        except Exception:
            pass

    def _approve(self, kind, details):
        if not self.engine:
            return False
        # Deliberately independent of cfg.auto_approve/autonomy. No global
        # setting silently authorizes eight agents to execute host code.
        from rich.text import Text
        done = threading.Event()
        self._approval_done = done
        self.ui._computer_approval_kind = kind
        self.ui._approve_result = False
        tool = Tool("computer_" + kind, "Explicit computer-mode approval", {}, lambda: "")
        self.ui._approve_request = (tool, details, done)
        self.ui.console.print(Text("COMPUTER APPROVAL\n" + safe_text(json.dumps(details, indent=2, ensure_ascii=False), 500000), style="#DE9255"))
        self.ui.console.print(Text("y: approve once · n: deny · a: exact command for this mission only (plan: approve once)", style="#999999"))
        self.ui.app.invalidate()
        try:
            while not done.wait(0.1):
                self.engine._control()
            self.engine._control()
            return self.ui._approve_result
        finally:
            self.ui._approve_request = None
            self.ui._computer_approval_kind = None
            self._approval_done = None
            self.ui.app.invalidate()

    def on(self, path=""):
        if self.ui._busy or self.running:
            raise ComputerError("Wait for or cancel the current operation before changing computer mode")
        root = Path(path.strip().strip('"').strip("'") or Path.cwd()).expanduser().resolve()
        if self.engine is None or root != self.engine.root:
            self.engine = Computer(root, config.APP_DIR / "computer", self._settings(),
                                   self._client, self._approve, self._event)
        else:
            self.engine.settings = self._settings()
        self.enabled = True
        self.exit_pending = False
        topology = "8 leads + 64 child workers" if self.engine.settings.hierarchy_enabled else "8 lead agents"
        self.ui.print_info(f"Computer mode ON — {topology}; active cap {self.engine.settings.max_parallel}. Workspace: {root}", "#5E9FE8")
        self.ui.print_info(f"API protection ON: {'adaptive one-lane slow-start' if self.engine.settings.api_adaptive else 'fixed admission'}, API cap {self.engine.settings.api_max_parallel}/domain. Local workers are separate. /computer api", "#DE9255")
        self.ui.print_info("Temporary API faults park and resume work; they do not grant unlimited tokens/time. More agents do not guarantee speed. /computer tree", "#999999")
        self.ui.print_info("Type a project goal. Selected source files go to your configured model provider; keep secrets out of the workspace. /computer help", "#999999")
        self.ui.app.invalidate()

    def off(self):
        if self.running:
            self.exit_pending = True
            self.engine.cancel()
            if self._approval_done:
                self._approval_done.set()
            self.ui.print_info("Cancelling; the dashboard closes after active workers stop.", "#DE9255")
        else:
            self.enabled = False
            self.ui.print_info("Computer mode OFF — normal chat restored.", "#999999")
        self.ui.app.invalidate()

    def submit(self, goal="", resume_id=None):
        if not self.enabled:
            raise ComputerError("Use /on [directory] first")
        if self.running or self.ui._busy:
            raise ComputerError("A mission is active; use /computer pause or cancel")
        self.engine.settings = self._settings()
        self._freeze_models()
        if goal:
            self.ui._emit_user(goal)
        mission = self.engine.start(goal, resume_id=resume_id)
        self.ui.print_info("Mission " + mission + " started. Ctrl+C cancels; /computer pause pauses new actions.", "#5E9FE8")

    def cancel(self):
        if self.engine:
            self.engine.cancel()
        if self._approval_done:
            self._approval_done.set()

    def shutdown(self):
        self.cancel()
        if self.engine:
            self.engine.join(timeout=2)

    def dashboard(self):
        if not self.enabled or not self.engine:
            return []
        try:
            size = self.ui.app.output.get_size()
            width, height = max(12, size.columns), max(4, min(21, size.rows-5))
        except Exception:
            width, height = 100, 19
        snapshot = self.engine.dashboard_snapshot()
        if self.focus_lead and snapshot.get("hierarchy", {}).get("enabled"):
            return family_fragments(snapshot, self.focus_lead, width, height)
        return fragments(snapshot, width, height)

    def command(self, arg):
        parts = shlex.split(arg)
        sub = parts[0].lower() if parts else "status"
        if sub == "help":
            self.ui.print_info(HELP, "#5E9FE8")
            return
        if not self.engine:
            raise ComputerError("Use /on [directory] first")
        if sub == "status":
            self.ui.print_info(status_text(self.engine.snapshot()), "#5E9FE8")
        elif sub == "api":
            if len(parts) != 1:
                raise ComputerError("Use /computer api")
            self.ui.print_info(api_text(self.engine.dashboard_snapshot()), "#DE9255")
        elif sub == "tree":
            if len(parts) == 1:
                self.ui.print_info(tree_text(self.engine.dashboard_snapshot()), "#5E9FE8")
            elif len(parts) == 2 and parts[1] in (*AGENT_IDS, "overview"):
                self.focus_lead = None if parts[1] == "overview" else parts[1]
                self.ui.print_info("Live view: " + parts[1], "#5E9FE8")
                self.ui.app.invalidate()
            else:
                raise ComputerError("Use /computer tree [a1–a8|overview]")
        elif sub == "inspect":
            if len(parts) != 2:
                raise ComputerError("Use /computer inspect <a1–a8 or a1.1–a8.8>")
            self.ui.print_info(inspect_text(self.engine.snapshot(), parts[1]), "#999999")
        elif sub == "swarm":
            if self.running or len(parts) != 2 or parts[1] not in ("on", "off"):
                raise ComputerError("While idle: /computer swarm on|off")
            self.command("set hierarchy_enabled " + parts[1])
            self.focus_lead = None
        elif sub == "pause":
            self.engine.pause()
        elif sub == "cancel":
            self.cancel()
        elif sub == "resume":
            if len(parts) == 1:
                self.engine.resume()
            elif len(parts) == 2:
                self.submit(resume_id=parts[1])
            else:
                raise ComputerError("Usage: /computer resume [mission-id]")
        elif sub == "list":
            rows = list_missions(self.engine.store, self.engine.root)
            self.ui.print_info("\n".join(f"{r['id']}  {r['status']:<16}  {safe_text(r['goal'], 100)}" for r in rows) or "No saved missions", "#999999")
        elif sub == "report":
            if self.running:
                # A consistent snapshot, not a fabricated finished report.
                self.ui.print_info("Mission is active. /computer status shows live state; report.md is finalized when it stops.", "#DE9255")
            else:
                self.ui.print_info("Report: " + str(self.engine.write_report()), "#72BC8F")
        elif sub == "set":
            if self.running or len(parts) != 3:
                raise ComputerError("While idle, use /computer set <setting> <value>")
            settings = asdict(self._settings())
            key, raw = parts[1:]
            if key not in settings:
                raise ComputerError("Settings: " + ", ".join(settings))
            if type(settings[key]) is bool:
                if raw.lower() not in ("true", "false", "on", "off"):
                    raise ComputerError("Use true/false or on/off")
                value = raw.lower() in ("true", "on")
            else:
                value = int(raw)
            settings[key] = value
            valid = Settings.from_dict(settings)
            self.ui.cfg.extra["computer_settings"] = asdict(valid)
            self.ui.cfg.save()
            self.engine.settings = valid
            self.ui.print_info(f"Next mission: {key} = {value}", "#72BC8F")
        elif sub == "model":
            if self.running or len(parts) != 3 or parts[1] not in ALL_AGENT_IDS:
                raise ComputerError("While idle: /computer model <a1-a8 or a1.1-a8.8> <model-id>")
            m = model_by_id(parts[2])
            if m is None or not m.supports_tools:
                raise ComputerError("Choose an existing tool-capable model from /model")
            self.ui.cfg.extra.setdefault("computer_models", {})[parts[1]] = m.id
            self.ui.cfg.save()
            self.ui.print_info(f"{parts[1]} uses {m.label} on the next mission", "#72BC8F")
        elif sub == "models":
            mappings = self.ui.cfg.extra.get("computer_models", {})
            self.ui.print_info("\n".join(f"{i}: {inherited_model(i, mappings, self.ui.cfg.model_id)}" for i in (ALL_AGENT_IDS if self._settings().hierarchy_enabled else AGENT_IDS)), "#999999")
        else:
            raise ComputerError("Unknown computer command; use /computer help")
