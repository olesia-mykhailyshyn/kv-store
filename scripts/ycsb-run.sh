#!/usr/bin/env bash
# One YCSB baseline run (task 13.3, spec benchmark-baseline), on the client EC2 inside the VPC.
#   1. YCSB 0.17.0 (downloaded once) + our binding (bench/ycsb-binding, built once)
#   2. table usertable (partition key ycsb_key) on the coordinator; 409 = already there, fine
#   3. load phase (inserts) + run phase (the workload mix), parameters from bench/params.env
#   4. raw output + parameters + configuration + date -> docs/m2/ycsb/<date>-<workload>/
#      and the same folder to the results bucket (versioned)
# Usage: scripts/ycsb-run.sh <workload>      workload: a, c, workloada, ...
# Settings (environment, or /etc/kvstore/client.env if it exists):
#   KV_URL          coordinator, e.g. http://10.0.11.10:8080 (required)
#   RESULTS_BUCKET  results bucket name (export kv-artifacts-ResultsBucket); empty -> no upload
#   SKIP_LOAD=1     skip the load phase (data already loaded by a previous run)
set -euo pipefail

cd "$(dirname "$0")/.."

YCSB_VERSION="0.17.0"
# The small release with YCSB core + workloads (13 MB) instead of the full 708 MB one.
YCSB_TGZ_URL="https://github.com/brianfrankcooper/YCSB/releases/download/$YCSB_VERSION/ycsb-rest-binding-$YCSB_VERSION.tar.gz"
YCSB_TGZ_SHA256="4c087e2205ae6a871466d55c96cd03f9eb3c109bae53a9aa669be210efd7fab7"
YCSB_HOME="${YCSB_HOME:-$HOME/ycsb-rest-binding-$YCSB_VERSION}"
PARAMS="bench/params.env"
BINDING_JAR="bench/ycsb-binding/build/kvstore-binding.jar"

if [ -f /etc/kvstore/client.env ]; then
  # shellcheck disable=SC1091
  . /etc/kvstore/client.env
fi
KV_URL="${KV_URL:-${COORDINATOR_URL:-}}"
RESULTS_BUCKET="${RESULTS_BUCKET:-}"

# "a" -> "workloada"; "workloada" stays.
WORKLOAD="${1:?usage: scripts/ycsb-run.sh <workload: a|c|workloada|...>}"
case "$WORKLOAD" in
  workload*) ;;
  *) WORKLOAD="workload$WORKLOAD" ;;
esac

if [ -z "$KV_URL" ]; then
  echo "KV_URL is not set (coordinator URL, e.g. http://10.0.11.10:8080)" >&2
  exit 1
fi

# Value of "name=value" from the parameters file (same parsing as lab-login.sh).
prop() { grep -E "^$1=" "$PARAMS" | cut -d= -f2- | tr -d '\r'; }
TABLE="$(prop kv.table)"
PK="$(prop kv.pk)"

echo "== 1. Java, YCSB, binding"
if ! command -v javac > /dev/null; then
  # The client is Amazon Linux 2023 with internet access (public subnet).
  sudo dnf install -y -q java-17-amazon-corretto-devel
fi
if [ ! -f "$YCSB_HOME/lib/core-$YCSB_VERSION.jar" ]; then
  tgz="$(mktemp)"
  curl -fsSL -o "$tgz" "$YCSB_TGZ_URL"
  # Checksum: run exactly the YCSB we checked, not whatever the URL serves today.
  echo "$YCSB_TGZ_SHA256  $tgz" | sha256sum -c --quiet
  tar xzf "$tgz" -C "$(dirname "$YCSB_HOME")"
  rm -f "$tgz"
fi
if [ ! -f "$BINDING_JAR" ]; then
  bench/ycsb-binding/build.sh "$YCSB_HOME"
fi
WORKLOAD_FILE="$YCSB_HOME/workloads/$WORKLOAD"
if [ ! -f "$WORKLOAD_FILE" ]; then
  echo "No workload $WORKLOAD_FILE" >&2
  exit 1
