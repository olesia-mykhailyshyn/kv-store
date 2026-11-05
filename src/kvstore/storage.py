"""Item storage: state in RAM, every change first appended to a write-ahead log (ADR-008).

Write path: append a JSON line to the WAL -> flush -> fsync -> apply to RAM -> return.
So when put()/delete() returns, the change is on disk (spec shard-storage).

Start: replay the WAL from the beginning. A last line without "\n" means the process died
mid-write before fsync, so the change was never confirmed: drop it and truncate the file.
A broken line that does end with "\n" means real corruption: refuse to start.

Used by the shard, by the coordinator in single-node mode and for the coordinator's
table definitions. sk is None for tables without a sort key.
"""
import json
import os
import threading
from typing import Dict, List, Optional, Tuple


class WalCorruptedError(Exception):
    """A complete WAL line cannot be parsed: data would be lost silently if we started."""


class Storage:
    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        # (table, pk) -> {sk: value}; grouping by PK makes Query cheap
        self._data: Dict[Tuple[str, str], Dict[Optional[str], dict]] = {}
        self._replay()
        self._wal = open(path, "a", encoding="utf-8")

    def put(self, table: str, pk: str, sk: Optional[str], value: dict) -> None:
        record = {"op": "put", "table": table, "pk": pk, "sk": sk, "value": value}
        with self._lock:
            self._append(record)
            self._apply(record)

    def delete(self, table: str, pk: str, sk: Optional[str]) -> bool:
        """True if the item existed. Deleting a missing item writes nothing."""
        record = {"op": "delete", "table": table, "pk": pk, "sk": sk}
        with self._lock:
            if sk not in self._data.get((table, pk), {}):
                return False
            self._append(record)
            self._apply(record)
            return True

    def get(self, table: str, pk: str, sk: Optional[str]) -> Optional[dict]:
        with self._lock:
            return self._data.get((table, pk), {}).get(sk)

    def query(self, table: str, pk: str, sk_from: Optional[str] = None,
              sk_to: Optional[str] = None) -> List[Tuple[str, dict]]:
        """Items of one (table, pk) ordered by sort key; bounds are inclusive."""
        with self._lock:
            items = dict(self._data.get((table, pk), {}))
        result = []
        for sk in sorted(k for k in items if k is not None):
            if sk_from is not None and sk < sk_from:
                continue
            if sk_to is not None and sk > sk_to:
                continue
            result.append((sk, items[sk]))
        return result

    def count(self) -> int:
        with self._lock:
            return sum(len(items) for items in self._data.values())

    def close(self) -> None:
        self._wal.close()

    def _append(self, record: dict) -> None:
        self._wal.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._wal.flush()               # Python buffer -> OS
        os.fsync(self._wal.fileno())    # OS cache -> disk

    def _apply(self, record: dict) -> None:
        key = (record["table"], record["pk"])
        if record["op"] == "put":
            self._data.setdefault(key, {})[record["sk"]] = record["value"]
        else:
            items = self._data.get(key, {})
            items.pop(record["sk"], None)
            if not items:
                self._data.pop(key, None)

    def _replay(self) -> None:
        if not os.path.exists(self._path):
            return
        with open(self._path, "rb") as f:
            content = f.read()

        *complete, tail = content.split(b"\n")  # tail == b"" when the file ends with "\n"
        offset = 0
        for number, line in enumerate(complete, start=1):
            try:
                self._apply(json.loads(line))
            except (ValueError, KeyError) as e:
                raise WalCorruptedError(f"{self._path}: line {number} is broken: {e}") from e
            offset += len(line) + 1

        if tail:
            with open(self._path, "r+b") as f:
                f.truncate(offset)
                os.fsync(f.fileno())
