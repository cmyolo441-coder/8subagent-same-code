from __future__ import annotations
import copy
import json
import tempfile
import threading
import time
import unittest
from dataclasses import asdict, replace
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace

from fullagent.computer.state import Settings, Board, ComputerError
from fullagent.computer.engine import Computer
from fullagent.computer.continuation import Continuations, ContinuationUnsafe
from fullagent.computer.governor import (AdaptiveGovernor, ApiDeferred, ApiUnavailable,
    provider_domain, parse_delay, rate_headers)
from fullagent.computer.transport import TransportError, provider_error
from fullagent.computer.view import api_text, dashboard_lines, cells
from tests.support import Driver, tool
from tests.hierarchy_support import HierarchyDriver
from tests.api_support import Clock, AcceleratedClock, FaultDriver, CommandDriver, ModelHTTPFixture, phase_and_owner
from tests.test_computer_network import HTTPFixture, client


def fixed(**kwargs):
    values = dict(api_adaptive=False, api_max_parallel=1, api_tokens_per_minute=0,
                  hierarchy_enabled=False, plan_rounds=1, repair_rounds=0,
                  token_budget=3000000, work_steps=6, review_steps=3, api_cooldown_seconds=1)
    values.update(kwargs)
    return Settings(**values)


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.g = AdaptiveGovernor(fixed(), clock=self.clock, wall=self.clock.wall)
    def send(self, request, owner="a1", key="p", cost=100):
        permit = self.g.acquire(key, key, request, owner, cost)
        self.g.sent(permit)
        return permit
    def row(self):
        return self.g.snapshot()["domains"][0]
    def test_defaults_decouple_eight_workers_from_api_capacity(self):
        s = Settings()
        g = AdaptiveGovernor(s, clock=self.clock, wall=self.clock.wall)
        p = g.acquire("p", "provider", "one", "a1", 100)
        with self.assertRaises(ApiDeferred): g.acquire("p", "provider", "two", "a2", 100)
        self.assertEqual(s.max_parallel, 8)
        self.assertEqual(s.api_max_parallel, 2)
        self.assertEqual(g.snapshot()["domains"][0]["cap"], 1)
        self.assertEqual(g.snapshot()["domains"][0]["rpm"], 12)
        g.abandon(p)
    def test_api_settings_reject_unbounded_and_truthy_strings(self):
        for value in ({"api_max_parallel": 0}, {"api_max_parallel": 65}, {"api_adaptive": "false"},
                      {"api_tokens_per_minute": -1}, {"api_cooldown_seconds": 0}):
            with self.subTest(value=value), self.assertRaises(ValueError): Settings(**value)
    def test_provider_aliases_share_one_credential_safe_domain(self):
        a = SimpleNamespace(provider=SimpleNamespace(base_url="https://EXAMPLE.com/v1", api_key="not-a-real-key", key="first"))
        b = SimpleNamespace(provider=SimpleNamespace(base_url="https://example.com:443/another-path", api_key="not-a-real-key", key="alias"))
        self.assertEqual(provider_domain(a), provider_domain(b))
        self.assertNotIn("not-a-real-key", repr(provider_domain(a)))
    def test_queued_attempts_take_no_permit_and_no_attempt_count(self):
        p = self.send("one")
        for who in range(2, 9):
            with self.assertRaises(ApiDeferred): self.g.acquire("p", "p", str(who), f"a{who}", 100)
        self.assertEqual(self.row()["attempts"], 1)
        self.assertEqual(self.row()["queued"], 7)
        self.assertEqual(self.row()["active"], 1)
        self.g.success(p)
    def test_family_round_robin_prevents_one_lead_flooding_queue(self):
        p = self.send("a1.1", "a1.1")
        order = ["a1.2", "a1.3", "a1.4"]+[f"a{i}.1" for i in range(2, 9)]
        for who in order:
            with self.assertRaises(ApiDeferred): self.g.acquire("p", "p", who, who, 100)
        self.g.success(p)
        admitted = []
        for who in [f"a{i}.1" for i in range(2, 9)]+["a1.2"]:
            p = self.send(who, who)
            admitted.append(who)
            self.g.success(p)
        self.assertEqual(admitted[:7], [f"a{i}.1" for i in range(2, 9)])
    def test_one_half_open_probe_and_useful_work_not_health_spam(self):
        p = self.send("one")
        self.g.failure(p, TransportError("busy", True, 5, 503))
        with self.assertRaises(ApiDeferred): self.g.acquire("p", "p", "two", "a2", 100)
        self.clock.advance(6)
        probe = self.send("two", "a2")
        self.assertTrue(probe.probe)
        with self.assertRaises(ApiDeferred): self.g.acquire("p", "p", "one", "a1", 100)
        self.assertEqual(self.row()["probes"], 1)
        self.g.success(probe)
        self.assertEqual(self.row()["mode"], "closed")
    def test_overlapping_failures_coalesce_one_congestion_epoch(self):
        self.g = AdaptiveGovernor(fixed(api_max_parallel=8), clock=self.clock, wall=self.clock.wall)
        permits = [self.send(str(i), f"a{i}") for i in range(1, 9)]
        for p in permits: self.g.failure(p, TransportError("limited", True, 2, 429))
        self.assertEqual(self.row()["congestions"], 8)
        self.assertEqual(self.row()["failures"], 1)
        self.assertEqual(self.row()["cap"], 1)
    def test_old_success_does_not_close_newer_outage(self):
        self.g = AdaptiveGovernor(fixed(api_max_parallel=2), clock=self.clock, wall=self.clock.wall)
        a, b = self.send("a", "a1"), self.send("b", "a2")
        self.g.failure(b, TransportError("outage", True, 30, 503))
        self.g.success(a)
        self.assertEqual(self.row()["mode"], "open")
        self.assertGreaterEqual(self.row()["cooldown_seconds"], 30)
    def test_unavailable_provider_does_not_block_another_domain(self):
        a = self.send("one")
        self.g.failure(a, TransportError("outage", True, 120, 503))
        b = self.send("other", "a2", key="healthy")
        self.g.success(b)
        self.assertEqual(self.g.snapshot()["recovering"], 1)
    def test_server_delays_are_not_truncated_to_fifteen_or_sixty(self):
        a = self.send("one")
        self.g.failure(a, TransportError("limited", True, 180, 429))
        self.assertGreaterEqual(self.row()["cooldown_seconds"], 180)
        self.clock.advance(61)
        with self.assertRaises(ApiDeferred) as c: self.g.acquire("p", "p", "one", "a1", 100)
        self.assertGreater(c.exception.delay, 100)
    def test_restart_preserves_cooldown_and_requires_a_probe(self):
        a = self.send("one")
        self.g.failure(a, TransportError("busy", True, 120, 503))
        saved = self.g.snapshot()
        self.clock.advance(20)
        restored = AdaptiveGovernor(fixed(), saved, clock=self.clock, wall=self.clock.wall)
        with self.assertRaises(ApiDeferred) as c: restored.acquire("p", "p", "job", "a1", 100)
        self.assertGreaterEqual(c.exception.delay, 100)
        self.clock.advance(101)
        p = restored.acquire("p", "p", "job", "a1", 100)
        self.assertTrue(p.probe)
        restored.abandon(p)
    def test_known_token_quota_prevents_another_call_until_reset(self):
        a = self.send("one")
        self.g.success(a, {"x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "1m30s"})
        with self.assertRaises(ApiDeferred) as c: self.g.acquire("p", "p", "two", "a2", 100)
        self.assertGreaterEqual(c.exception.delay, 90)
        self.clock.advance(91)
        self.g.success(self.send("two", "a2"))
    def test_old_quota_header_cannot_restore_stale_headroom(self):
        self.g = AdaptiveGovernor(fixed(api_max_parallel=2), clock=self.clock, wall=self.clock.wall)
        a, b = self.send("a", "a1"), self.send("b", "a2")
        self.g.success(b, {"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "60s"})
        self.g.success(a, {"x-ratelimit-remaining-requests": "100", "x-ratelimit-reset-requests": "60s"})
        self.assertEqual(self.row()["remaining_requests"], 0)
    def test_large_estimated_request_keeps_debt_not_infinite_starvation(self):
        self.g = AdaptiveGovernor(fixed(api_tokens_per_minute=1000), clock=self.clock, wall=self.clock.wall)
        a = self.send("one", cost=1500)
        self.g.success(a)
        self.assertLess(self.row()["credit"], 0)
        with self.assertRaises(ApiDeferred): self.g.acquire("p", "p", "two", "a2", 1000)
        self.clock.advance(91)
        self.g.success(self.send("two", "a2", cost=1000))
    def test_adaptive_ramp_respects_learned_request_limit(self):
        self.g = AdaptiveGovernor(replace(Settings(), requests_per_minute=60), clock=self.clock, wall=self.clock.wall)
        for i in range(10):
            self.clock.advance(10)
            p = self.send(str(i))
            self.clock.advance(.1)
            self.g.success(p, {"x-ratelimit-limit-requests": "10"})
        self.assertEqual(self.row()["cap"], 2)
        self.assertLessEqual(self.row()["rpm"], 10)
    def test_availability_fault_is_not_a_fictitious_lower_rpm_quota(self):
        self.g = AdaptiveGovernor(Settings(), clock=self.clock, wall=self.clock.wall)
        p = self.send("one")
        self.g.failure(p, TransportError("unavailable", True, 2, 503))
        self.assertEqual(self.row()["rpm"], 12)
    def test_billing_block_is_not_repeated_automatically(self):
        p = self.send("one")
        self.g.failure(p, provider_error(429, {"error": {"code": "insufficient_quota"}}))
        for i in range(2, 9):
            with self.assertRaises(ApiUnavailable): self.g.acquire("p", "p", str(i), f"a{i}", 100)
        self.assertEqual(self.row()["attempts"], 1)
    def test_explicit_resume_gets_one_billing_validation_probe(self):
        p = self.send("one")
        self.g.failure(p, provider_error(402, {"error": "payment required"}))
        restored = AdaptiveGovernor(fixed(), self.g.snapshot(), clock=self.clock, wall=self.clock.wall)
        p = restored.acquire("p", "p", "resume", "a1", 100)
        self.assertTrue(p.probe)
        restored.abandon(p)
    def test_unsent_permit_can_be_released_and_duplicate_request_is_blocked(self):
        p = self.g.acquire("p", "p", "one", "a1", 100)
        with self.assertRaises(ApiDeferred): self.g.acquire("p", "p", "one", "a1", 100)
        self.g.abandon(p)
        self.assertEqual(self.row()["active"], 0)
        self.assertEqual(self.row()["attempts"], 0)
    def test_duration_and_http_date_parsers_reject_nonfinite_values(self):
        self.assertEqual(parse_delay("1m2.5s", reset=True), 62.5)
        stamp = format_datetime(datetime.fromtimestamp(1700000180, timezone.utc), usegmt=True)
        self.assertEqual(parse_delay(stamp, 1700000000), 180)
        for s in ("nan", "inf", "-1", "nonsense"):
            self.assertEqual(parse_delay(s), 0)
    def test_malformed_response_metadata_is_ignored_without_crashing_admission(self):
        for headers in (None, [], "bad metadata", 123, {"Retry-After": {"unexpected":"object"}}):
            with self.subTest(headers=headers): self.assertEqual(rate_headers(headers), {})

    def test_retained_metadata_excludes_auth_cookies_and_is_bounded(self):
        headers = rate_headers({"Authorization": "private", "Set-Cookie": "private", "Retry-After": "1"*1000})
        self.assertEqual(set(headers), {"retry-after"})
        self.assertEqual(len(headers["retry-after"]), 256)
    def test_error_classification_distinguishes_credentials_from_load(self):
        for status, kind, retry in [(401, "authentication", False), (403, "request", False), (400, "request", False),
                                     (429, "congestion", True), (503, "unavailable", True), (529, "unavailable", True)]:
            e = provider_error(status, {"error": "fixture"})
            self.assertEqual((e.kind, e.retryable), (kind, retry))
    def test_real_http_date_retry_after_and_success_metadata(self):
        date = format_datetime(datetime.fromtimestamp(1700000000, timezone.utc), usegmt=True)
        later = format_datetime(datetime.fromtimestamp(1700000180, timezone.utc), usegmt=True)
        with HTTPFixture([(429, "application/json", b'{"error":"limited"}', {"Retry-After": later, "Date": date})]) as server:
            c = client(server.url)
            try:
                with self.assertRaises(TransportError) as caught: c.chat([{"role":"system","content":"test"},{"role":"user","content":"test"}], [], 512, lambda: None)
                # HTTP servers may combine automatic/supplied Date headers;
                # parsing is separately tested with an exact server clock.
                self.assertTrue(caught.exception.retryable)
                self.assertIn("retry-after", caught.exception.headers)
                self.assertEqual(len(server.received), 1)
            finally: c.close()
        e = TransportError("rate", True, headers={"date": date, "retry-after": later})
        self.assertEqual(e.retry_after, 180)


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.board = Board(root, root/"state", "continuation fixture", fixed())
        self.store = Continuations(self.board)
        self.state = {"stage": "request", "step": 1, "messages": [{"role":"user","content":"fixture"}], "repeats": {}}
    def test_roundtrip_does_not_put_conversations_in_main_checkpoint(self):
        self.store.save("job", "binding", self.state, "a1")
        self.assertEqual(self.store.load("job", "binding"), self.state)
        self.assertNotIn("messages", self.board.data["continuations"]["job"])
    def test_changed_authority_is_rejected(self):
        self.store.save("job", "binding", self.state, "a1")
        with self.assertRaises(ContinuationUnsafe): self.store.load("job", "different")
    def test_tampered_and_missing_record_are_not_replayed(self):
        self.store.save("job", "binding", self.state, "a1")
        path = next(self.store.folder.glob("*.json"))
        path.write_text("{}")
        with self.assertRaises(ContinuationUnsafe): self.store.load("job")
        path.unlink()
        with self.assertRaises(ContinuationUnsafe): self.store.load("job")
    def test_uncertain_stage_remains_explicit_and_complete_record_is_small(self):
        value = dict(self.state, stage="uncertain")
        self.store.save("job", "binding", value, "a1")
        self.assertEqual(self.store.load("job")["stage"], "uncertain")
        self.store.complete("job", "binding", {"status":"done"}, "a1")
        self.assertNotIn("messages", self.store.load("job"))
    def test_unchanged_wait_does_not_rewrite_disk(self):
        self.store.save("job", "binding", self.state, "a1")
        path = next(self.store.folder.glob("*.json")); before = path.stat().st_mtime_ns
        self.store.save("job", "binding", self.state, "a1")
        self.assertEqual(path.stat().st_mtime_ns, before)
    def test_release_unsent_does_not_invent_billed_or_reported_tokens(self):
        ticket = self.board.reserve(100, "a1")
        self.board.release_unsent(ticket, "a1")
        self.assertEqual(self.board.data["requests"], 0)
        self.assertEqual(self.board.data["charged_tokens"], 0)
        self.assertEqual(self.board.data["reserved_tokens"], 0)
    def test_symlinked_records_are_rejected(self):
        self.store.save("job", "binding", self.state, "a1")
        p = next(self.store.folder.glob("*.json")); outside = Path(self.temp.name)/"outside.json"
        outside.write_bytes(p.read_bytes()); p.unlink(); p.symlink_to(outside)
        with self.assertRaises(ContinuationUnsafe): self.store.load("job")


class RecoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/"project"; self.root.mkdir()
        self.state = Path(self.temp.name)/"state"
        self.engines = []
        self.clock = AcceleratedClock(100)
    def tearDown(self):
        for e in self.engines:
            if e.running: e.cancel()
            e.join(10)
    def engine(self, factory, settings=None, governor=None, listener=None):
        e = Computer(self.root, self.state, settings or fixed(api_max_parallel=2), factory,
                     lambda k,d: True, listener, governor_factory=governor or self.clock.factory)
        self.engines.append(e)
        return e
    def finish(self, e, resume=None):
        e.start("Build a real eight-file local fault-test fixture", resume_id=resume)
        self.assertTrue(e.join(100), "fixture did not drain")
        return e.snapshot()
    def test_more_than_three_errors_resume_without_repeating_write(self):
        driver = FaultDriver(rules={("a3", "building", 2): 4})
        e = self.engine(driver.factory); d = self.finish(e)
        self.assertEqual(d["status"], "completed", d.get("error"))
        self.assertEqual(len(driver.faults), 4)
        self.assertEqual(len(d["files"]), 8)
        self.assertEqual(d["tasks"]["a3"]["attempts"], 1)
        self.assertEqual(d["api"]["active"], 0)
        self.assertEqual(d["reserved_tokens"], 0)
    def test_command_runs_once_despite_repeated_model_errors_after_receipt(self):
        driver = FaultDriver(CommandDriver(), {("a3", "building", 1): 4})
        d = self.finish(self.engine(driver.factory))
        self.assertEqual(d["status"], "completed", d.get("error"))
        self.assertIn("run_count = 1", (self.root/"a3.py").read_text())
        self.assertEqual(len(driver.faults), 4)
    def test_planning_survives_repeated_outage_without_consuming_correction_limit(self):
        driver = FaultDriver(Driver(malformed_plan=True), {("a1", "planning", 0): 4})
        d = self.finish(self.engine(driver.factory, fixed(plan_only=True)))
        self.assertEqual(d["status"], "planned", d.get("error"))
        self.assertEqual(driver.base.plans, 2)
        self.assertEqual(len(driver.faults), 4)
    def test_real_http_eight_agents_share_protected_alias_capacity(self):
        with ModelHTTPFixture(latency=.025) as server:
            settings = replace(Settings(), hierarchy_enabled=False, plan_rounds=1, repair_rounds=0,
                               work_steps=6, token_budget=3000000)
            e = self.engine(server.factory, settings)
            d = self.finish(e)
            self.assertEqual(d["status"], "completed", d.get("error"))
            self.assertEqual(len(d["agents"]), 8)
            self.assertEqual(len(d["api"]["domains"]), 1)
            self.assertLessEqual(server.peak, 2)
            self.assertEqual({who for who,phase,at in server.requests if phase == "building"}, {f"a{i}" for i in range(1,9)})
            self.assertEqual(len(list(self.root.glob("a*.py"))), 8)
    def test_real_http_503_is_recovered_not_mapped_to_mission_failure(self):
        driver = FaultDriver(rules={("a3", "building", 2): 4})
        with ModelHTTPFixture(driver) as server:
            d = self.finish(self.engine(server.factory))
            self.assertEqual(d["status"], "completed", d.get("error"))
            self.assertEqual(len(driver.faults), 4)
            self.assertEqual(len(d["files"]), 8)
    def test_healthy_provider_finishes_work_while_other_is_parked(self):
        driver = FaultDriver(rules={("a1", "research", 0): -1}, delay=120,
                             groups={"a1":"unavailable", **{f"a{i}":"healthy" for i in range(2,9)}})
        e = self.engine(driver.factory, governor=lambda s,p: AdaptiveGovernor(s,p))
        e.start("Isolate a deliberately unavailable local test provider")
        self.assertTrue(driver.reached.wait(3))
        until = time.monotonic()+5
        while time.monotonic() < until:
            reports = e.snapshot().get("reports", {}).get("research", {})
            if sum(r.get("status") == "ok" for r in reports.values()) == 7: break
            time.sleep(.03)
        self.assertEqual(sum(r.get("status") == "ok" for r in reports.values()), 7)
        self.assertTrue(e.running)
        self.assertEqual(e.snapshot()["agents"]["a1"]["status"], "waiting")
        self.assertEqual(len(driver.faults), 1)
        e.cancel(); self.assertTrue(e.join(3))
        self.assertEqual(e.snapshot()["status"], "cancelled")
    def test_cancelled_wait_resumes_committed_history_after_server_deadline(self):
        clock = Clock()
        governor = lambda s,p: AdaptiveGovernor(s,p,clock=clock,wall=clock.wall)
        driver = FaultDriver(rules={("a3", "building", 2):-1}, delay=120)
        e = self.engine(driver.factory, governor=governor)
        e.start("Persist a local API-wait fixture without repeating writes")
        self.assertTrue(driver.reached.wait(20))
        time.sleep(.1); e.cancel(); self.assertTrue(e.join(5))
        before = e.snapshot(); clock.advance(121)
        second = FaultDriver()
        restored = self.engine(second.factory, governor=governor)
        d = self.finish(restored, before["id"])
        self.assertEqual(d["status"], "completed", d.get("error"))
        resumed_calls = [n for who,phase,n in second.base.calls if who == "a3" and phase == "building"]
        self.assertTrue(resumed_calls)
        self.assertTrue(all(n >= 2 for n in resumed_calls), resumed_calls)
        self.assertEqual(len(d["files"]), 8)
    def test_missing_one_client_does_not_cancel_healthy_research(self):
        base = Driver()
        def factory(who):
            if who == "a4": raise ComputerError("Missing test credentials")
            return base.factory(who)
        d = self.finish(self.engine(factory))
        self.assertEqual(d["status"], "needs_attention")
        self.assertEqual(sum(r.get("status") == "ok" for r in d["reports"]["research"].values()), 7)
    def test_uncertain_completed_command_is_not_replayed_on_resume(self):
        holder, injected = {}, [False]
        def listener(event):
            if event["kind"] == "phase" and event["message"] == "building" and not injected[0]:
                store = holder["e"].continuations
                original = store.save
                def save(key,binding,state,owner):
                    if owner == "a3" and state.get("stage") == "tools" and state.get("cursor") == 1 and not injected[0]:
                        injected[0] = True
                        raise OSError("Injected loss of the post-command receipt")
                    return original(key,binding,state,owner)
                store.save = save
        e = self.engine(CommandDriver().factory, listener=listener); holder["e"] = e
        first = self.finish(e)
        self.assertEqual(first["status"], "needs_attention")
        self.assertIn("run_count = 1", (self.root/"a3.py").read_text())
        d = self.finish(self.engine(CommandDriver().factory), first["id"])
        self.assertEqual(d["status"], "needs_attention")
        self.assertIn("run_count = 1", (self.root/"a3.py").read_text())
    def test_hierarchy_research_decomposition_build_and_review_all_recover(self):
        driver = FaultDriver(HierarchyDriver(), {("a1.1","child-research",0):1,
            ("a2","decomposing",0):1, ("a3.3","child-building",2):4,
            ("a4.4","child-review-0",1):1})
        settings = fixed(hierarchy_enabled=True, api_max_parallel=4, child_work_steps=5, child_review_steps=3)
        d = self.finish(self.engine(driver.factory, settings))
        self.assertEqual(d["status"], "completed", d.get("error"))
        self.assertEqual(len(driver.faults), 7)
        self.assertEqual(len(d["agents"]), 72)
        self.assertEqual(len(list(self.root.glob("a*/w*.py"))), 64)
        self.assertEqual(len(d["files"]), 64)
        self.assertTrue(all(t["attempts"] == 1 for t in d["hierarchy"]["tasks"].values()))
    def test_budget_boundary_releases_admission_without_sending(self):
        base = Driver()
        d = self.finish(self.engine(base.factory, fixed(token_budget=1000)))
        self.assertEqual(d["status"], "budget_exhausted")
        self.assertEqual(base.calls, [])
        self.assertEqual(d["api"]["active"], 0)
        self.assertEqual(d["requests"], 0)
    def test_resume_refreshes_read_only_review_after_fresh_acceptance_checks(self):
        clock = Clock()
        governor = lambda s,p: AdaptiveGovernor(s,p,clock=clock,wall=clock.wall)
        driver = FaultDriver(rules={("a3", "review-0", 1):-1}, delay=120)
        e = self.engine(driver.factory, governor=governor)
        e.start("Preserve completed implementation and refresh read-only review evidence")
        self.assertTrue(driver.reached.wait(20))
        time.sleep(.1); e.cancel(); self.assertTrue(e.join(5))
        before = e.snapshot(); clock.advance(121)
        d = self.finish(self.engine(Driver().factory, governor=governor), before["id"])
        self.assertEqual(d["status"], "completed", d.get("error"))
        self.assertEqual(len(d["files"]), 8)
        self.assertTrue(all(c["ok"] for c in d["checks"]))

    def test_fixed_idle_diagnostics_show_effective_window_and_no_fixed_pacing(self):
        e = self.engine(Driver().factory, fixed(api_max_parallel=4, max_parallel=2))
        text = api_text(e.snapshot())
        self.assertIn("fixed window 2/domain", text)
        self.assertIn("no fixed RPM pacing", text)
        self.assertNotIn("12 RPM warmup", text)

    def test_adaptive_diagnostics_show_explicit_smaller_ceiling(self):
        e = self.engine(Driver().factory, replace(Settings(), requests_per_minute=5))
        self.assertIn("warmup 5 RPM, ceiling 5", api_text(e.snapshot()))

    def test_active_unpaced_diagnostics_do_not_claim_zero_request_capacity(self):
        settings = fixed()
        g = AdaptiveGovernor(settings)
        p = g.acquire("p", "test provider", "job", "a1", 10)
        g.sent(p); g.success(p)
        text = api_text({"settings": asdict(settings), "api": g.snapshot()})
        self.assertIn("Effective pacing no fixed RPM pacing", text)
        self.assertIn("local token pacing disabled", text)

    def test_cancel_during_half_open_probe_drains_all_permits(self):
        clock = Clock()
        base = Driver(); base.release = threading.Event()
        driver = FaultDriver(base, {("a1", "research", 0):1}, delay=5)
        # A single local slot guarantees the intentional first fault comes
        # before the held useful-work probe; this is not a throughput test.
        e = self.engine(driver.factory, fixed(max_parallel=1),
                        governor=lambda s,p: AdaptiveGovernor(s,p,clock=clock,wall=clock.wall))
        e.start("Cancel a held useful-work recovery probe")
        try:
            self.assertTrue(driver.reached.wait(3))
            until = time.monotonic()+3
            while not e.snapshot().get("api", {}).get("recovering") and time.monotonic()<until: time.sleep(.01)
            self.assertTrue(e.snapshot()["api"]["recovering"])
            clock.advance(6)
            self.assertTrue(base.reached.wait(3))
            self.assertTrue(any(r["mode"] == "half_open" for r in e.snapshot()["api"]["domains"]))
            e.cancel(); self.assertTrue(e.join(3))
            self.assertEqual(e.snapshot()["status"], "cancelled")
            self.assertEqual(e.snapshot()["api"]["active"], 0)
            self.assertEqual(e.snapshot()["reserved_tokens"], 0)
        finally:
            base.release.set()

    def test_real_http_authentication_error_blocks_without_retry_storm(self):
        driver = FaultDriver(rules={("a1", "research", 0):-1}, status=401)
        with ModelHTTPFixture(driver) as server:
            d = self.finish(self.engine(server.factory, fixed(api_max_parallel=1)))
            self.assertEqual(d["status"], "needs_attention")
            self.assertEqual(len(driver.faults), 1)
            self.assertEqual(d["api"]["blocked"], 1)
            self.assertEqual(server.requests[-1][:2], ("a1", "research"))
            self.assertEqual(d["files"], [])

    def test_uncertain_review_note_is_not_erased_by_repair_or_restart(self):
        base = Driver()
        def factory(who):
            original = base.factory(who)
            class Client:
                model = original.model
                def chat(self,messages,schemas,max_tokens,control,on_update=None):
                    phase, _ = phase_and_owner(messages,who)
                    if who == "a3" and phase.startswith("review-") and not any(m["role"] == "tool" for m in messages):
                        return tool("share_note", {"message":"Fixture note with uncertain receipt", "to":"a1"})
                    return original.chat(messages,schemas,max_tokens,control,on_update)
                def close(self): original.close()
            return Client()
        holder, injected = {}, [False]
        def listener(event):
            if event["kind"] == "phase" and event["message"] == "review-0" and not injected[0]:
                store = holder["e"].continuations; original = store.save
                def save(key,binding,state,owner):
                    if owner == "a3" and ":review-0:" in key and state.get("stage") == "tools" and state.get("cursor") == 1 and not injected[0]:
                        injected[0] = True
                        raise OSError("Injected post-note receipt failure")
                    return original(key,binding,state,owner)
                store.save = save
        settings = fixed(api_max_parallel=2, repair_rounds=2)
        e = self.engine(factory, settings, listener=listener); holder["e"] = e
        first = self.finish(e)
        for d in (first, self.finish(self.engine(factory, settings), first["id"])):
            self.assertEqual(d["status"], "needs_attention")
            self.assertEqual(d["repair_round"], 0)
            self.assertTrue(any(r["owner"] == "a3" and r["stage"] == "uncertain" for r in d["continuations"].values()))
            self.assertEqual(sum(n["message"] == "Fixture note with uncertain receipt" for n in d.get("notes", [])), 1)
            self.assertTrue(all(t["attempts"] == 1 for t in d["tasks"].values()))

    def test_waiting_children_are_not_colored_as_running_work(self):
        e = self.engine(Driver().factory, replace(Settings(), hierarchy_enabled=True))
        d = e.snapshot()
        for who,row in d["agents"].items():
            if "." in who: row.update(status="waiting",activity="API capacity queue")
        lines = dashboard_lines(d, 120, 21)
        parents = [text for style,text in lines if "Atlas" in text or "Scout" in text]
        self.assertTrue(all("waiting" in line for line in parents))
        self.assertTrue(all(cells(text)<=120 for style,text in lines))
        self.assertIn("no offline/fake progress", api_text(d))


if __name__ == "__main__":
    unittest.main()
