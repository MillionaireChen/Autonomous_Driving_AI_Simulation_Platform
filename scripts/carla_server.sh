#!/usr/bin/env bash
#
# Start / stop / inspect the CARLA server.
#
# This host is headless (no DISPLAY) and shares four GPUs with other people's
# jobs, so the server always runs off-screen and always pinned to one card
# (spec section 4). It must never be allowed to see all four.
#
# Usage:
#   ./scripts/carla_server.sh start
#   ./scripts/carla_server.sh status
#   ./scripts/carla_server.sh stop

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -f .env ]]; then
    set -a; source .env; set +a
fi

CARLA_VERSION="${CARLA_VERSION:-0.9.16}"
CARLA_ROOT="${CARLA_ROOT:-$HOME/carla/CARLA_${CARLA_VERSION}}"
CARLA_HOST="${CARLA_HOST:-127.0.0.1}"
CARLA_RPC_PORT="${CARLA_RPC_PORT:-2000}"
CARLA_GPU="${CARLA_GPU:-0}"
CARLA_QUALITY="${CARLA_QUALITY:-Epic}"

LOG_DIR="$REPO_ROOT/logs"
PID_FILE="$LOG_DIR/carla_server.pid"
LOG_FILE="$LOG_DIR/carla_server.log"
STARTUP_TIMEOUT="${CARLA_STARTUP_TIMEOUT:-180}"

log() { printf '[carla-server] %s\n' "$*"; }
die() { printf '[carla-server] ERROR: %s\n' "$*" >&2; exit 1; }

port_open() {
    (exec 3<>"/dev/tcp/${CARLA_HOST}/${CARLA_RPC_PORT}") 2>/dev/null && exec 3<&- && return 0
    return 1
}

server_pid() {
    [[ -f "$PID_FILE" ]] || return 1
    local pid; pid="$(cat "$PID_FILE")"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && { echo "$pid"; return 0; }
    return 1
}

cmd_start() {
    if server_pid >/dev/null; then
        log "already running (pid $(server_pid))"; exit 0
    fi
    if port_open; then
        die "port ${CARLA_RPC_PORT} is already in use by another process"
    fi
    [[ -x "$CARLA_ROOT/CarlaUE4.sh" ]] \
        || die "CARLA not found at $CARLA_ROOT - run ./scripts/install_carla.sh"

    mkdir -p "$LOG_DIR"
    log "starting CARLA ${CARLA_VERSION} on GPU ${CARLA_GPU}, RPC port ${CARLA_RPC_PORT}"

    # -RenderOffScreen  : render without a display server
    # -graphicsadapter  : pick the Vulkan device (UE4-level GPU selection)
    # CUDA_VISIBLE_DEVICES pins any CUDA-side work to the same card
    (
        cd "$CARLA_ROOT"
        CUDA_VISIBLE_DEVICES="$CARLA_GPU" \
        SDL_VIDEODRIVER=offscreen \
        nohup ./CarlaUE4.sh \
            -RenderOffScreen \
            -nosound \
            -carla-rpc-port="$CARLA_RPC_PORT" \
            -quality-level="$CARLA_QUALITY" \
            -graphicsadapter="$CARLA_GPU" \
            >"$LOG_FILE" 2>&1 &
        echo $! > "$PID_FILE"
    )

    log "waiting up to ${STARTUP_TIMEOUT}s for the RPC port (first boot off NFS is slow)"
    for ((i = 0; i < STARTUP_TIMEOUT; i++)); do
        if port_open; then
            log "server is up (pid $(cat "$PID_FILE")) after ${i}s"
            log "log: $LOG_FILE"
            exit 0
        fi
        if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            log "server process died during startup; last log lines:"
            tail -30 "$LOG_FILE" >&2
            die "startup failed"
        fi
        sleep 1
    done
    die "port ${CARLA_RPC_PORT} did not open within ${STARTUP_TIMEOUT}s (see $LOG_FILE)"
}

cmd_stop() {
    local pid
    if pid="$(server_pid)"; then
        log "stopping pid $pid"
        # CarlaUE4.sh is a wrapper; kill the whole process group.
        pkill -TERM -P "$pid" 2>/dev/null || true
        kill -TERM "$pid" 2>/dev/null || true
        for ((i = 0; i < 20; i++)); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 1
        done
        kill -0 "$pid" 2>/dev/null && { log "still alive, sending KILL"; kill -KILL "$pid" 2>/dev/null || true; }
    else
        log "no tracked process"
    fi
    # The shipping binary can outlive the wrapper.
    pkill -f 'CarlaUE4-Linux-Shipping' 2>/dev/null && log "killed stray CarlaUE4-Linux-Shipping" || true
    rm -f "$PID_FILE"
    log "stopped"
}

cmd_status() {
    local pid running=no
    if pid="$(server_pid)"; then running="yes (pid $pid)"; fi
    printf 'process : %s\n' "$running"
    printf 'rpc     : %s:%s %s\n' "$CARLA_HOST" "$CARLA_RPC_PORT" \
        "$(port_open && echo open || echo closed)"
    printf 'gpu     : %s\n' "$CARLA_GPU"
    printf 'root    : %s\n' "$CARLA_ROOT"
    printf 'log     : %s\n' "$LOG_FILE"
    if command -v nvidia-smi >/dev/null 2>&1; then
        printf 'gpu use :\n'
        nvidia-smi --query-compute-apps=gpu_bus_id,pid,process_name,used_memory \
            --format=csv,noheader 2>/dev/null | sed 's/^/          /' || true
    fi
}

case "${1:-}" in
    start)  cmd_start ;;
    stop)   cmd_stop ;;
    status) cmd_status ;;
    *)      echo "usage: $0 {start|stop|status}" >&2; exit 2 ;;
esac
