"""Coordinator in sharded mode + 2 shard processes on localhost (spec sharding, spec kv-api).

TestKvApiSharded re-runs every kv-api scenario from test_api_single.py, now through the
ring and the shards' internal API. The other tests check the sharding requirements.
Which shard holds a key is read from the shards themselves (internal GET), so the
tests do not depend on the ring's internals.
"""
import http.client
import json

import pytest

from conftest import Launcher, assert_error, assert_schema, call, wait_for
from test_api_single import TestKvApi as _KvApiScenarios  # "_" name: not collected twice


def ring_size(coordinator_url: str) -> int:
    _, body, _ = call(coordinator_url, "GET", "/health")
    return len(body["shards"])


def start_cluster(launch: Launcher):
    """Coordinator first, then shard-1 and shard-2; returns once both are on the ring."""
    coordinator = launch.coordinator("sharded")
    shards = [launch.shard(f"shard-{n}", coordinator.url) for n in (1, 2)]
    wait_for(lambda: ring_size(coordinator.url) == 2)
    return coordinator, shards


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    launch = Launcher(str(tmp_path_factory.mktemp("sharded")))
    coordinator, _ = start_cluster(launch)
    yield coordinator.url
    launch.kill_all()


class TestKvApiSharded(_KvApiScenarios):
    """Same scenarios, the "api" fixture above points at the sharded coordinator."""


# --- helpers ---------------------------------------------------------------------------

def make_users_table(url: str) -> None:
    assert call(url, "POST", "/tables", {"name": "users", "partitionKey": "id"})[0] == 201


def holders(shards, table: str, pk: str, sk=None):
    """Ids of the shards whose storage has this item (asked through the internal API)."""
    query = f"/internal/items?table={table}&pk={pk}" + (f"&sk={sk}" if sk else "")
    return [s.args[s.args.index("--id") + 1] for s in shards if call(s.url, "GET", query)[0] == 200]


def write_users(url: str, count: int):
    for i in range(count):
        assert call(url, "PUT", "/tables/users/items", {"id": f"user{i}", "value": {"n": i}})[0] == 201


# --- sharding requirements -------------------------------------------------------------

def test_items_of_one_pk_land_on_one_shard(launcher):
    coordinator, shards = start_cluster(launcher)
    definition = {"name": "orders", "partitionKey": "userId", "sortKey": "orderDate"}
    assert call(coordinator.url, "POST", "/tables", definition)[0] == 201

    for user in ("42", "43", "44", "45"):
        for date in ("2026-11-01", "2026-11-05", "2026-11-07"):
            call(coordinator.url, "PUT", "/tables/orders/items",
                 {"userId": user, "orderDate": date, "value": {}})
        places = {tuple(holders(shards, "orders", user, date))
                  for date in ("2026-11-01", "2026-11-05", "2026-11-07")}
        assert len(places) == 1 and len(next(iter(places))) == 1, places


def test_both_shards_get_keys_and_health_counts_them(launcher):
    coordinator, shards = start_cluster(launcher)
    make_users_table(coordinator.url)
    write_users(coordinator.url, 100)

    counts = []
    for shard in shards:
        status, body, _ = call(shard.url, "GET", "/health")
        assert status == 200
        assert_schema("Health", body)
        assert body["role"] == "shard"
        counts.append(body["items"])
    assert sum(counts) == 100
    assert min(counts) > 0  # with 100 keys both shards get some


def test_coordinator_health_lists_shards(launcher):
    coordinator, shards = start_cluster(launcher)
    status, body, _ = call(coordinator.url, "GET", "/health")
    assert status == 200
    assert_schema("Health", body)
    assert body["mode"] == "sharded"
    assert sorted(s["id"] for s in body["shards"]) == ["shard-1", "shard-2"]
    assert {s["url"] for s in body["shards"]} == {s.url for s in shards}


