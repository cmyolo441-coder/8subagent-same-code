"""Capacity-aware model admission, not one independent retry loop per agent.

One governor belongs to one running Computer. Aliases using the same origin
and credential share a domain. No provider/key rotation, invented capacity,
model-response cache or background health-inference requests are used.
"""
from __future__ import annotations

import hashlib
import math
import re
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from .state import ComputerError, safe_text


RATE_HEADERS = frozenset({
    "retry-after", "date", "ratelimit-limit", "ratelimit-remaining", "ratelimit-reset",
    *[prefix + suffix for prefix in ("x-ratelimit-", "x-rate-limit-")
      for suffix in ("limit-requests", "remaining-requests", "reset-requests",
                     "limit-tokens", "remaining-tokens", "reset-tokens")],
    *["anthropic-ratelimit-" + kind + "-" + part
      for kind in ("requests", "tokens") for part in ("limit", "remaining", "reset")],
})


def rate_headers(headers):
    """Retain only bounded quota metadata, never cookies/auth/raw headers."""
    if not isinstance(headers, Mapping):
        return {}
    return {k.lower(): str(v)[:256] for k, v in headers.items()
            if isinstance(k, str) and k.lower() in RATE_HEADERS
            and type(v) in (str, int, float)}


def finite_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def parse_delay(value, now=None, reset=False):
    """Seconds, HTTP-date, ISO date or reset durations such as 1m2.5s.

    A valid server delay is NOT shortened to 15/60 seconds. An excessive
    delay is bounded by the mission's explicit wall deadline/cancellation,
    not by sending earlier than the server requested.
    """
    now = time.time() if now is None else now
    text = str(value or "").strip()[:256]
    number = finite_number(text)
    if number is not None:
        if reset and number > 100_000_000:
            number = number / 1000 if number > 100_000_000_000 else number
            return max(0.0, number - now)
        return number
    if reset and re.fullmatch(r"(?:\d+(?:\.\d+)?(?:ms|s|m|h|d))+", text):
        units = {"ms": .001, "s": 1, "m": 60, "h": 3600, "d": 86400}
        return sum(float(n) * units[u] for n, u in re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)", text))
    try:
        stamp = parsedate_to_datetime(text)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0.0, stamp.timestamp() - now)
    except (TypeError, ValueError, OverflowError):
        if reset:
            try:
                from datetime import datetime
                stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                return max(0.0, stamp.timestamp() - now)
            except (TypeError, ValueError, OverflowError):
                pass
    return 0.0


def header_reference(headers, fallback):
    # Server Date prevents a fast client wall clock from defeating an
    # HTTP-date cooldown. A slow local clock is deliberately conservative.
    try:
        stamp = parsedate_to_datetime(headers.get("date", ""))
        return min(fallback, stamp.timestamp())
    except (TypeError, ValueError, OverflowError):
        return fallback


def provider_domain(client):
    """Group model/provider aliases without logging credential material."""
    provider = getattr(client, "provider", None)
    base = str(getattr(provider, "base_url", "") or "")
    if base:
        parsed = urlsplit(base)
        host = (parsed.hostname or "configured-provider").lower()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        origin = f"{parsed.scheme.lower()}://{host}:{port}"
        label = host + (f":{port}" if port not in (80, 443) else "")
        credential = str(getattr(provider, "api_key", "") or "")
        identity = origin + "\0" + credential
    else:
        # Custom clients/test fixtures can expose a stable provider key.
        label = safe_text(getattr(provider, "key", "configured-provider"), 80)
        identity = "custom-client:" + label
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24], safe_text(label, 100)


class ApiDeferred(Exception):
    """Nonfatal scheduler signal: preserve the job and release its thread."""
    def __init__(self, domain, request, reason, delay, revision, now):
        super().__init__(reason)
        self.domain, self.request, self.reason = domain, request, reason
        self.delay = max(.01, float(delay))
        self.ready_at = now + self.delay
        self.revision = revision


