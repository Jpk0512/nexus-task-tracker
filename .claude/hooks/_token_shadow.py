#!/usr/bin/env python3
"""_token_shadow.py — F1-03 dual-parse SHADOW helper (broker-gate.py only).

nexus-foundation/plans/wave-1.md track (a): F1-02 (`broker.capability_token`,
nexus-broker/src/broker/capability_token.py) mints a signed HMAC capability
token per approved node when the plan-validation gate PASSes. F1-03 is a
SHADOW step only: the per-dispatch validate/notepad ritual (broker-gate.py's
own state/notepad/planning-gate checks) stays the SOLE authority for every
gate verdict. This module's ONLY job is to ALSO verify a capability token
(when one is present) and log every agreement/divergence between the token
verdict and the ritual verdict to `.memory/token_shadow.jsonl` — so F1-04 can
measure divergence count before cutover. Every function here is advisory /
best-effort: `log_token_shadow_event` never raises, and no caller may branch
its own DENY/ALLOW decision on this module's return value (mirrors
`_envelope_shadow.py`'s F1-07 contract exactly — read that file first if this
one is unclear).

HAND-MIRRORED, NOT IMPORTED — ZERO broker imports. Hooks run under ambient
python3 (3.9, stdlib only); `nexus-broker/src/broker/capability_token.py`
itself imports `datetime.UTC` (3.11-only) and lives in a uv-managed venv, so
it can never be imported from here. `verify_token` below reproduces
capability_token.verify_token's fail-closed matrix CLAIM-FOR-CLAIM: the same
canonical payload serialization (`canonical_claims`), the same 60s clock-skew
tolerance, the same kid -> key lookup (current + retained, unknown-kid fails
closed), and the same jti deny-list check. If capability_token.py's verify
logic ever changes, this twin must be updated by hand in the same change-set
— there is no shared import to keep them in sync automatically.

TOKEN DISCOVERY CHANNEL (documented per TASKS.md F1-03 brief): a
`capability_token` field on the ALREADY-read `broker_state.json` dict.
broker-gate.py's stage 1b already reads this file once per dispatch (P2-10);
riding that existing read is the least-invasive channel — no new file, no new
I/O, no change to stage 1b's own fail-closed contract. F1-02 does not yet
write this field into broker_state.json at mint time (that wiring is F1-04's
job); until then `extract_token` returns None for every real dispatch and
`verify_token` resolves that to reason='absent' — an EXPECTED divergence
class pre-cutover, not a bug.

3.9 IMPORT-SAFETY: the package twin runs this file un-shimmed under ambient
python3 — no `datetime.UTC`, no def-time `X | None`, no `match`/`case`.
`from __future__ import annotations` keeps PEP-604 unions safe in signatures;
`timezone.utc` call sites keep their explicit `# noqa: UP017` (mirrors every
other hook in this directory).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Mirrors broker.capability_token exactly (design doc §2/§6). Hardcoded here
# rather than imported — see module docstring's ZERO-broker-imports rule.
SCHEMA_VERSION = 1
ALG = "HS256"
SKEW_SECONDS = 60

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

# Event-type vocabulary written to the shadow log's `verdict` field.
EVENT_AGREE = "agree"
EVENT_DIVERGE = "diverge"

# Truncation is not needed here (tokens are small, fixed-shape claims) unlike
# _envelope_shadow.py's raw-agent-return snippet — every field logged below is
# already bounded.


# ---------------------------------------------------------------------------
# Path resolution (mirrors broker-gate.py's own _repo_root / env-override
# pattern; kept as an independent copy so this module has ZERO imports from
# broker-gate.py or anywhere under nexus-broker/).
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    env = os.environ.get("_HOOK_REPO_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".memory").is_dir():
            return candidate
    return here.parent.parent.parent


def _default_key_path() -> Path:
    override = os.environ.get("_HOOK_TOKEN_KEY_PATH")
    if override:
        return Path(override)
    return _repo_root() / ".memory" / "files" / "broker_token_key.json"


def _default_denylist_path() -> Path:
    override = os.environ.get("_HOOK_TOKEN_DENYLIST_PATH")
    if override:
        return Path(override)
    return _repo_root() / ".memory" / "files" / "token_denylist.jsonl"


def _default_shadow_log_path() -> Path:
    override = os.environ.get("_HOOK_TOKEN_SHADOW_LOG_PATH")
    if override:
        return Path(override)
    return _repo_root() / ".memory" / "token_shadow.jsonl"


# ---------------------------------------------------------------------------
# Verify semantics — mirrors broker.capability_token EXACTLY.
# ---------------------------------------------------------------------------

def canonical_claims(token: dict) -> bytes:
    """Deterministic serialization (sorted keys, UTF-8, no insignificant
    whitespace) of every claim EXCEPT `sig` — byte-identical to
    capability_token.canonical_claims. The exact bytes `sig` signs."""
    payload = {k: v for k, v in token.items() if k != "sig"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sign(key: bytes, token: dict) -> str:
    mac = hmac.new(key, canonical_claims(token), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def _lookup_key(kid: str, key_path: Path | None = None) -> bytes | None:
    """Resolve `kid` against the current key or a retained (rotation-overlap)
    key — mirrors capability_token._lookup_key. Unknown kid -> None -> caller
    fails closed with reason='unknown-kid'."""
    path = key_path or _default_key_path()
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


def _is_jti_denylisted(jti: str, denylist_path: Path | None = None) -> bool:
    path = denylist_path or _default_denylist_path()
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


def _parse_iso(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp claim must be a string")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # noqa: UP017
    return dt


def verify_token(
    token: object,
    *,
    now: datetime | None = None,
    key_path: Path | None = None,
    denylist_path: Path | None = None,
    skew_seconds: int = SKEW_SECONDS,
) -> tuple[bool, str]:
    """Fail-CLOSED verification, hand-mirrored claim-for-claim from
    capability_token.verify_token's matrix. Returns (ok, reason); `reason` is
    one of: absent / unknown-schema-version / alg-downgrade / tampered /
    unknown-kid / jti-denylisted / malformed-timestamp / expired /
    not-yet-valid / ok. Any of these EXCEPT 'ok' denies — never a silent pass.
    """
    if not isinstance(token, dict):
        return False, "absent"
    for claim in _REQUIRED_CLAIMS:
        if claim not in token:
            return False, "absent"

    if token.get("schema_version") != SCHEMA_VERSION:
        return False, "unknown-schema-version"

    if token.get("alg") != ALG:
        return False, "alg-downgrade"

    sig = token.get("sig")
    if not isinstance(sig, str) or not sig:
        return False, "tampered"

    kid = token.get("kid")
    key = _lookup_key(kid, key_path) if isinstance(kid, str) and kid else None
    if key is None:
        return False, "unknown-kid"

    expected_sig = _sign(key, token)
    if not hmac.compare_digest(expected_sig, sig):
        return False, "tampered"

    jti = token.get("jti")
    if isinstance(jti, str) and jti and _is_jti_denylisted(jti, denylist_path):
        return False, "jti-denylisted"

    try:
        issued_at = _parse_iso(token.get("issued_at"))
        expires_at = _parse_iso(token.get("expires_at"))
    except (ValueError, TypeError):
        return False, "malformed-timestamp"

    current = now or datetime.now(timezone.utc)  # noqa: UP017
    skew = timedelta(seconds=skew_seconds)
    if current > expires_at + skew:
        return False, "expired"
    if current < issued_at - skew:
        return False, "not-yet-valid"

    return True, "ok"


def extract_token(state: object) -> object:
    """Token discovery: `state["capability_token"]` on the already-read
    broker_state.json dict (see module docstring). Returns None for any
    non-dict `state` or a missing field — never raises."""
    if not isinstance(state, dict):
        return None
    return state.get("capability_token")


# ---------------------------------------------------------------------------
# Shadow logging
# ---------------------------------------------------------------------------

def log_token_shadow_event(
    *,
    gate: str,
    persona: str,
    token: object,
    ritual_pass: bool,
    now: datetime | None = None,
    key_path: Path | None = None,
    denylist_path: Path | None = None,
    log_path: str | Path | None = None,
) -> dict:
    """Compare the token verdict against `ritual_pass` — the value the CALLER
    already decided via its own ritual logic, which remains the AUTHORITATIVE
    result for every gate verdict (this function's return value must never be
    branched on for a deny/allow decision). Appends exactly ONE JSONL row to
    the shadow log and returns it. Carries jti/persona/gate/agree|diverge/
    reason (TASKS.md F1-03 acceptance).

    NEVER raises: any failure (bad log_path, disk error, malformed token,
    etc.) is swallowed and a best-effort {'gate': gate, 'verdict': 'error'}
    dict is returned instead — this module's entire contract is "advisory,
    never changes caller behavior" (C-06).
    """
    try:
        token_ok, token_reason = verify_token(
            token, now=now, key_path=key_path, denylist_path=denylist_path
        )
        jti = token.get("jti") if isinstance(token, dict) else None
        ritual_pass_bool = bool(ritual_pass)
        verdict = EVENT_AGREE if token_ok == ritual_pass_bool else EVENT_DIVERGE

        row = {
            "ts": time.time(),
            "gate": gate,
            "persona": persona,
            "jti": jti,
            "token_present": token is not None,
            "token_valid": token_ok,
            "token_reason": token_reason,
            "ritual_pass": ritual_pass_bool,
            "verdict": verdict,
            "reason": (
                "token and ritual verdicts agree"
                if verdict == EVENT_AGREE
                else (
                    f"token_valid={token_ok} ({token_reason}) vs "
                    f"ritual_pass={ritual_pass_bool}"
                )
            ),
        }
        path = Path(log_path) if log_path else _default_shadow_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return row
    except Exception:
        return {"gate": gate, "verdict": "error"}
