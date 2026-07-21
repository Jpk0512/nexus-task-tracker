"""Tests for plan-13 N21 — the daemon-side active-Workflow-to-touched-file
OWNERSHIP TRACKING store (`broker.daemon.ownership`). This is the STRUCTURAL
fix for `project.db` NATIVE-14 / NATIVE-4-2 ("lens-gate.sh false-blocks
DONE — attributes whole-repo dirty tree to the finishing agent"): the
NATIVE-14 scenario is reproduced below with a naive whole-tree attribution
helper (mirroring the buggy mechanism), then shown fixed by
`OwnershipRegistry`-backed per-Workflow attribution.

No `.claude/hooks/lens-gate.sh` import or subprocess call anywhere in this
suite — N21's write scope excludes the hook (N22, hermes, consumes this
store separately); this module's contract is verified standalone.
"""
from __future__ import annotations

import threading

import pytest
from syrupy.assertion import SnapshotAssertion

from broker.daemon.ownership import (
    OwnershipRegistry,
    UnknownWorkflow,
    handle_ownership_request,
)


def _naive_whole_tree_attribution(dirty_files: list[str], finishing_workflow: str) -> dict[str, str]:
    """Reproduces the NATIVE-14 bug on purpose: a whole-repo `git diff`
    based check has no notion of "which Workflow touched this file", so it
    attributes EVERY currently-dirty file to whichever Workflow's gate check
    happens to be running — including files a concurrent sibling Workflow
    touched. This is the exact mechanism this node structurally replaces,
    not a strawman."""
    return {f: finishing_workflow for f in dirty_files}


class TestNative14ScenarioReproducedThenFixed:
    def test_naive_whole_tree_attribution_misattributes_sibling_edit(self) -> None:
        """First, prove the bug is real: two concurrent Workflows touch
        disjoint files; a whole-tree diff at wf1's gate-check time sees BOTH
        files dirty and blames wf1 for wf2's file too."""
        dirty_files = ["src/a.py", "src/b.py"]
        attribution = _naive_whole_tree_attribution(dirty_files, finishing_workflow="wf1")
        assert attribution["src/a.py"] == "wf1"
        # The bug: wf2's own file is misattributed to wf1.
        assert attribution["src/b.py"] == "wf1"

    def test_ownership_registry_attributes_disjoint_files_correctly(self) -> None:
        """Now the fix: the same two-Workflow, disjoint-file scenario,
        through OwnershipRegistry — each Workflow sees only its own file."""
        registry = OwnershipRegistry()
        registry.register("wf1")
        registry.register("wf2")
        registry.record_touch("wf1", "src/a.py")
        registry.record_touch("wf2", "src/b.py")

        assert registry.owners_of("src/a.py") == ["wf1"]
        assert registry.owners_of("src/b.py") == ["wf2"]
        # The NATIVE-14 misattribution does NOT reproduce: wf1 never claims
        # wf2's file, in either direction.
        assert "wf2" not in registry.owners_of("src/a.py")
        assert "wf1" not in registry.owners_of("src/b.py")


class TestConcurrentWorkflowsDisjointFiles:
    def test_two_real_threads_touching_disjoint_files_stay_isolated(self) -> None:
        """The literal acceptance criterion: two CONCURRENT fixture
        Workflows (real threads, not just sequential calls) touching
        disjoint files each see only their own files attributed."""
        registry = OwnershipRegistry()
        registry.register("wf1")
        registry.register("wf2")

        wf1_files = [f"src/wf1/file_{i}.py" for i in range(50)]
        wf2_files = [f"src/wf2/file_{i}.py" for i in range(50)]
        start = threading.Barrier(2)

        def _touch_all(workflow_id: str, files: list[str]) -> None:
            start.wait()
            for f in files:
                registry.record_touch(workflow_id, f)

        t1 = threading.Thread(target=_touch_all, args=("wf1", wf1_files))
        t2 = threading.Thread(target=_touch_all, args=("wf2", wf2_files))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert not t1.is_alive() and not t2.is_alive()

        for f in wf1_files:
            assert registry.owners_of(f) == ["wf1"]
        for f in wf2_files:
            assert registry.owners_of(f) == ["wf2"]

    def test_same_file_touched_by_both_reports_both_never_drops_either(self) -> None:
        """A genuinely shared file (not the disjoint case) must report BOTH
        owners — the registry must never silently pick one, which would be
        just a differently-shaped misattribution."""
        registry = OwnershipRegistry()
        registry.register("wf1")
        registry.register("wf2")
        registry.record_touch("wf1", "src/shared.py")
        registry.record_touch("wf2", "src/shared.py")
        assert registry.owners_of("src/shared.py") == ["wf1", "wf2"]


class TestCacheOnlyRestartSemantics:
    def test_fresh_registry_falls_back_to_unattributed_never_stale(self) -> None:
        """Cache-only contract: a brand-new registry (== daemon restart)
        holds nothing, so it must answer any query as unattributed — never
        error, never a stale/wrong owner fabricated from nowhere."""
        old_registry = OwnershipRegistry()
        old_registry.register("wf1")
        old_registry.record_touch("wf1", "src/a.py")
        assert old_registry.owners_of("src/a.py") == ["wf1"]

        # Simulate a daemon restart: a fresh, unrelated registry instance.
        restarted_registry = OwnershipRegistry()
        assert restarted_registry.owners_of("src/a.py") == []
        assert restarted_registry.owners_of("never/seen.py") == []

    def test_owners_of_unknown_file_is_empty_list_not_error(self) -> None:
        registry = OwnershipRegistry()
        assert registry.owners_of("does/not/exist.py") == []


