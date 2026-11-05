"""Coordinator: the single entry point of the store (ADR-008, ADR-009, design decision 2).

It implements the public API of api/openapi.yaml and keeps table definitions in its own
WAL (data-dir/meta.wal), so tables survive a restart. Items go to one of two backends:

- single mode:  LocalItems   - the coordinator stores items itself (data-dir/items.wal);
- sharded mode: ShardedItems - the ring picks the shard by "{table}#{pk}" (ADR-006/007) and
  the request goes to that shard's internal API. Shards join by POST /shards/register
  (heartbeat every 5 s); the ring itself is never written to disk: after a restart the
  heartbeats rebuild it within one heartbeat interval.

A shard that does not answer (connection error or 2 s timeout) means 503 for its keys -
never 404 and never another shard (spec kv-api). Silent shards stay on the ring in M2.

Run: python3 -m kvstore.coordinator --mode sharded --port 8080 --data-dir /var/lib/kvstore
"""
import argparse
import http.client
import json
import logging
import os
import threading
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlsplit

from kvstore.http_common import (HttpError, Request, Response, Router, is_key_value, is_name,
                                 make_server, setup_logging)
from kvstore.ring import Ring, ring_key
from kvstore.storage import Storage

log = logging.getLogger("kvstore.coordinator")

SHARD_TIMEOUT_SECONDS = 2.0
# All table definitions live under one (table, pk) of the meta WAL, sk = table name,
# so Storage.query() lists them all on start.
META_TABLE, META_PK = "tables", "all"


class Tables:
    """Table definitions: RAM + meta WAL, through the same Storage class as the shards."""

    def __init__(self, path: str):
        self._storage = Storage(path)
        self._lock = threading.Lock()  # "check, then create" must be one step

    def create(self, definition: dict) -> bool:
        """False if a table with this name already exists (then nothing changes)."""
        with self._lock:
            if self.get(definition["name"]) is not None:
                return False
            self._storage.put(META_TABLE, META_PK, definition["name"], definition)
            return True

    def get(self, name: str) -> Optional[dict]:
        return self._storage.get(META_TABLE, META_PK, name)

    def count(self) -> int:
        return len(self._storage.query(META_TABLE, META_PK))


class LocalItems:
    """Single-node mode: items in the coordinator's own Storage."""

    def __init__(self, path: str):
        self._storage = Storage(path)

    def put(self, table: str, pk: str, sk: Optional[str], value: dict) -> None:
        self._storage.put(table, pk, sk, value)

    def get(self, table: str, pk: str, sk: Optional[str]) -> Optional[dict]:
        return self._storage.get(table, pk, sk)

    def exists(self, table: str, pk: str, sk: Optional[str]) -> bool:
        return self._storage.get(table, pk, sk) is not None

    def delete(self, table: str, pk: str, sk: Optional[str]) -> bool:
        return self._storage.delete(table, pk, sk)

    def query(self, table: str, pk: str, sk_from: Optional[str],
              sk_to: Optional[str]) -> List[Tuple[str, dict]]:
        return self._storage.query(table, pk, sk_from, sk_to)

    def health(self) -> dict:
        return {"items": self._storage.count()}


