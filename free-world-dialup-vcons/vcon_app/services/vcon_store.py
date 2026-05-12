import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional


class VconStore:
    def __init__(self, maxlen: int = 100):
        self._store: deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def add(self, headers: dict, payload: Any) -> dict:
        entry = {
            "id": str(uuid.uuid4()),
            "received_at": datetime.now(timezone.utc).isoformat(),
            "headers": headers,
            "payload": payload,
        }
        with self._lock:
            self._store.appendleft(entry)
        return entry

    def list(self) -> list:
        with self._lock:
            return list(self._store)

    def get(self, vcon_id: str) -> Optional[dict]:
        with self._lock:
            for entry in self._store:
                if entry["id"] == vcon_id:
                    return entry
        return None
