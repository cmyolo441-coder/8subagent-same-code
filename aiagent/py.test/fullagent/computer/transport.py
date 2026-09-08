"""Bounded OpenAI-compatible transport for concurrent computer workers.

Uses FullAgent's configured models/providers and payload compatibility,
not eight copies of the heavyweight main Agent. Each HTTP attempt is
visible to the engine's token reservation/retry governor.
"""
from __future__ import annotations

import copy
import json
import threading
import time
from dataclasses import replace
from urllib.parse import urlsplit

import requests

from ..client import StreamResult, build_payload
from .state import ComputerError, safe_text
from .governor import rate_headers, finite_number, parse_delay, header_reference

_PAYLOAD_LOCK = threading.Lock()


class TransportError(ComputerError):
    def __init__(self, message, retryable=False, retry_after=0, status_code=None, headers=None, kind=""):
        super().__init__(message)
        self.headers = rate_headers(headers)
        self.kind = kind or ("congestion" if status_code == 429 else "unavailable" if retryable else "request")
        self.retryable = bool(retryable)
        self.status_code = status_code
        reference = header_reference(self.headers, time.time())
        self.retry_after = max(finite_number(retry_after) or 0,
                               parse_delay(self.headers.get("retry-after"), reference))
        self.requires_user_action = not self.retryable


def provider_error(status, detail, headers=None):
    """Separate temporary congestion from credentials/billing/request faults."""
    try:
        value = json.loads(detail) if isinstance(detail, str) else detail
        error = value.get("error", value) if isinstance(value, dict) else value
    except (TypeError, ValueError):
        error = detail
    codes = " ".join(str(error.get(k, "")) for k in ("code", "type", "reason")) if isinstance(error, dict) else ""
    text = (codes + " " + str(error)).lower()
    billing = ("insufficient_quota", "billing_hard_limit_reached", "billing_not_active",
               "insufficient_balance", "credit_balance_too_low", "credit balance is too low")
    if status == 402 or any(code in text for code in billing):
        kind, retryable = "billing", False
    elif status == 401 or any(code in codes.lower() for code in ("invalid_api_key", "authentication_error", "invalid_authentication")):
        kind, retryable = "authentication", False
    elif any(code in codes.lower() for code in ("content_filter", "policy_violation", "moderation_blocked")):
        kind, retryable = "request", False
    elif status in (408, 425, 429, 500, 502, 503, 504, 529):
        kind, retryable = ("congestion" if status == 429 else "unavailable"), True
    elif status == 200 and any(code in codes.lower() for code in ("overloaded", "server_error", "api_error", "timeout", "rate_limit")):
        kind, retryable = ("congestion" if "rate_limit" in codes.lower() else "unavailable"), True
    else:
        kind, retryable = "request", False
    return TransportError(f"Provider HTTP {status}: {safe_text(error, 350)}", retryable,
                          status_code=status, headers=headers, kind=kind)


class SessionPool:
    """One HTTP session per executing thread, shared across logical agents.

    Each session is used by exactly one thread. Authorization is supplied
    per request, cookies are cleared and ambient netrc/proxy auth is off.
    Cached host pools are bounded; close only after all workers drain.
    """
    def __init__(self, max_sessions):
        self.max_sessions = max_sessions
        self.lock = threading.Lock()
        self.sessions = {}
        self.closed = False
        self.peak = 0

    def session(self):
        ident = threading.get_ident()
        with self.lock:
            if self.closed:
                raise ComputerError("HTTP session pool is closed")
            if ident not in self.sessions:
                if len(self.sessions) >= self.max_sessions:
                    raise ComputerError("HTTP session pool limit reached")
                session = requests.Session()
                session.trust_env = False
                adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=1, max_retries=0, pool_block=True)
                session.mount("https://", adapter)
                session.mount("http://", adapter)
                self.sessions[ident] = session
                self.peak = max(self.peak, len(self.sessions))
            session = self.sessions[ident]
            session.cookies.clear()
            return session

    def close(self):
        with self.lock:
            self.closed = True
            for session in self.sessions.values():
                session.close()
            self.sessions.clear()


