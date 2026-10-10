"""Append-only model request bodies, one UTF-8 JSON record per line."""

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path


LOG_PATH = Path("logs/requests.jsonl")
_WRITE_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


class RequestRecorder:
    def __init__(self):
        self.path = LOG_PATH.resolve()

    async def record(self, path: str, raw: bytes | bytearray | str):
        if not isinstance(raw, str):
            raw = raw.decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
            # Invalid Unicode/non-finite JSON stays a raw string instead.
            json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, UnicodeError, RecursionError):
            body = raw
        entry = {"time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                 "path": path, "body": body}
        line = json.dumps(entry, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        line = line.encode("utf-8", errors="backslashreplace") + b"\n"
        try:
            await asyncio.to_thread(self._append, line)
        except OSError:
            # A full/read-only disk must not break model responses or log secrets.
            logger.error("Could not append request log; check logs directory permissions and disk space")

    def _append(self, line: bytes):
        with _WRITE_LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab") as handle:
                # Protect Linux deployments with multiple worker processes too.
                try:
                    import fcntl
                except ImportError:
                    fcntl = None
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    handle.write(line)
                    handle.flush()
                finally:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
