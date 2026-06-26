"""
Tests para app/core/llm.py — abstracción de provider (ADR-011).

Estrategia: Gotcha #29 — mockear LLMProvider (ABC) o las dependencias externas
(OllamaClient, anthropic.AsyncAnthropic), NUNCA los providers concretos directamente.

Cubre:
    - LLMResponse dataclass
    - OllamaProvider: stripping de <think>, logging, estimación de tokens
    - AnthropicProvider: caching, conteo exacto de tokens, NO strip_think
    - get_provider(): routing por llm_mode y task_complexity
    - _estimate_cost_usd(): cálculo de costo con y sin cache_hit
    - _log_llm_call(): evento structlog llm.call_completed y run.cost_alert
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.llm import (
    LLMProvider,
    LLMResponse,
    OllamaProvider,
    AnthropicProvider,
    _estimate_cost_usd,
    _strip_think,
    get_provider,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_settings_hybrid(monkeypatch):
    """Settings en modo hybrid (default)."""
    monkeypatch.setattr("app.core.llm.settings.llm_mode", "hybrid")
    monkeypatch.setattr("app.core.llm.settings.anthropic_api_key", "sk-ant-test")
    monkeypatch.setattr("app.core.llm.settings.ollama_model", "qwen3.5:35b-a3b")
    monkeypatch.setattr("app.core.llm.settings.ollama_model_fast", "qwen2.5-coder:14b")
    monkeypatch.setattr("app.core.llm.settings.frontier_architect", "claude-sonnet-4-6")
    monkeypatch.setattr("app.core.llm.settings.frontier_reviewer", "claude-sonnet-4-6")
    monkeypatch.setattr("app.core.llm.settings.frontier_coder", "claude-sonnet-4-6")
    monkeypatch.setattr("app.core.llm.settings.run_cost_alert_usd", 0.50)


@pytest.fixture()
def mock_settings_full_local(monkeypatch, mock_settings_hybrid):
    """Settings en modo full_local."""
    monkeypatch.setattr("app.core.llm.settings.llm_mode", "full_local")


@pytest.fixture()
def mock_settings_full_frontier(monkeypatch, mock_settings_hybrid):
    """Settings en modo full_frontier."""
    monkeypatch.setattr("app.core.llm.settings.llm_mode", "full_frontier")


# ---------------------------------------------------------------------------
# LLMResponse
# ---------------------------------------------------------------------------

class TestLLMResponse:
    def test_defaults(self):
        r = LLMResponse(content="hello", input_tokens=10, output_tokens=5, model="test")
        assert r.cache_hit is False

    def test_cache_hit_true(self):
        r = LLMResponse(
            content="x", input_tokens=1, output_tokens=1, model="m", cache_hit=True
        )
        assert r.cache_hit is True


# ---------------------------------------------------------------------------
# _strip_think
# ---------------------------------------------------------------------------

class TestStripThink:
    def test_strips_think_block(self):
        raw = "<think>reasoning here</think>valid json"
        assert _strip_think(raw) == "valid json"

    def test_strips_multiline_think(self):
        raw = "<think>\nline1\nline2\n</think>result"
        assert _strip_think(raw) == "result"

    def test_case_insensitive(self):
        raw = "<THINK>abc</THINK>output"
        assert _strip_think(raw) == "output"

    def test_no_think_block_unchanged(self):
        raw = '{"key": "value"}'
        assert _strip_think(raw) == raw

    def test_strips_and_trims_whitespace(self):
        raw = "<think>r</think>  content  "
        assert _strip_think(raw) == "content"


# ---------------------------------------------------------------------------
# _estimate_cost_usd
# ---------------------------------------------------------------------------

class TestEstimateCostUsd:
    def test_local_model_returns_zero(self):
        r = LLMResponse(content="", input_tokens=1000, output_tokens=500, model="qwen3.5:35b-a3b")
        assert _estimate_cost_usd(r) == 0.0

    def test_sonnet_no_cache(self):
        r = LLMResponse(
            content="", input_tokens=1_000_000, output_tokens=1_000_000, model="claude-sonnet-4-6"
        )
        cost = _estimate_cost_usd(r)
        # $3 input + $15 output = $18.0
        assert abs(cost - 18.0) < 0.001

    def test_sonnet_with_cache_hit_discounts_input(self):
        r = LLMResponse(
            content="", input_tokens=1_000_000, output_tokens=0,
            model="claude-sonnet-4-6", cache_hit=True,
        )
        cost = _estimate_cost_usd(r)
        # $3 × 0.10 = $0.30
        assert abs(cost - 0.30) < 0.001

    def test_opus_cost(self):
        r = LLMResponse(
            content="", input_tokens=1_000_000, output_tokens=0, model="claude-opus-4-8"
        )
        cost = _estimate_cost_usd(r)
        assert abs(cost - 5.0) < 0.001


# ---------------------------------------------------------------------------
# OllamaProvider
# ---------------------------------------------------------------------------

class TestOllamaProvider:
    @pytest.mark.asyncio
    async def test_generate_strips_think_tags(self):
        """OllamaProvider debe devolver contenido sin bloques <think>."""
        provider = OllamaProvider(model="qwen3.5:35b-a3b", agent="coder")

        with patch("app.core.llm._ollama_client") as mock_client:
            mock_client.generate = AsyncMock(
                return_value='<think>internal reasoning</think>{"result": "ok"}'
            )
            response = await provider.generate(system="sys", user="user")

        assert "<think>" not in response.content
        assert response.content == '{"result": "ok"}'

    @pytest.mark.asyncio
    async def test_generate_format_none_enforced(self):
        """OllamaProvider siempre pasa format=None (ADR-009)."""
        provider = OllamaProvider(model="qwen3.5:35b-a3b", agent="architect")

        with patch("app.core.llm._ollama_client") as mock_client:
            mock_client.generate = AsyncMock(return_value="content")
            await provider.generate(system="s", user="u")
            call_kwargs = mock_client.generate.call_args.kwargs
            assert call_kwargs.get("format") is None

    @pytest.mark.asyncio
    async def test_generate_uses_instance_num_ctx(self):
        """OllamaProvider usa los parámetros configurados en el constructor."""
        provider = OllamaProvider(
            model="qwen3.5:35b-a3b", agent="coder", num_ctx=32768, num_predict=8192
        )

        with patch("app.core.llm._ollama_client") as mock_client:
            mock_client.generate = AsyncMock(return_value="ok")
            await provider.generate(system="s", user="u")
            call_kwargs = mock_client.generate.call_args.kwargs
            assert call_kwargs["num_ctx"] == 32768
            assert call_kwargs["num_predict"] == 8192

    @pytest.mark.asyncio
    async def test_generate_returns_llm_response(self):
        provider = OllamaProvider(model="qwen3.5:35b-a3b", agent="tester")

        with patch("app.core.llm._ollama_client") as mock_client:
            mock_client.generate = AsyncMock(return_value="test output")
            response = await provider.generate(system="s", user="u")

        assert isinstance(response, LLMResponse)
        assert response.model == "qwen3.5:35b-a3b"
        assert response.cache_hit is False
        assert response.input_tokens >= 1
        assert response.output_tokens >= 1

    @pytest.mark.asyncio
    async def test_generate_emits_structlog_event(self):
        provider = OllamaProvider(model="qwen3.5:35b-a3b", agent="auditor")

        with patch("app.core.llm._ollama_client") as mock_client, \
             patch("app.core.llm.logger") as mock_logger:
            mock_client.generate = AsyncMock(return_value="output")
            await provider.generate(system="s", user="u")
            mock_logger.info.assert_called_once()
            call_kwargs = mock_logger.info.call_args
            assert call_kwargs[0][0] == "llm.call_completed"


# ---------------------------------------------------------------------------
# AnthropicProvider
# ---------------------------------------------------------------------------

def _make_anthropic_response(
    content: str = '{"ok": true}',
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read: int = 0,
) -> MagicMock:
    """Construir un mock de anthropic.types.Message."""
    msg = MagicMock()
    msg.content = [MagicMock(text=content)]
    msg.usage.input_tokens = input_tokens
    msg.usage.output_tokens = output_tokens
    msg.usage.cache_read_input_tokens = cache_read
    return msg


class TestAnthropicProvider:
    @pytest.mark.asyncio
    async def test_generate_does_not_strip_think(self):
        """Gotcha #27: AnthropicProvider NO debe aplicar _strip_think."""
        provider = AnthropicProvider(model="claude-sonnet-4-6", agent="architect")

        raw = '{"plan": "step <think>leak</think> end"}'
        mock_msg = _make_anthropic_response(content=raw)

        with patch.object(provider._client.messages, "create", new=AsyncMock(return_value=mock_msg)):
            response = await provider.generate(system="s", user="u")

        # El contenido debe llegar intacto — NO stripeado
        assert response.content == raw

    @pytest.mark.asyncio
    async def test_generate_sends_system_with_cache_control(self):
        """ADR-012: system debe ir como bloque con cache_control ephemeral."""
        provider = AnthropicProvider(model="claude-sonnet-4-6", agent="reviewer")
        mock_msg = _make_anthropic_response()

        with patch.object(
            provider._client.messages, "create", new=AsyncMock(return_value=mock_msg)
        ) as mock_create:
            await provider.generate(system="sys prompt", user="u")
            call_kwargs = mock_create.call_args.kwargs
            system_arg = call_kwargs["system"]
            assert isinstance(system_arg, list)
            assert system_arg[0]["type"] == "text"
            assert system_arg[0]["text"] == "sys prompt"
            assert system_arg[0]["cache_control"] == {"type": "ephemeral"}

    @pytest.mark.asyncio
    async def test_generate_reports_cache_hit(self):
        """cache_hit=True cuando cache_read_input_tokens > 0."""
        provider = AnthropicProvider(model="claude-sonnet-4-6", agent="architect")
        mock_msg = _make_anthropic_response(cache_read=80)

        with patch.object(provider._client.messages, "create", new=AsyncMock(return_value=mock_msg)):
            response = await provider.generate(system="s", user="u")

        assert response.cache_hit is True

    @pytest.mark.asyncio
    async def test_generate_cache_miss(self):
        """cache_hit=False cuando cache_read_input_tokens == 0."""
        provider = AnthropicProvider(model="claude-sonnet-4-6", agent="architect")
        mock_msg = _make_anthropic_response(cache_read=0)

        with patch.object(provider._client.messages, "create", new=AsyncMock(return_value=mock_msg)):
            response = await provider.generate(system="s", user="u")

        assert response.cache_hit is False

    @pytest.mark.asyncio
    async def test_generate_exact_token_counts(self):
        """AnthropicProvider usa los token counts exactos del SDK."""
        provider = AnthropicProvider(model="claude-sonnet-4-6", agent="reviewer")
        mock_msg = _make_anthropic_response(input_tokens=1234, output_tokens=567)

        with patch.object(provider._client.messages, "create", new=AsyncMock(return_value=mock_msg)):
            response = await provider.generate(system="s", user="u")

        assert response.input_tokens == 1234
        assert response.output_tokens == 567


