"""broker.capability_token — HMAC-SHA256 capability tokens (F1-02, FDEC-4).

Design: nexus-foundation/plans/artifacts/capability-token-design.md (owner-ratified).
Schema: nexus-foundation/plans/artifacts/capability-token-schema.json.

Mints ONE signed token per APPROVED node when the plan-validation gate PASSes a
plan (`mint_tokens_for_plan`) and verifies a token fail-closed (`verify_token`).
Minting is a pure PASS side-effect: `broker.plan_validation.score.score_plan` is
never imported or modified here, and this module never changes a gate's own
PASS/FAIL verdict — it only reacts to one already computed elsewhere.

C-07 (this leaf, F1-02): mint + verify + tests only. No deny-capable gate calls
`verify_token` yet — that consumer wiring is F1-03 (SHADOW) / F1-04 (CUTOVER).

stdlib-only: hmac/hashlib/json/secrets/base64 — no third-party dependency.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from broker.state import REPO_ROOT

SCHEMA_VERSION = 1
ALG = "HS256"
DEFAULT_TTL_SECONDS = 4 * 60 * 60  # 4h, aligned to PLANNING_GATE_WINDOW (design doc §3)
SKEW_SECONDS = 60

KEY_PATH: Path = REPO_ROOT / ".memory" / "files" / "broker_token_key.json"
DENYLIST_PATH: Path = REPO_ROOT / ".memory" / "files" / "token_denylist.jsonl"

_REQUIRED_CLAIMS = (
    "schema_version",
    "plan_id",
    "task_id",
    "persona",
    "write_scope",
    "tier",
    "issued_at",
    "expires_at",
    "jti",
    "kid",
    "alg",
    "sig",
)


@dataclass(frozen=True)
class VerifyResult:
    """One verify_token verdict. `ok=False` always fails closed — see `reason`
    for the specific fail-closed-matrix row (design doc §6) that fired."""

    ok: bool
    reason: str


def _resolve_ttl_seconds() -> int:
    raw = os.getenv("NEXUS_TOKEN_TTL_SECONDS")
    if not raw:
        return DEFAULT_TTL_SECONDS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_TTL_SECONDS
    return value if value > 0 else DEFAULT_TTL_SECONDS


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _parse_iso(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp claim must be a string")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _write_json_atomic(path: Path, data: dict) -> None:
    """Same atomic-rename discipline as `broker.state.write_state` (0600 —
    the key file is a secret, see design doc §2)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(json.dumps(data, indent=2))
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def load_or_create_signing_key(key_path: Path | None = None) -> tuple[str, bytes]:
    """Return (kid, key_bytes) for the CURRENT signing key, creating one
    (32 random bytes, `secrets.token_bytes(32)`) on first use if absent or
    unreadable. A corrupt key file is replaced (fail-closed for old tokens —
    they simply stop verifying, they are never silently accepted)."""
    path = key_path or KEY_PATH
    if path.exists():
        try:
            data = json.loads(path.read_text())
            current = data["current"]
            kid = current["kid"]
            key = base64.b64decode(current["key_b64"])
            if isinstance(kid, str) and kid and isinstance(key, (bytes, bytearray)):
                return kid, bytes(key)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError, OSError):
            pass
    kid = secrets.token_hex(8)
    key = secrets.token_bytes(32)
    _write_json_atomic(
        path,
        {
            "current": {"kid": kid, "key_b64": base64.b64encode(key).decode("ascii")},
            "retained": [],
        },
    )
    return kid, key


def _lookup_key(kid: str, key_path: Path | None = None) -> bytes | None:
    """Resolve `kid` against the current key or a retained (rotation-overlap)
    key. An unknown kid — not current, not retained — returns None, which
    `verify_token` treats as fail-closed `unknown-kid` (design doc §6)."""
    path = key_path or KEY_PATH
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    current = data.get("current")
    if isinstance(current, dict) and current.get("kid") == kid:
        try:
            return base64.b64decode(current["key_b64"])
        except (KeyError, ValueError, TypeError):
            return None
    for retained in data.get("retained") or []:
        if isinstance(retained, dict) and retained.get("kid") == kid:
            try:
                return base64.b64decode(retained["key_b64"])
            except (KeyError, ValueError, TypeError):
                return None
    return None


