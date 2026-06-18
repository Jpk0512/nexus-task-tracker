"""Tests for the PRISM CLI (prism/cli.py)."""
from __future__ import annotations

import sys

import pytest

from prism import cli


def test_status_prints_collection_counts(monkeypatch, capsys, qdrant_client):
    from prism.genome import BugGenome

    genome = BugGenome(client=qdrant_client)
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)

    class _Cfg:
        genome_path = ":memory:"

    monkeypatch.setattr("prism.config.Config.from_env", classmethod(lambda cls: _Cfg()))
    monkeypatch.setattr("prism.genome.BugGenome", lambda path: genome)
    monkeypatch.setattr(sys, "argv", ["prism", "status"])

    cli.main()
    out = capsys.readouterr().out
    assert "bug_patterns: 0 points" in out
    assert "risk_scores: 0 points" in out


def test_no_command_prints_help(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(sys, "argv", ["prism"])
    cli.main()
    out = capsys.readouterr().out
    assert "usage" in out.lower()


def test_start_subcommand_is_removed(monkeypatch):
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(sys, "argv", ["prism", "start", "."])
    with pytest.raises(SystemExit):
        cli.main()
