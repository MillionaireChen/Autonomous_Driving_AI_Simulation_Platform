#!/usr/bin/env bash
#
# Download and unpack the CARLA server package.
#
# The packaged release ships a precompiled UE4 runtime, so there is no Unreal
# Engine to install and no source build to run. It is ~8 GB compressed and
# ~18 GB unpacked, which is why it is installed OUTSIDE the repository at
# $CARLA_ROOT and never enters git.
#
# Safe to re-run: an existing install is left alone and a partial download
# resumes where it stopped.
#
# Usage:
#   ./scripts/install_carla.sh                 # install, then delete the archive
#   ./scripts/install_carla.sh --keep-archive  # keep the .tar.gz after unpacking

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -f .env ]]; then
    set -a; source .env; set +a
fi

CARLA_VERSION="${CARLA_VERSION:-0.9.16}"
CARLA_ROOT="${CARLA_ROOT:-$HOME/carla/CARLA_${CARLA_VERSION}}"

KEEP_ARCHIVE=0
[[ "${1:-}" == "--keep-archive" ]] && KEEP_ARCHIVE=1

# Official redirector for the Linux package of this release.
URL="https://tiny.carla.org/carla-${CARLA_VERSION//./-}-linux"
ARCHIVE_DIR="$(dirname "$CARLA_ROOT")"
ARCHIVE="${ARCHIVE_DIR}/CARLA_${CARLA_VERSION}.tar.gz"

log() { printf '[install-carla] %s\n' "$*"; }
die() { printf '[install-carla] ERROR: %s\n' "$*" >&2; exit 1; }

# --- already installed? -------------------------------------------------
if [[ -x "$CARLA_ROOT/CarlaUE4.sh" ]]; then
    log "already installed at $CARLA_ROOT"
    exit 0
fi

mkdir -p "$ARCHIVE_DIR"

# --- disk space check ---------------------------------------------------
# Unpacking needs roughly 18 GB on top of whatever the archive occupies.
avail_kb=$(df -Pk "$ARCHIVE_DIR" | awk 'NR==2 {print $4}')
avail_gb=$(( avail_kb / 1024 / 1024 ))
if (( avail_gb < 20 )); then
    die "only ${avail_gb} GB free at ${ARCHIVE_DIR}; need about 20 GB to unpack"
fi
log "target: $CARLA_ROOT (${avail_gb} GB free)"

# --- download (resumable) ----------------------------------------------
expected_size=$(curl -sIL "$URL" | awk 'BEGIN{IGNORECASE=1} /^content-length:/ {v=$2} END{gsub(/\r/,"",v); print v}')
[[ -n "$expected_size" ]] || die "could not determine download size; is the network up?"

current_size=0
[[ -f "$ARCHIVE" ]] && current_size=$(stat -c%s "$ARCHIVE")

if (( current_size != expected_size )); then
    log "downloading ${expected_size} bytes (resuming from ${current_size})"
    curl -L -C - --retry 5 --retry-delay 10 --progress-bar -o "$ARCHIVE" "$URL"
    current_size=$(stat -c%s "$ARCHIVE")
    (( current_size == expected_size )) \
        || die "size mismatch after download: got ${current_size}, expected ${expected_size}"
fi
log "archive complete: $(numfmt --to=iec "$current_size")"

# --- unpack -------------------------------------------------------------
# The tarball expands loose (CarlaUE4.sh, CarlaUE4/, PythonAPI/ ...) rather
# than into a versioned directory, so give it one explicitly.
log "unpacking into $CARLA_ROOT (several minutes on NFS)"
mkdir -p "$CARLA_ROOT"
tar -xzf "$ARCHIVE" -C "$CARLA_ROOT"

[[ -f "$CARLA_ROOT/CarlaUE4.sh" ]] || die "CarlaUE4.sh missing after unpack"
chmod +x "$CARLA_ROOT/CarlaUE4.sh"

# Town04 carries the highway used by the first scenario; it ships in the base
# package, so AdditionalMaps is not needed.
if ! ls "$CARLA_ROOT"/CarlaUE4/Content/Carla/Maps/Town04* >/dev/null 2>&1; then
    log "WARNING: Town04 not found in the package"
fi

if (( KEEP_ARCHIVE == 0 )); then
    log "removing archive to reclaim $(numfmt --to=iec "$current_size")"
    rm -f "$ARCHIVE"
fi

log "installed CARLA ${CARLA_VERSION}: $(du -sh "$CARLA_ROOT" | cut -f1)"
log "next: ./scripts/carla_server.sh start"
