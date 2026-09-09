#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# AssetAtlas - Single entry point for the local environment.
#
# Modes:
#   dev   (default) — Flask --debug (auto-reload), Vite dev server (HMR)
#   prod            — gunicorn API, built React + node express server
#
# Usage:
#   ./scripts/start.sh                 # start in dev mode (default)
#   ./scripts/start.sh prod            # start in prod-like mode
#   ./scripts/start.sh stop            # stop all services
#   ./scripts/start.sh restart [mode]  # restart (preserves mode unless overridden)
#   ./scripts/start.sh status          # show service status

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

PID_DIR="$PROJECT_ROOT/tmp"
MODE_FILE="$PID_DIR/mode"
REDIS_PID="$PID_DIR/redis.pid"
API_PID="$PID_DIR/api.pid"
SCHEDULER_PID="$PID_DIR/scheduler.pid"
MCP_PID="$PID_DIR/mcp.pid"
UI_PID="$PID_DIR/ui.pid"

mkdir -p "$PID_DIR"
cd "$PROJECT_ROOT"

# ---------------------------------------------------------------------------
# Argument parsing — first positional is the action, second (optional) is mode
# ---------------------------------------------------------------------------

ACTION="${1:-start}"
case "$ACTION" in
    stop|status|restart|start|dev|prod) ;;
    *)
        echo -e "${RED}Unknown command: $ACTION${NC}"
        echo "Usage: $0 [start|prod|stop|restart [dev|prod]|status]"
        exit 1
        ;;
esac

# Allow `start dev` / `start prod` shorthand
if [ "$ACTION" == "dev" ] || [ "$ACTION" == "prod" ]; then
    REQUESTED_MODE="$ACTION"
    ACTION="start"
else
    REQUESTED_MODE="${2:-}"
fi

