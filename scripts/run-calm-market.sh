#!/usr/bin/env bash
# Corrida end-to-end: calm-market (control), los 3 agentes activos.
#
# Por defecto: 25 días (escenario de baseline tranquilo, sin spikes).
# Pensado como CONTROL para comparar contra holiday-rush.
#
# El backend de agente se selecciona via TURN_ENGINE_AGENT
# (claude | cursor | github). Default: claude. Para gpt-5-mini gratis con
# Copilot:
#
#   TURN_ENGINE_AGENT=github TURN_ENGINE_MODEL=gpt-5-mini ./scripts/run-calm-market.sh --days 25
#
# Uso:   ./scripts/run-calm-market.sh [--days N]
# Logs:  logs/uvicorn/{provider,manufacturer,retailer}.log
#        logs/day-NNN-{role}.log
# Salida: charts/calm<N>/{inventory,prices,fulfillment,events}.png + summary.txt

set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

PY="$REPO_ROOT/.venv/bin/python"
SCENARIO="scenarios/calm-market.json"
DAYS=25

while [[ $# -gt 0 ]]; do
  case "$1" in
    --days)  DAYS="$2"; shift 2 ;;
    -h|--help) sed -n '1,18p' "$0"; exit 0 ;;
    *) echo "ERROR: flag desconocida: $1"; exit 1 ;;
  esac
done

RUN_NAME="calm-${DAYS}d"
CHARTS_DIR="charts/calm${DAYS}"
BACKEND="${TURN_ENGINE_AGENT:-claude}"
MODEL="${TURN_ENGINE_MODEL:-default}"

echo "=== Pre-flight ==="
[[ -x "$PY" ]]       || { echo "ERROR: $PY no existe — activa el venv del proyecto"; exit 1; }
[[ -f "$SCENARIO" ]] || { echo "ERROR: $SCENARIO no existe"; exit 1; }

case "$BACKEND" in
  claude)
    command -v claude >/dev/null \
      || { echo "ERROR: claude CLI no encontrado (necesario para TURN_ENGINE_AGENT=claude)"; exit 1; }
    ;;
  cursor)
    command -v cursor-agent >/dev/null || command -v agent >/dev/null \
      || { echo "ERROR: cursor-agent/agent CLI no encontrado (TURN_ENGINE_AGENT=cursor)"; exit 1; }
    ;;
  github|copilot|github-copilot)
    command -v copilot >/dev/null \
      || { echo "ERROR: copilot CLI no encontrado. Instala con: npm install -g @github/copilot"; exit 1; }
    ;;
  *) echo "ERROR: TURN_ENGINE_AGENT=$BACKEND desconocido (claude|cursor|github)"; exit 1 ;;
esac

echo "  python:    $PY"
echo "  scenario:  $SCENARIO ($DAYS días)"
echo "  agent:     $BACKEND  (modelo: $MODEL)"
echo "  output:    $CHARTS_DIR/"

echo
echo "=== Matando procesos en puertos 8001/8002/8003 ==="
for p in 8001 8002 8003; do
  pid=$(lsof -tiTCP:$p -sTCP:LISTEN 2>/dev/null || true)
  if [[ -n "${pid:-}" ]]; then
    echo "  port $p: kill $pid"
    kill "$pid" 2>/dev/null || true
  else
    echo "  port $p: libre"
  fi
done
sleep 2

echo
echo "=== Limpiando DBs ==="
rm -f provider/provider.db \
      manufacturer/manufacturer.db \
      retailer/retailer.db \
      retailer/printerworld.db
remaining=$(ls provider/*.db manufacturer/*.db retailer/*.db 2>/dev/null || true)
if [[ -n "$remaining" ]]; then
  echo "ERROR: aún quedan DBs: $remaining"
  exit 1
fi
echo "  OK, sin DBs residuales"

echo
echo "=== Seed manufacturer ==="
"$PY" -m manufacturer.seed

mkdir -p logs/uvicorn
echo
echo "=== Arrancando uvicorn (background) ==="
"$PY" -m uvicorn provider.api:app     --port 8001 \
    > logs/uvicorn/provider.log     2>&1 &
PROVIDER_PID=$!
"$PY" -m uvicorn manufacturer.main:app --port 8002 \
    > logs/uvicorn/manufacturer.log 2>&1 &
MFG_PID=$!
"$PY" -m uvicorn retailer.main:app    --port 8003 \
    > logs/uvicorn/retailer.log     2>&1 &
RETAILER_PID=$!

echo "  provider=$PROVIDER_PID  manufacturer=$MFG_PID  retailer=$RETAILER_PID"

cleanup() {
  echo
  echo "=== Cleanup: matando uvicorn ==="
  kill "$PROVIDER_PID" "$MFG_PID" "$RETAILER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo
echo "=== Health check (hasta 30s por server) ==="
for port in 8001 8002 8003; do
  ok=false
  for i in $(seq 1 30); do
    if curl -sf "http://localhost:$port/api/day/current" >/dev/null 2>&1; then
      echo "  port $port: ready (${i}s)"
      ok=true
      break
    fi
    sleep 1
  done
  if [[ "$ok" != true ]]; then
    echo "ERROR: port $port no responde tras 30s — revisa logs/uvicorn/"
    exit 1
  fi
done

echo
echo "=== Turn engine: $SCENARIO × $DAYS días ($BACKEND) ==="
echo "    (esto puede tardar 30-120 min con agentes activos)"
"$PY" turn_engine.py config/sim.json "$SCENARIO" "$DAYS" "$RUN_NAME"

echo
echo "=== Charts ==="
METRICS_FILE=$(ls -1t "${RUN_NAME}"_*_metrics.jsonl 2>/dev/null | head -1 || true)
if [[ -z "$METRICS_FILE" ]]; then
  echo "ERROR: no se encontró ningún ${RUN_NAME}_*_metrics.jsonl"
  exit 1
fi
echo "  metrics: $METRICS_FILE"
"$PY" analysis/plot_results.py "$METRICS_FILE" "$CHARTS_DIR/" --scenario "$SCENARIO"

echo
echo "=========================================================="
echo "=== DONE ================================================="
echo "=========================================================="
echo
echo "--- summary.txt ---"
cat "$CHARTS_DIR/summary.txt"
echo
echo "--- archivos generados ---"
ls -la "$CHARTS_DIR/"
echo
echo "Logs de uvicorn:    logs/uvicorn/"
echo "Logs de agentes:    logs/day-001-*.log … logs/day-${DAYS}-*.log"
echo "Métricas:           $METRICS_FILE"
echo
echo "Para revisar las DBs después del run:"
echo "  sqlite3 manufacturer/manufacturer.db \".tables\""
echo "  sqlite3 retailer/retailer.db        \".tables\""
echo "  sqlite3 provider/provider.db        \".tables\""
