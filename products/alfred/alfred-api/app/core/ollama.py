import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger()


class OllamaClient:
    """
    Cliente async para Ollama.
    Expone dos métodos principales:
    - generate(): para completaciones simples
    - embed(): para generar embeddings de texto
    """

    def __init__(self) -> None:
        self.base_url = settings.ollama_base_url
        self.model = settings.ollama_model
        self.embed_model = settings.ollama_embed_model
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(120.0),  # los modelos grandes pueden tardar
        )

    async def generate(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        stream: bool = False,
    ) -> str:
        """
        Genera texto con el modelo configurado.
        Retorna el texto completo (no streaming por ahora).
        """
        payload: dict = {
            "model": model or self.model,
            "prompt": prompt,
            "stream": stream,
        }
        if system:
            payload["system"] = system

        log = logger.bind(model=payload["model"], prompt_len=len(prompt))
        log.info("ollama.generate.start")

        response = await self._client.post("/api/generate", json=payload)
        response.raise_for_status()

        result = response.json()
        log.info("ollama.generate.done", tokens=result.get("eval_count", 0))
        return result["response"]

    async def chat(
        self,
        messages: list[dict],
        system: str | None = None,
        model: str | None = None,
    ) -> str:
        """
        Chat completions — formato OpenAI compatible.
        Usado por LangGraph para comunicarse con el modelo.
        """
        payload: dict = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
        }
        if system:
            # Prepend system message si no está ya en messages
            if not any(m.get("role") == "system" for m in messages):
                payload["messages"] = [{"role": "system", "content": system}] + messages

        response = await self._client.post("/api/chat", json=payload)
        response.raise_for_status()

        result = response.json()
        return result["message"]["content"]

    async def embed(self, text: str) -> list[float]:
        """
        Genera un embedding de 768 dimensiones con nomic-embed-text.
        Usado para indexar archivos del repo y búsqueda semántica.
        """
        response = await self._client.post(
            "/api/embeddings",
            json={"model": self.embed_model, "prompt": text},
        )
        response.raise_for_status()
        return response.json()["embedding"]

    async def health(self) -> bool:
        """Verifica que Ollama está corriendo y el modelo está disponible."""
        try:
            response = await self._client.get("/api/tags")
            response.raise_for_status()
            models = [m["name"] for m in response.json().get("models", [])]
            return self.model in models
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()


# Instancia global
ollama = OllamaClient()