class APIClient:
    def __init__(self, provider, model, effort, timeout=45, session_pool=None):
        self.provider, self.model, self.effort = provider, model, effort
        self.timeout = timeout
        self.session_pool = session_pool
        self._local = threading.local()
        self._lock = threading.Lock()
        self._sessions = []
        self._include_usage = True
        self._stream = True
        p = urlsplit(provider.base_url)
        if p.scheme not in ("https", "http") or not p.hostname or p.username or p.password:
            raise ComputerError("Provider base_url must be an HTTP(S) endpoint without embedded credentials")
        if p.scheme == "http" and p.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ComputerError("Model providers require HTTPS except explicit loopback/local endpoints")
        if not model.supports_tools:
            raise ComputerError(f"Selected model {model.id} does not support tools")
        if not provider.api_key and p.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ComputerError(f"No API key configured for {provider.name}; configure it locally before starting")

    def _session(self):
        if self.session_pool is not None:
            return self.session_pool.session()
        if not getattr(self._local, "session", None):
            session = requests.Session()
            session.trust_env = False
            adapter = requests.adapters.HTTPAdapter(pool_connections=2, pool_maxsize=2, max_retries=0)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            self._local.session = session
            with self._lock:
                self._sessions.append(session)
        self._local.session.cookies.clear()
        return self._local.session

    def close(self):
        with self._lock:
            for session in self._sessions:
                session.close()
            self._sessions.clear()

    def chat(self, messages, schemas, max_tokens, control, on_update=None):
        control()
        # build_payload sanitizes messages and reads shared calibration.
        # Only preparation is locked; the governor separately controls API overlap.
        with _PAYLOAD_LOCK:
            payload = build_payload(self.model, replace(self.effort, max_tokens=max_tokens),
                                    copy.deepcopy(messages), schemas or None, stream=self._stream)
        payload["max_tokens"] = min(max_tokens, payload.get("max_tokens", max_tokens))
        if self._stream and self._include_usage:
            payload["stream_options"] = {"include_usage": True}
        url = self.provider.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream,application/json"}
        if self.provider.api_key:
            headers["Authorization"] = "Bearer " + self.provider.api_key
        started = time.monotonic()
        quota_headers = {}
        try:
            with self._session().post(url, json=payload, headers=headers, stream=True, allow_redirects=False,
                                      timeout=(min(10, self.timeout), min(15, self.timeout))) as response:
                quota_headers = rate_headers(response.headers)
                if response.status_code != 200:
                    error_bytes = next(response.iter_content(4096), b"")
                    detail = error_bytes.decode("utf-8", errors="replace")
                    # A compatibility retry is a NEW budgeted attempt.
                    if response.status_code in (400, 422) and "stream_options" in detail and self._include_usage:
                        self._include_usage = False
                        raise TransportError("Provider rejected stream_options; negotiate without usage streaming", True, headers=quota_headers, kind="compatibility")
                    if response.status_code in (400, 422) and "stream" in detail.lower() and self._stream:
                        self._stream = False
                        raise TransportError("Provider rejected streaming; negotiate bounded JSON response", True, headers=quota_headers, kind="compatibility")
                    raise provider_error(response.status_code, detail, quota_headers)
                ctype = response.headers.get("Content-Type", "").lower()
                limit = min(2_000_000, max(131072, max_tokens * 40))
                raw = bytearray()
                size = 0
                result = StreamResult(model=self.model.id, response_headers=quota_headers)
                accum = {}
                got_finish = False
                data_lines = []

                def consume(event):
                    nonlocal got_finish
                    if event.strip() == "[DONE]":
                        got_finish = True
                        return
                    if not event.strip():
                        return
                    value = json.loads(event)
                    if value.get("error"):
                        raise provider_error(200, value, quota_headers)
                    if value.get("usage"):
                        result.usage = value["usage"]
                    if value.get("model"):
                        result.model = str(value["model"])
                    choices = value.get("choices") or []
                    if not choices:
                        return
                    choice = choices[0]
                    if choice.get("finish_reason"):
                        result.finish_reason = choice["finish_reason"]
                        got_finish = True
                    delta = choice.get("delta", {})
                    piece = delta.get("content")
                    if isinstance(piece, str):
                        result.content += piece
                        if on_update:
                            on_update("receiving response", len(result.content))
                    # Deliberately do not store or expose private reasoning.
                    for tool in delta.get("tool_calls") or []:
                        index = tool.get("index", 0)
                        if type(index) is not int or not 0 <= index < 8:
                            raise TransportError("Provider emitted too many tool calls")
                        current = accum.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        current["id"] = tool.get("id") or current["id"]
                        fn = tool.get("function") or {}
                        current["function"]["name"] += fn.get("name") or ""
                        current["function"]["arguments"] += fn.get("arguments") or ""
                        if on_update and fn.get("name"):
                            on_update("preparing " + str(fn["name"]), 0)

                for chunk in response.iter_content(4096):
                    control()
                    if time.monotonic() - started > self.timeout:
                        raise TransportError("Model request deadline exceeded", True, headers=quota_headers, kind="unavailable")
                    size += len(chunk)
                    if size > limit:
                        raise TransportError("Model response exceeded the bounded buffer")
                    raw.extend(chunk)
                    if "text/event-stream" not in ctype:
                        continue
                    while b"\n" in raw:
                        line, _, rest = raw.partition(b"\n")
                        raw[:] = rest
                        text = line.decode("utf-8", errors="replace").rstrip("\r")
                        if text.startswith("data:"):
                            data_lines.append(text[5:].lstrip())
                        elif not text and data_lines:
                            consume("\n".join(data_lines))
                            data_lines.clear()
                control()
                if "text/event-stream" in ctype:
                    if raw.startswith(b"data:"):
                        data_lines.append(raw[5:].decode("utf-8", errors="replace").strip())
                    if data_lines:
                        consume("\n".join(data_lines))
                    if not got_finish:
                        raise TransportError("Provider stream ended without completion; no partial tool call was executed", True, headers=quota_headers, kind="unavailable")
                    result.tool_calls = [accum[k] for k in sorted(accum)]
                else:
                    value = json.loads(bytes(raw).decode("utf-8"))
                    if value.get("error"):
                        raise provider_error(200, value, quota_headers)
                    choices = value.get("choices") or []
                    if not choices:
                        raise TransportError("Provider returned no choices", True, headers=quota_headers, kind="unavailable")
                    choice = choices[0]
                    message = choice.get("message") or {}
                    result.content = message.get("content") or ""
                    result.tool_calls = message.get("tool_calls") or []
                    result.finish_reason = choice.get("finish_reason")
                    result.usage = value.get("usage")
                if len(result.tool_calls) > 8:
                    raise TransportError("Provider emitted more than eight tool calls in one step")
                if result.finish_reason in ("length", "content_filter"):
                    raise TransportError("Model response was truncated/filtered; no incomplete tool call was executed")
                for i, tc in enumerate(result.tool_calls):
                    if not tc.get("id"):
                        tc["id"] = f"call_{i}"
                return result
        except requests.RequestException as exc:
            # URLs and authorization values are never included in errors.
            raise TransportError(f"Model connection failed ({type(exc).__name__})", retryable=True, headers=quota_headers, kind="unavailable") from exc
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise TransportError(f"Malformed model response ({type(exc).__name__})", True,
                                 headers=quota_headers, kind="unavailable") from exc
