#!/usr/bin/env bash
# Demo en vivo: arranca los 3 FastAPI + la UI (Vite) + corre el turn engine
# con scenarios/demo.json (3 días por defecto) y abre el navegador para que
# la audiencia vea el dashboard en tiempo real.
#
# Flujo:
#   1. Mata uvicorn/vite vivos en 8001/8002/8003/5173
#   2. Limpia DBs y reseedea el manufacturer
#   3. Arranca los 3 servers FastAPI en background
#   4. Arranca la UI (Vite dev server) en background
#   5. Health-check de los 4 endpoints
#   6. Abre el navegador en http://localhost:5173
#   7. Pausa hasta que pulses Enter (para que la audiencia se acomode)
#   8. Corre turn_engine.py demo.json × DAYS días
#   9. Genera charts en charts/demo/
#  10. DEJA todo vivo hasta que pulses Enter de nuevo → cleanup
#
# Uso:
#   ./scripts/run-demo.sh                  # agentes reales (config/sim.json), 3 días
#   ./scripts/run-demo.sh --stub           # modo determinista (config/sim-stub.json), rápido
#   ./scripts/run-demo.sh --days 5         # override de días
#   ./scripts/run-demo.sh --no-pause       # sin pausa inicial ni final (CI / pruebas)
#   ./scripts/run-demo.sh --pause-days     # pausa también entre cada día (Enter para avanzar)
#   ./scripts/run-demo.sh --no-pause-days  # desactiva la pausa entre días
#   ./scripts/run-demo.sh --stub --days 3 --no-pause
#
# Pausa entre días:
#   - En --stub se activa por defecto (los días duran milisegundos, hay que
#     poder explicar entre uno y otro).
#   - En modo agentes se desactiva por defecto (cada día ya tarda 1-3 min
#     mientras el LLM piensa, y su output va apareciendo en el terminal).
#   - Usa --pause-days / --no-pause-days para forzar el comportamiento.

set -euo pipefail

# ------------------------------------------------------------------
# 0) Parseo de flags y posicionamiento en la raíz del repo
# ------------------------------------------------------------------
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

CONFIG="config/sim.json"
SCENARIO="scenarios/demo.json"
DAYS=3
RUN_NAME="demo"
CHARTS_DIR="charts/demo"
PAUSE=true
MODE_LABEL="agentes"
# Pausa entre días: "" = decidir según modo, "1" = on, "0" = off.
PAUSE_DAYS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stub)           CONFIG="config/sim-stub.json"; MODE_LABEL="stub"; shift ;;
    --days)           DAYS="$2"; shift 2 ;;
    --no-pause)       PAUSE=false; shift ;;
    --pause-days)     PAUSE_DAYS=1; shift ;;
    --no-pause-days)  PAUSE_DAYS=0; shift ;;
    -h|--help)
      sed -n '1,40p' "$0"; exit 0 ;;
    *)
      echo "ERROR: flag desconocida: $1"; exit 1 ;;
  esac
done

# Default de PAUSE_DAYS según modo: on para stub, off para agentes.
if [[ -z "$PAUSE_DAYS" ]]; then
  if [[ "$MODE_LABEL" == "stub" ]]; then PAUSE_DAYS=1; else PAUSE_DAYS=0; fi
fi

PY="$REPO_ROOT/.venv/bin/python"

echo "=== Pre-flight ==="
[[ -x "$PY" ]]            || { echo "ERROR: $PY no existe — activa el venv del proyecto"; exit 1; }
[[ -f "$CONFIG" ]]        || { echo "ERROR: $CONFIG no existe"; exit 1; }
[[ -f "$SCENARIO" ]]      || { echo "ERROR: $SCENARIO no existe"; exit 1; }
command -v npm >/dev/null || { echo "ERROR: npm no encontrado en PATH (necesario para la UI)"; exit 1; }
[[ -d "ui/node_modules" ]] || {
  echo "  ui/node_modules ausente — corriendo 'npm install' en ui/ …"
  (cd ui && npm install)
}
if [[ "$CONFIG" == "config/sim.json" ]]; then
  command -v claude >/dev/null || {
    echo "WARN: claude CLI no encontrado — los agentes caerán a stub. Usa --stub para silenciar."
  }
fi

echo "  python:    $PY"
echo "  config:    $CONFIG  ($MODE_LABEL)"
echo "  scenario:  $SCENARIO ($DAYS días)"
echo "  output:    $CHARTS_DIR/"
echo "  UI:        http://localhost:5173"
if [[ "$PAUSE_DAYS" == "1" ]]; then
  echo "  pausa entre días: SÍ (Enter para avanzar al siguiente día)"
else
  echo "  pausa entre días: no"
fi

# ------------------------------------------------------------------
# 1) Mata cualquier proceso vivo en 8001/8002/8003/5173
# ------------------------------------------------------------------
echo
echo "=== Matando procesos en 8001/8002/8003/5173 ==="
for p in 8001 8002 8003 5173; do
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
# 2) Limpia DBs
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
# 3) Seed del manufacturer
# ------------------------------------------------------------------
echo
echo "=== Seed manufacturer ==="
"$PY" -m manufacturer.seed

# ------------------------------------------------------------------
# 4) Arranca los 3 uvicorn + la UI en background
# ------------------------------------------------------------------
mkdir -p logs/uvicorn logs/ui
echo
echo "=== Arrancando uvicorn + UI (background) ==="
"$PY" -m uvicorn provider.api:app      --port 8001 \
    > logs/uvicorn/provider.log     2>&1 &
