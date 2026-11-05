# YCSB binding for kv-store (task 13.2)

## Why not YCSB's built-in `rest` binding

Checked in the YCSB 0.17.0 source (`rest/src/main/java/site/ycsb/webservice/rest/RestClient.java`):

| | `rest` binding | our API (`api/openapi.yaml`) |
|---|---|---|
| insert | `POST {url.prefix}{key}` | `PUT /tables/{table}/items` |
| update | `PUT {url.prefix}{key}` | `PUT /tables/{table}/items` |
| body | only the field `data`, raw text, `Content-Type` form/xml | JSON `{"ycsb_key": "<key>", "value": {...}}` |
| table name | ignored | part of the path |

Insert uses the wrong method and address, and the body is not our JSON, so every write
would be `400`/`405`. The binding is not configurable enough to fix this, hence our own.

## What this binding does

`src/kvstore/ycsb/KvStoreClient.java` (one class, `site.ycsb.DB` subclass, `java.net.http`):

| YCSB | HTTP | OK when |
|---|---|---|
| `insert`, `update` | `PUT /tables/{kv.table}/items`, body `{"<kv.pk>": key, "value": {"field0": "...", ...}}` | `201` |
| `read` | `GET /tables/{kv.table}/items/{key}` | `200` (`404` → `NOT_FOUND`) |
| `delete` | `DELETE /tables/{kv.table}/items/{key}` | `204` |
| `scan` | — | `NOT_IMPLEMENTED` (workloads A and C do not scan) |

`503` from the coordinator → `SERVICE_UNAVAILABLE`; anything else → `ERROR` (printed to stderr).

Properties: `kv.url` (required, coordinator), `kv.table` (default `usertable`),
`kv.pk` (default `ycsb_key`), `kv.timeout.ms` (default `10000`).

Limits, on purpose:
- **Update = full replace.** Our `PUT` replaces the whole item, while YCSB's update means
  "change these fields". With `writeallfields=true` (in `bench/params.env`) YCSB sends all
  fields, so replace gives the same result.
- **Read does not parse the body.** It is read in full (so latency is honest) but not
  turned into fields: YCSB uses them only for `dataintegrity=true`, which we do not run.
- One `HttpClient` shared by all YCSB threads: thread-safe, keeps connections alive
  (the coordinator speaks HTTP/1.1 keep-alive, design decision 5). HTTP/1.1 is forced.

## Build

On the client EC2 (`scripts/ycsb-run.sh` does this by itself):

```bash
bench/ycsb-binding/build.sh ~/ycsb-rest-binding-0.17.0   # -> bench/ycsb-binding/build/kvstore-binding.jar
```

Needs JDK 11+ (`java-17-amazon-corretto-devel`) and `lib/core-0.17.0.jar` from a YCSB 0.17.0
release. The script uses the small `ycsb-rest-binding-0.17.0.tar.gz` release (13 MB, contains the
YCSB core, workloads and libraries) instead of the full 708 MB `ycsb-0.17.0.tar.gz`.

## Run by hand

```bash
java -cp "$HOME/ycsb-rest-binding-0.17.0/lib/*:bench/ycsb-binding/build/kvstore-binding.jar" \
  site.ycsb.Client -load -db kvstore.ycsb.KvStoreClient \
  -P ~/ycsb-rest-binding-0.17.0/workloads/workloada -P bench/params.env \
  -p kv.url=http://<coordinator-ip>:8080 -p recordcount=1000 -s
```

`-load` = load phase (inserts), `-t` = run phase (the workload mix).
