"""aggregate() — fleet read + dedupe on (session_id, prompt_hash)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from broker.router_train import aggregate, prompt_hash


def _write_decisions(install_root: Path, records: list[dict]) -> None:
    files_dir = install_root / ".memory" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    path = files_dir / "router_decisions.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_prompt_hash_matches_shared_convention() -> None:
    assert prompt_hash("hello") == hashlib.sha256(b"hello").hexdigest()


def test_aggregate_reads_and_dedupes_across_installs(tmp_path: Path) -> None:
    a = tmp_path / "install-a"
    b = tmp_path / "install-b"
    ph = prompt_hash("shared prompt")
    row = {"session_id": "s1", "prompt": "shared prompt", "prompt_hash": ph}
    other = {"session_id": "s2", "prompt": "distinct prompt", "prompt_hash": prompt_hash("distinct prompt")}
    _write_decisions(a, [row, other])
    _write_decisions(b, [row])

    records = aggregate([a, b])
    keys = {(r["session_id"], r["prompt_hash"]) for r in records}
    assert keys == {("s1", ph), ("s2", other["prompt_hash"])}
    assert len(records) == 2


def test_aggregate_recovers_hash_for_legacy_rows_without_prompt_hash(tmp_path: Path) -> None:
    install = tmp_path / "legacy"
    legacy_row = {"session_id": "s1", "prompt": "v1 row, no prompt_hash field"}
    _write_decisions(install, [legacy_row, legacy_row])

    records = aggregate([install])
    assert len(records) == 1


def test_aggregate_stamps_source_project(tmp_path: Path) -> None:
    install = tmp_path / "stamped"
    _write_decisions(install, [{"session_id": "s", "prompt": "p"}])
    records = aggregate([install])
    assert records[0]["source_project"] == str(install)


def test_aggregate_skips_missing_files(tmp_path: Path) -> None:
    assert aggregate([tmp_path / "no-such-install"]) == []
