"""Phase 5a — vault_graph_query + vault_health smoke tests."""
from __future__ import annotations

import json

import pytest
from broker.vault.graph import vault_graph_query_impl, vault_health_impl


@pytest.mark.asyncio
async def test_graph_missing_returns_error(config_local) -> None:
    result = await vault_graph_query_impl(
        config=config_local, repo_path="35-ai-techniques/does-not-exist", jq_expr=None
    )
    assert result.get("error") == "graph_not_found"


@pytest.mark.asyncio
async def test_graph_returns_json(config_local) -> None:
    repo_dir = config_local.vault_root / "35-ai-techniques" / "demo-repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    payload = {"nodes": [{"id": "A"}, {"id": "B"}], "edges": [{"from": "A", "to": "B"}]}
    (repo_dir / "knowledge-graph.json").write_text(json.dumps(payload))
    result = await vault_graph_query_impl(
        config=config_local, repo_path="35-ai-techniques/demo-repo", jq_expr=None
    )
    assert result["graph"]["nodes"][0]["id"] == "A"
    assert result["filtered"] is False


@pytest.mark.asyncio
async def test_health_synthesized(config_local) -> None:
    result = await vault_health_impl(config=config_local)
    assert result["source"] == "synthesized"
    assert "10-knowledge" in result["file_counts"]
    assert result["file_counts"]["10-knowledge"] >= 2  # golden-note + personal-secret


@pytest.mark.asyncio
async def test_health_cached_overrides(config_local) -> None:
    cached = config_local.vault_root / "_meta" / "vault-health.json"
    cached.write_text(json.dumps({"source": "cached", "ok": True}))
    result = await vault_health_impl(config=config_local)
    assert result["source"] == "cached"
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_health_b4_no_eval_data(config_local) -> None:
    """B4 default-inert: no baseline + no latest -> recall_disabled stays false."""
    result = await vault_health_impl(config=config_local)
    assert result["recall_disabled"] is False
    assert result["eval"]["state"] == "no_data"


@pytest.mark.asyncio
async def test_health_b4_ok_when_at_baseline(config_local) -> None:
    """Current top3 == baseline -> ratio 1.0 >= 0.85, recall_disabled=false."""
    meta = config_local.vault_root / "_meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "eval-baseline.json").write_text(json.dumps({
        "baseline_top3": 0.88,
        "threshold_pct": 0.85,
    }))
    (meta / "eval-run-latest.json").write_text(json.dumps({
        "aggregate": {"top3_accuracy": 0.88, "n": 100},
    }))
    result = await vault_health_impl(config=config_local)
    assert result["recall_disabled"] is False
    assert result["eval"]["state"] == "ok"
    assert result["eval"]["ratio"] == 1.0


@pytest.mark.asyncio
async def test_health_b4_auto_disable_on_regression(config_local) -> None:
    """Phase 7d binding (App C #7d): synthetic eval breach -> recall_disabled=true.

    baseline_top3=0.88, current_top3=0.50 -> ratio 0.568 < 0.85 -> degraded.
    """
    meta = config_local.vault_root / "_meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "eval-baseline.json").write_text(json.dumps({
        "baseline_top3": 0.88,
        "threshold_pct": 0.85,
    }))
    (meta / "eval-run-latest.json").write_text(json.dumps({
        "aggregate": {"top3_accuracy": 0.50, "n": 100},
    }))
    result = await vault_health_impl(config=config_local)
    assert result["recall_disabled"] is True
    assert result["eval"]["state"] == "degraded"
    assert result["eval"]["current_top3"] == 0.50
    assert result["eval"]["baseline_top3"] == 0.88
    assert result["eval"]["ratio"] < 0.85


@pytest.mark.asyncio
async def test_health_b4_recovery_flips_back(config_local) -> None:
    """Recovery (eval restored above threshold) -> recall_disabled flips back to false."""
    meta = config_local.vault_root / "_meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "eval-baseline.json").write_text(json.dumps({
        "baseline_top3": 0.88,
        "threshold_pct": 0.85,
    }))
    # Degraded state first.
    (meta / "eval-run-latest.json").write_text(json.dumps({
        "aggregate": {"top3_accuracy": 0.50, "n": 100},
    }))
    bad = await vault_health_impl(config=config_local)
    assert bad["recall_disabled"] is True
    # Recovery: write a fresh eval run above threshold.
    (meta / "eval-run-latest.json").write_text(json.dumps({
        "aggregate": {"top3_accuracy": 0.85, "n": 100},
    }))
    good = await vault_health_impl(config=config_local)
    assert good["recall_disabled"] is False
    assert good["eval"]["state"] == "ok"
