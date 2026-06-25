# ALFRED_MISSION.md
## Contexto de misión — inyectado en el Architect antes de planificar

---

## Qué es Alfred y qué construye

Alfred es un asistente de desarrollo de software con agentes IA construido por Cognity SpA.
Su objetivo es generar código funcional, revisado y testeado a partir de un prompt
en lenguaje natural.

Alfred existe para acelerar el desarrollo de **Cognity Stratum** — una plataforma de
reconciliación P2P para empresas medianas chilenas. Todo código que Alfred genera
es código de Stratum o código de Alfred mismo (bootstrapping).

---

## Stack de Alfred (el sistema que planificas)

```
Backend:  Python 3.11 · FastAPI · asyncpg · SQLModel · Pydantic v2 · structlog
Cola:     Redis streams (XADD/XREADGROUP)
Agentes:  LangGraph 1.2.6 · Ollama (qwen3.5:35b-a3b)
DB:       Postgres + pgvector (Supabase) · alfred_db
Tests:    pytest · httpx · unittest.mock
UI:       Next.js 15 · shadcn/ui · Monaco Editor
```

---

## Reglas de planning — OBLIGATORIAS

### 1. Asignación de agentes

**`agent: "coder"`** — SIEMPRE que la task crea o modifica archivos.
Esto incluye: routers, schemas, tests, modelos, configuraciones, scripts, migraciones,
archivos markdown, fixtures, utilidades, y cualquier otro archivo de código.

**`agent: "coder"` para archivos de test.** Si la task es "escribir tests para X",
el agente es `coder`, NO `tester`. El agente `tester` ejecuta los tests
que el `coder` ya escribió — no los genera.

**`agent: "tester"`** — SOLO cuando la task es verificar si código existente pasa pytest.
Raramente aparece en un plan inicial. Solo en plans de verificación de regresión.

**`agent: "reviewer"`** — SOLO para tasks de revisión arquitectónica sin generar archivos.
Raramente necesario — el Reviewer ya es parte del pipeline automático.

### 2. Tasks atómicas y concretas

Cada task debe:
- Implementarse en menos de 100 líneas de código
- Tener `files_to_create` o `files_to_modify` con rutas explícitas y concretas
- Tener una descripción que mencione el patrón de referencia cuando exista
  (ej: "siguiendo el patrón de app/api/routes/runs.py")

**NUNCA** crear tasks sin `files_to_create` o `files_to_modify` — el Coder
necesita saber exactamente qué archivos producir.

### 3. Máximo 3 tasks por run

Un run de Alfred tarda ~5 minutos por task. Más de 3 tasks hace el run
inmanejable y aumenta el riesgo de fallos acumulados.

Si el objetivo requiere más de 3 tasks, prioriza las más importantes
y deja el resto para runs siguientes.

### 4. Dependencias explícitas

Si task_2 necesita lo que produce task_1, declara `depends_on: ["task_1"]`.
Si pueden ejecutarse independientemente, `depends_on: []`.
NUNCA crear dependencias circulares.

### 5. Rutas de archivos relativas

Todas las rutas en `files_to_create` y `files_to_modify` son relativas
a `alfred-api/` para código Python y a `alfred-ui/` para código TypeScript.

---

## Ejemplos — task bien formada vs mal formada

### MAL — task asignada a agente incorrecto
```json
{
  "id": "task_1",
  "title": "Escribir tests para el router de proyectos",
  "agent": "tester",
  "files_to_create": []
}
```
**Problema:** `agent=tester` no genera archivos. Task irá a skip_node.

### BIEN — misma task, agente correcto
```json
{
  "id": "task_1",
  "title": "Escribir tests para el router de proyectos",
  "agent": "coder",
  "files_to_create": ["tests/unit/test_projects.py"],
  "description": "Crear tests pytest con ASGITransport (httpx) y AsyncSessionLocal mockeado con unittest.mock. Seguir patrón de tests/unit/test_health.py. Cubrir GET list, GET by id (200 y 404), POST create, PATCH update (200 y 404)."
}
```

### MAL — task sin archivos explícitos
```json
{
  "id": "task_1",
  "title": "Implementar autenticación",
  "agent": "coder",
  "files_to_create": [],
  "description": "Agregar autenticación al sistema"
}
```
**Problema:** El Coder no sabe qué archivos crear. Output impredecible.

### BIEN — task con archivos y patrón de referencia
```json
{
  "id": "task_1",
  "title": "Implementar middleware de autenticación JWT",
  "agent": "coder",
  "files_to_create": ["app/core/auth.py"],
  "files_to_modify": ["app/main.py"],
  "description": "Crear app/core/auth.py con función verify_token(token: str) -> dict que valida JWT con python-jose. Agregar dependency get_current_user en FastAPI. Registrar en app/main.py. Seguir patrón de app/core/config.py para settings."
}
```

---

## Antipatrones a evitar

| Antipatrón | Por qué falla | Corrección |
|---|---|---|
| `agent=tester` para "escribe tests" | tester no genera archivos → skip | `agent=coder` siempre para crear archivos |
| `agent=reviewer` para "revisa código" | reviewer no genera archivos → skip | Solo usar si la task no produce archivos |
| `files_to_create: []` | Coder no sabe qué producir | Siempre listar rutas explícitas |
| Más de 3 tasks en un plan | Runs de 15+ minutos, difíciles de debuggear | Máximo 3, priorizar las críticas |
| Tasks de "setup" sin código concreto | Vagas, el Coder las interpreta distinto cada vez | Especificar exactamente qué archivo y qué función |
| Dependencias innecesarias | Serializa tasks que pueden correr en paralelo | Solo declarar si hay dependencia real de datos |

---

*ALFRED_MISSION.md · Cognity SpA · Junio 2026*
*Actualizar cuando cambie el stack o las reglas de planning*