def canonical_claims(token: dict) -> bytes:
    """Deterministic serialization (sorted keys, UTF-8, no insignificant
    whitespace) of every claim EXCEPT `sig` — the exact bytes `sig` signs
    (design doc §2)."""
    payload = {k: v for k, v in token.items() if k != "sig"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sign(key: bytes, token: dict) -> str:
    mac = hmac.new(key, canonical_claims(token), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


# DEC-096: workflow-leg token authority. A capability token carries an explicit
# CLOSED `allowed_personas` set — the personas this token authorizes a dispatch
# to. A single-persona (non-Workflow) dispatch is the DEGENERATE one-element set
# [persona]; a Workflow wave carries its full declared roster. The set is part
# of the signed claims (`canonical_claims` covers every field but `sig`), so it
# cannot be widened after mint without invalidating the signature — the allow
# list is tamper-proof DATA, never a bypass.
#
# "Every member a known dispatchable persona" is validated by the caller that
# owns the persona registry (`server.nexus_validate_brief` against
# ALLOWED_PERSONAS) — this module stays stdlib-only and registry-agnostic,
# enforcing only the structural constraints that need no external roster:
# non-empty, and NO wildcard/'all' sentinel (Option C — a blanket bypass — is
# permanently rejected).
_ALLOWED_PERSONAS_WILDCARDS = frozenset({"*", "all", "any", "everyone", "wildcard"})


def _normalize_allowed_personas(
    allowed_personas: list | None, persona: str
) -> list:
    """Normalize + structurally validate the closed allowed-persona set.

    `None` ⇒ the DEGENERATE one-element set `[persona]` (single-persona
    dispatch). There is NO special-case branch downstream — the membership
    check treats a one-element set exactly like any wider set, so a degenerate
    set is exact-match-equivalent. A provided set is lower/stripped,
    de-duplicated (order-preserving), and MUST be non-empty with NO wildcard
    sentinel; either violation raises ValueError (fail-closed at mint)."""
    base = [persona] if allowed_personas is None else allowed_personas
    normalized: list = []
    for raw in base:
        name = str(raw).lower().strip()
        if not name:
            continue
        if name in _ALLOWED_PERSONAS_WILDCARDS:
            raise ValueError(
                f"allowed_personas must not contain a wildcard/'all' sentinel "
                f"(got {raw!r}) — DEC-096 permanently rejects a blanket bypass"
            )
        if name not in normalized:
            normalized.append(name)
    if not normalized:
        raise ValueError(
            "allowed_personas must be a non-empty CLOSED set (DEC-096)"
        )
    return normalized


def mint_token(
    *,
    plan_id: str,
    task_id: str,
    persona: str,
    write_scope: list | None,
    tier: str,
    allowed_personas: list | None = None,
    intent: str | None = None,
    work_type: str | None = None,
    issued_at: datetime | None = None,
    ttl_seconds: int | None = None,
    key_path: Path | None = None,
) -> dict:
    """Mint one signed capability token. Pure claim construction + signature —
    the only I/O is loading/creating the broker-held signing key.

    `allowed_personas` (DEC-096) is the CLOSED set of personas this token
    authorizes; omit it (None) for the degenerate single-persona set [persona].
    It is signed alongside every other claim."""
    kid, key = load_or_create_signing_key(key_path)
    now = issued_at or datetime.now(UTC)
    ttl = _resolve_ttl_seconds() if ttl_seconds is None else ttl_seconds
    expires = now + timedelta(seconds=ttl)

    token: dict = {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan_id,
        "task_id": task_id,
        "persona": persona,
        "allowed_personas": _normalize_allowed_personas(allowed_personas, persona),
        "write_scope": list(write_scope or []),
        "tier": tier,
        "issued_at": _iso(now),
        "expires_at": _iso(expires),
        "jti": secrets.token_hex(16),
        "kid": kid,
        "alg": ALG,
    }
    if intent is not None:
        token["intent"] = intent
    if work_type is not None:
        token["work_type"] = work_type

    token["sig"] = _sign(key, token)
    return token


def mint_tokens_for_plan(
    doc: dict,
    score_result: dict,
    plan_id: str,
    **mint_kwargs: Any,
) -> list[dict]:
    """The GOAL entry point: given a scored plan doc + the plan-validation
    gate's own verdict (`broker.plan_validation.score.score_plan(doc)`, never
    recomputed here), mint exactly one token per node when `overall_pass` is
    True, and mint NONE on FAIL. Never touches `score_result["overall_pass"]`
    — zero change to gate PASS/FAIL semantics (C-07 / TASKS.md F1-02)."""
    if not score_result.get("overall_pass"):
        return []

    tokens: list[dict] = []
    for node in doc.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = node.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            continue
        tokens.append(
            mint_token(
                plan_id=plan_id,
                task_id=node_id,
                persona=node.get("agent_persona"),
                write_scope=node.get("write_scope"),
                tier=node.get("risk_tier"),
                intent=node.get("intent"),
                work_type=node.get("work_type"),
                **mint_kwargs,
            )
        )
    return tokens


def is_jti_denylisted(jti: str, denylist_path: Path | None = None) -> bool:
    path = denylist_path or DENYLIST_PATH
    if not path.exists():
        return False
    try:
        text = path.read_text()
    except OSError:
        return False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("jti") == jti:
            return True
    return False


def deny_jti(jti: str, reason: str, denylist_path: Path | None = None) -> None:
    """Append a `{jti, revoked_at, reason}` row — the secondary,
    mid-flight-revocation layer (design doc §5). Short-TTL expiry stays
    primary; this is only for a token that must die before `expires_at`."""
    path = denylist_path or DENYLIST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"jti": jti, "revoked_at": _iso(datetime.now(UTC)), "reason": reason}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def verify_token(
    token: Any,
    *,
    now: datetime | None = None,
    key_path: Path | None = None,
    denylist_path: Path | None = None,
    skew_seconds: int = SKEW_SECONDS,
) -> VerifyResult:
    """Fail-CLOSED verification (design doc §6 matrix). Any of missing/tampered
    /expired/alg-downgrade/unknown-kid/jti-denylisted/unknown-schema_version
    denies — never a silent pass."""
    if not isinstance(token, dict):
        return VerifyResult(False, "absent")
    for claim in _REQUIRED_CLAIMS:
        if claim not in token:
            return VerifyResult(False, "absent")

    if token.get("schema_version") != SCHEMA_VERSION:
        return VerifyResult(False, "unknown-schema-version")

    if token.get("alg") != ALG:
        return VerifyResult(False, "alg-downgrade")

    sig = token.get("sig")
    if not isinstance(sig, str) or not sig:
        return VerifyResult(False, "tampered")

    kid = token.get("kid")
    key = _lookup_key(kid, key_path) if isinstance(kid, str) and kid else None
    if key is None:
        return VerifyResult(False, "unknown-kid")

    expected_sig = _sign(key, token)
    if not hmac.compare_digest(expected_sig, sig):
        return VerifyResult(False, "tampered")

    jti = token.get("jti")
    if isinstance(jti, str) and jti and is_jti_denylisted(jti, denylist_path):
        return VerifyResult(False, "jti-denylisted")

    try:
        issued_at = _parse_iso(token.get("issued_at"))
        expires_at = _parse_iso(token.get("expires_at"))
    except (ValueError, TypeError):
        return VerifyResult(False, "malformed-timestamp")

    current = now or datetime.now(UTC)
    skew = timedelta(seconds=skew_seconds)
    if current > expires_at + skew:
        return VerifyResult(False, "expired")
    if current < issued_at - skew:
        return VerifyResult(False, "not-yet-valid")

    return VerifyResult(True, "ok")
