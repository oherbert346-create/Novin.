#!/bin/bash

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

STATE_DIR=".deploy_state"
BACKUP_HOOK="./scripts/pre_deploy_backup.sh"
PREV_IMAGE_ID_FILE="$STATE_DIR/previous_backend_image_id"
PREV_IMAGE_NAME_FILE="$STATE_DIR/previous_backend_image_name"
NO_CACHE="${NOVIN_DEPLOY_NO_CACHE:-0}"

ensure_state_dir() {
    mkdir -p "$STATE_DIR"
}

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo -e "${RED}❌ Missing required command: $1${NC}"
        exit 1
    fi
}

on_error() {
    echo -e "${RED}❌ Deployment command failed${NC}"
    docker compose ps || true
    docker compose logs --tail=120 backend || true
}

trap on_error ERR

check_env() {
    echo -e "${YELLOW}Checking environment...${NC}"
    if [ ! -f ".env" ]; then
        echo -e "${RED}❌ .env file is missing${NC}"
        exit 1
    fi
    if [ -z "${INGEST_API_KEY:-}" ] && [ -z "${LOCAL_API_CREDENTIAL:-}" ] && ! grep -Eq '^(INGEST_API_KEY|LOCAL_API_CREDENTIAL)=.+' .env; then
        echo -e "${RED}❌ INGEST_API_KEY or LOCAL_API_CREDENTIAL not set${NC}"
        echo "Set INGEST_API_KEY or LOCAL_API_CREDENTIAL in .env or export it in your shell."
        exit 1
    fi
    if [ -z "${BASIC_AUTH_USER:-}" ] && [ -z "${BASIC_AUTH_PASS:-}" ] && ! grep -Eq '^BASIC_AUTH_USER=.+' .env; then
        echo -e "${YELLOW}⚠ BASIC_AUTH_USER/BASIC_AUTH_PASS not set - Basic Auth will be disabled${NC}"
        echo "Generate credentials with: python3 scripts/generate_api_key.py --basic-auth"
    fi
    local vision_provider="${VISION_PROVIDER:-$(grep -E '^VISION_PROVIDER=' .env | tail -1 | cut -d= -f2-)}"
    local reasoning_provider="${REASONING_PROVIDER:-$(grep -E '^REASONING_PROVIDER=' .env | tail -1 | cut -d= -f2-)}"
    vision_provider="${vision_provider:-siliconflow}"
    reasoning_provider="${reasoning_provider:-groq}"

    case "$vision_provider" in
        siliconflow)
            if [ -z "${SILICONFLOW_API_KEY:-}" ] && ! grep -Eq '^SILICONFLOW_API_KEY=.+' .env; then
                echo -e "${RED}❌ SILICONFLOW_API_KEY not set for VISION_PROVIDER=siliconflow${NC}"
                exit 1
            fi
            ;;
        together)
            if [ -z "${TOGETHER_API_KEY:-}" ] && ! grep -Eq '^TOGETHER_API_KEY=.+' .env; then
                echo -e "${RED}❌ TOGETHER_API_KEY not set for VISION_PROVIDER=together${NC}"
                exit 1
            fi
            ;;
        groq)
            if [ -z "${GROQ_API_KEY:-}" ] && ! grep -Eq '^GROQ_API_KEY=.+' .env; then
                echo -e "${RED}❌ GROQ_API_KEY not set for VISION_PROVIDER=groq${NC}"
                exit 1
            fi
            ;;
    esac

    case "$reasoning_provider" in
        cerebras)
            if [ -z "${CEREBRAS_API_KEY:-}" ] && ! grep -Eq '^CEREBRAS_API_KEY=.+' .env; then
                echo -e "${RED}❌ CEREBRAS_API_KEY not set for REASONING_PROVIDER=cerebras${NC}"
                exit 1
            fi
            ;;
        siliconflow)
            if [ -z "${SILICONFLOW_API_KEY:-}" ] && ! grep -Eq '^SILICONFLOW_API_KEY=.+' .env; then
                echo -e "${RED}❌ SILICONFLOW_API_KEY not set for REASONING_PROVIDER=siliconflow${NC}"
                exit 1
            fi
            ;;
        together)
            if [ -z "${TOGETHER_API_KEY:-}" ] && ! grep -Eq '^TOGETHER_API_KEY=.+' .env; then
                echo -e "${RED}❌ TOGETHER_API_KEY not set for REASONING_PROVIDER=together${NC}"
                exit 1
            fi
            ;;
        groq)
            if [ -z "${GROQ_API_KEY:-}" ] && ! grep -Eq '^GROQ_API_KEY=.+' .env; then
                echo -e "${RED}❌ GROQ_API_KEY not set for REASONING_PROVIDER=groq${NC}"
                exit 1
            fi
            ;;
    esac
    echo -e "${GREEN}✓ Environment OK${NC}"
}

check_preflight() {
    echo -e "${YELLOW}Running deployment preflight...${NC}"
    require_cmd docker
    require_cmd curl
    require_cmd python3
    if [ ! -f "docker-compose.yml" ]; then
        echo -e "${RED}❌ docker-compose.yml not found${NC}"
        exit 1
    fi
    docker compose config -q
    echo -e "${GREEN}✓ Preflight OK${NC}"
}

resolve_backend_image_name() {
    docker compose config --images | awk 'NR==1 {print; exit}'
}

