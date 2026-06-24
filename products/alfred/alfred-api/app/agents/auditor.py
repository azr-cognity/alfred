"""
Agente Auditor — el guardián de seguridad de Alfred (S8).

Responsabilidad:
  Corre Bandit y semgrep sobre todos los archivos del run y abre una PR
  en GitHub si el audit pasa.

Flujo:
  1. Recibe todos los archivos escritos durante el run
  2. Corre Bandit en subprocess (seguridad Python)
  3. Corre semgrep en subprocess (patrones de seguridad)
  4. Consolida findings por severidad
  5. Si hay findings HIGH → AuditResult(passed=False, feedback=...)
  6. Si solo MEDIUM/LOW o ninguno → AuditResult(passed=True) + abre PR en GitHub
"""

import asyncio
import json
import sys
from pathlib import Path

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger()

AUDIT_TIMEOUT = 120  # segundos por herramienta
PROJECT_ROOT = Path(settings.alfred_project_root)


class AuditFinding:
    """Un finding de Bandit o semgrep."""

    def __init__(
        self,
        tool: str,
        severity: str,
        message: str,
        file_path: str,
        line: int = 0,
    ) -> None:
        self.tool = tool
        self.severity = severity.upper()
        self.message = message
        self.file_path = file_path
        self.line = line

    def __str__(self) -> str:
        return f"[{self.tool}][{self.severity}] {self.file_path}:{self.line} — {self.message}"


class AuditorResult:
    """Resultado del Auditor."""

    def __init__(
        self,
        passed: bool,
        feedback: str,
        findings: list[AuditFinding],
        pr_url: str = "",
    ) -> None:
        self.passed = passed
        self.feedback = feedback
        self.findings = findings
        self.pr_url = pr_url

    @property
    def high_findings(self) -> list[AuditFinding]:
        return [f for f in self.findings if f.severity == "HIGH"]

    @property
    def medium_findings(self) -> list[AuditFinding]:
        return [f for f in self.findings if f.severity == "MEDIUM"]

    @property
    def low_findings(self) -> list[AuditFinding]:
        return [f for f in self.findings if f.severity == "LOW"]


# --------------------------------------------------------------------------- #
# Bandit
# --------------------------------------------------------------------------- #

async def _run_bandit(file_paths: list[str]) -> list[AuditFinding]:
    """Ejecuta Bandit sobre los archivos Python y retorna findings."""
    py_files = [p for p in file_paths if p.endswith(".py")]
    if not py_files:
        return []

    abs_paths = [str(PROJECT_ROOT / p) for p in py_files]

    cmd = [
        sys.executable, "-m", "bandit",
        "-f", "json",
        "-q",
        *abs_paths,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=AUDIT_TIMEOUT)
        output = stdout.decode("utf-8", errors="replace")

        if not output.strip():
            return []

        data = json.loads(output)
        findings = []
        for result in data.get("results", []):
            findings.append(AuditFinding(
                tool="bandit",
                severity=result.get("issue_severity", "LOW"),
                message=result.get("issue_text", ""),
                file_path=result.get("filename", "").replace(str(PROJECT_ROOT) + "/", "").replace("\\", "/"),
                line=result.get("line_number", 0),
            ))
        return findings

    except asyncio.TimeoutError:
        logger.warning("auditor.bandit.timeout")
        return []
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("auditor.bandit.error", error=str(e))
        return []


# --------------------------------------------------------------------------- #
# semgrep
# --------------------------------------------------------------------------- #

async def _run_semgrep(file_paths: list[str]) -> list[AuditFinding]:
    """Ejecuta semgrep con ruleset python sobre los archivos y retorna findings."""
    py_files = [p for p in file_paths if p.endswith(".py")]
    if not py_files:
        return []

    abs_paths = [str(PROJECT_ROOT / p) for p in py_files]

    # semgrep executable en el venv
    semgrep_bin = str(Path(sys.executable).parent / "semgrep")

    cmd = [
        semgrep_bin,
        "--config", "p/python",
        "--json",
        "--quiet",
        *abs_paths,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=AUDIT_TIMEOUT)
        output = stdout.decode("utf-8", errors="replace")

        if not output.strip():
            return []

        data = json.loads(output)
        findings = []
        for result in data.get("results", []):
            severity = result.get("extra", {}).get("severity", "WARNING")
            # semgrep usa ERROR/WARNING/INFO — mapear a HIGH/MEDIUM/LOW
            severity_map = {"ERROR": "HIGH", "WARNING": "MEDIUM", "INFO": "LOW"}
            mapped = severity_map.get(severity.upper(), "LOW")

            findings.append(AuditFinding(
                tool="semgrep",
                severity=mapped,
                message=result.get("extra", {}).get("message", ""),
                file_path=result.get("path", "").replace(str(PROJECT_ROOT), "").lstrip("/\\").replace("\\", "/"),
                line=result.get("start", {}).get("line", 0),
            ))
        return findings

    except asyncio.TimeoutError:
        logger.warning("auditor.semgrep.timeout")
        return []
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("auditor.semgrep.error", error=str(e))
        return []


# --------------------------------------------------------------------------- #
# GitHub PR
# --------------------------------------------------------------------------- #