class ShardedItems:
    """Sharded mode: the ring picks the shard, the request goes to its internal API."""

    def __init__(self):
        # one lock for the ring and the address table, so they never disagree
        self._lock = threading.Lock()
        self._ring = Ring()
        self._urls: Dict[str, str] = {}         # shard id -> base URL
        self._last_seen: Dict[str, float] = {}  # shard id -> time.monotonic() of last heartbeat
        # every server thread keeps its own keep-alive connection per shard
        # (http.client connections must not be shared between threads)
        self._local = threading.local()
        # shard id -> time.monotonic() of the last "unavailable" warning (one per 10 s)
        self._last_warned: Dict[str, float] = {}

    # --- registration ---------------------------------------------------------------

    def register(self, shard_id: str, url: str) -> Tuple[bool, int]:
        """(True if the id is new, number of shards). A known id only gets its URL updated:
        ring positions depend on the id alone (ADR-009)."""
        with self._lock:
            old_url = self._urls.get(shard_id)
            new = self._ring.add(shard_id)
            self._urls[shard_id] = url
            self._last_seen[shard_id] = time.monotonic()
            count = len(self._ring)
        if new:
            log.info("shard %s joined the ring at %s (%d shards)", shard_id, url, count)
        elif old_url != url:
            log.info("shard %s moved %s -> %s, ring unchanged", shard_id, old_url, url)
        return new, count

    def health(self) -> dict:
        now = time.monotonic()
        with self._lock:
            shards = [{"id": shard_id, "url": self._urls[shard_id],
                       "lastSeenSeconds": round(now - self._last_seen[shard_id], 1)}
                      for shard_id in self._ring.shard_ids()]
        return {"shards": shards}

    # --- items: same methods as LocalItems --------------------------------------------

    def put(self, table: str, pk: str, sk: Optional[str], value: dict) -> None:
        body = {"table": table, "pk": pk, "sk": sk, "value": value}
        status, _ = self._call(table, pk, "PUT", "/internal/items", body)
        _expect(status, 201)

    def get(self, table: str, pk: str, sk: Optional[str]) -> Optional[dict]:
        status, body = self._call(table, pk, "GET", "/internal/items?" + _key_query(table, pk, sk))
        if status == 404:
            return None
        _expect(status, 200)
        return body["value"]

    def exists(self, table: str, pk: str, sk: Optional[str]) -> bool:
        status, _ = self._call(table, pk, "HEAD", "/internal/items?" + _key_query(table, pk, sk))
        if status == 404:
            return False
        _expect(status, 200)
        return True

    def delete(self, table: str, pk: str, sk: Optional[str]) -> bool:
        status, _ = self._call(table, pk, "DELETE", "/internal/items?" + _key_query(table, pk, sk))
        if status == 404:
            return False
        _expect(status, 204)
        return True

    def query(self, table: str, pk: str, sk_from: Optional[str],
              sk_to: Optional[str]) -> List[Tuple[str, dict]]:
        params = {"table": table, "pk": pk}
        if sk_from is not None:
            params["skFrom"] = sk_from
        if sk_to is not None:
            params["skTo"] = sk_to
        status, body = self._call(table, pk, "GET", "/internal/query?" + urlencode(params))
        _expect(status, 200)
        return [(item["sk"], item["value"]) for item in body["items"]]

    # --- talking to a shard -----------------------------------------------------------

    def _owner(self, table: str, pk: str) -> Tuple[str, str]:
        """(shard id, URL) that owns "{table}#{pk}"."""
        with self._lock:
            if len(self._ring) == 0:
                raise HttpError(503, "no shards registered yet")
            shard_id = self._ring.owner(ring_key(table, pk))
            return shard_id, self._urls[shard_id]

    def _call(self, table: str, pk: str, method: str, path: str,
              body: Optional[dict] = None) -> Tuple[int, Optional[dict]]:
        shard_id, url = self._owner(table, pk)
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        try:
            return self._send(url, method, path, data)
        except (OSError, http.client.HTTPException) as e:  # refused, reset, 2 s timeout, ...
            self._drop_connection(url)
            self._warn_unavailable(shard_id, url, f"{method} {path}: {e!r}")
            raise HttpError(503, f"{shard_id} unavailable")

    def _warn_unavailable(self, shard_id: str, url: str, detail: str) -> None:
        """At most one warning per shard per 10 s: a dead shard under YCSB would otherwise
        write thousands of log lines per second."""
        now = time.monotonic()
        with self._lock:
            if now - self._last_warned.get(shard_id, -1e9) < 10:
                return
            self._last_warned[shard_id] = now
        log.warning("shard %s (%s) unavailable: %s", shard_id, url, detail)

    def _send(self, url: str, method: str, path: str,
              data: Optional[bytes]) -> Tuple[int, Optional[dict]]:
        conn, reused = self._connection(url)
        try:
            return _exchange(conn, method, path, data)
        except ConnectionError:
            if not reused:
                raise
        # A kept-alive connection can be stale: the shard restarted since our last request.
        # The request never reached the new process, so one retry on a new connection is safe.
        self._drop_connection(url)
        conn, _ = self._connection(url)
        return _exchange(conn, method, path, data)

    def _connection(self, url: str) -> Tuple[http.client.HTTPConnection, bool]:
        """(this thread's connection to url, True if it was used before)."""
        if not hasattr(self._local, "connections"):
            self._local.connections = {}
        connections = self._local.connections
        if url in connections:
            return connections[url], True
        parts = urlsplit(url)
        conn = http.client.HTTPConnection(parts.hostname, parts.port or 80,
                                          timeout=SHARD_TIMEOUT_SECONDS)
        connections[url] = conn
        return conn, False

    def _drop_connection(self, url: str) -> None:
        conn = getattr(self._local, "connections", {}).pop(url, None)
        if conn is not None:
            conn.close()