resolve_mode() {
    if [ -n "$REQUESTED_MODE" ]; then
        echo "$REQUESTED_MODE"
    elif [ -f "$MODE_FILE" ]; then
        cat "$MODE_FILE"
    else
        echo "dev"
    fi
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

is_running() {
    local pid_file="$1"
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        rm -f "$pid_file"
    fi
    return 1
}

stop_service() {
    local name="$1"
    local pid_file="$2"
    if is_running "$pid_file"; then
        local pid
        pid=$(cat "$pid_file")
        echo -e "${YELLOW}Stopping $name (PID $pid)...${NC}"
        # Kill child processes first (e.g. vite spawned by node, flask reloader child)
        pkill -P "$pid" 2>/dev/null || true
        kill "$pid" 2>/dev/null || true
        for i in $(seq 1 10); do
            if ! kill -0 "$pid" 2>/dev/null; then
                break
            fi
            sleep 0.5
        done
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null || true
            pkill -9 -P "$pid" 2>/dev/null || true
        fi
        rm -f "$pid_file"
        echo -e "${GREEN}✓${NC} $name stopped"
    else
        echo -e "${YELLOW}$name is not running${NC}"
    fi
}

stop_all() {
    stop_service "UI" "$UI_PID"
    stop_service "MCP" "$MCP_PID"
    stop_service "Scheduler" "$SCHEDULER_PID"
    stop_service "API" "$API_PID"
    stop_service "Redis" "$REDIS_PID"
}

show_status() {
    local mode="unknown"
    [ -f "$MODE_FILE" ] && mode="$(cat "$MODE_FILE")"

    echo -e "${BLUE}"
    echo "============================================================"
    echo "            AssetAtlas - Service Status (mode: $mode)"
    echo "============================================================"
    echo -e "${NC}"

    if is_running "$REDIS_PID"; then
        echo -e "  Redis:     ${GREEN}running${NC} (PID $(cat "$REDIS_PID"))"
    elif redis-cli ping 2>/dev/null | grep -q PONG; then
        echo -e "  Redis:     ${GREEN}running${NC} (external)"
    else
        echo -e "  Redis:     ${RED}stopped${NC}"
    fi

    if is_running "$API_PID"; then
        echo -e "  API:       ${GREEN}running${NC} (PID $(cat "$API_PID")) → http://localhost:5001/api"
    else
        echo -e "  API:       ${RED}stopped${NC}"
    fi

    if is_running "$SCHEDULER_PID"; then
        echo -e "  Scheduler: ${GREEN}running${NC} (PID $(cat "$SCHEDULER_PID"))"
    else
        echo -e "  Scheduler: ${RED}stopped${NC}"
    fi

    if is_running "$MCP_PID"; then
        echo -e "  MCP:       ${GREEN}running${NC} (PID $(cat "$MCP_PID")) → http://localhost:${MCP_PORT:-8100}/mcp/"
    else
        echo -e "  MCP:       ${RED}stopped${NC}"
    fi

    if is_running "$UI_PID"; then
        echo -e "  UI:        ${GREEN}running${NC} (PID $(cat "$UI_PID")) → http://localhost:3000"
    else
        echo -e "  UI:        ${RED}stopped${NC}"
    fi

    echo ""
}

# ---------------------------------------------------------------------------
# Dispatch stop/status/restart
# ---------------------------------------------------------------------------

if [ "$ACTION" == "stop" ]; then
    echo -e "${BLUE}"
    echo "============================================================"
    echo "         AssetAtlas - Stopping All Services"
    echo "============================================================"
    echo -e "${NC}"
    stop_all
    rm -f "$MODE_FILE"
    echo ""
    echo -e "${GREEN}All services stopped.${NC}"
    exit 0
fi

if [ "$ACTION" == "status" ]; then
    show_status
    exit 0
fi

MODE="$(resolve_mode)"
if [ "$MODE" != "dev" ] && [ "$MODE" != "prod" ]; then
    echo -e "${RED}Invalid mode: $MODE (expected: dev|prod)${NC}"
    exit 1
fi

if [ "$ACTION" == "restart" ]; then
    echo -e "${BLUE}"
    echo "============================================================"
    echo "      AssetAtlas - Restarting All Services (mode: $MODE)"
    echo "============================================================"
    echo -e "${NC}"
    stop_all
    echo ""
    echo -e "${YELLOW}Starting services...${NC}"
    echo ""
fi

# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

echo -e "${BLUE}"
echo "============================================================"
echo "      AssetAtlas - Starting All Services (mode: $MODE)"
echo "============================================================"
echo -e "${NC}"

if [ ! -d "venv" ]; then
    echo -e "${RED}Error: Virtual environment not found${NC}"
    echo "Create it: python3 -m venv venv && ./venv/bin/pip install -r requirements-prod.txt -r requirements-test.txt"
    exit 1
fi

if [ -f ".env" ]; then
    echo -e "${GREEN}✓${NC} Loading environment variables from .env"
    # set -a + source preserves quoted values, escaped chars, and empty
    # assignments — `xargs` was mangling values that contained spaces or
    # equals signs. The set -a / set +a pair scopes auto-export to this block.
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
else
    echo -e "${YELLOW}⚠${NC} Warning: .env file not found"
fi

export PYTHONPATH="$PROJECT_ROOT"

if [ -z "$DATABASE_URL" ]; then
    echo -e "${RED}Error: DATABASE_URL not set${NC}"
    exit 1
fi

# Persist mode for status/restart
echo "$MODE" > "$MODE_FILE"

# --- Redis ---
if redis-cli ping 2>/dev/null | grep -q PONG; then
    echo -e "${GREEN}✓${NC} Redis already running"
elif is_running "$REDIS_PID"; then
    echo -e "${GREEN}✓${NC} Redis already running (PID $(cat "$REDIS_PID"))"
else
    if command -v redis-server &>/dev/null; then
        echo -e "${BLUE}Starting Redis...${NC}"
        redis-server --daemonize yes --pidfile "$REDIS_PID" \
            --appendonly yes --maxmemory 256mb --maxmemory-policy allkeys-lru \
            --loglevel warning
        echo -e "${GREEN}✓${NC} Redis started"
    else
        echo -e "${YELLOW}⚠${NC} redis-server not found — API will fall back to in-memory cache"
    fi
fi

# --- API Server ---
if is_running "$API_PID"; then
    echo -e "${GREEN}✓${NC} API already running (PID $(cat "$API_PID"))"
else
    export SCHEDULER_MODE=disabled
    if [ "$MODE" == "prod" ]; then
        echo -e "${BLUE}Starting API server (gunicorn)...${NC}"
        if ! ./venv/bin/python -c "import gunicorn" 2>/dev/null; then
            echo "Installing gunicorn..."
            ./venv/bin/pip install gunicorn -q
        fi
        nohup ./venv/bin/gunicorn -c gunicorn.conf.py -w 4 -b 0.0.0.0:5001 \
            --timeout 300 --access-logfile - --error-logfile - --log-level info \
            "src.api.factory:create_app()" \
            > "$PID_DIR/api.log" 2>&1 &
        echo $! > "$API_PID"
    else
        echo -e "${BLUE}Starting API server (Flask --debug, auto-reload)...${NC}"
        # --debug enables both the reloader and the Werkzeug debugger.
        # Reloader fork is safe here (verified 2026-04-25) — the old grpc/abseil
        # mutex deadlock no longer reproduces with the current dependency set.
        nohup ./venv/bin/python -m flask --app src.api.factory:create_app run \
            --host 0.0.0.0 --port 5001 --debug \
            > "$PID_DIR/api.log" 2>&1 &
        echo $! > "$API_PID"
    fi
    echo -e "${GREEN}✓${NC} API started (PID $(cat "$API_PID")) → http://localhost:5001/api"
fi

# --- Scheduler Worker ---
if is_running "$SCHEDULER_PID"; then
    echo -e "${GREEN}✓${NC} Scheduler already running (PID $(cat "$SCHEDULER_PID"))"
else
    echo -e "${BLUE}Starting scheduler worker...${NC}"
    export SCHEDULER_MODE=worker
    nohup ./venv/bin/python src/scheduler_worker.py \
        > "$PID_DIR/scheduler.log" 2>&1 &
    echo $! > "$SCHEDULER_PID"
    echo -e "${GREEN}✓${NC} Scheduler started (PID $(cat "$SCHEDULER_PID"))"
fi

# --- MCP Server (HTTP mode) ---
if is_running "$MCP_PID"; then
    echo -e "${GREEN}✓${NC} MCP already running (PID $(cat "$MCP_PID"))"
else
    echo -e "${BLUE}Starting MCP server...${NC}"
    nohup ./venv/bin/python -m src.mcp.server --http --port "${MCP_PORT:-8100}" \
        > "$PID_DIR/mcp.log" 2>&1 &
    echo $! > "$MCP_PID"
    echo -e "${GREEN}✓${NC} MCP started (PID $(cat "$MCP_PID")) → http://localhost:${MCP_PORT:-8100}/mcp/"
fi

# --- UI ---
if is_running "$UI_PID"; then
    echo -e "${GREEN}✓${NC} UI already running (PID $(cat "$UI_PID"))"
else
    if ! command -v node &> /dev/null; then
        echo -e "${RED}Error: Node.js is not installed${NC}"
        exit 1
    fi

    if [ ! -d "ui/prod/node_modules" ]; then
        echo "Installing UI dependencies..."
        ( cd ui/prod && npm install )
    fi

    if [ "$MODE" == "prod" ]; then
        # Rebuild only when source changed (avoids serving a stale bundle after edits).
        needs_build=0
        if [ ! -f "ui/prod/dist/index.html" ]; then
            needs_build=1
        elif [ -n "$(find ui/prod/src ui/prod/index.html ui/prod/package.json \
                       ui/prod/vite.config.ts ui/prod/tsconfig.json \
                       -type f -newer ui/prod/dist/index.html 2>/dev/null | head -n 1)" ]; then
            needs_build=1
        fi
        if [ "$needs_build" -eq 1 ]; then
            echo -e "${BLUE}Building React frontend...${NC}"
            ( cd ui/prod && npm run build )
            echo -e "${GREEN}✓${NC} React build complete"
        fi

        echo -e "${BLUE}Starting UI (express, prebuilt bundle)...${NC}"
        nohup node ui/prod/server.js > "$PID_DIR/ui.log" 2>&1 &
        echo $! > "$UI_PID"
    else
        echo -e "${BLUE}Starting UI (Vite dev server, HMR)...${NC}"
        # Subshell so cwd change doesn't leak; the inner $! is the vite PID.
        ( cd ui/prod && nohup ./node_modules/.bin/vite > "$PID_DIR/ui.log" 2>&1 & echo $! > "$UI_PID" )
    fi
    echo -e "${GREEN}✓${NC} UI started (PID $(cat "$UI_PID")) → http://localhost:3000"
fi

echo ""
echo -e "${GREEN}All services started (mode: $MODE).${NC}"
echo ""
echo -e "  API:       http://localhost:5001/api"
echo -e "  Swagger:   http://localhost:5001/api/docs"
echo -e "  MCP:       http://localhost:${MCP_PORT:-8100}/mcp/"
echo -e "  UI:        http://localhost:3000"
echo -e "  Logs:      $PID_DIR/{api,scheduler,mcp,ui}.log"
echo ""
echo -e "  ${BLUE}./scripts/start.sh status${NC}        — check status"
echo -e "  ${BLUE}./scripts/start.sh stop${NC}          — stop all services"
echo -e "  ${BLUE}./scripts/start.sh restart${NC}       — restart (keeps mode)"
echo -e "  ${BLUE}./scripts/start.sh restart prod${NC}  — restart in prod mode"