def test_register_new_and_known_shard(launcher):
    coordinator, _ = start_cluster(launcher)
    status, body, _ = call(coordinator.url, "POST", "/shards/register",
                           {"id": "shard-9", "url": "http://127.0.0.1:1"})
    assert status == 200
    assert_schema("ShardRegistrationResult", body)
    assert body == {"id": "shard-9", "newShard": True, "shards": 3}

    _, body, _ = call(coordinator.url, "POST", "/shards/register",
                      {"id": "shard-9", "url": "http://127.0.0.1:2"})
    assert body == {"id": "shard-9", "newShard": False, "shards": 3}


@pytest.mark.parametrize("body", [
    {"id": "shard#1", "url": "http://127.0.0.1:1"},
    {"id": "shard-1"},
    {"id": "shard-1", "url": "not a url"},
    {"id": "shard-1", "url": "http://127.0.0.1:1", "extra": 1},
])
def test_register_bad_body(launcher, body):
    coordinator = launcher.coordinator("sharded")
    assert_error(call(coordinator.url, "POST", "/shards/register", body), 400, "bad_request")


def test_register_rejects_url_of_another_host(launcher):
    """Only a shard's own address is accepted: the url host must be the sender's IP (ADR-009)."""
    coordinator, _ = start_cluster(launcher)
    for url in ("http://10.9.9.9:8080", "http://169.254.169.254:80"):
        answer = call(coordinator.url, "POST", "/shards/register", {"id": "shard-x", "url": url})
        assert_error(answer, 400, "bad_request")
    assert ring_size(coordinator.url) == 2