PROVIDER_PID=$!
"$PY" -m uvicorn manufacturer.main:app --port 8002 \
    > logs/uvicorn/manufacturer.log 2>&1 &
MFG_PID=$!
"$PY" -m uvicorn retailer.main:app     --port 8003 \
    > logs/uvicorn/retailer.log     2>&1 &
RETAILER_PID=$!

(cd ui && npm run dev -- --port 5173 --strictPort) \
    > logs/ui/vite.log 2>&1 &
UI_PID=$!

echo "  provider=$PROVIDER_PID  manufacturer=$MFG_PID  retailer=$RETAILER_PID  ui=$UI_PID"

cleanup() {
  echo
  echo "=== Cleanup: matando uvicorn + UI ==="
  kill "$PROVIDER_PID" "$MFG_PID" "$RETAILER_PID" "$UI_PID" 2>/dev/null || true
  # Vite arranca subprocesos node; rematamos por puerto por si quedó algo
  for p in 8001 8002 8003 5173; do
    pid=$(lsof -tiTCP:$p -sTCP:LISTEN 2>/dev/null || true)
    [[ -n "${pid:-}" ]] && kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

# ------------------------------------------------------------------
# 5) Health check de los 4 endpoints
# ------------------------------------------------------------------
echo
echo "=== Health check (hasta 30s por endpoint) ==="
for port in 8001 8002 8003; do
  ok=false
  for i in $(seq 1 30); do
    if curl -sf "http://localhost:$port/api/day/current" >/dev/null 2>&1; then
      echo "  port $port (FastAPI): ready (${i}s)"
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

ok=false
for i in $(seq 1 30); do
  if curl -sf "http://localhost:5173/" >/dev/null 2>&1; then
    echo "  port 5173 (UI):       ready (${i}s)"
    ok=true
    break
  fi
  sleep 1
done
if [[ "$ok" != true ]]; then
  echo "ERROR: UI no responde tras 30s — revisa logs/ui/vite.log"
  exit 1
fi

# ------------------------------------------------------------------
# 6) Abre el navegador
# ------------------------------------------------------------------
echo
echo "=== Abriendo el navegador en http://localhost:5173 ==="
if command -v open >/dev/null 2>&1; then
  open http://localhost:5173 || true
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open http://localhost:5173 || true
else
  echo "  (no se pudo abrir automáticamente — abre manualmente http://localhost:5173)"
fi

# ------------------------------------------------------------------
# 7) Pausa antes de lanzar el turn engine
# ------------------------------------------------------------------
if [[ "$PAUSE" == true ]]; then
  echo
  echo "----------------------------------------------------------"
  echo "  Todo arriba. La UI está en http://localhost:5173"
  echo "  Cuando estés listo, pulsa Enter para empezar la demo"
  echo "  (turn_engine: $SCENARIO × $DAYS días en modo $MODE_LABEL)"
  echo "----------------------------------------------------------"
  read -r _
fi

# ------------------------------------------------------------------
# 8) Corre el turn engine
# ------------------------------------------------------------------
echo
echo "=== Turn engine: $SCENARIO × $DAYS días ($MODE_LABEL) ==="
if [[ "$MODE_LABEL" == "agentes" ]]; then
  echo "    (la respuesta de cada agente se imprime aquí cuando termina;"
  echo "     copia íntegra en logs/day-NNN-<role>.log)"
fi
TURN_ENGINE_PAUSE="$PAUSE_DAYS" "$PY" turn_engine.py "$CONFIG" "$SCENARIO" "$DAYS" "$RUN_NAME"

# ------------------------------------------------------------------
# 9) Genera charts
# ------------------------------------------------------------------
echo
echo "=== Charts ==="
METRICS_FILE=$(ls -1t "${RUN_NAME}"_*_metrics.jsonl 2>/dev/null | head -1 || true)
if [[ -z "$METRICS_FILE" ]]; then
  echo "WARN: no se encontró ningún ${RUN_NAME}_*_metrics.jsonl — salto charts"
else
  echo "  metrics: $METRICS_FILE"
  "$PY" analysis/plot_results.py "$METRICS_FILE" "$CHARTS_DIR/" --scenario "$SCENARIO" || true
  if [[ -f "$CHARTS_DIR/summary.txt" ]]; then
    echo
    echo "--- summary.txt ---"
    cat "$CHARTS_DIR/summary.txt"
  fi
fi

# ------------------------------------------------------------------
# 10) Dejar todo vivo hasta que el usuario diga "ya"
# ------------------------------------------------------------------
echo
echo "=========================================================="
echo "=== DEMO TERMINADA ======================================="
echo "=========================================================="
echo
echo "Servidores + UI siguen vivos para que la audiencia explore:"
echo "  Dashboard:        http://localhost:5173"
echo "  Provider docs:    http://localhost:8001/docs"
echo "  Manufacturer:     http://localhost:8002/docs"
echo "  Retailer docs:    http://localhost:8003/docs"
echo
echo "Logs uvicorn:       logs/uvicorn/{provider,manufacturer,retailer}.log"
echo "Logs UI:            logs/ui/vite.log"
echo "Logs agentes:       logs/day-NNN-*.log"
[[ -n "${METRICS_FILE:-}" ]] && echo "Métricas:           $METRICS_FILE"
[[ -d "$CHARTS_DIR" ]]       && echo "Charts:             $CHARTS_DIR/"

if [[ "$PAUSE" == true ]]; then
  echo
  echo "Pulsa Enter para cerrar todo y limpiar…"
  read -r _
fi
