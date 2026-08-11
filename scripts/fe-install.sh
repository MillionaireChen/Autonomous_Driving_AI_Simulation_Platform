#!/usr/bin/env bash
#
# Install frontend dependencies onto local disk, then link them into the repo.
#
# Why this is not just `npm install`:
#
#   node_modules is hundreds of megabytes of small files. The repo lives on
#   NFS, where opening each one costs milliseconds, so a dev server reading
#   node_modules from there is painfully slow.
#
#   The obvious fix - symlink frontend/node_modules to local disk - does not
#   survive: `npm install` deletes the symlink and writes a real directory in
#   its place, silently putting everything back on NFS.
#
#   So npm never runs inside the repo. It runs in a staging directory on local
#   disk holding only package.json, and the resulting node_modules is linked
#   into the repo afterwards.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -f .env ]]; then
    set -a; source .env; set +a
fi

NODE_HOME="${ARENA_NODE_HOME:-/var/tmp/fls/adarena/node}"
STAGE="${ARENA_FE_STAGE:-/var/tmp/fls/adarena/fe-stage}"
export PATH="$NODE_HOME/bin:$PATH"

command -v node >/dev/null || { echo "node not found at $NODE_HOME/bin" >&2; exit 1; }
echo "[fe-install] node $(node --version), npm $(npm --version)"

mkdir -p "$STAGE"
cp frontend/package.json "$STAGE/package.json"
[[ -f frontend/package-lock.json ]] && cp frontend/package-lock.json "$STAGE/" || true

echo "[fe-install] installing in $STAGE (local disk)"
( cd "$STAGE" && npm install --no-audit --no-fund )

# Link, do not copy: the point is to keep the files off NFS.
rm -rf frontend/node_modules
ln -s "$STAGE/node_modules" frontend/node_modules

# Bring the refreshed lockfile back so it can be committed.
[[ -f "$STAGE/package-lock.json" ]] && cp "$STAGE/package-lock.json" frontend/

echo "[fe-install] done: frontend/node_modules -> $STAGE/node_modules"