def test_stale_keepalive_connection_to_shard_is_retried(launcher):
    """Coordinator thread keeps a connection to each shard; a restarted shard makes it stale.
    One client connection = one coordinator thread, so the second GET reuses that connection."""
    coordinator, shards = start_cluster(launcher)
    make_users_table(coordinator.url)
    conn = http.client.HTTPConnection(coordinator.url.split("://")[1], timeout=10)
    try:
        body = json.dumps({"id": "u1", "value": {"v": 1}})
        conn.request("PUT", "/tables/users/items", body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 201
        for shard in shards:  # restart both shards on the same ports, data stays
            shard.kill()
            launcher.shard(shard.args[2], coordinator.url, port=shard.port)
        conn.request("GET", "/tables/users/items/u1")
        resp = conn.getresponse()
        assert resp.status == 200, resp.read()
        assert json.loads(resp.read())["value"] == {"v": 1}
    finally:
        conn.close()


def test_new_url_keeps_distribution(launcher):
    """shard-1 comes back on another port (= new IP): only its address changes."""
    coordinator, shards = start_cluster(launcher)
    make_users_table(coordinator.url)
    write_users(coordinator.url, 50)
    before = {f"user{i}": holders(shards, "users", f"user{i}") for i in range(50)}

    shards[0].kill()
    moved = launcher.shard("shard-1", coordinator.url)  # same id and data dir, new port
    wait_for(lambda: moved.url in {s["url"] for s in call(coordinator.url, "GET", "/health")[1]["shards"]})

    assert ring_size(coordinator.url) == 2
    after = {f"user{i}": holders([moved, shards[1]], "users", f"user{i}") for i in range(50)}
    assert after == before
    for i in range(50):  # the coordinator reaches every key at the new address
        assert call(coordinator.url, "GET", f"/tables/users/items/user{i}")[0] == 200


def test_coordinator_restart_ring_back_by_heartbeat(launcher):
    coordinator, _ = start_cluster(launcher)
    make_users_table(coordinator.url)
    write_users(coordinator.url, 20)

    coordinator.kill()
    coordinator = launcher.coordinator("sharded", port=coordinator.port)

    took = wait_for(lambda: ring_size(coordinator.url) == 2, timeout=10)
    assert took <= 10
    assert call(coordinator.url, "GET", "/tables/users")[0] == 200  # tables from meta WAL
    for i in range(20):
        status, body, _ = call(coordinator.url, "GET", f"/tables/users/items/user{i}")
        assert (status, body["value"]) == (200, {"n": i})


def test_killed_shard_gives_503_not_404(launcher):
    coordinator, shards = start_cluster(launcher)
    make_users_table(coordinator.url)
    write_users(coordinator.url, 40)
    owner = {f"user{i}": holders(shards, "users", f"user{i}")[0] for i in range(40)}

    shards[1].kill()  # shard-2 is down, it stays on the ring (spec sharding)
    for key, shard_id in owner.items():
        answer = call(coordinator.url, "GET", f"/tables/users/items/{key}")
        if shard_id == "shard-2":
            assert_error(answer, 503, "shard_unavailable")
            status, _, raw = call(coordinator.url, "HEAD", f"/tables/users/items/{key}")
            assert (status, raw) == (503, b"")
            body = {"id": key, "value": {}}
            assert_error(call(coordinator.url, "PUT", "/tables/users/items", body),
                         503, "shard_unavailable")
            assert_error(call(coordinator.url, "DELETE", f"/tables/users/items/{key}"),
                         503, "shard_unavailable")
        else:
            assert answer[0] == 200
    assert ring_size(coordinator.url) == 2


def test_no_shards_yet_gives_503(launcher):
    coordinator = launcher.coordinator("sharded")
    make_users_table(coordinator.url)
    answer = call(coordinator.url, "PUT", "/tables/users/items", {"id": "1", "value": {}})
    assert_error(answer, 503, "shard_unavailable")


# --- shard internal API ----------------------------------------------------------------

def test_shard_internal_api(launcher):
    shard = launcher.shard("shard-1", "http://127.0.0.1:1")  # no coordinator: heartbeat just fails
    item = {"table": "orders", "pk": "42", "sk": "2026-11-05", "value": {"total": 300}}
    status, body, _ = call(shard.url, "PUT", "/internal/items", item)
    assert (status, body) == (201, item)
    assert_schema("InternalItem", body)

    query = "/internal/items?table=orders&pk=42&sk=2026-11-05"
    status, body, _ = call(shard.url, "GET", query)
    assert (status, body) == (200, item)
    assert call(shard.url, "HEAD", query)[0] == 200

    call(shard.url, "PUT", "/internal/items", dict(item, sk="2026-10-30"))
    status, body, _ = call(shard.url, "GET", "/internal/query?table=orders&pk=42&skTo=2026-11-01")
    assert status == 200
    assert [i["sk"] for i in body["items"]] == ["2026-10-30"]
    assert_schema("InternalItem", body["items"][0])

    assert call(shard.url, "DELETE", query)[0] == 204
    assert_error(call(shard.url, "DELETE", query), 404, "not_found")
    assert_error(call(shard.url, "GET", query), 404, "not_found")
    assert call(shard.url, "HEAD", query)[0] == 404

    no_sk = {"table": "users", "pk": "u1", "sk": None, "value": {}}
    assert call(shard.url, "PUT", "/internal/items", no_sk)[0] == 201
    assert call(shard.url, "GET", "/internal/items?table=users&pk=u1")[1] == no_sk

    assert_error(call(shard.url, "GET", "/internal/items?table=users"), 400, "bad_request")
    assert_error(call(shard.url, "PUT", "/internal/items", {"table": "t", "pk": "1"}),
                 400, "bad_request")
    assert "unavailable" in shard.log() or "failed" in shard.log()  # heartbeat errors are logged
    assert shard.popen.poll() is None                                 # ... and the shard keeps running


def test_shard_keeps_data_after_kill(launcher):
    shard = launcher.shard("shard-1", "http://127.0.0.1:1")
    item = {"table": "users", "pk": "u1", "sk": None, "value": {"a": 1}}
    assert call(shard.url, "PUT", "/internal/items", item)[0] == 201
    shard.kill()
    shard = launcher.shard("shard-1", "http://127.0.0.1:1", port=shard.port)
    assert call(shard.url, "GET", "/internal/items?table=users&pk=u1")[1] == item