def _exchange(conn: http.client.HTTPConnection, method: str, path: str,
              data: Optional[bytes]) -> Tuple[int, Optional[dict]]:
    """One request/response on an open connection -> (status, JSON body or None)."""
    headers = {"Content-Type": "application/json"} if data is not None else {}
    conn.request(method, path, body=data, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()  # read fully, otherwise the connection cannot be reused
    return resp.status, (json.loads(raw) if raw else None)


def _key_query(table: str, pk: str, sk: Optional[str]) -> str:
    params = {"table": table, "pk": pk}
    if sk is not None:
        params["sk"] = sk
    return urlencode(params)


def _expect(status: int, expected: int) -> None:
    """The shard answered something the protocol does not allow: a bug, not a client error."""
    if status != expected:
        raise HttpError(500, f"shard answered {status}, expected {expected}")


class Coordinator:
    """Public API handlers. Each takes a Request and returns (status, body)."""

    def __init__(self, mode: str, data_dir: str):
        self.mode = mode
        self.tables = Tables(os.path.join(data_dir, "meta.wal"))
        if mode == "single":
            self.items = LocalItems(os.path.join(data_dir, "items.wal"))
        else:
            self.items = ShardedItems()

    def router(self) -> Router:
        router = Router()
        router.add("POST", "/tables", self.create_table)
        router.add("GET", "/tables/{table}", self.describe_table)
        router.add("PUT", "/tables/{table}/items", self.put_item)
        # the same handlers serve both address shapes; they check the shape against the table
        for pattern in ("/tables/{table}/items/{pk}", "/tables/{table}/items/{pk}/{sk}"):
            router.add("GET", pattern, self.get_item)
            router.add("HEAD", pattern, self.item_exists)
            router.add("DELETE", pattern, self.delete_item)
        router.add("POST", "/shards/register", self.register_shard)
        router.add("GET", "/health", self.health)
        return router

    # --- tables -----------------------------------------------------------------------

    def create_table(self, req: Request) -> Response:
        body = req.json()
        unknown = set(body) - {"name", "partitionKey", "sortKey"}
        if unknown:
            raise HttpError(400, f"unknown fields: {', '.join(sorted(unknown))}")
        if not is_name(body.get("name")):
            raise HttpError(400, "name must match [A-Za-z0-9_-]{1,64}")
        for field in ("partitionKey", "sortKey"):
            if field == "partitionKey" or field in body:
                if not is_name(body.get(field)) or body[field] == "value":
                    raise HttpError(400, f"{field} must match [A-Za-z0-9_-]{{1,64}} "
                                         f"and must not be 'value'")
        if body.get("sortKey") == body["partitionKey"]:
            raise HttpError(400, "sortKey must differ from partitionKey")

        definition = {k: body[k] for k in ("name", "partitionKey", "sortKey") if k in body}
        if not self.tables.create(definition):
            raise HttpError(409, f"table {definition['name']} already exists")
        log.info("table created: %s", definition)
        return 201, definition

    def describe_table(self, req: Request) -> Response:
        return 200, self._table(req.params["table"])

    def _table(self, name: str) -> dict:
        definition = self.tables.get(name)
        if definition is None:
            raise HttpError(404, f"table {name} not found")
        return definition

    # --- items ------------------------------------------------------------------------

    def put_item(self, req: Request) -> Response:
        definition = self._table(req.params["table"])
        body = req.json()
        key_names = _key_names(definition)
        for name in key_names:
            if name not in body:
                raise HttpError(400, f"missing key attribute {name}")
            if not is_key_value(body[name]):
                raise HttpError(400, f"{name} must be a string of 1..1024 characters")
        unknown = set(body) - set(key_names) - {"value"}
        if unknown:
            raise HttpError(400, f"unknown attributes: {', '.join(sorted(unknown))}")
        if not isinstance(body.get("value"), dict):
            raise HttpError(400, "value must be a JSON object")

        pk = body[key_names[0]]
        sk = body[key_names[1]] if len(key_names) == 2 else None
        self.items.put(definition["name"], pk, sk, body["value"])
        return 201, _item(definition, pk, sk, body["value"])

    def get_item(self, req: Request) -> Response:
        definition = self._table(req.params["table"])
        if "sortKey" in definition and "sk" not in req.params:
            return self._query(definition, req)  # GET /items/{pk} on a sort-key table
        pk, sk = _key_from_path(definition, req.params)
        value = self.items.get(definition["name"], pk, sk)
        if value is None:
            raise HttpError(404, "item not found")
        return 200, _item(definition, pk, sk, value)

    def item_exists(self, req: Request) -> Response:
        definition = self._table(req.params["table"])
        pk, sk = _key_from_path(definition, req.params)
        found = self.items.exists(definition["name"], pk, sk)
        return (200 if found else 404), None

    def delete_item(self, req: Request) -> Response:
        definition = self._table(req.params["table"])
        pk, sk = _key_from_path(definition, req.params)
        if not self.items.delete(definition["name"], pk, sk):
            raise HttpError(404, "item not found")
        return 204, None

    def _query(self, definition: dict, req: Request) -> Response:
        pk = req.params["pk"]
        sk_from, sk_to = req.query.get("skFrom"), req.query.get("skTo")
        for value in (pk, sk_from, sk_to):
            if value is not None and not is_key_value(value):
                raise HttpError(400, "keys must be strings of 1..1024 characters")
        rows = self.items.query(definition["name"], pk, sk_from, sk_to)
        return 200, {"items": [_item(definition, pk, sk, value) for sk, value in rows]}

    # --- service ----------------------------------------------------------------------

    def register_shard(self, req: Request) -> Response:
        if self.mode != "sharded":
            raise HttpError(400, "coordinator runs in single mode, it has no shards")
        body = req.json()
        if set(body) != {"id", "url"}:
            raise HttpError(400, "body must have exactly the fields id and url")
        if not is_name(body["id"]):
            raise HttpError(400, "id must match [A-Za-z0-9_-]{1,64}")
        if not _is_http_url(body["url"]):
            raise HttpError(400, "url must look like http://host:port")
        # A shard registers only its own address: the host in url must be the IP the request
        # comes from. Otherwise any client that reaches port 8080 could add a fake shard or
        # send shard-1's traffic elsewhere, e.g. to 169.254.169.254 (ADR-009).
        if urlsplit(body["url"]).hostname != req.client_ip:
            raise HttpError(400, f"url host must be the sender's address {req.client_ip}")
        new, count = self.items.register(body["id"], body["url"].rstrip("/"))
        return 200, {"id": body["id"], "newShard": new, "shards": count}

    def health(self, req: Request) -> Response:
        body = {"status": "ok", "role": "coordinator", "mode": self.mode}
        body.update(self.items.health())
        return 200, body


def _key_names(definition: dict) -> List[str]:
    """[partition key name] or [partition key name, sort key name]."""
    names = [definition["partitionKey"]]
    if "sortKey" in definition:
        names.append(definition["sortKey"])
    return names


def _key_from_path(definition: dict, params: Dict[str, str]) -> Tuple[str, Optional[str]]:
    """(pk, sk) from the address; the shape must match the table (openapi: else 400)."""
    has_sort_key = "sortKey" in definition
    if has_sort_key and "sk" not in params:
        raise HttpError(400, f"table {definition['name']} has a sort key: use /items/{{pk}}/{{sk}}")
    if not has_sort_key and "sk" in params:
        raise HttpError(400, f"table {definition['name']} has no sort key: use /items/{{pk}}")
    pk, sk = params["pk"], params.get("sk")
    if not is_key_value(pk) or (sk is not None and not is_key_value(sk)):
        raise HttpError(400, "keys must be strings of 1..1024 characters")
    return pk, sk


def _item(definition: dict, pk: str, sk: Optional[str], value: dict) -> dict:
    """openapi Item: {"key": {<pk name>: pk, <sk name>: sk}, "value": {...}}."""
    key = {definition["partitionKey"]: pk}
    if sk is not None:
        key[definition["sortKey"]] = sk
    return {"key": key, "value": value}


def _is_http_url(url) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
        parts.port  # raises ValueError on a bad port
    except ValueError:
        return False
    return parts.scheme == "http" and bool(parts.hostname)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="kv-store coordinator")
    parser.add_argument("--mode", choices=["single", "sharded"], required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--data-dir", default="/var/lib/kvstore")
    args = parser.parse_args(argv)

    setup_logging()
    os.makedirs(args.data_dir, exist_ok=True)
    coordinator = Coordinator(args.mode, args.data_dir)
    log.info("coordinator started: mode=%s data-dir=%s tables=%d",
             args.mode, args.data_dir, coordinator.tables.count())
    make_server(coordinator.router(), args.host, args.port).serve_forever()


if __name__ == "__main__":
    main()
