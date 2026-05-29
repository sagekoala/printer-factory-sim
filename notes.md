# Notes — Problemas y Errores Encontrados (Week 7)

Registro de todo lo que salió mal o requirió corrección durante el desarrollo y pruebas de Week 7.

---

## 1. `gh` CLI no estaba instalado

**Problema:** Al intentar crear issues en GitHub desde la terminal, el comando `gh` no existía.  
**Solución:** Instalado via `winget install --id GitHub.cli`. Luego fue necesario autenticarse con `gh auth login` (requiere interacción del usuario vía browser).

---

## 2. Flag `--prompt` no existe en el Claude CLI

**Problema:** El turn engine llamaba `claude --print --prompt "..."`, pero el Claude CLI no reconoce `--prompt`. Error: `unknown option '--prompt'`.  
**Solución:** El prompt se pasa como argumento posicional: `claude --print "..."`.

---

## 3. `./manufacturer-cli` no funciona en Windows con venv

**Problema:** El skill file usaba `./manufacturer-cli` siguiendo el ejemplo del PDF. Esto falla porque el CLI instalado via `pip install -e .` vive en `.venv/Scripts/`, no en el directorio actual.  
**Solución:** Cambiar todas las referencias a simplemente `manufacturer-cli` (sin `./`), que sí es encontrado en el PATH del venv.

---

## 4. `subprocess.run(["claude", ...])` no resuelve `.CMD` en Windows

**Problema:** `shutil.which("claude")` devuelve `claude.CMD` (instalado via npm), pero `subprocess.run(["claude", ...])` sin `shell=True` no resuelve extensiones `.CMD` en Windows. Resultado: `FileNotFoundError`.  
**Solución:** Usar `cmd /c <ruta_completa_claude.CMD>` como comando. Se implementó la función `_claude_cmd()` en `turn_engine.py` que detecta la plataforma y construye el comando correcto.

---

## 5. Timeout de 180s insuficiente para el agente

**Problema:** El agente Claude necesita leer el skill file, ejecutar varios comandos CLI, y generar una respuesta. 180 segundos no es suficiente para completar el ciclo completo, resultando en `[TIMEOUT]`.  
**Solución:** Aumentado a 300s (5 minutos). El PDF menciona 180s como razonable, pero en la práctica con múltiples comandos CLI se necesita más tiempo.

---

## 6. Warning "no stdin data received" del Claude CLI

**Problema:** Al invocar `claude --print` desde subprocess, el CLI emitía: `Warning: no stdin data received in 3s, proceeding without it`. Esto ensuciaba el output del agente capturado en los logs.  
**Solución:** Agregar `input=""` al `subprocess.run()` para redirigir stdin explícitamente y evitar la espera.

---

## 7. Base de datos del manufacturer vacía al arrancar

**Problema:** Al levantar el manufacturer por primera vez, el endpoint `/inventory` devolvía `[]`. El seed no se ejecuta automáticamente al iniciar el servidor (a diferencia del provider y el retailer).  
**Solución:** Ejecutar manualmente `python -m manufacturer.seed` después del primer arranque. Documentado en el README.

---

## 8. UTF-8 BOM en archivos exportados desde PowerShell

**Problema:** Al usar `Out-File` en PowerShell para guardar el output del CLI export, el archivo resultante tenía BOM (Byte Order Mark) UTF-16 LE. Python no podía leer el JSON resultante: `JSONDecodeError: Expecting value: line 1 column 1`.  
**Solución:** Usar `Out-File -Encoding utf8NoBOM` o mejor aún, exportar via API HTTP y guardar con Python directamente (que no añade BOM).

---

## 9. Provider no tiene endpoint `/health`

**Problema:** El turn engine y los checks intentaban `GET /health` en los 3 puertos. El provider devuelve `404` porque no implementa ese endpoint.  
**Impacto:** Solo cosmético — el provider sí está corriendo. Para verificar, usar `/api/day/current` o `/api/catalog`.

---

## 10. Manufacturer no completaba MOs en el día 1

**Problema:** En el primer `POST /api/day/advance`, el manufacturer devolvía 11 pending MOs y 0 completed, por lo que `newly_produced = 0` y el finished_printer_stock no se actualizaba.  
**Causa raíz:** El día 1 las MOs se crean Y se procesan en la misma llamada a `advance_day()`. El conteo before/after funcionó correctamente, pero la BD del manufacturer fue seeded después de que el servidor ya había iniciado, y el primer advance_day del endpoint `/simulation/advance` (no el nuevo `/api/day/advance`) no genera finished stock.  
**Solución:** En el día 2, el sistema funciona correctamente: 10 MOs completadas, finished_printer_stock actualizado, sales orders despachadas.

