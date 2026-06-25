"""
Tests para app/agents/routing.py — select_coder_model() v2.4.

CAMBIO RESPECTO A v2.3: select_coder_model() ahora retorna tuple[str, str]
en lugar de str. Todos los asserts actualizados para desempaquetar la tupla.

Cubre:
    - Alta complejidad + riesgo → frontier
    - Alta complejidad sin riesgo → local 35B
    - Baja complejidad → local 14B
    - Media + boilerplate score ≥ 2 + 1 archivo → local 14B
    - Media + keyword de riesgo → frontier
    - Media sin señales → local 35B (default)
    - Casos borde: sin keywords, keywords mixtos
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.agents.routing import select_coder_model, _HIGH_RISK_KEYWORDS, _BOILERPLATE_KEYWORDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_task(
    title: str = "task",
    description: str = "",
    complexity: str = "medium",
    files_to_create: list[str] | None = None,
) -> MagicMock:
    """Construir un mock de Task con los campos que usa select_coder_model."""
    task = MagicMock()
    task.title = title
    task.description = description
    task.estimated_complexity = complexity
    task.files_to_create = files_to_create or []
    return task


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch):
    monkeypatch.setattr("app.agents.routing.settings.ollama_model", "qwen3.5:35b-a3b")
    monkeypatch.setattr("app.agents.routing.settings.ollama_model_fast", "qwen2.5-coder:14b")
    monkeypatch.setattr("app.agents.routing.settings.frontier_coder", "claude-sonnet-4-6")


# ---------------------------------------------------------------------------
# Alta complejidad
# ---------------------------------------------------------------------------

class TestHighComplexity:
    def test_high_complexity_with_risk_keyword_returns_frontier(self):
        task = make_task(
            title="Motor de conciliacion multitenant",
            description="Implementar motor de reconciliation con RLS",
            complexity="high",
        )
        provider_type, model_name = select_coder_model(task)
        assert provider_type == "frontier"
        assert model_name == "claude-sonnet-4-6"

    def test_high_complexity_no_risk_returns_local_35b(self):
        task = make_task(
            title="Agregar campo a schema",
            description="Agregar campo opcional a Pydantic schema",
            complexity="high",
        )
        provider_type, model_name = select_coder_model(task)
        assert provider_type == "local"
        assert model_name == "qwen3.5:35b-a3b"

    def test_high_complexity_monto_keyword_returns_frontier(self):
        task = make_task(
            title="Calcular monto total",
            description="Suma de montos con Decimal",
            complexity="high",
        )
        provider_type, model_name = select_coder_model(task)
        assert provider_type == "frontier"


# ---------------------------------------------------------------------------
# Baja complejidad
# ---------------------------------------------------------------------------

class TestLowComplexity:
    def test_low_complexity_always_returns_local_fast(self):
        task = make_task(title="Ping endpoint", complexity="low")
        provider_type, model_name = select_coder_model(task)
        assert provider_type == "local"
        assert model_name == "qwen2.5-coder:14b"

    def test_low_complexity_ignores_risk_keywords(self):
        task = make_task(
            title="conciliacion schema simple",
            description="test fixture mock",
            complexity="low",
        )
        provider_type, model_name = select_coder_model(task)
        # baja complejidad siempre → local fast, sin importar keywords
        assert provider_type == "local"
        assert model_name == "qwen2.5-coder:14b"


# ---------------------------------------------------------------------------
# Complejidad media
# ---------------------------------------------------------------------------

class TestMediumComplexity:
    def test_medium_boilerplate_single_file_returns_local_fast(self):
        task = make_task(
            title="Crear schema pydantic para usuario",
            description="Schema con campos crud y mock para test",
            complexity="medium",
            files_to_create=["app/api/schemas/users.py"],
        )
        provider_type, model_name = select_coder_model(task)
        assert provider_type == "local"
        assert model_name == "qwen2.5-coder:14b"

    def test_medium_boilerplate_multiple_files_returns_local_35b(self):
        """Boilerplate score ≥ 2 pero múltiples archivos → no aplica fast."""
        task = make_task(
            title="Crear schema pydantic y crud",
            description="Schema con test fixture",
            complexity="medium",
            files_to_create=[
                "app/api/schemas/x.py",
                "app/api/routes/x.py",
            ],
        )
        provider_type, model_name = select_coder_model(task)
        # 2 archivos → no aplica regla boilerplate + 1 archivo → default 35B
        assert provider_type == "local"
        assert model_name == "qwen3.5:35b-a3b"

    def test_medium_with_risk_keyword_returns_frontier(self):
        task = make_task(
            title="Endpoint de autenticacion",
            description="Auth JWT con validacion de token",
            complexity="medium",
        )
        provider_type, model_name = select_coder_model(task)
        assert provider_type == "frontier"
        assert model_name == "claude-sonnet-4-6"

    def test_medium_no_signals_returns_local_35b(self):
        """Media sin señales claras → local principal (default seguro)."""
        task = make_task(
            title="Agregar campo opcional",
            description="Campo nullable en el modelo",
            complexity="medium",
        )
        provider_type, model_name = select_coder_model(task)
        assert provider_type == "local"
        assert model_name == "qwen3.5:35b-a3b"

    def test_medium_rls_keyword_returns_frontier(self):
        task = make_task(
            title="Aplicar RLS a tabla proyectos",
            description="tenant_isolation policy",
            complexity="medium",
        )
        provider_type, model_name = select_coder_model(task)
        assert provider_type == "frontier"

    def test_medium_boilerplate_score_1_uses_35b(self):
        """Un solo keyword de boilerplate no es suficiente para fast."""
        task = make_task(
            title="Crear schema de usuario",
            description="Modelo de datos",
            complexity="medium",
            files_to_create=["app/schemas/user.py"],
        )
        provider_type, model_name = select_coder_model(task)
        # score=1 → no cumple threshold de 2 → default 35B
        assert provider_type == "local"
        assert model_name == "qwen3.5:35b-a3b"


# ---------------------------------------------------------------------------
# Invariantes del resultado
# ---------------------------------------------------------------------------

class TestResultInvariants:
    def test_always_returns_tuple_of_two_strings(self):
        """select_coder_model siempre retorna (str, str)."""
        tasks = [
            make_task(complexity="high"),
            make_task(complexity="low"),
            make_task(complexity="medium"),
        ]
        for task in tasks:
            result = select_coder_model(task)
            assert isinstance(result, tuple)
            assert len(result) == 2
            provider_type, model_name = result
            assert isinstance(provider_type, str)
            assert isinstance(model_name, str)

    def test_provider_type_is_local_or_frontier(self):
        tasks = [
            make_task(title="motor conciliacion", complexity="high"),
            make_task(title="ping", complexity="low"),
            make_task(title="schema pydantic crud test fixture", complexity="medium",
                      files_to_create=["f.py"]),
        ]
        for task in tasks:
            provider_type, _ = select_coder_model(task)
            assert provider_type in ("local", "frontier"), f"Valor inesperado: {provider_type}"