class ApiUnavailable(ComputerError):
    """A configuration/billing block cannot be repaired by extra traffic."""
    requires_user_action = True


@dataclass
class Permit:
    id: str
    domain: str
    request: str
    owner: str
    cost: int
    generation: int
    probe: bool
    started: float
    sent: bool = False
    sequence: int = 0


@dataclass
class Domain:
    label: str
    cap: int
    rpm: float
    tpm: float
    credit: float
    updated: float
    queue: OrderedDict = field(default_factory=OrderedDict)
    active: dict = field(default_factory=dict)
    last_family: str = ""
    next_at: float = 0.0
    not_before: float = 0.0
    mode: str = "closed"
    blocked: str = ""
    generation: int = 0
    revision: int = 0
    streak: int = 0
    failures: int = 0
    latency: float = 0.0
    baseline: float = 0.0
    attempts: int = 0
    successes: int = 0
    congestions: int = 0
    outages: int = 0
    probes: int = 0
    peak: int = 0
    header_sequence: int = 0
    server_request_limit: float | None = None
    remaining_requests: float | None = None
    remaining_tokens: float | None = None
    request_reset: float = 0.0
    token_reset: float = 0.0
    last_reason: str = ""


class AdaptiveGovernor:
    """Family-fair queue + token admission + latency/AIMD + circuit epochs.

    Waiting requests hold neither an HTTP permit nor a token reservation.
    One half-open request tests recovery using useful queued work. An old
    in-flight success cannot close a newer outage's circuit.
    """
    def __init__(self, settings, previous=None, clock=None, wall=None):
        self.settings = settings
        self.clock, self.wall = clock or time.monotonic, wall or time.time
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.domains = {}
        self.saved = {row["id"]: row for row in (previous or {}).get("domains", [])
                      if isinstance(row, dict) and isinstance(row.get("id"), str)}
        self.maximum = min(settings.max_parallel, settings.api_max_parallel)
        self.rpm_ceiling = settings.requests_per_minute or (60 if settings.api_adaptive else 0)
        self.tpm_ceiling = settings.api_tokens_per_minute

    def _domain(self, key, label):
        if key in self.domains:
            return self.domains[key]
        if len(self.domains) >= 128:
            raise ComputerError("Too many API traffic domains in one mission")
        now, epoch = self.clock(), self.wall()
        rpm = min(12, self.rpm_ceiling) if self.settings.api_adaptive else self.rpm_ceiling
        row = Domain(label, 1 if self.settings.api_adaptive else self.maximum,
                     float(rpm), float(self.tpm_ceiling), float(self.tpm_ceiling), now)
        old = self.saved.get(key, {})
        # Restart always ramps concurrency from the safe initial window.
        # Server deadlines, account blocks and traffic debt survive restart.
        for attr, source in (("not_before", "not_before_epoch"), ("next_at", "next_request_epoch"),
                             ("request_reset", "request_reset_epoch"), ("token_reset", "token_reset_epoch")):
            value = finite_number(old.get(source))
            if value is not None:
                setattr(row, attr, now + max(0, value - epoch))
        # Constructing from a saved mission follows an EXPLICIT user resume.
        # A previously blocked account gets one guarded validation request,
        # not an automatic infinite credential/billing retry loop.
        row.mode = "open" if old.get("blocked") or old.get("mode") in ("open", "half_open") or row.not_before > now else "closed"
        row.server_request_limit = finite_number(old.get("server_request_limit"))
        old_rpm = finite_number(old.get("rpm"))
        if old_rpm is not None and old_rpm > 0 and row.rpm:
            row.rpm = min(row.rpm, old_rpm)
        old_tpm = finite_number(old.get("tpm"))
        if old_tpm is not None and old_tpm > 0 and row.tpm:
            row.tpm = min(row.tpm, old_tpm)
            row.credit = min(row.credit, row.tpm)
        for attr in ("attempts", "successes", "congestions", "outages", "probes", "failures"):
            value = old.get(attr, 0)
            if type(value) is int and 0 <= value <= 10_000_000:
                setattr(row, attr, value)
        for attr, reset in (("remaining_requests", row.request_reset), ("remaining_tokens", row.token_reset)):
            if reset > now:
                setattr(row, attr, finite_number(old.get(attr)))
        saved_credit = old.get("credit")
        saved_at = finite_number(old.get("saved_at"))
        if type(saved_credit) in (int, float) and math.isfinite(saved_credit) and saved_at is not None and row.tpm:
            row.credit = min(row.tpm, saved_credit + max(0, epoch-saved_at) * row.tpm / 60)
        self.domains[key] = row
        return row

    def _changed(self, row):
        row.revision += 1
        self.condition.notify_all()

    def _refresh(self, row, now):
        if row.tpm:
            row.credit = min(row.tpm, row.credit + max(0, now-row.updated) * row.tpm / 60)
        row.updated = now
        if row.request_reset and row.request_reset <= now:
            row.remaining_requests, row.request_reset = None, 0.0
        if row.token_reset and row.token_reset <= now:
            row.remaining_tokens, row.token_reset = None, 0.0

    @staticmethod
    def _first(row):
        families = sorted({entry["family"] for entry in row.queue.values()})
        if not families:
            return None
        family = next((f for f in families if f > row.last_family), families[0])
        return next(k for k, entry in row.queue.items() if entry["family"] == family)

    def _defer(self, key, request, row, reason, delay):
        return ApiDeferred(key, request, reason, delay, row.revision, self.clock())

    def acquire(self, key, label, request, owner, cost):
        if type(cost) is not int or cost <= 0:
            raise ValueError("API admission requires a positive estimated token cost")
        with self.condition:
            row = self._domain(key, label)
            now = self.clock()
            self._refresh(row, now)
            if row.blocked:
                row.queue.pop(request, None)
                raise ApiUnavailable(row.blocked)
            if any(p.request == request for p in row.active.values()):
                raise self._defer(key, request, row, "same logical request is already in flight", 30)
            if request not in row.queue:
                if len(row.queue) >= 256:
                    raise ComputerError("Bounded API queue is full")
                row.queue[request] = {"family": owner.split(".", 1)[0], "owner": owner, "cost": cost}
            if row.not_before > now:
                raise self._defer(key, request, row, "shared circuit cooldown; no extra model calls", row.not_before-now)
            if row.mode == "half_open" or (row.mode == "open" and row.active):
                raise self._defer(key, request, row, "single recovery probe / older calls draining", 30)
            limit = 1 if row.mode == "open" else row.cap
            if len(row.active) >= limit:
                raise self._defer(key, request, row, "API concurrency lane full; worker thread released", 30)
            if self._first(row) != request:
                raise self._defer(key, request, row, "family-fair API capacity queue", 30)
            delays = [(max(0, row.next_at-now), "request-rate admission")]
            if row.remaining_requests is not None and row.remaining_requests < 1:
                delays.append((max(.01, row.request_reset-now), "server request quota"))
            # One request may exceed an *estimated* local bucket. Allow it
            # only from a full bucket, then retain debt; never starve it
            # forever or silently discard its required scope/instructions.
            demand = min(cost, row.tpm) if row.tpm else 0
            if row.tpm and row.credit < demand:
                delays.append(((demand-row.credit)*60/row.tpm, "estimated token-rate budget"))
            if row.remaining_tokens is not None and row.remaining_tokens < cost:
                delays.append((max(.01, row.token_reset-now), "server token quota; context kept intact"))
            delay, reason = max(delays, key=lambda pair: pair[0])
            if delay > .000001:
                raise self._defer(key, request, row, reason, delay)
            row.queue.pop(request)
            row.last_family = owner.split(".", 1)[0]
            probe = row.mode == "open"
            if probe:
                row.mode = "half_open"
            permit = Permit(uuid.uuid4().hex, key, request, owner, cost, row.generation, probe, now)
            row.active[permit.id] = permit
            if row.rpm:
                row.next_at = now + 60/row.rpm
            if row.tpm:
                row.credit -= cost
            if row.remaining_requests is not None:
                row.remaining_requests = max(0, row.remaining_requests-1)
            if row.remaining_tokens is not None:
                row.remaining_tokens = max(0, row.remaining_tokens-cost)
            row.peak = max(row.peak, len(row.active))
            self._changed(row)
            return permit

    def sent(self, permit):
        with self.condition:
            row = self.domains[permit.domain]
            if row.active.get(permit.id) is not permit or permit.sent:
                raise ComputerError("API permit is invalid or already used")
            permit.sent = True
            permit.started = self.clock()
            row.attempts += 1
            permit.sequence = row.attempts
            if permit.probe:
                row.probes += 1

    def abandon(self, permit):
        """Release an unsent permit when cancellation/budget prevents IO."""
        with self.condition:
            row = self.domains[permit.domain]
            if row.active.pop(permit.id, None) is None:
                return
            if not permit.sent:
                if row.tpm:
                    row.credit = min(row.tpm, row.credit + permit.cost)
                if row.remaining_requests is not None:
                    row.remaining_requests += 1
                if row.remaining_tokens is not None:
                    row.remaining_tokens += permit.cost
            if permit.probe:
                row.mode = "open"
            self._changed(row)

    def _headers(self, row, permit, headers):
        headers = rate_headers(headers)
        now = self.clock()
        reference = header_reference(headers, self.wall())
        for kind in ("requests", "tokens"):
            def value(part):
                keys = [f"x-ratelimit-{part}-{kind}", f"x-rate-limit-{part}-{kind}",
                        f"anthropic-ratelimit-{kind}-{part}"]
                if kind == "requests":
                    keys.append("ratelimit-"+part)
                return next((headers[k] for k in keys if k in headers), None)
            limit = finite_number(value("limit"))
            remaining = finite_number(value("remaining"))
            reset = value("reset")
            reset_name = "request_reset" if kind == "requests" else "token_reset"
            attr = "remaining_"+kind
            fresh = permit.sequence >= row.header_sequence
            if remaining is not None:
                pending = sum(1 if kind == "requests" else p.cost for p in row.active.values() if p.sent)
                remaining = max(0, remaining-pending)
                current = getattr(row, attr)
                # A late response may tighten a quota, never restore stale
                # headroom over a newer response's lower remainder.
                setattr(row, attr, remaining if current is None or fresh else min(current, remaining))
                expiry = now + (parse_delay(reset, reference, reset=True) if reset is not None else 60)
                if fresh or getattr(row, reset_name) <= now:
                    setattr(row, reset_name, max(now+.01, expiry))
            if limit is not None and limit > 0:
                if kind == "requests" and self.rpm_ceiling:
                    row.server_request_limit = min(row.server_request_limit or limit, limit)
                    row.rpm = min(row.rpm or self.rpm_ceiling, self.rpm_ceiling, row.server_request_limit)
                elif kind == "tokens" and self.tpm_ceiling:
                    row.tpm = min(self.tpm_ceiling, limit)
                    row.credit = min(row.credit, row.tpm)
        row.header_sequence = max(row.header_sequence, permit.sequence)
        delay = parse_delay(headers.get("retry-after"), reference)
        if delay:
            row.not_before = max(row.not_before, now+delay)
        return delay

    def success(self, permit, headers=None, usage=None):
        with self.condition:
            row = self.domains[permit.domain]
            if row.active.pop(permit.id, None) is not permit:
                raise ComputerError("Unknown or settled API permit")
            now = self.clock()
            self._refresh(row, now)
            if isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0 for k in ("prompt_tokens", "completion_tokens")):
                actual = usage["prompt_tokens"] + usage["completion_tokens"]
                total = usage.get("total_tokens")
                if type(total) is int:
                    actual = max(actual, total)
                if row.tpm:
                    row.credit = min(row.tpm, row.credit + permit.cost-actual)
            self._headers(row, permit, headers)
            row.successes += 1
            elapsed = max(.0001, now-permit.started)
            row.latency = elapsed if not row.latency else .8*row.latency + .2*elapsed
            row.baseline = elapsed if not row.baseline else min(row.baseline, row.latency)
            if permit.generation == row.generation:
                if permit.probe:
                    row.mode, row.failures, row.streak = "closed", 0, 0
                    row.last_reason = "recovery probe succeeded; slow-start resumed"
                else:
                    row.streak += 1
                if self.settings.api_adaptive and row.mode == "closed":
                    if row.latency > max(.5, row.baseline * 1.8):
                        row.cap = max(1, row.cap//2)
                        row.rpm = max(.1, row.rpm*.85)
                        row.streak = 0
                    elif row.streak >= 8:
                        row.cap = min(self.maximum, row.cap+1)
                        ceiling = min(self.rpm_ceiling, row.server_request_limit or self.rpm_ceiling)
                        row.rpm = min(ceiling, row.rpm+max(1, row.rpm*.1))
                        row.streak = 0
            self._changed(row)

    def failure(self, permit, error):
        with self.condition:
            row = self.domains[permit.domain]
            if row.active.pop(permit.id, None) is not permit:
                raise ComputerError("Unknown or settled API permit")
            delay = self._headers(row, permit, getattr(error, "headers", {}))
            retryable = bool(getattr(error, "retryable", False))
            kind = getattr(error, "kind", "")
            row.last_reason = safe_text(str(error), 350)
            if kind == "compatibility":
                # A capability negotiation is visible and separately billed,
                # but is not evidence of a provider-wide outage.
                row.queue[permit.request] = {"family": permit.owner.split(".", 1)[0], "owner": permit.owner, "cost": permit.cost}
                if permit.probe:
                    row.mode = "open"
            elif retryable:
                current_epoch = permit.generation == row.generation
                if current_epoch:
                    # Coalesce a burst of overlapping failures into ONE
                    # congestion epoch, not 64 multiplicative reductions.
                    row.generation += 1
                    row.failures += 1
                    row.streak = 0
                    row.cap = 1
                    if row.rpm and (getattr(error, "status_code", None) == 429 or kind == "congestion"):
                        row.rpm = max(.1, row.rpm*.5)
                if getattr(error, "status_code", None) == 429 or kind == "congestion":
                    row.congestions += 1
                else:
                    row.outages += 1
                # Availability failures do not invent evidence of a lower
                # RPM quota. They open a circuit, then resume slow-start.
                fallback = min(300.0, self.settings.api_cooldown_seconds * 2**min(max(0, row.failures-1), 10))
                jitter = (int(hashlib.sha256(f"{permit.domain}:{row.failures}".encode()).hexdigest()[:4], 16)/65535) * fallback*.1
                server = finite_number(getattr(error, "retry_after", 0)) or 0
                row.not_before = max(row.not_before, self.clock()+max(delay, server, fallback+jitter))
                row.mode = "open"
                row.queue[permit.request] = {"family": permit.owner.split(".", 1)[0], "owner": permit.owner, "cost": permit.cost}
            else:
                if kind in ("authentication", "billing"):
                    row.blocked = row.last_reason + "; update local credentials/billing, then resume explicitly"
                    row.mode = "blocked"
                elif permit.probe:
                    # The endpoint answered, but this request needs a user
                    # fix. Do not poison other models with its 400/404/403.
                    row.mode = "closed"
            self._changed(row)
            return self._defer(permit.domain, permit.request, row,
                               "capability negotiation" if kind == "compatibility" else "shared API recovery; continuation preserved",
                               max(.01, row.not_before-self.clock())) if retryable else None

    def discard(self, key, request):
        with self.condition:
            row = self.domains.get(key)
            if row and row.queue.pop(request, None) is not None:
                self._changed(row)

    def discard_owner(self, owner):
        with self.condition:
            for row in self.domains.values():
                keys = [k for k, entry in row.queue.items() if entry["owner"] == owner]
                for key in keys:
                    row.queue.pop(key)
                if keys:
                    self._changed(row)

    def close(self):
        # Called only after all worker/client calls have drained.
        with self.condition:
            for row in self.domains.values():
                if row.active:
                    raise ComputerError("Cannot close traffic governor with active requests")
                row.queue.clear()
                self._changed(row)

    def ready(self, deferred):
        with self.lock:
            row = self.domains.get(deferred.domain)
            return row is None or row.revision != deferred.revision or self.clock() >= deferred.ready_at

    def wait(self, timeout=.2):
        with self.condition:
            self.condition.wait(timeout)

    def snapshot(self):
        with self.lock:
            now, epoch = self.clock(), self.wall()
            rows = []
            for key, row in self.domains.items():
                self._refresh(row, now)
                rows.append({"id": key, "label": row.label, "mode": row.mode, "cap": row.cap,
                    "active": len(row.active), "queued": len(row.queue), "rpm": round(row.rpm, 3), "tpm": row.tpm,
                    "credit": round(row.credit, 2), "saved_at": epoch,
                    "not_before_epoch": epoch+max(0, row.not_before-now),
                    "next_request_epoch": epoch+max(0, row.next_at-now),
                    "request_reset_epoch": epoch+max(0, row.request_reset-now),
                    "token_reset_epoch": epoch+max(0, row.token_reset-now),
                    "remaining_requests": row.remaining_requests, "remaining_tokens": row.remaining_tokens,
                    "server_request_limit": row.server_request_limit,
                    "cooldown_seconds": round(max(0, row.not_before-now), 2),
                    "latency_seconds": round(row.latency, 3), "attempts": row.attempts,
                    "successes": row.successes, "congestions": row.congestions, "outages": row.outages,
                    "probes": row.probes, "failures": row.failures, "peak": row.peak,
                    "blocked": row.blocked, "last_reason": row.last_reason})
            return {"enabled": True, "adaptive": self.settings.api_adaptive, "max_parallel_per_domain": self.maximum,
                    "active": sum(r["active"] for r in rows), "queued": sum(r["queued"] for r in rows),
                    "recovering": sum(r["mode"] in ("open", "half_open") for r in rows),
                    "blocked": sum(r["mode"] == "blocked" for r in rows), "domains": rows,
                    "scope": "One running Computer; not a distributed/cross-process API gateway"}


class RequestGovernor:
    """Compatibility helper for older callers; production uses AdaptiveGovernor."""
    def __init__(self, requests_per_minute=0, clock=None, sleeper=None):
        self.rpm = requests_per_minute
        self.clock, self.sleep = clock or time.monotonic, sleeper or time.sleep
        self.lock = threading.Lock()
        self.next_slot, self.cooldown = {}, {}

    def admit(self, provider, control, on_wait=None):
        announced = False
        while True:
            control()
            with self.lock:
                now = self.clock()
                delay = max(self.cooldown.get(provider, 0), self.next_slot.get(provider, 0))-now
                if delay <= 0:
                    if self.rpm:
                        self.next_slot[provider] = now+60/self.rpm
                    return
            if on_wait and not announced:
                on_wait(f"provider pacing/cooldown ({delay:.1f}s)")
                announced = True
            self.sleep(min(.1, max(.005, delay)))

    def penalize(self, provider, seconds):
        seconds = finite_number(seconds) or 0
        with self.lock:
            self.cooldown[provider] = max(self.cooldown.get(provider, 0), self.clock()+seconds)

    def status(self):
        with self.lock:
            return {k: round(max(0, until-self.clock()), 2) for k, until in self.cooldown.items() if until > self.clock()}
