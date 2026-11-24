#!/usr/bin/env bash
# Download and unpack the latest version of the data archive from Zenodo, then check its integrity.
# Usage: bash scripts/fetch_data.sh [DEST]
set -euo pipefail
DEST=$(mkdir -p "${1:-.}" && cd "${1:-.}" && pwd)
CONCEPT=23998871
NAME=agent-concurrency-vldb-data.tar.gz
URL=$(curl -sfL "https://zenodo.org/api/records?q=conceptrecid:$CONCEPT&sort=mostrecent&size=1" | python3 -c '
import json, sys
hit = json.load(sys.stdin)["hits"]["hits"][0]
print(next(f["links"]["self"] for f in hit["files"] if f["key"] == sys.argv[1]))' "$NAME")
echo "Downloading $URL"
curl -fL -o "$DEST/$NAME" "$URL"
tar -xzf "$DEST/$NAME" -C "$DEST"
(cd "$DEST/agent-concurrency-vldb-data" && shasum -a 256 -c SHA256SUMS --quiet)
echo "Data in $DEST/agent-concurrency-vldb-data (checksums match)"
