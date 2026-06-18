from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from prism.config import Config
from prism.genome import BugGenome

logger = logging.getLogger("prism")


class ClaudeCliError(RuntimeError):
    """Raised when the `claude` CLI backend fails — never swallowed silently."""


@dataclass
class PipelineResult:
    file: str
    line: int
    suspicious: bool
    escalated: bool
    severity: int
    category: str
    root_cause: str
    confidence: float
    error: str | None = None


class LLMClient(Protocol):
    async def complete(self, model: str, prompt: str, *, max_tokens: int = 512) -> str:
        """Return the model's text completion for a single-user-turn prompt."""
        ...


class AnthropicClient:
    """Default backend: Claude via the Nexus broker, using the anthropic SDK."""

    def __init__(self, api_key: str, base_url: str = "") -> None:
        import anthropic

        if not api_key:
            logger.warning(
                "AnthropicClient created without an API key — every completion will fail."
            )
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.AsyncAnthropic(**kwargs)

    async def complete(self, model: str, prompt: str, *, max_tokens: int = 512) -> str:
        msg = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text  # type: ignore[union-attr]


class ClaudeCliClient:
    """Default backend: the Claude Code CLI in non-interactive print mode.

    Uses the user's existing OAuth (Claude subscription) via ``claude -p`` — no
    API key required. Each completion spawns ``claude -p --output-format json
    --model <model>`` as an async subprocess with the prompt on stdin, then parses
    the JSON envelope's ``result`` field (the model's text). Fails LOUDLY on every
    failure path (missing CLI, non-zero exit, unparseable output, error envelope,
    timeout) — never a silent no-op.
    """

    def __init__(
        self,
        cli_path: str = "claude",
        timeout: float = 120.0,
    ) -> None:
        self._cli_path = cli_path
        self._timeout = timeout

    async def complete(self, model: str, prompt: str, *, max_tokens: int = 512) -> str:
        if shutil.which(self._cli_path) is None:
            msg = (
                f"claude CLI not found on PATH (looked for {self._cli_path!r}). "
                "Install Claude Code or set PRISM_CLAUDE_CLI to its path."
            )
            logger.warning(msg)
            raise ClaudeCliError(msg)

        argv = [
            self._cli_path,
            "-p",
            "--output-format",
            "json",
            "--model",
            model,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")),
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            msg = f"claude CLI timed out after {self._timeout}s for model {model}"
            logger.warning(msg)
            raise ClaudeCliError(msg) from exc
        except OSError as exc:
            msg = f"claude CLI could not be spawned: {exc}"
            logger.warning(msg)
            raise ClaudeCliError(msg) from exc

        if proc.returncode != 0:
            detail = stderr.decode("utf-8", "replace").strip() or "<no stderr>"
            msg = f"claude CLI exited {proc.returncode} for model {model}: {detail}"
            logger.warning(msg)
            raise ClaudeCliError(msg)

        try:
            envelope = json.loads(stdout.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            msg = f"claude CLI returned unparseable JSON for model {model}: {exc}"
            logger.warning(msg)
            raise ClaudeCliError(msg) from exc

        if envelope.get("is_error") or envelope.get("subtype") != "success":
            msg = (
                f"claude CLI reported an error envelope for model {model}: "
                f"subtype={envelope.get('subtype')!r} "
                f"api_error_status={envelope.get('api_error_status')!r}"
            )
            logger.warning(msg)
            raise ClaudeCliError(msg)

        result = envelope.get("result")
        if not isinstance(result, str):
            msg = f"claude CLI envelope had no string 'result' field for model {model}"
            logger.warning(msg)
            raise ClaudeCliError(msg)
        return result


class LMStudioClient:
    """Optional local backend. Fails LOUDLY when unreachable — never a silent no-op."""

    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base_url = base_url
        self._transport = transport

    async def complete(self, model: str, prompt: str, *, max_tokens: int = 512) -> str:
        try:
            async with httpx.AsyncClient(timeout=90.0, transport=self._transport) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": max_tokens,
                        "temperature": 0.0,
                    },
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            logger.warning("LM Studio backend unreachable at %s: %s", self._base_url, exc)
            raise


def build_client(config: Config) -> LLMClient:
    if config.backend == "lmstudio":
        return LMStudioClient(config.lmstudio_base_url)
    if config.backend == "anthropic":
        return AnthropicClient(config.anthropic_api_key, config.claude_base_url)
    return ClaudeCliClient(config.claude_cli_path, config.claude_cli_timeout)


def _parse_json(content: str) -> dict[str, Any]:
    """Tolerant JSON extraction — models sometimes wrap JSON in prose or fences."""
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```", 2)[1]
        if content.startswith("json"):
            content = content[4:]
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1:
        raise json.JSONDecodeError("no JSON object in response", content, 0)
    return json.loads(content[start : end + 1])


class AssemblyLine:
    def __init__(
        self,
        genome: BugGenome,
        config: Config | None = None,
        client: LLMClient | None = None,
    ) -> None:
        self._genome = genome
        self._cfg = config or Config.from_env()
        self._client = client or build_client(self._cfg)

    async def _triage(self, code_chunk: str) -> dict[str, Any]:
        prompt = (
            "Is this code suspicious (likely to contain a bug)? "
            'Respond with JSON only: {"suspicious": bool, "category": str, "confidence": float}\n\n'
            f"```python\n{code_chunk}\n```"
        )
        content = await self._client.complete(self._cfg.triage_model, prompt, max_tokens=128)
        return _parse_json(content)

    async def _diagnose(self, code_chunk: str) -> dict[str, Any]:
        prompt = (
            "Diagnose this code for bugs. Respond with JSON only: "
            '{"severity": int, "finding": str, "confidence": float} where severity is 1-10.\n\n'
            f"```python\n{code_chunk}\n```"
        )
        content = await self._client.complete(self._cfg.diagnose_model, prompt, max_tokens=256)
        return _parse_json(content)

    async def _root_cause(self, finding: str) -> str:
        return await self._client.complete(
            self._cfg.rca_model,
            f"Root cause analysis in 2 sentences: {finding}",
            max_tokens=256,
        )

    async def process(self, code_chunk: str, file: str = "", line: int = 0) -> PipelineResult:
        try:
            triage = await self._triage(code_chunk)
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
            json.JSONDecodeError,
            ClaudeCliError,
        ) as exc:
            logger.warning("triage failed for %s:%d: %s", file, line, exc)
            return PipelineResult(
                file=file,
                line=line,
                suspicious=False,
                escalated=False,
                severity=0,
                category="error",
                root_cause="",
                confidence=0.0,
                error=str(exc),
            )

        suspicious: bool = triage.get("suspicious", False)
        category: str = triage.get("category", "none")
        confidence: float = float(triage.get("confidence", 0.0))

        if not suspicious or confidence < 0.5:
            return PipelineResult(
                file=file,
                line=line,
                suspicious=False,
                escalated=False,
                severity=0,
                category=category,
                root_cause="",
                confidence=confidence,
            )

        try:
            diagnosis = await self._diagnose(code_chunk)
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
            json.JSONDecodeError,
            ClaudeCliError,
        ) as exc:
            logger.warning("diagnose failed for %s:%d: %s", file, line, exc)
            return PipelineResult(
                file=file,
                line=line,
                suspicious=True,
                escalated=False,
                severity=0,
                category=category,
                root_cause="",
                confidence=confidence,
                error=str(exc),
            )

        severity: int = int(diagnosis.get("severity", 0))
        finding: str = diagnosis.get("finding", "")
        diag_confidence: float = float(diagnosis.get("confidence", confidence))

        root_cause = ""
        escalated = False
        if severity >= self._cfg.severity_threshold:
            escalated = True
            try:
                root_cause = await self._root_cause(finding)
            except (
                httpx.ConnectError,
                httpx.TimeoutException,
                httpx.HTTPStatusError,
                ClaudeCliError,
            ) as exc:
                logger.warning("RCA escalation failed for %s:%d: %s", file, line, exc)
                root_cause = f"RCA unavailable: {exc}"

        result = PipelineResult(
            file=file,
            line=line,
            suspicious=True,
            escalated=escalated,
            severity=severity,
            category=category,
            root_cause=root_cause,
            confidence=diag_confidence,
        )

        await self._genome.record_finding(
            technique="pipeline",
            description=finding,
            file=file,
            line=line,
            severity=severity,
            extra={"category": category, "root_cause": root_cause},
        )

        return result
