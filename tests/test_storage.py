"""Shard storage: RAM + append-only WAL (spec shard-storage, ADR-008).

"Restart" in these tests = close a Storage and open a new one on the same file.
"""
import os

import pytest

from kvstore.storage import Storage, WalCorruptedError


@pytest.fixture
def wal_path(tmp_path):
    return str(tmp_path / "wal.log")


def reopen(storage, path):
    storage.close()
    return Storage(path)


def test_put_then_get(wal_path):
    s = Storage(wal_path)
    s.put("orders", "42", "2026-11-05", {"total": 300})
    assert s.get("orders", "42", "2026-11-05") == {"total": 300}


def test_get_missing_returns_none(wal_path):
    s = Storage(wal_path)
    assert s.get("orders", "42", "2026-11-05") is None


def test_put_same_key_overwrites(wal_path):
    s = Storage(wal_path)
    s.put("orders", "42", "2026-11-05", {"total": 300})
    s.put("orders", "42", "2026-11-05", {"total": 150})
    assert s.get("orders", "42", "2026-11-05") == {"total": 150}
    assert s.count() == 1


def test_delete(wal_path):
    s = Storage(wal_path)
    s.put("orders", "42", "2026-11-05", {"total": 300})
    assert s.delete("orders", "42", "2026-11-05") is True
    assert s.get("orders", "42", "2026-11-05") is None
    assert s.delete("orders", "42", "2026-11-05") is False


def test_table_without_sort_key(wal_path):
    s = Storage(wal_path)
    s.put("usertable", "user1", None, {"field0": "a"})
    assert s.get("usertable", "user1", None) == {"field0": "a"}
    assert s.delete("usertable", "user1", None) is True
    assert s.get("usertable", "user1", None) is None


def test_same_pk_in_different_tables_is_independent(wal_path):
    s = Storage(wal_path)
    s.put("users", "42", None, {"name": "Olena"})
    s.put("orders", "42", "2026-11-05", {"total": 300})
    assert s.get("users", "42", None) == {"name": "Olena"}
    assert s.get("orders", "42", "2026-11-05") == {"total": 300}


def test_restart_replays_overwrites_and_deletes(wal_path):
    s = Storage(wal_path)
    s.put("t", "A", None, {"v": 1})
    s.put("t", "A", None, {"v": 2})
    s.put("t", "B", None, {"v": 1})
    s.delete("t", "B", None)

    s = reopen(s, wal_path)

    assert s.get("t", "A", None) == {"v": 2}
    assert s.get("t", "B", None) is None
    assert s.count() == 1


def test_record_is_on_disk_when_put_returns(wal_path):
    s = Storage(wal_path)
    s.put("orders", "42", "2026-11-05", {"total": 300})
    with open(wal_path, encoding="utf-8") as f:
        assert '"total": 300' in f.read()


def test_put_and_delete_call_fsync(wal_path, monkeypatch):
    calls = []
    real_fsync = os.fsync

    def spy(fd):
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy)
    s = Storage(wal_path)

    s.put("t", "A", None, {"v": 1})
    assert len(calls) == 1
    s.delete("t", "A", None)
    assert len(calls) == 2


def test_truncated_last_line_is_dropped(wal_path):
    s = Storage(wal_path)
    s.put("t", "A", None, {"v": 1})
    s.put("t", "B", None, {"v": 2})
    s.close()
    size_before_crash = os.path.getsize(wal_path)
    with open(wal_path, "a", encoding="utf-8") as f:
        f.write('{"op": "put", "table": "t", "pk": "C"')  # process died mid-write

    s = Storage(wal_path)

    assert s.get("t", "A", None) == {"v": 1}
    assert s.get("t", "B", None) == {"v": 2}
    assert s.get("t", "C", None) is None
    assert os.path.getsize(wal_path) == size_before_crash


def test_storage_keeps_working_after_dropping_truncated_line(wal_path):
    s = Storage(wal_path)
    s.put("t", "A", None, {"v": 1})
    s.close()
    with open(wal_path, "a", encoding="utf-8") as f:
        f.write('{"op": "pu')

    s = Storage(wal_path)
    s.put("t", "B", None, {"v": 2})
    s = reopen(s, wal_path)

    assert s.get("t", "A", None) == {"v": 1}
    assert s.get("t", "B", None) == {"v": 2}


def test_corrupted_line_in_the_middle_refuses_to_start(wal_path):
    s = Storage(wal_path)
    s.put("t", "A", None, {"v": 1})
    s.close()
    with open(wal_path, "a", encoding="utf-8") as f:
        f.write("this is not json\n")
        f.write('{"op": "put", "table": "t", "pk": "B", "sk": null, "value": {"v": 2}}\n')

    with pytest.raises(WalCorruptedError):
        Storage(wal_path)


def test_query_returns_items_of_one_pk_ordered_by_sort_key(wal_path):
    s = Storage(wal_path)
    s.put("orders", "42", "2026-11-05", {"n": 3})
    s.put("orders", "42", "2026-10-30", {"n": 1})
    s.put("orders", "42", "2026-11-01", {"n": 2})
    s.put("orders", "7", "2026-11-02", {"n": 99})

    assert s.query("orders", "42") == [
        ("2026-10-30", {"n": 1}),
        ("2026-11-01", {"n": 2}),
        ("2026-11-05", {"n": 3}),
    ]


def test_query_bounds_are_inclusive(wal_path):
    s = Storage(wal_path)
    for sk in ["2026-10-30", "2026-11-01", "2026-11-05", "2026-11-07"]:
        s.put("orders", "42", sk, {"d": sk})

    result = s.query("orders", "42", sk_from="2026-11-01", sk_to="2026-11-05")

    assert [sk for sk, _ in result] == ["2026-11-01", "2026-11-05"]


def test_query_with_one_bound(wal_path):
    s = Storage(wal_path)
    for sk in ["a", "b", "c"]:
        s.put("t", "p", sk, {})

    assert [sk for sk, _ in s.query("t", "p", sk_from="b")] == ["b", "c"]
    assert [sk for sk, _ in s.query("t", "p", sk_to="b")] == ["a", "b"]


def test_query_unknown_pk_is_empty(wal_path):
    s = Storage(wal_path)
    assert s.query("orders", "nobody") == []
