import json
import os
import time
import uuid
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import fcntl

from .protocol import compute_crc32, verify_crc32


ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class LockTimeout(Exception):
    def __init__(self, path: Path, timeout: float, holder: Optional[dict] = None):
        self.path = path
        self.timeout = timeout
        self.holder = holder or {}
        super().__init__(f"Lock timeout on {path} after {timeout}s")


@dataclass
class LockInfo:
    pid: int
    acquired_at: float

    def to_dict(self) -> dict:
        return {"pid": self.pid, "acquiredAt": self.acquired_at}


class FileLock:
    def __init__(self, path: Path, timeout: float = 30.0, poll_interval: float = 0.1, shared: bool = False):
        self.path = Path(path)
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.shared = shared
        self._fh = None

    def _write_lock_info(self):
        info = LockInfo(pid=os.getpid(), acquired_at=time.time()).to_dict()
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(json.dumps(info))
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def _read_lock_info(self) -> Optional[dict]:
        try:
            with open(self.path, "r") as fh:
                raw = fh.read().strip()
                if not raw:
                    return None
                return json.loads(raw)
        except Exception:
            return None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+")
        lock_flag = fcntl.LOCK_SH if self.shared else fcntl.LOCK_EX
        start = time.time()
        while True:
            try:
                fcntl.flock(self._fh.fileno(), lock_flag | fcntl.LOCK_NB)
                self._write_lock_info()
                return
            except BlockingIOError:
                if time.time() - start >= self.timeout:
                    holder = self._read_lock_info() or {}
                    raise LockTimeout(self.path, self.timeout, holder)
                time.sleep(self.poll_interval)

    def release(self):
        if self._fh:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime(ISO_FORMAT)

def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def atomic_write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


class StateManager:
    def __init__(self, base_dir: Path, lock_timeout: float = 30.0):
        self.base_dir = Path(base_dir)
        self.audit_dir = self.base_dir / "audit"
        self.derived_dir = self.base_dir / "derived"
        self.events_path = self.audit_dir / "events.ndjson"
        self.status_path = self.base_dir / "status.json"
        self.sequence_path = self.derived_dir / "sequence.json"
        self.id_index_path = self.derived_dir / "idempotency-index.json"
        self.security_log = self.audit_dir / "security.log"
        self.lock_timeout = lock_timeout

    def _ensure_dirs(self):
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.derived_dir.mkdir(parents=True, exist_ok=True)

    def _load_idempotency_index(self) -> Dict[str, int]:
        if not self.id_index_path.exists():
            return {}
        try:
            with open(self.id_index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("keys", {})
        except Exception:
            return {}

    def _save_idempotency_index(self, keys: Dict[str, int]):
        atomic_write_json(self.id_index_path, {"keys": keys})

    def _read_last_sequence(self) -> int:
        if self.sequence_path.exists():
            try:
                with open(self.sequence_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return int(data.get("lastSequence", 0))
            except Exception:
                pass
        if not self.events_path.exists():
            return 0
        # fallback: read last non-empty line
        last_seq = 0
        try:
            with open(self.events_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                if size == 0:
                    return 0
                # read from end in chunks
                chunk = b""
                pos = size
                while pos > 0:
                    read_size = min(4096, pos)
                    pos -= read_size
                    f.seek(pos)
                    chunk = f.read(read_size) + chunk
                    if b"\n" in chunk:
                        break
                lines = [ln for ln in chunk.splitlines() if ln.strip()]
                if not lines:
                    return 0
                try:
                    last = json.loads(lines[-1].decode("utf-8"))
                    last_seq = int(last.get("sequenceNumber", 0))
                except Exception:
                    last_seq = 0
        except Exception:
            last_seq = 0
        return last_seq

    def _write_sequence(self, last_sequence: int):
        atomic_write_json(self.sequence_path, {"lastSequence": last_sequence, "updatedAt": utc_now()})

    def append_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_dirs()
        if "idempotencyKey" not in event:
            raise ValueError("idempotencyKey is required")

        lock = FileLock(self.events_path.with_suffix(self.events_path.suffix + ".lock"), timeout=self.lock_timeout)
        try:
            with lock:
                keys = self._load_idempotency_index()
                key = event["idempotencyKey"]
                if key in keys:
                    return {"status": "deduped", "event": None}

                seq = self._read_last_sequence() + 1
                event = {**event}
                event.setdefault("eventId", f"e-{uuid.uuid4().hex}")
                event.setdefault("schemaVersion", 1)
                event.setdefault("sequenceNumber", seq)
                event.setdefault("at", utc_now())

                event["crc32"] = compute_crc32(event)

                line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                self.events_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.events_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                    f.flush()
                    os.fsync(f.fileno())

                keys[key] = seq
                self._save_idempotency_index(keys)
                self._write_sequence(seq)
                return {"status": "appended", "event": event}
        except LockTimeout as e:
            self._append_security_log({
                "type": "LOCK_TIMEOUT_DETECTED",
                "path": str(self.events_path),
                "timeout": self.lock_timeout,
                "holder": e.holder,
                "at": utc_now(),
            })
            raise

    def write_status(self, status: Dict[str, Any]):
        self._ensure_dirs()
        lock = FileLock(self.status_path.with_suffix(self.status_path.suffix + ".lock"), timeout=self.lock_timeout)
        try:
            with lock:
                atomic_write_json(self.status_path, status)
        except LockTimeout as e:
            self._append_security_log({
                "type": "LOCK_TIMEOUT_DETECTED",
                "path": str(self.status_path),
                "timeout": self.lock_timeout,
                "holder": e.holder,
                "at": utc_now(),
            })
            raise

    def _append_security_log(self, entry: Dict[str, Any]):
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False)
        with open(self.security_log, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    def build_corrupted_event_payload(self, line_offset: int, raw_line: str, reason: str, project: str = "unknown") -> Tuple[dict, dict]:
        content_hash = sha256_hex(raw_line)
        corrupted_event = {
            "type": "CORRUPTED_LINE_DETECTED",
            "actor": "orchestrator",
            "project": project,
            "payload": {
                "lineOffset": line_offset,
                "contentHash": content_hash,
                "reason": reason,
            },
            "idempotencyKey": f"{project}:CORRUPTED_LINE:{line_offset}:{content_hash}",
        }
        recovery_event = {
            "type": "RECOVERY_STARTED",
            "actor": "orchestrator",
            "project": project,
            "payload": {
                "lineOffset": line_offset,
                "contentHash": content_hash,
                "reason": reason,
            },
            "idempotencyKey": f"{project}:RECOVERY_STARTED:{line_offset}:{content_hash}",
        }
        return corrupted_event, recovery_event