fi

echo "== 2. Coordinator and table"
HEALTH="$(curl -fsS --max-time 5 "$KV_URL/health")"
echo "$HEALTH"
code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 -X POST \
  -H 'Content-Type: application/json' \
  -d "{\"name\": \"$TABLE\", \"partitionKey\": \"$PK\"}" "$KV_URL/tables")"
case "$code" in
  201) echo "table $TABLE created" ;;
  409) echo "table $TABLE already exists" ;;
  *) echo "create table $TABLE failed: HTTP $code" >&2; exit 1 ;;
esac

NAME="$(date -u +%Y-%m-%dT%H%M%SZ)-$WORKLOAD"
OUT="docs/m2/ycsb/$NAME"
mkdir -p "$OUT"
cp "$PARAMS" "$OUT/params.env"
cp "$WORKLOAD_FILE" "$OUT/$WORKLOAD"

# Client instance type from instance metadata (IMDSv2); "unknown" outside EC2.
imds_token="$(curl -fsS --max-time 2 -X PUT http://169.254.169.254/latest/api/token \
  -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null || true)"
client_type="$(curl -fsS --max-time 2 -H "X-aws-ec2-metadata-token: $imds_token" \
  http://169.254.169.254/latest/meta-data/instance-type 2>/dev/null || echo unknown)"
shard_count="$(echo "$HEALTH" | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("shards", [])))')"

# Configuration of the measured system, saved next to the numbers (spec benchmark-baseline).
cat > "$OUT/config.txt" <<CONFIG
date_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)
workload: $WORKLOAD
coordinator: $KV_URL
coordinator_health: $HEALTH
shards: $shard_count
vnodes_per_shard: 500 (ADR-006)
fsync_policy: fsync on every write before the response (ADR-008)
client_instance_type: $client_type
server_instance_types: ${SERVER_INSTANCE_TYPES:-see infra/app.yaml}
ycsb: $YCSB_VERSION, binding kvstore.ycsb.KvStoreClient (bench/ycsb-binding)
java: $(java -version 2>&1 | head -1)
git_commit: $(git rev-parse HEAD 2>/dev/null || echo unknown)
CONFIG

ycsb() {
  java -cp "$YCSB_HOME/lib/*:$BINDING_JAR" site.ycsb.Client "$@" \
    -db kvstore.ycsb.KvStoreClient -P "$WORKLOAD_FILE" -P "$PARAMS" -p "kv.url=$KV_URL" -s
}

# stdout = final measurements, stderr = progress every 10 s.
if [ "${SKIP_LOAD:-0}" = "1" ]; then
  echo "== 3. load skipped (SKIP_LOAD=1)"
else
  echo "== 3. load"
  ycsb -load > "$OUT/load.txt" 2> "$OUT/load.log"
fi
echo "== 4. run"
ycsb -t > "$OUT/run.txt" 2> "$OUT/run.log"

# Throughput, operation count, average/p95/p99 latency and return codes per operation type.
summary() {
  grep -E '^\[(OVERALL|READ|UPDATE|INSERT|READ-FAILED|UPDATE-FAILED|INSERT-FAILED)\], (RunTime|Throughput|Operations|AverageLatency|95thPercentile|99thPercentile|Return)' "$1" || true
}
{
  [ -f "$OUT/load.txt" ] && { echo "# load"; summary "$OUT/load.txt"; }
  echo "# run"
  summary "$OUT/run.txt"
} > "$OUT/summary.txt"

echo "== 5. results"
if [ -n "$RESULTS_BUCKET" ]; then
  aws s3 cp --recursive --only-show-errors "$OUT" "s3://$RESULTS_BUCKET/ycsb/$NAME/"
  echo "uploaded to s3://$RESULTS_BUCKET/ycsb/$NAME/"
else
  echo "RESULTS_BUCKET not set: results kept only in $OUT" >&2
fi
echo "saved in $OUT"
cat "$OUT/summary.txt"
