#!/usr/bin/env bash
# Corrida end-to-end: holiday-rush, los 3 agentes activos.
#
# Por defecto: 25 días (el escenario completo Q4 con chip_shortage
# solapado a christmas_season). Si lanzas --days 15 hace la versión
# corta que cabe dentro de la ventana de 5h de Claude Pro.
#
# El backend de agente se selecciona via TURN_ENGINE_AGENT
# (claude | cursor | github). Default: claude. Para gpt-5-mini gratis con
# Copilot:
#
#   TURN_ENGINE_AGENT=github TURN_ENGINE_MODEL=gpt-5-mini ./scripts/run-holiday-15d.sh --days 25
#
# Uso:   ./scripts/run-holiday-15d.sh [--days N]
# Logs:  logs/uvicorn/{provider,manufacturer,retailer}.log
#        logs/day-NNN-{role}.log
# Salida: charts/holiday<N>/{inventory,prices,fulfillment,events}.png + summary.txt

set -euo pipefail

# ------------------------------------------------------------------
# 0) Posicionarse en la raíz del repo y configurar paths
# ------------------------------------------------------------------
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

PY="$REPO_ROOT/.venv/bin/python"
SCENARIO="scenarios/holiday-rush.json"
DAYS=25

while [[ $# -gt 0 ]]; do
  case "$1" in
    --days)  DAYS="$2"; shift 2 ;;
    -h|--help) sed -n '1,18p' "$0"; exit 0 ;;
    *) echo "ERROR: flag desconocida: $1"; exit 1 ;;
  esac
done

RUN_NAME="holiday-${DAYS}d"
CHARTS_DIR="charts/holiday${DAYS}"
BACKEND="${TURN_ENGINE_AGENT:-claude}"
MODEL="${TURN_ENGINE_MODEL:-default}"

echo "=== Pre-flight ==="
[[ -x "$PY" ]]       || { echo "ERROR: $PY no existe — activa el venv del proyecto"; exit 1; }
[[ -f "$SCENARIO" ]] || { echo "ERROR: $SCENARIO no existe"; exit 1; }

# Verifica que el CLI del backend seleccionado está disponible. Cada CLI tiene
# su propio binario; fallar aquí es mucho más barato que descubrirlo a la
# mitad del run de 90 minutos.
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

# ------------------------------------------------------------------
# 1) Mata cualquier uvicorn vivo en 8001/8002/8003
# ------------------------------------------------------------------
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

# ------------------------------------------------------------------
# 2) Limpia DBs (incluyendo printerworld.db por si existe de runs viejos)
# ------------------------------------------------------------------
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

# ------------------------------------------------------------------
# 3) Seed del manufacturer (provider y retailer se auto-seedan)
# ------------------------------------------------------------------
echo
echo "=== Seed manufacturer ==="
"$PY" -m manufacturer.seed

# ------------------------------------------------------------------
# 4) Arranca los 3 uvicorn en background
# ------------------------------------------------------------------
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

# ------------------------------------------------------------------
# 5) Espera (hasta 30s) a que respondan los 3 servers
# ------------------------------------------------------------------
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

# ------------------------------------------------------------------
# 6) Corre el turn engine
# ------------------------------------------------------------------
echo
echo "=== Turn engine: $SCENARIO × $DAYS días ($BACKEND) ==="
echo "    (esto puede tardar 30-120 min con agentes activos)"
"$PY" turn_engine.py config/sim.json "$SCENARIO" "$DAYS" "$RUN_NAME"

# ------------------------------------------------------------------
# 7) Genera charts
# ------------------------------------------------------------------
echo
echo "=== Charts ==="
METRICS_FILE=$(ls -1t "${RUN_NAME}"_*_metrics.jsonl 2>/dev/null | head -1 || true)
if [[ -z "$METRICS_FILE" ]]; then
  echo "ERROR: no se encontró ningún ${RUN_NAME}_*_metrics.jsonl"
  exit 1
fi
echo "  metrics: $METRICS_FILE"
"$PY" analysis/plot_results.py "$METRICS_FILE" "$CHARTS_DIR/" --scenario "$SCENARIO"

# ------------------------------------------------------------------
# 8) Resumen final
# ------------------------------------------------------------------
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
