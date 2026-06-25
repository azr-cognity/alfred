# alfred.rego
# La primera política de Alfred: evalúa el output del agente Coder
# antes de pasarlo al Reviewer.
#
# Cómo funciona:
# FastAPI envía un JSON con el output del Coder a OPA via HTTP:
#   POST http://localhost:8181/v1/data/alfred/coder/deny
# OPA evalúa las reglas y responde:
#   {"result": []}           → sin violaciones → el output avanza
#   {"result": ["razón 1"]}  → violación → vuelve al Coder con feedback

package alfred.coder

import rego.v1

# ── REGLAS DE FORMATO ─────────────────────────────────────────────────────────

# Una función Python no puede tener más de 50 líneas.
# El Coder recibe esta razón y la usa como instrucción para corregir.
deny contains reason if {
    file := input.files[_]
    file.language == "python"
    line_count := count(split(file.content, "\n"))
    line_count > 300           # primero validamos el archivo completo
    reason := sprintf(
        "Archivo '%s' tiene %d líneas (máximo 300). Divide en módulos.",
        [file.path, line_count]
    )
}

# No se permite print() en código Python — usar structlog - cambio deny por warn
warn contains reason if {
    file := input.files[_]
    file.language == "python"
    contains(file.content, "print(")
    not contains(file.path, "test_")   # excepto en tests
    reason := sprintf(
        "Archivo '%s' usa print(). Reemplazar con structlog.get_logger().",
        [file.path]
    )
}

# Todo endpoint FastAPI debe tener un response_model definido
deny contains reason if {
    file := input.files[_]
    file.language == "python"
    contains(file.content, "@router.")
    not contains(file.content, "response_model")
    reason := sprintf(
        "Archivo '%s' tiene endpoints sin response_model. Agregar tipado explícito.",
        [file.path]
    )
}

# No se permiten secretos hardcodeados (detección básica)
deny contains reason if {
    file := input.files[_]
    secret_pattern := ["password =", "api_key =", "secret =", "token ="]
    pattern := secret_pattern[_]
    contains(lower(file.content), pattern)
    not contains(file.path, ".example")
    not contains(file.path, "test_")
    reason := sprintf(
        "Archivo '%s' puede tener un secreto hardcodeado ('%s'). Usar variables de entorno.",
        [file.path, pattern]
    )
}

# ── REGLAS DE IMPORTS ─────────────────────────────────────────────────────────

# No se permiten imports de librerías no canónicas del stack
# (evita que el Coder proponga pandas cuando el stack usa solo SQLModel)
deny contains reason if {
    file := input.files[_]
    file.language == "python"
    banned := ["pandas", "flask", "django", "sqlalchemy"]
    lib := banned[_]
    contains(file.content, sprintf("import %s", [lib]))
    reason := sprintf(
        "Archivo '%s' importa '%s' que no está en el stack canónico de Alfred.",
        [file.path, lib]
    )
}

# ── WARNINGS (no bloquean, solo informan) ────────────────────────────────────
# Los warnings van al output pero no detienen el pipeline.

warn contains reason if {
    file := input.files[_]
    file.language == "python"
    not contains(file.content, "\"\"\"")   # sin docstrings
    contains(file.content, "def ")
    reason := sprintf(
        "Archivo '%s' tiene funciones sin docstrings. Considera agregar documentacion.",
        [file.path]
    )
}