# ---------------------------------------------------------------------------
# get_provider
# ---------------------------------------------------------------------------

class TestGetProvider:
    def test_full_local_always_returns_ollama(self, mock_settings_full_local):
        for agent in ["architect", "reviewer", "coder", "tester", "auditor"]:
            provider = get_provider(agent, task_complexity="high")
            assert isinstance(provider, OllamaProvider), f"Fallo en agente: {agent}"

    def test_hybrid_architect_returns_anthropic(self, mock_settings_hybrid):
        provider = get_provider("architect")
        assert isinstance(provider, AnthropicProvider)

    def test_hybrid_reviewer_returns_anthropic(self, mock_settings_hybrid):
        provider = get_provider("reviewer")
        assert isinstance(provider, AnthropicProvider)

    def test_hybrid_architect_high_uses_opus(self, mock_settings_hybrid):
        provider = get_provider("architect", task_complexity="high")
        assert isinstance(provider, AnthropicProvider)
        assert provider._model == "claude-opus-4-8"

    def test_hybrid_architect_medium_uses_sonnet(self, mock_settings_hybrid):
        provider = get_provider("architect", task_complexity="medium")
        assert isinstance(provider, AnthropicProvider)
        assert provider._model == "claude-sonnet-4-6"

    def test_hybrid_coder_high_returns_anthropic(self, mock_settings_hybrid):
        provider = get_provider("coder", task_complexity="high")
        assert isinstance(provider, AnthropicProvider)

    def test_hybrid_coder_medium_returns_ollama(self, mock_settings_hybrid):
        provider = get_provider("coder", task_complexity="medium")
        assert isinstance(provider, OllamaProvider)

    def test_hybrid_tester_returns_ollama_fast(self, mock_settings_hybrid):
        provider = get_provider("tester")
        assert isinstance(provider, OllamaProvider)
        assert provider._model == "qwen2.5-coder:14b"

    def test_hybrid_auditor_returns_ollama_fast(self, mock_settings_hybrid):
        provider = get_provider("auditor")
        assert isinstance(provider, OllamaProvider)
        assert provider._model == "qwen2.5-coder:14b"

    def test_full_frontier_high_uses_opus(self, mock_settings_full_frontier):
        provider = get_provider("tester", task_complexity="high")
        assert isinstance(provider, AnthropicProvider)
        assert provider._model == "claude-opus-4-8"

    def test_full_frontier_medium_uses_sonnet(self, mock_settings_full_frontier):
        provider = get_provider("coder", task_complexity="medium")
        assert isinstance(provider, AnthropicProvider)
        assert provider._model == "claude-sonnet-4-6"

    def test_provider_stores_agent_name(self, mock_settings_hybrid):
        """El provider debe registrar el nombre del agente para logs."""
        provider = get_provider("architect")
        assert provider._agent == "architect"


# ---------------------------------------------------------------------------
# LLMProvider como ABC — no instanciable directamente
# ---------------------------------------------------------------------------

class TestLLMProviderABC:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            LLMProvider()  # type: ignore[abstract]

    def test_concrete_subclass_must_implement_generate(self):
        class Incomplete(LLMProvider):
            pass  # no implementa generate

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]