backup_current_backend_image() {
    ensure_state_dir
    local image_name
    image_name="$(resolve_backend_image_name)"
    if [ -z "$image_name" ]; then
        echo -e "${YELLOW}⚠️ Unable to resolve backend image name; skipping image backup${NC}"
        return 0
    fi
    if docker image inspect "$image_name" >/dev/null 2>&1; then
        local image_id
        image_id="$(docker image inspect --format '{{.Id}}' "$image_name")"
        printf '%s\n' "$image_id" > "$PREV_IMAGE_ID_FILE"
        printf '%s\n' "$image_name" > "$PREV_IMAGE_NAME_FILE"
        echo -e "${GREEN}✓ Saved previous backend image reference${NC}"
        return 0
    fi
    echo -e "${YELLOW}⚠️ No existing backend image found; skipping image backup${NC}"
}

run_backup_hook() {
    if [ -x "$BACKUP_HOOK" ]; then
        echo -e "${YELLOW}Running pre-deploy backup hook...${NC}"
        "$BACKUP_HOOK"
        echo -e "${GREEN}✓ Backup hook completed${NC}"
        return 0
    fi
    echo -e "${YELLOW}⚠️ No executable backup hook at $BACKUP_HOOK${NC}"
}

build() {
    echo -e "${YELLOW}Building containers...${NC}"
    if [ "$NO_CACHE" = "1" ]; then
        docker compose build --no-cache backend
    else
        docker compose build backend
    fi
    echo -e "${GREEN}✓ Build complete${NC}"
}

start() {
    echo -e "${YELLOW}Starting services...${NC}"
    docker compose up -d
    echo -e "${GREEN}✓ Services started${NC}"
}

wait_health() {
    echo -e "${YELLOW}Waiting for readiness...${NC}"
    local max_attempts=30
    local attempt=0
    while [ $attempt -lt $max_attempts ]; do
        if curl -fsS --max-time 3 http://localhost:8000/health/ready >/dev/null 2>&1; then
            echo -e "${GREEN}✓ Service is ready${NC}"
            return 0
        fi
        attempt=$((attempt + 1))
        echo -n "."
        sleep 2
    done
    echo ""
    echo -e "${RED}❌ Service failed readiness checks${NC}"
    docker compose logs --tail=120 backend
    exit 1
}

test() {
    echo -e "${YELLOW}Running smoke tests...${NC}"
    local status_json
    local health_json
    status_json="$(curl -fsS --max-time 5 http://localhost:8000/api/status)"
    health_json="$(curl -fsS --max-time 5 http://localhost:8000/health/ready)"
    python3 - "$status_json" "$health_json" <<'PY'
import json
import sys

status_payload = json.loads(sys.argv[1])
health_payload = json.loads(sys.argv[2])

required_status_keys = {
    "active_streams",
    "ws_connections",
    "vision_model",
    "reasoning_provider",
    "reasoning_model",
    "readiness",
}
missing = sorted(required_status_keys - set(status_payload.keys()))
if missing:
    raise SystemExit(f"Status payload missing keys: {missing}")

if health_payload.get("status") != "ok":
    raise SystemExit(f"Readiness failed: {health_payload}")

checks = health_payload.get("checks", {})
if not checks or not all(checks.values()):
    raise SystemExit(f"Readiness checks not all healthy: {checks}")
PY
    echo -e "${GREEN}✓ Smoke tests passed${NC}"
}

show_status() {
    echo ""
    echo "========================================"
    echo "         Novin Status"
    echo "========================================"
    curl -fsS http://localhost:8000/api/status | python3 -m json.tool 2>/dev/null || curl -fsS http://localhost:8000/api/status
    echo ""
    echo "========================================"
    echo "Endpoints:"
    echo "  Ready:     http://localhost:8000/health/ready"
    echo "  Status:    http://localhost:8000/api/status"
    echo "  Events:    http://localhost:8000/api/events"
    echo "  Ingest:    http://localhost:8000/api/novin/ingest"
    echo "  WebSocket: ws://localhost:8000/api/ws/events"
    echo "========================================"
}

rollback_backend() {
    ensure_state_dir
    if [ ! -f "$PREV_IMAGE_ID_FILE" ] || [ ! -f "$PREV_IMAGE_NAME_FILE" ]; then
        echo -e "${RED}❌ No rollback image reference found${NC}"
        echo "Run at least one successful deploy before rollback."
        exit 1
    fi
    local previous_id
    local image_name
    previous_id="$(cat "$PREV_IMAGE_ID_FILE")"
    image_name="$(cat "$PREV_IMAGE_NAME_FILE")"
    if ! docker image inspect "$previous_id" >/dev/null 2>&1; then
        echo -e "${RED}❌ Previous backend image not found locally${NC}"
        exit 1
    fi
    echo -e "${YELLOW}Rolling back backend image...${NC}"
    docker image tag "$previous_id" "$image_name"
    docker compose up -d backend
    wait_health
    test
    show_status
    echo -e "${GREEN}✓ Rollback complete${NC}"
}

echo "🚀 Novin Deployment Starting..."

case "${1:-deploy}" in
    deploy)
        check_env
        check_preflight
        run_backup_hook
        backup_current_backend_image
        build
        start
        wait_health
        test
        show_status
        ;;
    start)
        check_preflight
        start
        wait_health
        show_status
        ;;
    stop)
        docker compose down
        ;;
    restart)
        check_preflight
        docker compose restart
        wait_health
        ;;
    rollback)
        check_preflight
        rollback_backend
        ;;
    status)
        show_status
        ;;
    logs)
        docker compose logs -f backend
        ;;
    test)
        test
        ;;
    *)
        echo "Usage: $0 {deploy|start|stop|restart|rollback|status|logs|test}"
        echo "Set NOVIN_DEPLOY_NO_CACHE=1 to force uncached rebuilds."
        exit 1
        ;;
esac
