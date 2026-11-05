"""Consistent hashing ring: which shard owns a key (ADR-006, spec sharding).

Every shard is put on the ring 500 times ("virtual nodes"), at positions computed from
labels "{shard_id}#{i}". A key belongs to the first label clockwise from the key's
position. With many labels per shard the arcs average out, so the shards get nearly
equal shares, and adding a shard only takes the keys that fall on its new arcs (~1/N).

Positions use MD5, not the built-in hash(): hash() of a string changes between
processes (PYTHONHASHSEED), so a restarted coordinator would route keys elsewhere.
Labels use the stable shard id, never its address: a re-created server with a new IP
must keep the same arcs (ADR-009).

Not thread-safe on purpose: the coordinator holds its own lock around the ring and
the shard address table, so one lock protects both together.
"""
import bisect
import hashlib
from typing import List, Tuple


def position(text: str) -> int:
    """Place on the ring 0 .. 2**64 - 1: first 8 bytes of MD5 as a big-endian number."""
    return int.from_bytes(hashlib.md5(text.encode("utf-8")).digest()[:8], "big")


def ring_key(table: str, pk: str) -> str:
    """Only the partition key goes on the ring, so all items of one PK share a shard (ADR-007)."""
    return f"{table}#{pk}"


class Ring:
    def __init__(self, vnodes: int = 500):
        self._vnodes = vnodes
        self._shards: List[str] = []
        # (position, shard_id), sorted by position; parallel list of positions for bisect
        self._labels: List[Tuple[int, str]] = []
        self._positions: List[int] = []

    def add(self, shard_id: str) -> bool:
        """True if the shard is new. Adding a known shard changes nothing."""
        if shard_id in self._shards:
            return False
        self._shards.append(shard_id)
        for i in range(self._vnodes):
            self._labels.append((position(f"{shard_id}#{i}"), shard_id))
        self._rebuild()
        return True

    def remove(self, shard_id: str) -> None:
        if shard_id not in self._shards:
            return
        self._shards.remove(shard_id)
        self._labels = [label for label in self._labels if label[1] != shard_id]
        self._rebuild()

    def owner(self, key: str) -> str:
        if not self._labels:
            raise LookupError("no shards on the ring")
        # first label strictly after the key; past the last label we wrap to the first
        index = bisect.bisect_right(self._positions, position(key))
        if index == len(self._positions):
            index = 0
        return self._labels[index][1]

    def shard_ids(self) -> List[str]:
        return sorted(self._shards)

    def __len__(self) -> int:
        return len(self._shards)

    def _rebuild(self) -> None:
        # sorting by (position, shard_id) also makes an (unlikely) position tie deterministic
        self._labels.sort()
        self._positions = [pos for pos, _ in self._labels]
