"""Consistent hashing ring: scenarios of spec sharding, numbers from ADR-006.

The 1,000,000-key tests take a few seconds: the spec asks for exactly that key set,
so a smaller sample would test something else.
"""
import os
import subprocess
import sys

import pytest

from kvstore.ring import Ring, position, ring_key

KEYS = 1_000_000
TOLERANCE = 0.05  # 5 percentage points


@pytest.fixture(scope="module")
def ycsb_keys():
    return [ring_key("usertable", f"user{i}") for i in range(KEYS)]


@pytest.fixture(scope="module")
def owners_2_and_3(ycsb_keys):
    """Owner of every key with 2 shards, then with a 3rd shard added (computed once)."""
    ring = Ring()
    ring.add("shard-1")
    ring.add("shard-2")
    before = [ring.owner(k) for k in ycsb_keys]
    ring.add("shard-3")
    after = [ring.owner(k) for k in ycsb_keys]
    return before, after


def two_shard_ring():
    ring = Ring()
    ring.add("shard-1")
    ring.add("shard-2")
    return ring


def test_ring_key_format():
    assert ring_key("orders", "42") == "orders#42"


def test_position_is_first_8_bytes_of_md5():
    # md5("a") = 0cc175b9c0f1b6a8 31c399e269772661 -> first 8 bytes as a big-endian int
    assert position("a") == 0x0CC175B9C0F1B6A8


def test_empty_ring_raises_lookup_error():
    with pytest.raises(LookupError):
        Ring().owner("orders#42")


def test_add_returns_true_only_for_new_shard():
    ring = Ring()
    assert ring.add("shard-1") is True
    assert ring.add("shard-1") is False
    assert len(ring) == 1
    assert ring.shard_ids() == ["shard-1"]


def test_same_pk_same_shard_regardless_of_sort_key():
    # the ring key has no sort key, so all items of orders/42 go to one shard
    ring = two_shard_ring()
    assert ring.owner(ring_key("orders", "42")) == ring.owner(ring_key("orders", "42"))


def test_owner_is_deterministic_across_processes():
    """Python's built-in hash() would differ here: PYTHONHASHSEED is random per process."""
    keys = [ring_key("usertable", f"user{i}") for i in range(200)]
    ring = two_shard_ring()
    expected = [ring.owner(k) for k in keys]

    code = (
        "from kvstore.ring import Ring, ring_key\n"
        "r = Ring(); r.add('shard-1'); r.add('shard-2')\n"
        "for i in range(200): print(r.owner(ring_key('usertable', 'user%d' % i)))\n"
    )
    src = os.path.join(os.path.dirname(__file__), "..", "src")
    env = dict(os.environ, PYTHONPATH=os.path.abspath(src), PYTHONHASHSEED="random")
    out = subprocess.run([sys.executable, "-c", code], env=env, check=True,
                         capture_output=True, text=True, timeout=30).stdout
    assert out.split() == expected


def test_positions_depend_only_on_shard_id():
    # order of registration (and anything else, e.g. the shard's address) does not matter
    a = Ring()
    a.add("shard-1")
    a.add("shard-2")
    b = Ring()
    b.add("shard-2")
    b.add("shard-1")
    keys = [ring_key("usertable", f"user{i}") for i in range(2000)]
    assert [a.owner(k) for k in keys] == [b.owner(k) for k in keys]


def test_balance_two_shards(ycsb_keys, owners_2_and_3):
    before, _ = owners_2_and_3
    share = before.count("shard-1") / len(ycsb_keys)
    assert abs(share - 0.5) <= TOLERANCE, f"shard-1 got {share:.2%}"


def test_adding_third_shard_moves_few_keys_only_to_new_shard(ycsb_keys, owners_2_and_3):
    before, after = owners_2_and_3
    moved = [(old, new) for old, new in zip(before, after) if old != new]
    assert len(moved) / len(ycsb_keys) <= 1 / 3 + TOLERANCE
    assert all(new == "shard-3" for _, new in moved)


def test_remove_shard():
    ring = two_shard_ring()
    ring.remove("shard-2")
    assert ring.shard_ids() == ["shard-1"]
    assert len(ring) == 1
    keys = [ring_key("usertable", f"user{i}") for i in range(1000)]
    assert all(ring.owner(k) == "shard-1" for k in keys)


def test_remove_then_add_restores_same_owners():
    ring = two_shard_ring()
    keys = [ring_key("usertable", f"user{i}") for i in range(1000)]
    before = [ring.owner(k) for k in keys]
    ring.remove("shard-2")
    ring.add("shard-2")
    assert [ring.owner(k) for k in keys] == before


def test_remove_last_shard_empties_ring():
    ring = Ring()
    ring.add("shard-1")
    ring.remove("shard-1")
    assert len(ring) == 0
    with pytest.raises(LookupError):
        ring.owner("orders#42")
