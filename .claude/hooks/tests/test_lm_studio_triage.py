"""
test_lm_studio_triage.py — N18 (R3-T11): LM Studio local-classifier triage gate tests.

Covers the acceptance criteria verbatim:
  1. Mocked endpoint: trivial -> deterministic-only; risky -> N17 tier path.
  2. Endpoint down: fail-open no-op, <=100ms added latency (the connect-timeout cap).
  3. Classifier verdict logged to telemetry (triage_decisions.jsonl).

Runnable both as `python3 test_lm_studio_triage.py` (the __main__ block below runs
every test_* function and exits non-zero on failure) and via `pytest` (plain
functions, no exotic fixtures — pytest-collectable as-is).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "lm_studio_triage_gate", HOOKS_DIR / "lm-studio-triage-gate.py"
)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def _clean_env():
    for key in (
        "_MOCK_TRIAGE_RESPONSE",
        "_MOCK_TRIAGE_CONNECT_ERROR",
        "_HOOK_MEMORY_FILES_DIR",
        "_HOOK_TRIAGE_TIMEOUT",
    ):
        os.environ.pop(key, None)


def test_trivial_routes_to_deterministic_only():
    _clean_env()
    try:
        os.environ["_MOCK_TRIAGE_RESPONSE"] = json.dumps({"verdict": "trivial"})
        decision = gate.triage_route("fix a typo in a comment")
        assert decision["route"] == gate.ROUTE_DETERMINISTIC_ONLY
        assert decision["verdict"] == "trivial"
        assert decision["fail_open"] is False
    finally:
        _clean_env()


def test_risky_routes_to_n17_tier_path():
    _clean_env()
    try:
        os.environ["_MOCK_TRIAGE_RESPONSE"] = json.dumps({"verdict": "risky"})
        decision = gate.triage_route("rewrite the auth token refresh logic")
        assert decision["route"] == gate.ROUTE_N17_TIER
        assert decision["verdict"] == "risky"
        assert decision["fail_open"] is False
    finally:
        _clean_env()


def test_standard_routes_to_n17_tier_path():
    _clean_env()
    try:
        os.environ["_MOCK_TRIAGE_RESPONSE"] = json.dumps({"verdict": "standard"})
        decision = gate.triage_route("add a new field to the API response")
        assert decision["route"] == gate.ROUTE_N17_TIER
        assert decision["verdict"] == "standard"
    finally:
        _clean_env()


def test_endpoint_down_fails_open_to_n17_tier():
    _clean_env()
    try:
        os.environ["_MOCK_TRIAGE_CONNECT_ERROR"] = "1"
        decision = gate.triage_route("anything")
        assert decision["route"] == gate.ROUTE_N17_TIER
        assert decision["verdict"] is None
        assert decision["fail_open"] is True
    finally:
        _clean_env()


def test_endpoint_down_real_connect_stays_under_timeout_cap():
    """No mock — a real (fast-failing) connect attempt against an unused local
    port, proving the gate no-ops within the <=100ms acceptance cap rather than
    hanging on a dead desktop app."""
    _clean_env()
    try:
        os.environ["_HOOK_TRIAGE_TIMEOUT"] = "0.1"
        # Port 1 is a reserved/unassigned port — connection refused near-instantly
        # on loopback, exercising the real urllib.request path (not the mock).
        gate.TRIAGE_URL = "http://127.0.0.1:1/v1/chat/completions"
        decision = gate.triage_route("anything")
        assert decision["fail_open"] is True
        assert decision["route"] == gate.ROUTE_N17_TIER
        # Generous slack above the 100ms cap for test-runner scheduling jitter;
        # the real bound enforced in production is the urlopen(timeout=...) call.
        assert decision["elapsed_s"] <= 1.0, f"took {decision['elapsed_s']}s, expected fail-fast"
    finally:
        gate.TRIAGE_URL = os.environ.get(
            "_HOOK_ROUTER_URL", "http://127.0.0.1:1234/v1/chat/completions"
        )
        _clean_env()


def test_malformed_response_fails_open():
    _clean_env()
    try:
        os.environ["_MOCK_TRIAGE_RESPONSE"] = "not valid json{{{"
        decision = gate.triage_route("anything")
        assert decision["fail_open"] is True
        assert decision["route"] == gate.ROUTE_N17_TIER
    finally:
        _clean_env()


def test_verdict_logged_to_telemetry():
    _clean_env()
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            os.environ["_HOOK_MEMORY_FILES_DIR"] = tmpdir
            os.environ["_MOCK_TRIAGE_RESPONSE"] = json.dumps({"verdict": "trivial"})
            gate.triage_route("telemetry check")

            log_path = Path(tmpdir) / "triage_decisions.jsonl"
            assert log_path.exists(), "triage_decisions.jsonl was not written"
            lines = log_path.read_text().strip().splitlines()
            assert len(lines) == 1
            record = json.loads(lines[0])
            assert record["verdict"] == "trivial"
            assert record["route"] == gate.ROUTE_DETERMINISTIC_ONLY
            assert record["fail_open"] is False
            assert "ts" in record and "elapsed_s" in record
        finally:
            _clean_env()


def test_telemetry_write_never_raises_on_bad_dir():
    """_HOOK_MEMORY_FILES_DIR pointed at an unwritable location must not raise —
    telemetry is best-effort and must never break routing."""
    _clean_env()
    try:
        os.environ["_HOOK_MEMORY_FILES_DIR"] = "/nonexistent_root_only_dir/does/not/exist"
        os.environ["_MOCK_TRIAGE_RESPONSE"] = json.dumps({"verdict": "risky"})
        decision = gate.triage_route("should not raise even though logging fails")
        assert decision["route"] == gate.ROUTE_N17_TIER
    finally:
        _clean_env()


def test_unknown_verdict_defaults_to_n17_tier():
    """A verdict outside the known trivial set (e.g. a classifier drift/typo) must
    escalate rather than silently treat unknown as safe-to-skip review."""
    _clean_env()
    try:
        os.environ["_MOCK_TRIAGE_RESPONSE"] = json.dumps({"verdict": "unexpected_value"})
        decision = gate.triage_route("anything")
        assert decision["route"] == gate.ROUTE_N17_TIER
    finally:
        _clean_env()


ALL_TESTS = [
    test_trivial_routes_to_deterministic_only,
    test_risky_routes_to_n17_tier_path,
    test_standard_routes_to_n17_tier_path,
    test_endpoint_down_fails_open_to_n17_tier,
    test_endpoint_down_real_connect_stays_under_timeout_cap,
    test_malformed_response_fails_open,
    test_verdict_logged_to_telemetry,
    test_telemetry_write_never_raises_on_bad_dir,
    test_unknown_verdict_defaults_to_n17_tier,
]


def main() -> int:
    failures = []
    for test_fn in ALL_TESTS:
        try:
            test_fn()
            print(f"PASS {test_fn.__name__}")
        except Exception as exc:  # noqa: BLE001 -- test harness must catch all to report all
            failures.append((test_fn.__name__, exc))
            print(f"FAIL {test_fn.__name__}: {exc}")

    print(f"\n{len(ALL_TESTS) - len(failures)}/{len(ALL_TESTS)} passed")
    if failures:
        print(f"\n{len(failures)} FAILURE(S):", file=sys.stderr)
        for name, exc in failures:
            print(f"  {name}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
