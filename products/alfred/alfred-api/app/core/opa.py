import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger()


class PolicyResult:
    """Resultado de evaluar un output de agente contra las políticas OPA."""

    def __init__(self, violations: list[str], warnings: list[str]) -> None:
        self.violations = violations   # bloquean el pipeline
        self.warnings = warnings       # informan pero no bloquean
        self.passed = len(violations) == 0

    def __repr__(self) -> str:
        return f"PolicyResult(passed={self.passed}, violations={self.violations})"


class OPAClient:
    """
    Cliente async para Open Policy Agent.

    Cómo funciona:
    1. El agente produce un output (código, plan, reporte)
    2. FastAPI llama a OPA con ese output como input
    3. OPA evalúa las reglas Rego y responde con violations/warnings
    4. Si hay violations → el output no avanza al siguiente agente
    5. Si no hay → avanza con los warnings como contexto informativo
    """

    def __init__(self) -> None:
        self.base_url = settings.opa_url
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(10.0),
        )

    async def evaluate(
        self,
        agent_name: str,
        output: dict,
    ) -> PolicyResult:
        """
        Evalúa el output de un agente contra sus políticas.

        Args:
            agent_name: "coder" | "reviewer" | "tester" | "auditor"
            output: el output del agente serializado como dict

        Returns:
            PolicyResult con violations (bloquean) y warnings (informan)
        """
        log = logger.bind(agent=agent_name)

        # Evaluar violaciones (deny rules)
        deny_url = f"/v1/data/alfred/{agent_name}/deny"
        warn_url = f"/v1/data/alfred/{agent_name}/warn"

        payload = {"input": output}

        violations: list[str] = []
        warnings: list[str] = []

        try:
            deny_resp = await self._client.post(deny_url, json=payload)
            deny_resp.raise_for_status()
            violations = deny_resp.json().get("result", []) or []
        except httpx.HTTPStatusError as e:
            # Si la política no existe, no bloqueamos
            if e.response.status_code == 404:
                log.debug("opa.policy.not_found", url=deny_url)
            else:
                log.error("opa.evaluate.error", error=str(e))

        try:
            warn_resp = await self._client.post(warn_url, json=payload)
            warn_resp.raise_for_status()
            warnings = warn_resp.json().get("result", []) or []
        except httpx.HTTPStatusError:
            pass  # warnings son opcionales

        result = PolicyResult(violations=violations, warnings=warnings)

        if result.passed:
            log.info("opa.evaluate.passed", warnings=len(warnings))
        else:
            log.warning("opa.evaluate.blocked", violations=violations)

        return result

    async def health(self) -> bool:
        """Verifica que OPA está corriendo."""
        try:
            response = await self._client.get("/health")
            return response.status_code == 200
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()


# Instancia global
opa = OPAClient()