---

## 11. Agente Claude alcanzó el límite de uso (rate limit)

**Problema:** Al correr el turn engine con el agente real, Claude respondió: `You've hit your limit · resets 10:20pm (Europe/Madrid)`. El agente no pudo ejecutar su turno.  
**Impacto:** Este es un límite externo (plan de uso de Claude), no un error del código. El turn engine manejó la situación correctamente — capturó el mensaje y lo guardó en el log.  
**Workaround:** Esperar a que el límite se resetee, o usar un plan con más capacidad.

---

## 12. Prompt del agente demasiado descriptivo

**Problema:** El primer prompt decía "Read the skill file at X" y el agente respondió describiendo el archivo en lugar de ejecutar los comandos. Típico comportamiento LLM cuando la instrucción es ambigua.  
**Solución:** Prompt reescrito para ser explícito: "Execute your daily decisions NOW by running the actual CLI commands... Do not describe what you would do — actually do it."

---

## 13. `git credential fill` bloqueaba el proceso

**Problema:** Al intentar extraer el token de GitHub del Credential Manager de Windows via `git credential fill`, el comando quedaba esperando input interactivo, bloqueando el proceso indefinidamente.  
**Solución:** Cancelar el proceso y pedir al usuario que autentique `gh auth login` manualmente en una terminal separada.

---

## 14. `cursor-agent` se cuelga en cuenta corporativa Sanofi

**Problema:** Con `TURN_ENGINE_AGENT=cursor`, el `cursor-agent -p --force --trust` falla con `Failed to trust workspace at .../<subdir>` o se queda colgado >5 min en una tarea trivial. La cuenta del team Sanofi-Accelerator tiene `approvalMode: "allowlist"` (`~/.cursor/cli-config.json`) que solo permite `Shell(ls)`, y la política sobreescribe el `--force`/`--trust` en modo headless.  
**Workaround:** No usar el backend `cursor` desde esa cuenta. Usar `TURN_ENGINE_AGENT=github` con el CLI de GitHub Copilot.

---

## 15. Backend GitHub Copilot — flags necesarios para headless

**Problema:** Al añadir `TURN_ENGINE_AGENT=github` (`copilot -p ...`), el agente fallaba con `Permission denied and could not request permission from user` al leer `skills/*-manager.md` (vive un nivel arriba del `cwd=<role>/`). El agente se inventaba hacks (insertar SQL directo en la DB) en vez de usar el CLI documentado.  
**Solución:** Llamar `copilot` con `--allow-all --add-dir <repo_root> --no-color`. El `--allow-all` cubre tools+paths+urls. El `--add-dir` lista explícitamente la raíz del repo para garantizar acceso a `skills/`. Implementado en `_build_agent_invocation()`.

---

## 16. `retailer-cli` / `manufacturer-cli` / `provider-cli` no estaban en el PATH del subprocess del agente

**Problema:** Los entry points instalados via `pip install -e .` viven en `.venv/bin/`. El turn engine se invoca con `.venv/bin/python turn_engine.py` (sin `source .venv/bin/activate`), por lo que el subprocess del agente hereda un `PATH` sin `.venv/bin/`. Resultado: el agente intentaba `retailer-cli` y obtenía `command not found`, y se caía en `python cli.py` (que falla porque pip no instala typer/httpx en el python global).  
**Solución:** En `run_agent_or_stub()`, prepender `.venv/bin` al `PATH` del subprocess via el parámetro `env=` de `subprocess.run`. El agente ahora puede invocar los entry points o `python -m <app>.cli`, ambos con las dependencias correctas.

---

## 17. Off-by-one entre día del escenario y `day current` de cada DB

**Problema:** El prompt del agente dice "Today is day {day}" pero cuando el agente ejecuta `<role>-cli day current` ve `day {day-1}`. Origen: `advance_all()` se llama al **final** de cada turno en `run_day()`, no al inicio. Mientras los agentes deciden, el contador del DB sigue mostrando el último día completado.  
**Impacto:** Solo cosmético — los agentes hacen las decisiones correctas (precios, releases, POs), simplemente reportan el día equivocado en su resumen.  
**Solución (mínima, sin riesgo):** Añadida una `DAY-COUNTER NOTE` al prompt de `run_agent_or_stub()` que avisa al agente de que confíe en el día del prompt y no en el CLI. La alternativa "arquitectónica" (mover `advance_all` al inicio del turno) cambiaría el momento exacto en que se procesan deliveries y stock — descartada por riesgo de afectar la dinámica de la simulación en mitad del proyecto.

