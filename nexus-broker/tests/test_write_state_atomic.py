"""OPT-056 — broker_state.json atomic write.

write_state previously used STATE_PATH.write_text(), which truncates-then-writes.
A parallel-Workflow reader (broker-gate.py / read_state) racing the writer could
observe a torn, half-written file and fall back to {} — erasing approved /
notepad_logged_at. write_state now writes a sibling temp file and os.replace()s it
onto the target (atomic rename), so a reader always sees the complete old or new
file. These tests pin that contract.
"""
from __future__ import annotations

import contextlib
import os
import threading
from pathlib import Path

import broker.state as state_mod
from broker.state import BrokerState, read_state, write_state


def _patch_path(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(state_mod, "STATE_PATH", path)


def test_write_state_leaves_no_temp_file(tmp_path: Path, monkeypatch) -> None:
    """After a successful write, only the target exists — the temp is renamed away."""
    target = tmp_path / "broker_state.json"
    _patch_path(monkeypatch, target)

    state: BrokerState = {"turn_id": "t1", "approved": True, "persona": "quill-py"}
    write_state(state)

    assert target.exists()
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != target.name]
    assert leftovers == [], f"temp file(s) left behind: {leftovers}"
    assert read_state() == state


def test_write_state_uses_os_replace_with_same_dir_temp(
    tmp_path: Path, monkeypatch
) -> None:
    """The atomic primitive is os.replace, fed a temp in the SAME directory.

    Same-dir matters: os.replace is only an atomic rename within one filesystem;
    a temp elsewhere could degrade to a cross-device copy and lose atomicity.
    """
    target = tmp_path / "broker_state.json"
    _patch_path(monkeypatch, target)

    seen: dict[str, Path] = {}
    real_replace = os.replace

    def spy_replace(src, dst):
        seen["src"] = Path(src)
        seen["dst"] = Path(dst)
        return real_replace(src, dst)

    monkeypatch.setattr(state_mod.os, "replace", spy_replace)
    write_state({"turn_id": "t2", "approved": True})

    assert "src" in seen, "os.replace was not used — write is not atomic"
    assert seen["dst"] == target
    assert seen["src"].parent == target.parent, "temp not in target dir (cross-device risk)"
    assert seen["src"] != target


def test_write_state_failure_leaves_no_partial(tmp_path: Path, monkeypatch) -> None:
    """If the rename fails mid-write, the target is untouched and no temp lingers.

    A reader during/after the failure sees the prior-complete file, never a torn one.
    """
    target = tmp_path / "broker_state.json"
    _patch_path(monkeypatch, target)

    prior: BrokerState = {"turn_id": "old", "approved": True, "persona": "atlas"}
    write_state(prior)

    def boom(src, dst):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(state_mod.os, "replace", boom)
    with contextlib.suppress(OSError):
        write_state({"turn_id": "new", "approved": False})

    # Old complete file survives; no temp left behind.
    assert read_state() == prior
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != target.name]
    assert leftovers == [], f"temp file(s) left behind after failed write: {leftovers}"


def test_concurrent_reader_never_sees_torn_file(tmp_path: Path, monkeypatch) -> None:
    """Hammer write_state while a reader loops read_state — no torn JSON ever.

    Without atomicity, a reader catching a half-truncated file would parse-fail.
    read_state swallows JSONDecodeError into {}, so the observable symptom of a
    torn read is a {} (or a dict missing the keys every written state carries).
    With os.replace, every read returns a fully-formed state.
    """
    target = tmp_path / "broker_state.json"
    _patch_path(monkeypatch, target)

    # A reasonably large payload widens the truncate-then-write race window that a
    # non-atomic writer would expose.
    big = "x" * 4000
    states: list[BrokerState] = [
        {"turn_id": f"turn-{i}", "approved": True, "persona": "quill-py", "filler": big}  # type: ignore[typeddict-unknown-key]
        for i in range(200)
    ]
    write_state(states[0])

    torn: list[object] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            s = read_state()
            # Every state we write carries these keys; a torn read manifests as {}
            # or a dict missing them.
            if not s or "turn_id" not in s or "persona" not in s:
                torn.append(s)

    r = threading.Thread(target=reader, daemon=True)
    r.start()
    for s in states:
        write_state(s)
    stop.set()
    r.join(timeout=5)

    assert torn == [], f"reader observed {len(torn)} torn/partial read(s): {torn[:3]}"
    final = read_state()
    assert final.get("turn_id") == "turn-199"
