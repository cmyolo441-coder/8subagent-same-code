"""Bounded durable worker continuations and write-ahead tool boundaries.

Transient model failures resume the same conversation, not a new worker
that repeats successful effects. This is NOT transactional exactly-once
host execution: an interrupted side-effecting tool is flagged for human
reconciliation, never blindly replayed after a crash.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from pathlib import Path

from .state import ComputerError, atomic_json

MAX_RECORD_BYTES = 4_000_000
MAX_RECORDS = 2048


class ContinuationUnsafe(ComputerError):
    requires_user_action = True


class ContinuationInputChanged(ContinuationUnsafe):
    """A valid stored record has different inputs; its effect stage matters."""
    def __init__(self, stage):
        super().__init__("Job inputs or authority changed; inspect/reset the affected branch before reusing its continuation")
        self.stage = stage


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


class Continuations:
    def __init__(self, board):
        self.board = board
        self.folder = board.path / "continuations"
        if self.folder.is_symlink():
            raise ContinuationUnsafe("Symlinked continuation directory is not allowed")
        self.folder.mkdir(exist_ok=True)
        try:
            self.folder.chmod(0o700)
        except OSError:
            pass
        self.lock = threading.RLock()
        with board.lock:
            refs = board.data.setdefault("continuations", {})
            if not isinstance(refs, dict) or len(refs) > MAX_RECORDS:
                raise ContinuationUnsafe("Invalid or oversized continuation index")

    def _path(self, key):
        if not isinstance(key, str) or not 1 <= len(key) <= 200:
            raise ContinuationUnsafe("Invalid continuation key")
        if self.folder.is_symlink():
            raise ContinuationUnsafe("Continuation directory was replaced by a symlink")
        path = self.folder / (hashlib.sha256(key.encode()).hexdigest()+".json")
        if path.is_symlink() or (path.exists() and path.stat().st_nlink != 1):
            raise ContinuationUnsafe("Linked continuation files are not allowed")
        return path

    def load(self, key, binding=None):
        with self.lock:
            path = self._path(key)
            with self.board.lock:
                ref = copy.deepcopy(self.board.data["continuations"].get(key))
            if ref is None:
                if path.exists():
                    raise ContinuationUnsafe("Uncommitted continuation found; inspect local recovery state before restarting this job")
                return None
            if not isinstance(ref, dict) or not path.is_file() or path.stat().st_size > MAX_RECORD_BYTES:
                raise ContinuationUnsafe("Missing/oversized continuation; refusing to replay prior actions")
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != ref.get("sha256"):
                raise ContinuationUnsafe("Continuation checksum mismatch; refusing automatic replay")
            try:
                data = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise ContinuationUnsafe("Invalid continuation JSON") from exc
            if not isinstance(data, dict) or data.get("version") != 1 or data.get("key") != key:
                raise ContinuationUnsafe("Invalid continuation identity/version")
            value = data.get("state")
            if (not isinstance(value, dict) or value.get("stage") not in
                    ("request", "response", "tools", "uncertain", "complete")
                    or type(value.get("step", 0)) is not int or not 0 <= value.get("step", 0) <= 1000):
                raise ContinuationUnsafe("Invalid continuation stage or step")
            if binding is not None and data.get("binding") != binding:
                raise ContinuationInputChanged(value["stage"])
            return value

    def save(self, key, binding, state, owner):
        with self.lock:
            path = self._path(key)
            value = {"version": 1, "key": key, "binding": binding, "state": state}
            raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
            if len(raw) > MAX_RECORD_BYTES:
                raise ContinuationUnsafe("Continuation exceeds the bounded buffer; no next action was executed")
            with self.board.lock:
                refs = self.board.data["continuations"]
                if refs.get(key, {}).get("sha256") == hashlib.sha256(raw).hexdigest() and path.is_file():
                    return  # unchanged parked state: no repeated disk rewrite
                if key not in refs and len(refs) >= MAX_RECORDS:
                    raise ContinuationUnsafe("Continuation record limit reached")
            # Publish the record BEFORE its index reference. A crash between
            # the two creates a detectable mismatch, not silent rollback to
            # a state that could blindly repeat a command.
            atomic_json(path, value)
            checksum = hashlib.sha256(raw).hexdigest()
            with self.board.lock:
                self.board.data["continuations"][key] = {
                    "sha256": checksum, "owner": owner, "stage": state["stage"],
                    "step": state.get("step", 0), "bytes": len(raw)}
                self.board.save()

    def complete(self, key, binding, result, owner, fingerprint=None, step=0):
        # Drop the finished conversation/repeat table, keeping a small
        # result until its scheduler has durably acknowledged it.
        self.save(key, binding, {"stage": "complete", "step": step, "result": result,
                                 "fingerprint": fingerprint}, owner)

    def discard(self, key):
        with self.lock:
            path = self._path(key)
            with self.board.lock:
                if key not in self.board.data["continuations"]:
                    return
                self.board.data["continuations"].pop(key)
                self.board.save()
            path.unlink(missing_ok=True)

    def discard_completed(self, key):
        with self.board.lock:
            ref = self.board.data["continuations"].get(key, {})
        if ref.get("stage") == "complete":
            self.discard(key)

    def invalidate_owner(self, owner):
        with self.board.lock:
            keys = [k for k, ref in self.board.data["continuations"].items() if ref.get("owner") == owner]
        for key in keys:
            self.discard(key)
