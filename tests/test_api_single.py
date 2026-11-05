"""Public API against a coordinator in single-node mode (spec kv-api, spec sharding "Single-node").

TestKvApi holds every kv-api scenario and only talks to the fixture "api" (a base URL).
test_api_sharded.py subclasses it with its own "api" fixture, so both modes are
checked by exactly the same scenarios. Every JSON answer is validated against api/openapi.yaml.
"""
import http.client
import itertools
from urllib.parse import quote

import pytest

from conftest import Launcher, assert_error, assert_schema, call

_names = itertools.count(1)


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    """One coordinator for all scenarios of this module; tests use their own table names."""
    launch = Launcher(str(tmp_path_factory.mktemp("single")))
    yield launch.coordinator("single").url
    launch.kill_all()


def put_order(api, table, user, date, value):
    return call(api, "PUT", f"/tables/{table}/items",
                {"userId": user, "orderDate": date, "value": value})


class TestKvApi:
    # fixtures inside the class, so test_api_sharded.py inherits them with the scenarios
    @pytest.fixture
    def orders(self, api):
        """A fresh table with a sort key: PK userId, SK orderDate."""
        name = f"orders{next(_names)}"
        status, _, _ = call(api, "POST", "/tables",
                            {"name": name, "partitionKey": "userId", "sortKey": "orderDate"})
        assert status == 201
        return name

    @pytest.fixture
    def users(self, api):
        """A fresh table without a sort key: PK userId."""
        name = f"users{next(_names)}"
        status, _, _ = call(api, "POST", "/tables", {"name": name, "partitionKey": "userId"})
        assert status == 201
        return name

    # --- tables -------------------------------------------------------------------------

    def test_create_table_with_sort_key(self, api):
        definition = {"name": f"t{next(_names)}", "partitionKey": "userId", "sortKey": "orderDate"}
        status, body, _ = call(api, "POST", "/tables", definition)
        assert status == 201
        assert_schema("TableDefinition", body)
        assert body == definition

        status, body, _ = call(api, "GET", f"/tables/{definition['name']}")
        assert status == 200
        assert_schema("TableDefinition", body)
        assert body == definition

    def test_create_table_without_sort_key(self, api):
        definition = {"name": f"t{next(_names)}", "partitionKey": "id"}
        status, body, _ = call(api, "POST", "/tables", definition)
        assert status == 201
        assert body == definition

    def test_table_already_exists(self, api, orders):
        answer = call(api, "POST", "/tables", {"name": orders, "partitionKey": "other"})
        assert_error(answer, 409, "conflict")
        _, body, _ = call(api, "GET", f"/tables/{orders}")
        assert body["partitionKey"] == "userId"  # definition did not change

    @pytest.mark.parametrize("name", ["bad#name", "with space", "", "x" * 65, "ключ"])
    def test_invalid_table_name(self, api, name):
        assert_error(call(api, "POST", "/tables", {"name": name, "partitionKey": "id"}),
                     400, "bad_request")

    @pytest.mark.parametrize("definition", [
        {"partitionKey": "id"},                                  # no name
        {"name": "t_missing_pk"},                                # no partition key
        {"name": "t_value_key", "partitionKey": "value"},        # "value" is reserved
        {"name": "t_same_keys", "partitionKey": "a", "sortKey": "a"},
        {"name": "t_extra", "partitionKey": "id", "color": "red"},
        {"name": "t_number", "partitionKey": 5},
        ["not", "an", "object"],
    ])
    def test_invalid_table_definition(self, api, definition):
        assert_error(call(api, "POST", "/tables", definition), 400, "bad_request")

    def test_describe_missing_table(self, api):
        assert_error(call(api, "GET", "/tables/nosuchtable"), 404, "not_found")

    # --- CREATE -------------------------------------------------------------------------

    def test_put_and_read_composite_key(self, api, orders):
        status, body, _ = put_order(api, orders, "42", "2026-11-05", {"total": 300})
        assert status == 201
        assert_schema("Item", body)
        assert body == {"key": {"userId": "42", "orderDate": "2026-11-05"}, "value": {"total": 300}}

        status, body, _ = call(api, "GET", f"/tables/{orders}/items/42/2026-11-05")
        assert status == 200
        assert_schema("Item", body)
        assert body["value"] == {"total": 300}

    def test_put_overwrites(self, api, orders):
        put_order(api, orders, "42", "2026-11-05", {"total": 300})
        put_order(api, orders, "42", "2026-11-05", {"total": 150})
        _, body, _ = call(api, "GET", f"/tables/{orders}/items/42/2026-11-05")
        assert body["value"] == {"total": 150}

    def test_put_into_missing_table(self, api):
        answer = call(api, "PUT", "/tables/nosuchtable/items", {"id": "1", "value": {}})
        assert_error(answer, 404, "not_found")

    @pytest.mark.parametrize("body", [
        {"orderDate": "2026-11-05", "value": {"a": 1}},              # no partition key
        {"userId": "42", "value": {"a": 1}},                         # no sort key
        {"userId": "42", "orderDate": "2026-11-05"},                 # no value
        {"userId": "42", "orderDate": "2026-11-05", "value": [1]},   # value not an object
        {"userId": 42, "orderDate": "2026-11-05", "value": {}},      # key not a string
        {"userId": "", "orderDate": "2026-11-05", "value": {}},      # empty key
        {"userId": "42", "orderDate": "2026-11-05", "value": {}, "extra": "x"},
    ])
    def test_put_bad_body_stores_nothing(self, api, orders, body):
        assert_error(call(api, "PUT", f"/tables/{orders}/items", body), 400, "bad_request")
        _, result, _ = call(api, "GET", f"/tables/{orders}/items/42")
        assert result == {"items": []}

    def test_body_not_json(self, api, orders):
        answer = call(api, "PUT", f"/tables/{orders}/items", raw=b"{not json")
        assert_error(answer, 400, "bad_request")

    def test_body_too_large(self, api, orders):
        big = {"userId": "1", "orderDate": "d", "value": {"blob": "x" * (64 * 1024)}}
        assert_error(call(api, "PUT", f"/tables/{orders}/items", big), 413, "payload_too_large")

    def test_key_longer_than_1024(self, api, users):
        body = {"userId": "k" * 1025, "value": {}}
        assert_error(call(api, "PUT", f"/tables/{users}/items", body), 400, "bad_request")

    # --- READ / EXISTS / DELETE ---------------------------------------------------------

    def test_read_missing_item(self, api, orders):
        assert_error(call(api, "GET", f"/tables/{orders}/items/42/2000-01-01"), 404, "not_found")

    def test_read_in_missing_table(self, api):
        assert_error(call(api, "GET", "/tables/nosuchtable/items/1"), 404, "not_found")

    def test_exists(self, api, orders):
        put_order(api, orders, "42", "2026-11-05", {"total": 300})
        status, _, raw = call(api, "HEAD", f"/tables/{orders}/items/42/2026-11-05")
        assert (status, raw) == (200, b"")
        status, _, raw = call(api, "HEAD", f"/tables/{orders}/items/42/2000-01-01")
        assert (status, raw) == (404, b"")
        status, _, raw = call(api, "HEAD", "/tables/nosuchtable/items/42/2000-01-01")
        assert (status, raw) == (404, b"")

    def test_delete(self, api, orders):
        put_order(api, orders, "42", "2026-11-05", {"total": 300})
        status, _, raw = call(api, "DELETE", f"/tables/{orders}/items/42/2026-11-05")
        assert (status, raw) == (204, b"")
        assert_error(call(api, "GET", f"/tables/{orders}/items/42/2026-11-05"), 404, "not_found")

    def test_delete_missing_item(self, api, orders):
        answer = call(api, "DELETE", f"/tables/{orders}/items/42/2000-01-01")
        assert_error(answer, 404, "not_found")

    def test_table_without_sort_key_crud(self, api, users):
        status, body, _ = call(api, "PUT", f"/tables/{users}/items",
                               {"userId": "u1", "value": {"name": "Olesia"}})
        assert status == 201
        assert body == {"key": {"userId": "u1"}, "value": {"name": "Olesia"}}

        status, body, _ = call(api, "GET", f"/tables/{users}/items/u1")
        assert status == 200
        assert_schema("Item", body)
        assert body == {"key": {"userId": "u1"}, "value": {"name": "Olesia"}}

        assert call(api, "HEAD", f"/tables/{users}/items/u1")[0] == 200
        assert call(api, "DELETE", f"/tables/{users}/items/u1")[0] == 204
        assert call(api, "HEAD", f"/tables/{users}/items/u1")[0] == 404
        assert_error(call(api, "GET", f"/tables/{users}/items/u1"), 404, "not_found")

    def test_url_encoded_keys(self, api, orders):
        user, date = "a/b c?#%", "2026-11-05T10:00/ü"
        assert put_order(api, orders, user, date, {"ok": True})[0] == 201
        path = f"/tables/{orders}/items/{quote(user, safe='')}/{quote(date, safe='')}"
        status, body, _ = call(api, "GET", path)
        assert status == 200
        assert body["key"] == {"userId": user, "orderDate": date}

    # --- path shape ---------------------------------------------------------------------

    def test_sort_key_table_needs_sk_in_path(self, api, orders):
        assert_error(call(api, "DELETE", f"/tables/{orders}/items/42"), 400, "bad_request")
        status, _, raw = call(api, "HEAD", f"/tables/{orders}/items/42")
        assert (status, raw) == (400, b"")

    def test_table_without_sort_key_rejects_sk_in_path(self, api, users):
        for method in ("GET", "DELETE"):
            assert_error(call(api, method, f"/tables/{users}/items/u1/extra"), 400, "bad_request")
        status, _, raw = call(api, "HEAD", f"/tables/{users}/items/u1/extra")
        assert (status, raw) == (400, b"")

    def test_unknown_route(self, api):
        assert_error(call(api, "GET", "/nothing/here"), 404, "not_found")

    # --- Query --------------------------------------------------------------------------

    def _four_orders(self, api, table):
        for date in ["2026-11-07", "2026-10-30", "2026-11-05", "2026-11-01"]:
            put_order(api, table, "42", date, {"date": date})
        put_order(api, table, "43", "2026-11-02", {"other": "user"})

    def test_query_range(self, api, orders):
        self._four_orders(api, orders)
        status, body, _ = call(api, "GET",
                               f"/tables/{orders}/items/42?skFrom=2026-11-01&skTo=2026-11-05")
        assert status == 200
        assert_schema("QueryResult", body)
        assert [i["key"]["orderDate"] for i in body["items"]] == ["2026-11-01", "2026-11-05"]
        assert body["items"][0] == {"key": {"userId": "42", "orderDate": "2026-11-01"},
                                    "value": {"date": "2026-11-01"}}

    def test_query_without_bounds(self, api, orders):
        self._four_orders(api, orders)
        status, body, _ = call(api, "GET", f"/tables/{orders}/items/42")
        assert status == 200
        assert_schema("QueryResult", body)
        assert [i["key"]["orderDate"] for i in body["items"]] == \
            ["2026-10-30", "2026-11-01", "2026-11-05", "2026-11-07"]

    def test_query_one_bound(self, api, orders):
        self._four_orders(api, orders)
        _, body, _ = call(api, "GET", f"/tables/{orders}/items/42?skFrom=2026-11-05")
        assert [i["key"]["orderDate"] for i in body["items"]] == ["2026-11-05", "2026-11-07"]

    def test_query_empty_pk(self, api, orders):
        status, body, _ = call(api, "GET", f"/tables/{orders}/items/nobody")
        assert status == 200
        assert body == {"items": []}

    # --- service ------------------------------------------------------------------------

    def test_health(self, api):
        status, body, _ = call(api, "GET", "/health")
        assert status == 200
        assert_schema("Health", body)
        assert body["role"] == "coordinator"

    def test_keep_alive(self, api, users):
        """HTTP/1.1: many requests over one TCP connection (YCSB relies on it)."""
        conn = http.client.HTTPConnection(api.split("://")[1], timeout=5)
        try:
            for i in range(3):
                conn.request("GET", f"/tables/{users}/items/u{i}")
                resp = conn.getresponse()
                resp.read()
                assert resp.status == 404
                assert resp.version == 11
        finally:
            conn.close()


