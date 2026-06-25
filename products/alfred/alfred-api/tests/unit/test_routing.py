"""Tests unitarios para app.agents.routing — select_coder_model()."""
from unittest.mock import patch

import pytest

from app.agents.routing import BOILERPLATE_KEYWORDS, BOILERPLATE_MAX_FILES, select_coder_model
from app.schemas.runs import Task

FAST_MODEL = "qwen2.5-coder:14b"
MAIN_MODEL = "qwen3.5:35b-a3b"


def _task(**kwargs) -> Task:
    """Construye una Task con valores por defecto razonables."""
    defaults = {
        "id": "task_1",
        "title": "Implementar algo",
        "description": "Descripción genérica",
        "agent": "coder",
        "priority": "medium",
        "files_to_create": ["app/utils/helper.py"],
        "files_to_modify": [],
        "estimated_complexity": "medium",
    }
    defaults.update(kwargs)
    return Task(**defaults)


@patch("app.agents.routing.settings")
def test_schema_keyword_single_file_returns_fast_model(mock_settings):
    """Task con keyword 'schema' y 1 archivo → modelo rápido."""
    mock_settings.ollama_model = MAIN_MODEL
    mock_settings.ollama_model_fast = FAST_MODEL

    task = _task(title="Crear schema Pydantic para proyectos", files_to_create=["app/schemas/projects.py"])
    assert select_coder_model(task) == FAST_MODEL


@patch("app.agents.routing.settings")
def test_high_complexity_returns_main_model(mock_settings):
    """estimated_complexity=high siempre retorna modelo principal."""
    mock_settings.ollama_model = MAIN_MODEL
    mock_settings.ollama_model_fast = FAST_MODEL

    task = _task(
        title="Crear schema básico",
        estimated_complexity="high",
        files_to_create=["app/schemas/x.py"],
    )
    assert select_coder_model(task) == MAIN_MODEL


@patch("app.agents.routing.settings")
def test_low_complexity_returns_fast_model(mock_settings):
    """estimated_complexity=low siempre retorna modelo rápido."""
    mock_settings.ollama_model = MAIN_MODEL
    mock_settings.ollama_model_fast = FAST_MODEL

    task = _task(
        title="Implementar lógica compleja de reconciliación",
        estimated_complexity="low",
        files_to_create=["app/services/complex.py"],
    )
    assert select_coder_model(task) == FAST_MODEL


@patch("app.agents.routing.settings")
def test_multiple_files_returns_main_model(mock_settings):
    """Keyword boilerplate presente pero 2 archivos → modelo principal."""
    mock_settings.ollama_model = MAIN_MODEL
    mock_settings.ollama_model_fast = FAST_MODEL

    task = _task(
        title="Crear crud de usuarios",
        files_to_create=["app/routes/users.py", "app/schemas/users.py"],
    )
    assert select_coder_model(task) == MAIN_MODEL


@patch("app.agents.routing.settings")
def test_no_keyword_medium_complexity_returns_main_model(mock_settings):
    """Sin keyword boilerplate y complejidad media → modelo principal."""
    mock_settings.ollama_model = MAIN_MODEL
    mock_settings.ollama_model_fast = FAST_MODEL

    task = _task(
        title="Implementar reconciliación P2P con grafo de dependencias",
        description="Lógica de negocio compleja con múltiples entidades",
        files_to_create=["app/services/reconciliation.py"],
    )
    assert select_coder_model(task) == MAIN_MODEL


def test_boilerplate_keywords_is_frozenset():
    """BOILERPLATE_KEYWORDS debe ser frozenset inmutable."""
    assert isinstance(BOILERPLATE_KEYWORDS, frozenset)
    assert "schema" in BOILERPLATE_KEYWORDS
    assert "crud" in BOILERPLATE_KEYWORDS
    assert "test" in BOILERPLATE_KEYWORDS


def test_boilerplate_max_files_is_one():
    """BOILERPLATE_MAX_FILES debe ser 1."""
    assert BOILERPLATE_MAX_FILES == 1
