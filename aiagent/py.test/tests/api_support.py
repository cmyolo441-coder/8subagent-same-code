"""TEST-ONLY faults and a local HTTP model fixture. Never production fallbacks."""
from __future__ import annotations
import json
import re
import sys
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

from fullagent.computer.governor import AdaptiveGovernor
from fullagent.computer.transport import TransportError, provider_error
from tests.support import Driver, answer, tool
from tests.test_computer_network import client


def phase_and_owner(messages, fallback="a1"):
    seed = messages[1]["content"]
    phase = re.search(r"PHASE: ([^\n]+)", seed)
    owner = re.search(r"YOUR AGENT ID: ([a-z0-9.]+)", seed) or re.search(r"PARENT LEAD: (a[1-8])", seed)
    return phase.group(1) if phase else "planning", owner.group(1) if owner else fallback


class Clock:
    def __init__(self, now=0.0, epoch=1700000000.0):
        self.now, self.epoch = now, epoch
    def __call__(self):
        return self.now
    def wall(self):
        return self.epoch+self.now
    def advance(self, seconds):
        self.now += seconds


class AcceleratedClock:
    """Virtual scheduler time; not a real provider latency/RPM benchmark."""
    def __init__(self, speed=100):
        self.origin, self.epoch, self.speed = time.monotonic(), time.time(), speed
    def __call__(self):
        return (time.monotonic()-self.origin)*self.speed
    def wall(self):
        return self.epoch+self()
    def factory(self, settings, previous=None):
        return AdaptiveGovernor(settings, previous, clock=self, wall=self.wall)


class FaultDriver:
    def __init__(self, base=None, rules=None, status=503, delay=0, groups=None):
        self.base = base or Driver()
        self.rules, self.status, self.delay = dict(rules or {}), status, delay
        self.groups = groups or {}
        self.lock = threading.Lock()
        self.faults = []
        self.reached = threading.Event()
    def factory(self, who):
        inner, outer = self.base.factory(who), self
        class Client:
            model = inner.model
            provider = SimpleNamespace(key=outer.groups.get(who, "shared-test-api"))
            def chat(self, messages, schemas, max_tokens, control, on_update=None):
                control()
                phase, _ = phase_and_owner(messages, who)
                key = (who, phase, sum(m["role"] == "tool" for m in messages))
                with outer.lock:
                    remaining = outer.rules.get(key, 0)
                    if remaining:
                        if remaining > 0:
                            outer.rules[key] -= 1
                        outer.faults.append(key)
                if remaining:
                    outer.reached.set()
                    raise provider_error(outer.status, {"error": {"code": "test_fault", "message": "Intentional test-only provider fault"}},
                                         {"retry-after": str(outer.delay)})
                return inner.chat(messages, schemas, max_tokens, control, on_update)
            def close(self):
                inner.close()
        return Client()


class CommandDriver(Driver):
    """One approved command increments a real owned-file counter."""
    def factory(self, who):
        base = super().factory(who)
        if who != "a3":
            return base
        class Client:
            model = base.model
            def chat(self, messages, schemas, max_tokens, control, on_update=None):
                phase, _ = phase_and_owner(messages, who)
                outputs = [m for m in messages if m["role"] == "tool"]
                if phase == "building":
                    if not outputs:
                        script = ("from pathlib import Path; import re; p=Path('a3.py'); "
                                  "s=p.read_text() if p.exists() else ''; m=re.search(r'run_count = (\\d+)',s); "
                                  "n=int(m.group(1))+1 if m else 1; p.write_text(\"value = 'FIXED'\\nrun_count = \"+str(n)+'\\n')")
                        return tool("run_command", {"argv": [sys.executable, "-c", script], "cwd": "."})
                    result = json.loads(outputs[-1]["content"])
                    return answer(json.dumps({"status": "done" if result.get("ok") else "blocked",
                                              "summary": "Fixture command produced the owned file", "issues": [] if result.get("ok") else [str(result)]}))
                return base.chat(messages, schemas, max_tokens, control, on_update)
            def close(self):
                base.close()
        return Client()


class ModelHTTPFixture:
    """Real localhost HTTP transport with deterministic, labeled responses."""
    def __init__(self, driver=None, latency=.01):
        self.driver, self.latency = driver or Driver(), latency
        self.lock = threading.Lock()
        self.active = self.peak = 0
        self.requests = []
    def __enter__(self):
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                phase, who = phase_and_owner(payload["messages"])
                with outer.lock:
                    outer.active += 1
                    outer.peak = max(outer.peak, outer.active)
                    outer.requests.append((who, phase, time.monotonic()))
                code, headers = 200, {}
                try:
                    time.sleep(outer.latency)
                    c = outer.driver.factory(who)
                    try:
                        result = c.chat(payload["messages"], payload.get("tools", []), payload.get("max_tokens", 1024), lambda: None)
                    finally:
                        c.close()
                    body = {"choices": [{"message": {"content": result.content, "tool_calls": result.tool_calls},
                                         "finish_reason": result.finish_reason}], "usage": result.usage}
                except TransportError as exc:
                    code, headers = exc.status_code or 503, {"Retry-After": str(exc.retry_after)}
                    body = {"error": {"code": "fixture_fault", "message": str(exc)}}
                except Exception as exc:
                    code = 500
                    body = {"error": {"code": "fixture_error", "message": str(exc)}}
                raw = json.dumps(body).encode()
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(raw)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with outer.lock:
                        outer.active -= 1
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        return self
    def factory(self, who):
        c = client(self.url)
        # Eight aliases must still share a traffic domain, not bypass it.
        c.provider = replace(c.provider, key="alias-"+who, api_key="fixture-key-only")
        return c
    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