# --- single-node only ------------------------------------------------------------------

def test_health_single_mode(api):
    _, body, _ = call(api, "GET", "/health")
    assert body["mode"] == "single"
    assert isinstance(body["items"], int)


def test_register_shard_rejected_in_single_mode(api):
    answer = call(api, "POST", "/shards/register", {"id": "shard-1", "url": "http://127.0.0.1:1"})
    assert_error(answer, 400, "bad_request")


def test_restart_keeps_tables_and_items(launcher):
    """Spec kv-api: table definitions survive a coordinator restart (kill -9, same data dir)."""
    coordinator = launcher.coordinator("single")
    definition = {"name": "orders", "partitionKey": "userId", "sortKey": "orderDate"}
    assert call(coordinator.url, "POST", "/tables", definition)[0] == 201
    put_order(coordinator.url, "orders", "42", "2026-11-05", {"total": 300})

    coordinator.kill()
    coordinator = launcher.coordinator("single", port=coordinator.port)

    status, body, _ = call(coordinator.url, "GET", "/tables/orders")
    assert (status, body) == (200, definition)
    _, body, _ = call(coordinator.url, "GET", "/tables/orders/items/42/2026-11-05")
    assert body["value"] == {"total": 300}
    assert call(coordinator.url, "POST", "/tables", definition)[0] == 409


