"""broker.adapters tests — R5-T03 N51 (plans/15-r5-dag.yaml).

Covers the node's acceptance criteria:
  1. capabilities()/schema()/invoke()/policy() answer on all three adapters.
  2. The nexus_discover/nexus_run equivalent: `broker.adapters.all_capabilities()`
     lists entries from all three adapters, and `broker.adapters.invoke()`
     drives a code search (socraticode), a vault query (vault), and a
     type-reference lookup (lsp_py) end-to-end.
  3. policy() denies a capability outside the requesting persona_contract
     (negative test).
  4. A per-call timeout on a hung CLI fixture returns a typed error packet,
     never a hang.
"""
from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

from broker import adapters
from broker.adapters import lsp_py, socraticode, vault
from broker.adapters.base import evaluate_policy

_ADAPTER_MODULES = {"vault": vault, "socraticode": socraticode, "lsp_py": lsp_py}


def _fixture_argv(tmp_path: Path, name: str, body: str) -> list[str]:
    script = tmp_path / name
    script.write_text(body)
    return [sys.executable, str(script)]


# ---------------------------------------------------------------------------
# 1. capabilities()/schema()/invoke()/policy() answer on all three adapters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_ADAPTER_MODULES))
def test_capabilities_nonempty_and_shaped(name: str) -> None:
    mod = _ADAPTER_MODULES[name]
    caps = mod.capabilities()
    assert caps, f"{name} adapter must expose at least one capability"
    for cap in caps:
        assert cap["adapter"] == name
        assert cap["id"]
        assert cap["kind"] in ("direct_import", "cli_wrapper")


@pytest.mark.parametrize("name", sorted(_ADAPTER_MODULES))
def test_schema_answers_for_every_declared_capability(name: str) -> None:
    mod = _ADAPTER_MODULES[name]
    for cap in mod.capabilities():
        s = mod.schema(cap["id"])
        assert "input" in s
        assert "output" in s


@pytest.mark.parametrize("name", sorted(_ADAPTER_MODULES))
def test_schema_unknown_capability_raises(name: str) -> None:
    mod = _ADAPTER_MODULES[name]
    with pytest.raises(KeyError):
        mod.schema("__no_such_capability__")


@pytest.mark.parametrize("name", sorted(_ADAPTER_MODULES))
def test_policy_allows_when_persona_contract_lists_capability(name: str) -> None:
    mod = _ADAPTER_MODULES[name]
    cap_id = mod.capabilities()[0]["id"]
    decision = mod.policy(
        cap_id, {}, {"role_id": "pipeline-async", "allowed_capabilities": [cap_id]}
    )
    assert decision["allowed"] is True


# ---------------------------------------------------------------------------
# 2. nexus_discover/nexus_run equivalent: aggregate discovery + 3 real invokes
# ---------------------------------------------------------------------------


def test_all_capabilities_lists_every_adapter() -> None:
    caps = adapters.all_capabilities()
    seen_adapters = {c["adapter"] for c in caps}
    assert seen_adapters == {"vault", "socraticode", "lsp_py"}


def test_vault_query_end_to_end(tmp_path: Path) -> None:
    domain_dir = tmp_path / "10-knowledge" / "testdomain"
    domain_dir.mkdir(parents=True)
    (domain_dir / "note.md").write_text("# hello\n")

    packet = adapters.invoke(
        "vault",
        "vault_query",
        {
            "query": None,
            "limit": 5,
            "vault_root": str(tmp_path),
            "db_path": str(tmp_path / "project.db"),
        },
    )
    assert packet["ok"] is True
    assert packet["result"]["count"] == 1
    assert packet["result"]["hits"][0]["path"] == "10-knowledge/testdomain/note.md"


def test_socraticode_code_search_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    argv = _fixture_argv(
        tmp_path,
        "fake_socraticode.py",
        "import sys, json\n"
        "payload = json.loads(sys.stdin.read() or '{}')\n"
        "print(json.dumps({'hits': [{'symbol': payload.get('query', '')}]}))\n",
    )
    monkeypatch.setenv("NEXUS_SOCRATICODE_CMD", shlex.join(argv))

    packet = adapters.invoke("socraticode", "code_search", {"query": "codebase_symbol"})
    assert packet["ok"] is True
    assert packet["result"]["hits"][0]["symbol"] == "codebase_symbol"


def test_lsp_py_type_reference_lookup_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _fixture_argv(
        tmp_path,
        "fake_lsp_py.py",
        "import sys, json\n"
        "payload = json.loads(sys.stdin.read() or '{}')\n"
        "print(json.dumps({'references': [{'file': 'x.py', 'symbol': payload.get('symbol')}]}))\n",
    )
    monkeypatch.setenv("NEXUS_LSP_PY_CMD", shlex.join(argv))

    packet = adapters.invoke("lsp_py", "type_reference_lookup", {"symbol": "Foo"})
    assert packet["ok"] is True
    assert packet["result"]["references"][0]["symbol"] == "Foo"


# ---------------------------------------------------------------------------
# 3. policy denies a capability outside the persona contract (negative test)
# ---------------------------------------------------------------------------


def test_policy_denies_capability_outside_persona_contract() -> None:
    decision = vault.policy(
        "vault_query", {}, {"role_id": "quill-py", "allowed_capabilities": ["vault_health"]}
    )
    assert decision["allowed"] is False
    assert "vault_query" in decision["reason"]


def test_policy_denies_when_persona_contract_declares_nothing() -> None:
    decision = socraticode.policy("code_search", {}, {})
    assert decision["allowed"] is False


def test_policy_denies_when_adapter_disabled_by_project_profile() -> None:
    decision = evaluate_policy(
        adapter="lsp_py",
        capability_id="type_reference_lookup",
        project_profile={"disabled_adapters": ["lsp_py"]},
        persona_contract={
            "role_id": "atlas",
            "allowed_capabilities": ["type_reference_lookup"],
        },
    )
    assert decision["allowed"] is False


def test_package_policy_denies_unknown_adapter() -> None:
    decision = adapters.policy("__no_such_adapter__", "x", {}, {"allowed_capabilities": ["x"]})
    assert decision["allowed"] is False


# ---------------------------------------------------------------------------
# 4. per-call timeout returns a typed error packet on a hung adapter fixture
# ---------------------------------------------------------------------------


def test_socraticode_invoke_times_out_on_hung_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _fixture_argv(tmp_path, "hung_socraticode.py", "import time\ntime.sleep(5)\n")
    monkeypatch.setenv("NEXUS_SOCRATICODE_CMD", shlex.join(argv))

    packet = adapters.invoke("socraticode", "code_search", {"query": "x"}, timeout_s=0.3)
    assert packet["ok"] is False
    assert packet["error_type"] == "timeout"


def test_lsp_py_invoke_degrades_on_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXUS_LSP_PY_CMD", "__definitely_not_a_real_binary__")
    packet = lsp_py.invoke("type_reference_lookup", {"symbol": "x"}, timeout_s=1.0)
    assert packet["ok"] is False
    assert packet["error_type"] == "unavailable"


def test_invoke_unknown_capability_id_returns_typed_error() -> None:
    packet = vault.invoke("__no_such_capability__", {})
    assert packet["ok"] is False
    assert packet["error_type"] == "unknown_capability"


def test_vault_invoke_times_out_on_wedged_read(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _hang(**_kwargs: object) -> None:
        import asyncio

        await asyncio.sleep(5)

    monkeypatch.setattr(vault, "vault_health_impl", _hang)
    packet = vault.invoke("vault_health", {}, timeout_s=0.2)
    assert packet["ok"] is False
    assert packet["error_type"] == "timeout"
