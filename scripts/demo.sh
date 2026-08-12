#!/usr/bin/env bash
#
# Bring the whole platform up for a demo, and print where to watch it.
#
#   ./scripts/demo.sh          # start everything
#   ./scripts/demo.sh stop     # stop everything and free the GPU
#
# Binds the API and dashboard on all interfaces so the demo can be watched from
# another machine. On a shared host, stop it when you are done.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
[[ -f .env ]] && { set -a; source .env; set +a; }

VENV="${UV_PROJECT_ENVIRONMENT:-/var/tmp/fls/adarena/venv}"
PY="$VENV/bin/python"
NODE_HOME="${ARENA_NODE_HOME:-/var/tmp/fls/adarena/node}"
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
LOG_DIR="$REPO_ROOT/logs"; mkdir -p "$LOG_DIR"

up() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

stop_all() {
    ./scripts/carla_server.sh stop 2000 >/dev/null 2>&1
    ./scripts/carla_server.sh stop 2010 >/dev/null 2>&1
    for pattern in "uvicorn backend.main" "service.py --port" "next-server"; do
        for pid in $(pgrep -f "$pattern" 2>/dev/null); do kill "$pid" 2>/dev/null; done
    done
    sleep 3
    "$VENV/lib/python3.11/site-packages/pgserver/pginstall/bin/pg_ctl" \
        -D "${ARENA_PGDATA:-/var/tmp/fls/adarena/pgdata}" -m fast stop >/dev/null 2>&1
    echo "stopped; GPU 0 free"
}

[[ "${1:-}" == "stop" ]] && { stop_all; exit 0; }

echo "== CARLA =="
./scripts/carla_server.sh start 2000 0 2>&1 | tail -1
./scripts/carla_server.sh start 2010 0 2>&1 | tail -1

echo "== model services =="
for spec in "dummy:51001:models/dummy/service.py" \
            "pid:51002:models/pid/service.py" \
            "cnn_il:51003:models/il/service.py"; do
    name="${spec%%:*}"; rest="${spec#*:}"; port="${rest%%:*}"; script="${rest#*:}"
    if up "$port"; then echo "  $name already on $port"; continue; fi
    if [[ "$name" == "cnn_il" && ! -f models/il/checkpoints/cnn_il.pt ]]; then
        echo "  cnn_il skipped: no checkpoint (run models/il/train.py)"; continue
    fi
    setsid nohup "$PY" "$script" --port "$port" \
        > "$LOG_DIR/${name}_service.log" 2>&1 < /dev/null &
    echo "  $name -> :$port"
done

echo "== backend =="
if up 8000; then echo "  already on 8000"; else
    ARENA_CORS_ORIGINS="http://${HOST_IP}:3000" PYTHONUNBUFFERED=1 \
    setsid nohup "$PY" -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 \
        > "$LOG_DIR/api.log" 2>&1 < /dev/null &
    for _ in $(seq 1 40); do up 8000 && break; sleep 1; done
    echo "  api -> :8000"
fi

echo "== dashboard =="
if up 3000; then echo "  already on 3000"; else
    export PATH="$NODE_HOME/bin:$PATH"
    # .next is a symlink to local disk, made by scripts/fe-install.sh.
    [[ -L frontend/.next ]] || ./scripts/fe-install.sh >/dev/null 2>&1
    ( cd frontend && NEXT_PUBLIC_API_BASE="http://${HOST_IP}:8000" \
        npm run build > "$LOG_DIR/fe-build.log" 2>&1 )
    ( cd frontend && setsid nohup npm start -- --hostname 0.0.0.0 \
        > "$LOG_DIR/frontend.log" 2>&1 < /dev/null & )
    for _ in $(seq 1 40); do up 3000 && break; sleep 1; done
    echo "  dashboard -> :3000"
fi

echo
echo "  Dashboard   http://${HOST_IP}:3000"
echo "  Arena       http://${HOST_IP}:3000/arena"
echo "  API docs    http://${HOST_IP}:8000/docs"
echo
echo "  stop with:  ./scripts/demo.sh stop"