# --- HTTP robustness (code review findings) ----------------------------------------------

def raw_request(api: str, request: bytes) -> bytes:
    """Send raw bytes, return what the server answers before it closes (5 s at most)."""
    import socket
    host, port = api.split("://")[1].split(":")
    with socket.create_connection((host, int(port)), timeout=5) as s:
        s.sendall(request)
        chunks = []
        try:
            while True:
                data = s.recv(65536)
                if not data:
                    break
                chunks.append(data)
        except socket.timeout:
            pass
    return b"".join(chunks)


def test_negative_content_length_is_400_not_a_hang(api):
    answer = raw_request(api, b"PUT /tables/x/items HTTP/1.1\r\nHost: t\r\nContent-Length: -1\r\n\r\n")
    assert answer.startswith(b"HTTP/1.1 400")
    assert b'"bad_request"' in answer


def test_chunked_body_is_400_and_connection_closed(api):
    answer = raw_request(api, b"PUT /tables/x/items HTTP/1.1\r\nHost: t\r\n"
                              b"Transfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n")
    assert answer.startswith(b"HTTP/1.1 400")
    assert b"Connection: close" in answer
    assert answer.count(b"HTTP/1.1") == 1  # the chunk bytes were not read as a second request


def test_unknown_method_gets_json_error(api):
    status, body, _ = call(api, "PATCH", "/tables")
    assert status == 501
    assert_schema("Error", body)


def test_invalid_utf8_in_path_is_400(api):
    assert call(api, "POST", "/tables", {"name": "utf8check", "partitionKey": "id"})[0] == 201
    assert_error(call(api, "GET", "/tables/utf8check/items/%FF"), 400, "bad_request")
