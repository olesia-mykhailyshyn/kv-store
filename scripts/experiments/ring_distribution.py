#!/usr/bin/env python3
"""M2 experiment for ADR-2: hash function and number of virtual nodes.

For every (hash function, virtual nodes per shard) pair it builds a consistent hashing
ring and measures:
  - balance: share of keys on shard-1 with 2 shards (ideal 50 %);
  - movement: share of keys that change shard when a 3rd shard is added (ideal 1/3).
One ring is one random draw (shard positions come from hashing their names), so every
configuration is repeated TRIALS times with different shard names; we report the mean
and the worst deviation from the ideal.
It also shows why Python's built-in hash() is unusable: its value for a string changes
between processes (PYTHONHASHSEED), so a restarted coordinator would look in the wrong shard.

Standard library only. No AWS.
Usage: python3 scripts/experiments/ring_distribution.py [number_of_keys]   (default 1 000 000)
"""
import bisect
import hashlib
import os
import subprocess
import sys

KEYS = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
VNODES = [1, 10, 50, 100, 200, 500]
TRIALS = 20


def first_8_bytes(digest):
    """Ring position: first 8 bytes of a digest as an integer in 0 … 2^64 - 1."""
    return int.from_bytes(digest[:8], "big")


HASHES = {
    "md5": lambda s: first_8_bytes(hashlib.md5(s.encode()).digest()),
    "sha256": lambda s: first_8_bytes(hashlib.sha256(s.encode()).digest()),
    "blake2b": lambda s: first_8_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest()),
}


def build_ring(shards, vnodes, h):
    """Sorted ring positions and, at the same index, the shard that owns each position."""
    points = sorted((h(f"{shard}#{i}"), shard) for shard in shards for i in range(vnodes))
    return [p for p, _ in points], [s for _, s in points]


def owner(positions, owners, key_pos):
    """First ring point clockwise from the key; past the last point wrap to index 0."""
    i = bisect.bisect_right(positions, key_pos)
    return owners[i % len(owners)]


def assign(key_positions, shards, vnodes, h):
    positions, owners = build_ring(shards, vnodes, h)
    return [owner(positions, owners, k) for k in key_positions]


def builtin_hash_in_new_process(text):
    """hash(text) computed by a fresh Python process with a random hash seed."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONHASHSEED"}
    out = subprocess.run([sys.executable, "-c", f"print(hash({text!r}))"],
                         capture_output=True, text=True, env=env, check=True)
    return out.stdout.strip()


def main():
    keys = [f"usertable#user{i}" for i in range(KEYS)]

    print(f"Keys: {KEYS:,}. Trials per row: {TRIALS} (different shard names).")
    print("Ideal: 50 % per shard with 2 shards; 33.3 % of keys moved when adding a 3rd.\n")
    print("| hash | vnodes/shard | balance: mean deviation from 50 % | balance: worst | "
          "moved on 2→3: mean | moved: worst deviation from 33.3 % |")
    print("|---|---|---|---|---|---|")
    for name, h in HASHES.items():
        key_positions = [h(k) for k in keys]
        for v in VNODES:
            balance, moved = [], []
            for t in range(TRIALS):
                two = [f"t{t}-shard-1", f"t{t}-shard-2"]
                three = two + [f"t{t}-shard-3"]
                before = assign(key_positions, two, v, h)
                after = assign(key_positions, three, v, h)
                balance.append(abs(before.count(two[0]) / KEYS * 100 - 50))
                moved.append(sum(b != a for b, a in zip(before, after)) / KEYS * 100)
            mean_moved = sum(moved) / TRIALS
            worst_moved = max(abs(m - 100 / 3) for m in moved)
            print(f"| {name} | {v} | {sum(balance) / TRIALS:.1f} pp | {max(balance):.1f} pp | "
                  f"{mean_moved:.1f} % | {worst_moved:.1f} pp |")

    # Baseline without a ring: shard = hash % N
    key_positions = [HASHES["md5"](k) for k in keys]
    moved = sum(p % 2 != p % 3 for p in key_positions) / KEYS * 100
    print(f"\nWithout a ring (md5 % N): moved on 2→3 = {moved:.1f} % of keys.\n")

    print("Built-in hash() of the same key in 3 separate Python processes:")
    for _ in range(3):
        print(f"  hash('usertable#user42') = {builtin_hash_in_new_process('usertable#user42')}")

if __name__ == "__main__":
    main()