---

## 18. `release_to_production` no creaba manufacturing_orders — pipeline de producción rota

**Problema (descubierto al analizar charts del run holiday-rush 25d):** El run terminó con `Total fulfilled = 5`, `Total backordered = 227`, `Backorder rate = 97.8%`, `finished_printer_stock = 0` y `retailer.stock = 0` durante los 25 días. La inspección directa de la DB del manufacturer reveló:

- `sales_orders`: 5 `pending` (540 unidades) + 6 `released` (332 unidades)
- `manufacturing_orders`: **0 registros**
- `finished_printer_stock`: 0

**Causa raíz:** En `manufacturer/sales_orders.py::release_to_production()`, al pasar una sales_order de `pending` a `released`, solo se cambiaba el campo `status` y se emitía un evento. **Nunca se creaban los `ManufacturingOrderRow`** que `_fulfill_manufacturing_orders()` necesita iterar para producir printers. El comentario en `simulation.py:143` confirma el origen: `# _generate_demand(db, day)  # Week 7: demand now comes from retailers via turn_engine`. En Week 5 las MOs se generaban automáticamente desde `_generate_demand`. En Week 7 se cambió la fuente de demanda a retailer-driven sales orders, pero se olvidaron de añadir la conversión sales_order → MOs. La pipeline `release → produce → ship` quedó desconectada en mitad del refactor.  

**Solución:** En `release_to_production()`, después de marcar la orden como released, encolar `order.quantity` MOs single-unit en estado `pending`. El cap real de producción sigue gestionado por `capacity_per_day` en `_fulfill_manufacturing_orders()`. Las MOs son anónimas (sin FK al sales_order original) — el modelo de stock compartido + `advance_sales_orders()` shipeando FIFO según fits es suficiente.

**Evidencia preservada:** El run roto se conserva en `charts/holiday25-pre-fix-prod-pipeline/` y `holiday-25d-pre-fix-prod-pipeline_metrics.jsonl` para comparar con el run post-fix. Es un buen ejemplo para el reporte de cómo un bug estructural (1 línea faltante en una función) se manifiesta como un colapso global del sistema observable solo desde las métricas agregadas.

---

## 19. Doble conteo de `finished_printer_stock` en `/api/day/advance`

**Problema (descubierto durante el refactor Week 9):** El handler de
`POST /api/day/advance` en `manufacturer/main.py` llamaba a
`advance_sales_orders(db, today)` **dos veces**: una directamente en el
handler y otra dentro de `advance_day()` (la lógica core de simulación).
La segunda invocación tenía efectos visibles porque
`advance_sales_orders` decrementa `finished_printer_stock` cada vez que
intenta servir órdenes. En la práctica, el bug se enmascaraba porque
`finished_printer_stock` solía estar a 0 cuando llegaba la segunda
llamada, pero ante un día con mucho stock libre podía consumir hasta el
doble de lo correcto.

**Solución:** Eliminada la llamada redundante en el handler API. La
lógica de avance vive solo en `simulation.advance_day()`. Test de
regresión añadido en `tests/test_manufacturer_simulation.py::test_advance_day_does_not_double_count_finished_stock`.

---

## 20. Inconsistencia `set_price` retailer vs manufacturer (Week 9)

**Problema:** El manufacturer expone `POST /api/prices/{model}` (model en
path, body `{ "price": N }`). El retailer exponía `POST /api/prices`
(body `{ "model": "X", "price": N }`). Dos estilos REST distintos para
la misma operación en el mismo sistema.

**Solución (breaking):** Unificado a `POST /api/prices/{model}` también
en el retailer. Actualizados CLI y dashboard React. Sin clientes
externos en producción que romper.

---

## 21. `try/except ModuleNotFoundError` en imports (Week 9)

**Problema:** Cada `__init__.py` y cada `cli.py` tenía bloques `try:
from .modulo import X / except ModuleNotFoundError: from modulo import X`
heredados de un experimento de empaquetado temprano. El paquete está
instalado correctamente vía `pip install -e .` con `pyproject.toml`
declarando `packages = ["provider", "manufacturer", "retailer"]`, así
que el fallback nunca se ejecuta. Solo añadía ruido y ocultaba errores
reales si una dependencia faltaba.

