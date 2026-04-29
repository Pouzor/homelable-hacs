#!/usr/bin/env bash
# Spin up Home Assistant in Docker pointing at this integration.
#
# Usage:
#   ./scripts/dev-ha.sh             # build frontend, start HA, follow logs
#   ./scripts/dev-ha.sh --no-build  # skip frontend build, just (re)start HA
#   ./scripts/dev-ha.sh stop        # stop the container
#   ./scripts/dev-ha.sh restart     # rebuild frontend, restart container
#   ./scripts/dev-ha.sh logs        # tail logs
#   ./scripts/dev-ha.sh shell       # bash inside container
#
# After first start: open http://localhost:8123 → onboard → Settings →
# Devices & Services → Add Integration → "Homelable".
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE_FILE="docker-compose.dev.yml"
SERVICE="homeassistant"

build_frontend() {
  echo "→ Building frontend bundle (npm run build:ha)..."
  (cd "$ROOT/frontend-src" && npm run build:ha)
}

ensure_config_dir() {
  mkdir -p "$ROOT/dev-config/custom_components"
}

cmd_up() {
  ensure_config_dir
  if [[ "${1:-}" != "--no-build" ]]; then
    build_frontend
  fi
  echo "→ Starting Home Assistant container..."
  docker compose -f "$COMPOSE_FILE" up -d "$SERVICE"
  echo
  echo "✅ HA running at http://localhost:8123"
  echo "   Following logs (Ctrl+C to stop following — container keeps running)..."
  echo
  docker compose -f "$COMPOSE_FILE" logs -f "$SERVICE"
}

cmd_stop() {
  echo "→ Stopping container..."
  docker compose -f "$COMPOSE_FILE" down
}

cmd_restart() {
  build_frontend
  echo "→ Restarting container..."
  docker compose -f "$COMPOSE_FILE" restart "$SERVICE"
  docker compose -f "$COMPOSE_FILE" logs -f "$SERVICE"
}

cmd_logs() {
  docker compose -f "$COMPOSE_FILE" logs -f "$SERVICE"
}

cmd_shell() {
  docker compose -f "$COMPOSE_FILE" exec "$SERVICE" /bin/bash
}

case "${1:-up}" in
  up|"")        cmd_up "${2:-}" ;;
  --no-build)   cmd_up "--no-build" ;;
  stop)         cmd_stop ;;
  restart)      cmd_restart ;;
  logs)         cmd_logs ;;
  shell)        cmd_shell ;;
  *)
    echo "Unknown command: $1" >&2
    echo "Usage: $0 [up|stop|restart|logs|shell|--no-build]" >&2
    exit 1
    ;;
esac
