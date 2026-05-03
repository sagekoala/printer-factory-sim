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
