from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass

logger = logging.getLogger("prism")


@dataclass
class Config:
    backend: str
    anthropic_api_key: str
    claude_base_url: str
    triage_model: str
    diagnose_model: str
    rca_model: str
    genome_path: str
    severity_threshold: int
    lmstudio_base_url: str
    claude_cli_path: str
    claude_cli_timeout: float

    @classmethod
    def from_env(cls) -> Config:
        cfg = cls(
            backend=os.getenv("PRISM_BACKEND", "claude_cli"),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            claude_base_url=os.getenv("AI_API_BASE_URL", "").rstrip("/").removesuffix("/v1"),
            triage_model=os.getenv("PRISM_TRIAGE_MODEL", "claude-haiku-4-5"),
            diagnose_model=os.getenv("PRISM_DIAGNOSE_MODEL", "claude-haiku-4-5"),
            rca_model=os.getenv("PRISM_RCA_MODEL") or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            genome_path=os.getenv("PRISM_GENOME_PATH", ".prism/genome"),
            severity_threshold=int(os.getenv("PRISM_SEVERITY_THRESHOLD", "5")),
            lmstudio_base_url=os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1"),
            claude_cli_path=os.getenv("PRISM_CLAUDE_CLI", "claude"),
            claude_cli_timeout=float(os.getenv("PRISM_CLAUDE_CLI_TIMEOUT", "120")),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.backend == "claude_cli" and shutil.which(self.claude_cli_path) is None:
            logger.warning(
                "PRISM claude_cli backend selected but %r is not on PATH — "
                "triage/diagnose/RCA will fail. Install Claude Code (which uses your "
                "existing OAuth, no API key needed) or set PRISM_CLAUDE_CLI to its path.",
                self.claude_cli_path,
            )
        elif self.backend == "anthropic" and not self.anthropic_api_key:
            logger.warning(
                "PRISM anthropic backend selected but ANTHROPIC_API_KEY is empty — "
                "triage/diagnose/RCA will fail. Set ANTHROPIC_API_KEY in the environment."
            )