**Solución:** Eliminados todos los bloques try/except de imports. Solo
imports relativos (`from .modulo import X`). Si alguien instala el repo
sin `pip install -e .`, fallará claro y temprano en lugar de seguir con
imports rotos.

---

## 22. Dashboard Streamlit duplicado con la UI React (Week 9)

**Problema:** `manufacturer/dashboard.py` (~200 LOC) era un dashboard
Streamlit del manufacturer. Toda su funcionalidad estaba ya cubierta
(con mejor UX y leyendo los 3 apps a la vez) por la UI React de `ui/`.

**Solución:** Borrado `manufacturer/dashboard.py` y la dependencia
`streamlit` del `pyproject.toml`. La UI oficial es la React+Vite de
`ui/`.

---

## 23. `manufacturer/provider_integration.py` shim legacy (Week 9)

**Problema:** `manufacturer/provider_integration.py` era un shim
delgado sobre `services/suppliers.py` mantenido por compatibilidad de
imports históricos. Toda la lógica viva está en `services/suppliers.py`.

**Solución:** Borrado el shim, actualizados los imports en `main.py` y
`cli.py` para apuntar directamente a `services.suppliers`.

---

## 24. `provider_config.json` y `config.json` duplicados (Week 9)

**Problema:** `manufacturer/` tenía dos archivos de configuración
prácticamente idénticos: `provider_config.json` (histórico, week 6) y
`config.json` (week 7+). Solo uno se leía realmente.

**Solución:** Borrado `provider_config.json`. La única fuente de
verdad para URLs de provider conocidos es `manufacturer/config.json`.

---

## 25. Provider sin `/health` (cerrando notes #9, Week 9)

**Problema:** notes #9 quedó como "solo cosmético". El turn engine no
lo necesita, pero el ranger de tests y los chequeos de readiness sí.

**Solución:** Añadido `GET /health → {"status": "ok"}` en
`provider/api.py`. Los tres apps exponen ahora el mismo endpoint.

---

## 26. Suite de tests pytest (Week 9)

**Antes:** Cero tests automatizados. Cada bug se descubría corriendo
escenarios de 25 días y leyendo charts.

**Ahora:** 34 tests pytest cubriendo:

- `tests/test_turn_engine.py` — `todays_signal`, multiplicación de
  modificadores en eventos solapados, `append_metrics`,
  `collect_metrics` con apps caídas, `RunState`.
- `tests/test_manufacturer_simulation.py` — release_to_production,
  idempotencia, capacity, BOM, regresión del bug #19 (doble conteo).
- `tests/test_retailer_simulation.py` — auto-fulfill backorders,
  fulfillment desde stock, set_price con path param.
- `tests/test_provider_services.py` — tier pricing, advance day con
  lead_time_modifier, validaciones.
- `tests/test_analysis.py` — `plot_results` robusto a JSONL roto.

Run con `pytest`. Configuración en `pyproject.toml` (`[tool.pytest.ini_options]`)
con `testpaths = ["tests"]` y `python_paths = ["."]` (para que
`turn_engine` se importe sin instalarlo como paquete).

---

## 27. `advance_all()` con parámetro muerto (Week 9)

**Problema:** `turn_engine.advance_all(signal, config)` recibía
`signal` y nunca lo usaba. El `lead_time_modifier` se calculaba dentro
de la función a partir de `signal` pero `signal` ya no se propagaba
correctamente porque la función se llamaba antes de recibirlo. Resultado:
el lead_time del provider siempre era 1.0 aunque el escenario dijera
otra cosa.

**Solución:** Encapsulado todo el estado por-run en un `RunState`
dataclass, eliminados los globals, y `advance_all()` toma `signal`
explícito y propaga `lead_time_modifier` al provider via
`?lead_time_modifier=X`.

---

## Estado final del checklist (week7.pdf Parte 7)

| Item | Estado |
|------|--------|
| All three apps start on their own ports | ✅ |
| Retailer CLI works for all core commands | ✅ |
| Manufacturer accepts inbound retailer orders | ✅ |
| Customer demand generator injects orders at retailers | ✅ |
| Turn engine runs deterministic (stub) mode 3 days | ✅ |
| One skill file exists (skills/manufacturer-manager.md) | ✅ |
| Turn engine runs with manufacturer-as-agent ≥ 1 day | ⚠️ Funciona pero alcanzó rate limit en la prueba |
| Manufacturer event log shows agent decisions | ⚠️ Pendiente de prueba sin rate limit |
| Agent output captured and stored (not just stdout) | ✅ logs/day-NNN-role.log |
| JSON export/import works for all three apps | ✅ |
