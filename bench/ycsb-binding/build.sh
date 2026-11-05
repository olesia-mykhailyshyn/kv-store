#!/usr/bin/env bash
# Compiles the kv-store YCSB binding into build/kvstore-binding.jar.
# Needs a JDK 11+ (java.net.http) and the YCSB core jar from the YCSB release.
# Usage: bench/ycsb-binding/build.sh <YCSB_HOME>   (e.g. ~/ycsb-0.17.0)
set -euo pipefail

YCSB_HOME="${1:?usage: build.sh <YCSB_HOME>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
CORE_JAR="$YCSB_HOME/lib/core-0.17.0.jar"

if [ ! -f "$CORE_JAR" ]; then
  echo "No $CORE_JAR (download the YCSB 0.17.0 release first)" >&2
  exit 1
fi

rm -rf "$HERE/build/classes"
mkdir -p "$HERE/build/classes"
# --release 11: the bytecode runs on any Java 11+ (Corretto 17 on the client).
javac --release 11 -cp "$CORE_JAR" -d "$HERE/build/classes" \
  "$HERE/src/kvstore/ycsb/KvStoreClient.java"
jar cf "$HERE/build/kvstore-binding.jar" -C "$HERE/build/classes" .
echo "built $HERE/build/kvstore-binding.jar"