class TestLifecycle:
    def test_register_is_idempotent_does_not_wipe_existing_touches(self) -> None:
        registry = OwnershipRegistry()
        registry.register("wf1")
        registry.record_touch("wf1", "src/a.py")
        # Redelivered dispatch event — must not clear already-recorded touches.
        registry.register("wf1")
        assert registry.owners_of("src/a.py") == ["wf1"]
        assert registry.is_active("wf1") is True

    def test_record_touch_on_unregistered_workflow_raises_unknown_workflow(self) -> None:
        registry = OwnershipRegistry()
        with pytest.raises(UnknownWorkflow):
            registry.record_touch("ghost-wf", "src/a.py")

    def test_complete_expires_registration_and_all_touched_files(self) -> None:
        registry = OwnershipRegistry()
        registry.register("wf1")
        registry.record_touch("wf1", "src/a.py")
        registry.record_touch("wf1", "src/b.py")

        released = registry.complete("wf1")
        assert released == 2
        assert registry.is_active("wf1") is False
        assert registry.owners_of("src/a.py") == []
        assert registry.owners_of("src/b.py") == []

    def test_complete_on_unknown_workflow_is_idempotent_noop(self) -> None:
        registry = OwnershipRegistry()
        assert registry.complete("never-registered") == 0

    def test_complete_one_workflow_does_not_disturb_a_shared_files_other_owner(self) -> None:
        registry = OwnershipRegistry()
        registry.register("wf1")
        registry.register("wf2")
        registry.record_touch("wf1", "src/shared.py")
        registry.record_touch("wf2", "src/shared.py")

        registry.complete("wf1")
        assert registry.owners_of("src/shared.py") == ["wf2"]

    def test_record_touch_after_complete_raises_unknown_workflow(self) -> None:
        registry = OwnershipRegistry()
        registry.register("wf1")
        registry.complete("wf1")
        with pytest.raises(UnknownWorkflow):
            registry.record_touch("wf1", "src/a.py")


class TestSnapshot:
    def test_snapshot_reports_raw_counts_no_invented_estimates(self) -> None:
        registry = OwnershipRegistry()
        registry.register("wf1")
        registry.record_touch("wf1", "src/a.py")
        registry.record_touch("wf1", "src/b.py")
        registry.register("wf2")
        registry.record_touch("wf2", "src/c.py")

        snap = registry.snapshot()
        assert snap["active_workflow_count"] == 2
        assert snap["tracked_file_count"] == 3
        assert snap["workflows"]["wf1"] == ["src/a.py", "src/b.py"]
        assert snap["workflows"]["wf2"] == ["src/c.py"]


class TestOwnershipRequestDispatch:
    def test_register_touch_owners_of_round_trip_via_dispatch(
        self, snapshot: SnapshotAssertion
    ) -> None:
        registry = OwnershipRegistry()
        r1 = handle_ownership_request(registry, "ownership_register", {"workflow_id": "wf1"})
        # envelope fixtures: each ownership-registry RPC response shape,
        # reviewed via snapshot (F3-04).
        assert r1 == snapshot(name="register_envelope")

        r2 = handle_ownership_request(
            registry, "ownership_record_touch", {"workflow_id": "wf1", "file_path": "src/a.py"}
        )
        assert r2 == snapshot(name="record_touch_envelope")

        r3 = handle_ownership_request(registry, "ownership_owners_of", {"file_path": "src/a.py"})
        assert r3 == snapshot(name="owners_of_envelope")

    def test_complete_via_dispatch_releases_files(self, snapshot: SnapshotAssertion) -> None:
        registry = OwnershipRegistry()
        handle_ownership_request(registry, "ownership_register", {"workflow_id": "wf1"})
        handle_ownership_request(
            registry, "ownership_record_touch", {"workflow_id": "wf1", "file_path": "src/a.py"}
        )
        result = handle_ownership_request(registry, "ownership_complete", {"workflow_id": "wf1"})
        assert result == snapshot(name="complete_envelope")
        after = handle_ownership_request(registry, "ownership_owners_of", {"file_path": "src/a.py"})
        assert after == snapshot(name="owners_of_after_release_envelope")

    def test_snapshot_via_dispatch(self) -> None:
        registry = OwnershipRegistry()
        handle_ownership_request(registry, "ownership_register", {"workflow_id": "wf1"})
        result = handle_ownership_request(registry, "ownership_snapshot", {})
        assert result["active_workflow_count"] == 1

    def test_unknown_method_raises_value_error(self) -> None:
        registry = OwnershipRegistry()
        with pytest.raises(ValueError, match="unknown ownership method"):
            handle_ownership_request(registry, "ownership_teleport", {})

    def test_dispatch_surfaces_unknown_workflow_as_exception_not_crash(self) -> None:
        """Mirrors how a real client loop wraps this: the exception must be
        a normal, catchable error the caller turns into an RPC error
        response — never an uncaught crash of the dispatch call itself."""
        registry = OwnershipRegistry()
        with pytest.raises(UnknownWorkflow):
            handle_ownership_request(
                registry, "ownership_record_touch", {"workflow_id": "ghost", "file_path": "src/a.py"}
            )