async def _create_github_pr(
    run_id: str,
    plan_summary: str,
    files_written: list[str],
    findings: list[AuditFinding],
) -> str:
    """Abre una PR en GitHub con los archivos del run. Retorna la URL de la PR."""
    if not settings.github_token:
        logger.warning("auditor.github.no_token")
        return ""

    token = settings.github_token
    repo = settings.github_repo
    base = settings.github_base_branch
    branch = f"alfred/run-{run_id[:8]}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        # Obtener SHA del branch base
        r = await client.get(
            f"https://api.github.com/repos/{repo}/git/ref/heads/{base}",
            headers=headers,
        )
        if r.status_code != 200:
            logger.error("auditor.github.get_ref_failed", status=r.status_code)
            return ""

        base_sha = r.json()["object"]["sha"]

        # Crear branch
        r = await client.post(
            f"https://api.github.com/repos/{repo}/git/refs",
            headers=headers,
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
        if r.status_code not in (200, 201, 422):  # 422 = branch ya existe
            logger.error("auditor.github.create_branch_failed", status=r.status_code)
            return ""

        # Obtener tree del base para hacer commit con todos los archivos
        blobs = []
        for file_path in files_written:
            full = PROJECT_ROOT / file_path
            if not full.exists():
                continue
            content = full.read_text(encoding="utf-8", errors="replace")

            r_blob = await client.post(
                f"https://api.github.com/repos/{repo}/git/blobs",
                headers=headers,
                json={"content": content, "encoding": "utf-8"},
            )
            if r_blob.status_code != 201:
                continue
            blobs.append({
                "path": file_path.replace("\\", "/"),
                "mode": "100644",
                "type": "blob",
                "sha": r_blob.json()["sha"],
            })

        if not blobs:
            logger.warning("auditor.github.no_blobs")
            return ""

        # Crear tree
        r = await client.post(
            f"https://api.github.com/repos/{repo}/git/trees",
            headers=headers,
            json={"base_tree": base_sha, "tree": blobs},
        )
        if r.status_code != 201:
            logger.error("auditor.github.create_tree_failed", status=r.status_code)
            return ""
        tree_sha = r.json()["sha"]

        # Crear commit
        r = await client.post(
            f"https://api.github.com/repos/{repo}/git/commits",
            headers=headers,
            json={
                "message": f"[Alfred] {plan_summary}",
                "tree": tree_sha,
                "parents": [base_sha],
            },
        )
        if r.status_code != 201:
            logger.error("auditor.github.create_commit_failed", status=r.status_code)
            return ""
        commit_sha = r.json()["sha"]

        # Actualizar branch ref
        await client.patch(
            f"https://api.github.com/repos/{repo}/git/refs/heads/{branch}",
            headers=headers,
            json={"sha": commit_sha, "force": True},
        )

        # Construir body de la PR
        files_list = "\n".join(f"- `{f}`" for f in files_written)
        findings_section = ""
        if findings:
            findings_lines = "\n".join(
                f"- [{f.severity}] `{f.file_path}:{f.line}` — {f.message}"
                for f in findings[:20]  # máximo 20 en el body
            )
            findings_section = f"\n\n## ⚠️ Findings del Auditor\n{findings_lines}"

        body = (
            f"## Generado por Alfred\n\n"
            f"**Run:** `{run_id}`\n\n"
            f"## Archivos creados\n{files_list}"
            f"{findings_section}\n\n"
            f"---\n*Este PR fue generado automáticamente por Alfred.*"
        )

        # Crear PR
        r = await client.post(
            f"https://api.github.com/repos/{repo}/pulls",
            headers=headers,
            json={
                "title": f"[Alfred] {plan_summary}",
                "body": body,
                "head": branch,
                "base": base,
            },
        )
        if r.status_code != 201:
            logger.error("auditor.github.create_pr_failed", status=r.status_code, body=r.text[:200])
            return ""

        pr_url = r.json().get("html_url", "")
        logger.info("auditor.github.pr_created", url=pr_url)
        return pr_url


# --------------------------------------------------------------------------- #
# Punto de entrada
# --------------------------------------------------------------------------- #

async def run_auditor(
    run_id: str,
    plan_summary: str,
    files_written: list[str],
) -> AuditorResult:
    """Punto de entrada del agente Auditor.

    Args:
        run_id: ID del run actual
        plan_summary: resumen del plan del Architect (para el título de la PR)
        files_written: lista de todos los archivos escritos durante el run

    Returns:
        AuditorResult con passed, feedback, findings y pr_url
    """
    log = logger.bind(agent="auditor", run_id=run_id, files=len(files_written))
    log.info("auditor.start")

    # Correr Bandit y semgrep en paralelo
    bandit_findings, semgrep_findings = await asyncio.gather(
        _run_bandit(files_written),
        _run_semgrep(files_written),
    )

    all_findings = bandit_findings + semgrep_findings
    high = [f for f in all_findings if f.severity == "HIGH"]

    log.info(
        "auditor.done",
        total=len(all_findings),
        high=len(high),
        medium=len([f for f in all_findings if f.severity == "MEDIUM"]),
        low=len([f for f in all_findings if f.severity == "LOW"]),
    )

    if high:
        feedback_lines = "\n".join(str(f) for f in high[:10])
        feedback = (
            f"El Auditor encontró {len(high)} finding(s) de severidad HIGH:\n\n"
            f"{feedback_lines}\n\n"
            f"Corrige estos problemas de seguridad antes de abrir la PR."
        )
        return AuditorResult(
            passed=False,
            feedback=feedback,
            findings=all_findings,
        )

    # Sin findings HIGH — abrir PR
    pr_url = await _create_github_pr(
        run_id=run_id,
        plan_summary=plan_summary,
        files_written=files_written,
        findings=all_findings,
    )

    medium_low = len(all_findings)
    if medium_low:
        feedback = (
            f"Audit pasó con {medium_low} finding(s) de severidad MEDIUM/LOW "
            f"(incluidos en la PR). PR: {pr_url}"
        )
    else:
        feedback = f"Audit limpio. Sin findings. PR: {pr_url}"

    return AuditorResult(
        passed=True,
        feedback=feedback,
        findings=all_findings,
        pr_url=pr_url,
    )