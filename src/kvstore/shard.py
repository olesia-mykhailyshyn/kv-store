"""Shard: stores the items the coordinator sends it (ADR-008, design decision 3).

The shard knows nothing about table definitions: the coordinator passes explicit
table / pk / sk (sk is null for tables without a sort key) to the internal API
(/internal/items, /internal/query in api/openapi.yaml). Items live in Storage
(RAM + WAL at data-dir/shard.wal), so a 201 means the item is fsynced to disk.

Registration (ADR-009): a background thread sends POST /shards/register {id, url} to the
coordinator right after start and then every --heartbeat-seconds (5 by default). A failed
heartbeat is logged and retried on the next tick; the shard keeps serving meanwhile.
The id is stable (from IaC); the url is this server's current address.

Run: python3 -m kvstore.shard --id shard-1 --data-dir /var/lib/kvstore \
       --coordinator-url http://10.0.1.10:8080 --advertise-url http://10.0.2.20:8080
"""
import argparse
import json
import logging
import os
import threading
import time
import urllib.request
from typing import List, Optional, Tuple

from kvstore.http_common import (HttpError, Request, Response, Router, is_key_value, is_name,
                                 make_server, setup_logging)
from kvstore.storage import Storage

log = logging.getLogger("kvstore.shard")

REGISTER_TIMEOUT_SECONDS = 2.0


class Shard:
    """Internal API handlers. Each takes a Request and returns (status, body)."""

    def __init__(self, shard_id: str, data_dir: str):
        self.id = shard_id
        self.storage = Storage(os.path.join(data_dir, "shard.wal"))

    def router(self) -> Router:
        router = Router()
        router.add("PUT", "/internal/items", self.put_item)
        router.add("GET", "/internal/items", self.get_item)
        router.add("HEAD", "/internal/items", self.item_exists)
        router.add("DELETE", "/internal/items", self.delete_item)
        router.add("GET", "/internal/query", self.query)
        router.add("GET", "/health", self.health)
        return router

    def put_item(self, req: Request) -> Response:
        body = req.json()
        if set(body) != {"table", "pk", "sk", "value"}:
            raise HttpError(400, "body must have exactly table, pk, sk, value")
        _check_key(body["table"], body["pk"], body["sk"])
        if not isinstance(body["value"], dict):
            raise HttpError(400, "value must be a JSON object")
        self.storage.put(body["table"], body["pk"], body["sk"], body["value"])
        return 201, body

    def get_item(self, req: Request) -> Response:
        table, pk, sk = _key_from_query(req)
        value = self.storage.get(table, pk, sk)
        if value is None:
            raise HttpError(404, "item not found")
        return 200, {"table": table, "pk": pk, "sk": sk, "value": value}

    def item_exists(self, req: Request) -> Response:
        table, pk, sk = _key_from_query(req)
        return (200 if self.storage.get(table, pk, sk) is not None else 404), None

    def delete_item(self, req: Request) -> Response:
        table, pk, sk = _key_from_query(req)
        if not self.storage.delete(table, pk, sk):
            raise HttpError(404, "item not found")
        return 204, None

    def query(self, req: Request) -> Response:
        table, pk = req.query.get("table"), req.query.get("pk")
        sk_from, sk_to = req.query.get("skFrom"), req.query.get("skTo")
        _check_key(table, pk, None)
        for bound in (sk_from, sk_to):
            if bound is not None and not is_key_value(bound):
                raise HttpError(400, "skFrom/skTo must be strings of 1..1024 characters")
        rows = self.storage.query(table, pk, sk_from, sk_to)
        return 200, {"items": [{"table": table, "pk": pk, "sk": sk, "value": value}
                               for sk, value in rows]}

    def health(self, req: Request) -> Response:
        return 200, {"status": "ok", "role": "shard", "id": self.id,
                     "items": self.storage.count()}


def _key_from_query(req: Request) -> Tuple[str, str, Optional[str]]:
    """table, pk, sk from ?table=&pk=&sk= (no sk -> None: a table without a sort key)."""
    table, pk, sk = req.query.get("table"), req.query.get("pk"), req.query.get("sk")
    _check_key(table, pk, sk)
    return table, pk, sk


def _check_key(table, pk, sk) -> None:
    if not is_name(table):
        raise HttpError(400, "table must match [A-Za-z0-9_-]{1,64}")
    if not is_key_value(pk):
        raise HttpError(400, "pk must be a string of 1..1024 characters")
    if sk is not None and not is_key_value(sk):
        raise HttpError(400, "sk must be null or a string of 1..1024 characters")


# --- heartbeat ---------------------------------------------------------------------------

def register(coordinator_url: str, shard_id: str, advertise_url: str) -> dict:
    """POST /shards/register once; returns the coordinator's answer."""
    data = json.dumps({"id": shard_id, "url": advertise_url}).encode("utf-8")
    request = urllib.request.Request(coordinator_url.rstrip("/") + "/shards/register",
                                     data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=REGISTER_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read())


def heartbeat_loop(coordinator_url: str, shard_id: str, advertise_url: str,
                   interval: float) -> None:
    """Register now and every `interval` seconds, forever. Never raises."""
    registered = False  # log successes only on a change, not every few seconds
    while True:
        try:
            result = register(coordinator_url, shard_id, advertise_url)
            if not registered or result.get("newShard"):
                log.info("registered at %s as %s (%d shards on the ring)",
                         coordinator_url, shard_id, result.get("shards", 0))
            registered = True
        except Exception as e:  # coordinator down, timeout, bad answer: try again next tick
            log.warning("heartbeat to %s failed: %s", coordinator_url, e)
            registered = False
        time.sleep(interval)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="kv-store shard")
    parser.add_argument("--id", required=True, help="stable shard id, e.g. shard-1")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--data-dir", default="/var/lib/kvstore")
    parser.add_argument("--coordinator-url", required=True, help="e.g. http://10.0.1.10:8080")
    parser.add_argument("--advertise-url", required=True,
                        help="this shard's address for the coordinator, e.g. http://10.0.2.20:8080")
    parser.add_argument("--heartbeat-seconds", type=float, default=5.0)
    args = parser.parse_args(argv)
    if not is_name(args.id):
        parser.error("--id must match [A-Za-z0-9_-]{1,64}")

    setup_logging()
    os.makedirs(args.data_dir, exist_ok=True)
    shard = Shard(args.id, args.data_dir)
    log.info("shard %s started: data-dir=%s items=%d", args.id, args.data_dir, shard.storage.count())

    # bind the port first, so the coordinator never gets an address that is not listening yet
    server = make_server(shard.router(), args.host, args.port)
    threading.Thread(target=heartbeat_loop, daemon=True,
                     args=(args.coordinator_url, args.id, args.advertise_url,
                           args.heartbeat_seconds)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
