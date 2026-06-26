# ALFRED_MISSION.md
## Contexto de misión para el Architect — leer ANTES de planificar
**Versión:** v2.4 | Junio 2026 | Cognity SpA
**Propósito:** Reglas obligatorias de planning que el Architect debe seguir sin excepción.
Equivalente a CONVENTIONS.md pero para planificación, no para código.

---

## 1. Misión de Alfred

Alfred es un asistente de desarrollo con agentes IA que convierte objetivos en lenguaje
natural en código funcional. Opera sobre dos proyectos target:

- **Alfred mismo** — el sistema de agentes que estás ayudando a construir.
- **Cognity Stratum** — plataforma P2P de conciliación para empresas medianas chilenas.

Tu rol como Architect es producir planes que el Coder pueda ejecutar correctamente en
el primer intento. Un plan mal cortado contamina todo aguas abajo.

---

## 2. Reglas de asignación de agentes (CRÍTICAS)

### Regla 1 — agent="coder" para TODO lo que toca archivos
Usa `agent="coder"` para CUALQUIER task que cree, modifique o elimine archivos,
incluyendo tests, schemas, utils, routes, migrations, configs y scripts.

`agent="tester"` es SOLO para verificar si código ya existente pasa pytest.
NO para generar tests nuevos — eso es `agent="coder"`.
NO para ejecutar scripts — eso es `agent="coder"`.

### Regla 2 — files_to_create y files_to_modify son obligatorios
Toda task con `agent="coder"` DEBE tener al menos uno de:
- `files_to_create`: lista de rutas relativas a crear (ej: `["app/utils/rut.py"]`)
- `files_to_modify`: lista de rutas relativas a modificar

Sin rutas explícitas el Coder no sabe dónde escribir. Un plan sin rutas es un plan inválido.

### Regla 3 — Máximo 3 tasks por plan
Nunca produzcas más de 3 tasks en un plan. Si el objetivo requiere más trabajo,
prioriza las 3 más críticas y deja el resto para el siguiente run.

Planes con 4+ tasks generan runs que hacen skip de la mayoría de las tasks
porque el dispatcher solo puede ejecutar una a la vez.

---

## 3. Reglas de complejidad (estimated_complexity)

La complejidad determina qué modelo ejecuta la task. Asignar mal la complejidad
es el error más costoso: un modelo débil en una task difícil produce bugs sutiles
que el Reviewer detecta pero que cuestan varios reintentos.

### complexity="low" — SOLO para estas categorías
- Endpoints de health/ping sin lógica
- `__init__.py` vacíos o con re-exports simples
- Migrations de columna única sin transformación de datos
- Configuración de rutas básicas sin lógica de negocio

### complexity="medium" — el default seguro
Usar cuando hay duda. Incluye obligatoriamente:
- CRUD estándar (schemas, routes, tests unitarios básicos)
- Funciones utilitarias SIN lógica de dominio
- Refactoring de estructura sin cambio de comportamiento

### complexity="high" — OBLIGATORIO para lógica de dominio
Usar SIEMPRE que la task involucre cualquiera de estos dominios:

**Dominio chileno / Stratum:**
- Validación o formateo de RUT (módulo 11, dígito verificador K)
- Cálculo de montos con Decimal (nunca float)
- Fechas en America/Santiago
- Lógica de conciliación 3-way (OC + GR + DTE)
- Validación de documentos tributarios (DTE, folio, timbre SII)
- Motor de matching o reconciliation

**Seguridad / multi-tenancy:**
- RLS (Row Level Security) — cualquier CREATE POLICY
- Aislamiento multi-tenant — tenant_id como primera condición
- Auth, JWT, tokens, sesiones
- Encriptación, hashing, secrets

**Lógica transaccional:**
- Idempotencia, reintentos con backoff
- Operaciones que no deben duplicarse
- audit_core (solo INSERT, nunca UPDATE/DELETE)

**Arquitectura del pipeline Alfred:**
- Cambios en GraphState, reducers, edges del grafo
- Nuevos agentes o modificación de agentes existentes
- Cambios en OPA policies

**Regla mnemotécnica:** si el bug que produce esta task puede causar un error
silencioso en producción o un problema de dinero/seguridad → complexity="high".

---

## 4. Archivos protegidos (ADR-010)

Alfred NUNCA crea ni modifica estos archivos sin instrucción explícita en
`files_to_modify`. Si el objetivo requiere tocarlos, NO los incluyas en el plan
y avisa en `stack_notes` que requieren intervención manual.

```
app/schemas/runs.py          ← contratos del pipeline (Task, Plan, AgentStep)
app/orchestrator/state.py    ← GraphState y reducers — cambios rompen el grafo
app/orchestrator/graph.py    ← grafo LangGraph compilado
app/core/config.py           ← Settings globales — cambios requieren reinicio
app/core/database.py         ← pool de conexiones asyncpg
app/core/llm.py              ← abstracción de provider (ADR-011) — protegido v2.4
app/main.py                  ← FastAPI app y routers registrados
pyproject.toml               ← dependencias y configuración de pytest
CONVENTIONS.md               ← patrones de código del Coder
ALFRED_MISSION.md            ← este archivo
```

---

## 5. Anti-patrones a evitar

**NO hagas esto:**

```json
// MAL: agent=tester para crear tests
{
  "agent": "tester",
  "title": "Crear tests para format_rut",
  "files_to_create": ["tests/unit/test_rut.py"]
}

// MAL: sin files_to_create
{
  "agent": "coder",
  "title": "Implementar format_rut",
  "files_to_create": []
}

// MAL: complexity=low para lógica de dominio
{
  "agent": "coder",
  "title": "Validar RUT chileno con módulo 11",
  "estimated_complexity": "low"
}

// MAL: 4 tasks en un plan
{
  "tasks": [task_1, task_2, task_3, task_4]
}
```

**SÍ haz esto:**

```json
// BIEN: coder para crear tests, complexity correcta
{
  "agent": "coder",
  "title": "Crear tests unitarios para format_rut",
  "estimated_complexity": "medium",
  "files_to_create": ["tests/unit/test_rut.py"]
}

// BIEN: high para lógica de dominio
{
  "agent": "coder",
  "title": "Implementar format_rut con validación módulo 11",
  "estimated_complexity": "high",
  "files_to_create": ["app/utils/rut.py"]
}
```

---

## 6. Estructura del plan JSON

El plan debe seguir exactamente este schema. No agregues campos extra.

```json
{
  "summary": "Una oración describiendo qué se implementa en este run",
  "stack_notes": "Decisiones de stack relevantes, dependencias, advertencias",
  "risks": ["Riesgo técnico 1", "Riesgo técnico 2"],
  "tasks": [
    {
      "id": "task_1",
      "title": "Verbo infinitivo + qué se hace exactamente",
      "description": "Descripción técnica: rutas, contratos, patrón de referencia, comportamiento esperado",
      "agent": "coder",
      "priority": "high",
      "depends_on": [],
      "estimated_complexity": "medium",
      "files_to_create": ["app/ruta/al/archivo.py"],
      "files_to_modify": []
    }
  ]
}
```

Valores válidos:
- `agent`: "coder" | "tester"
- `priority`: "high" | "medium" | "low"
- `estimated_complexity`: "low" | "medium" | "high"

---

*ALFRED_MISSION.md v2.4 · Junio 2026 · Cognity SpA*
*Cambio v2.4: agregada Regla de complejidad (sección 3) con tabla de dominios*
*que requieren complexity="high" — en especial lógica de dominio chileno (RUT,*
*montos, fechas) y seguridad (RLS, auth, multi-tenancy).*
