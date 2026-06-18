"""Single-writer daemon — drains vault_jobs serially (plan §7.1, B3).

CLI:
    python -m broker.vault.writer [--db PATH] [--vault-root PATH] [--once]

Single-instance enforcement: fcntl.flock on ~/.cache/nexus-research/writer.lock.
A second instance refuses to start (sys.exit(1)).

Job kinds handled:
  - append_inbox  → research/40-inbox/raw/<id>.md
  - capture_idea  → research/20-workshop/brainstorms/capsules/<id>.md (kind=brainstorm)
                  → research/20-workshop/pulled/<id>.md             (kind=pulled)
  - ingest_url    → research/40-inbox/_jobs/<id>.yaml   (Phase 6 picks it up)
  - ingest_repo   → research/40-inbox/_jobs/<id>.yaml   (Phase 6 picks it up)
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from broker.vault._server import build_config
from broker.vault.jobs import claim_next_queued, mark_done, mark_failed

POLL_INTERVAL_SEC = 2.0


def _lockfile_path() -> Path:
    return Path(os.path.expanduser("~/.cache/nexus-research/writer.lock"))


def _acquire_lock() -> int | None:
    """Returns the lock fd, or None if another writer already holds it."""
    lock = _lockfile_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            return None
        raise
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd


def _slugify(s: str, max_len: int = 60) -> str:
    s = re.sub(r"[^a-zA-Z0-9_\- ]+", "", s or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    return s[:max_len] or "untitled"


def _yaml_escape(s: str) -> str:
    if not s:
        return "''"
    if any(c in s for c in ":#\n'\""):
        return "'" + s.replace("'", "''") + "'"
    return s


def _frontmatter(fm: dict[str, Any]) -> str:
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            if not v:
                lines.append(f"{k}: []")
            else:
                lines.append(f"{k}: [{', '.join(_yaml_escape(str(x)) for x in v)}]")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif v is None:
            lines.append(f"{k}: null")
        elif isinstance(v, (int, float)):
            lines.append(f"{k}: {v}")
        else:
            lines.append(f"{k}: {_yaml_escape(str(v))}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def _handle_append_inbox(vault_root: Path, job: dict[str, Any]) -> dict[str, Any]:
    payload = job["payload"]
    job_id = job["job_id"]
    inbox = vault_root / "40-inbox" / "raw"
    inbox.mkdir(parents=True, exist_ok=True)
    slug = _slugify(payload.get("title", "")) or job_id[:8]
    target = inbox / f"{job_id[:8]}-{slug}.md"
    fm = {
        "id": job_id,
        "title": payload.get("title", "(untitled)"),
        "kind": "inbox-capture",
        "domain": "general-knowledge",
        "maturity": "inbox",
        "secondary_domains": [],
        "ai-first": True,
        "source": payload.get("source", "manual"),
        "captured": _now_iso(),
        "confidence": 1,
        "tags": payload.get("tags", []) or [],
    }
    body = payload.get("body", "")
    target.write_text(_frontmatter(fm) + "\n" + body + "\n", encoding="utf-8")
    return {"path": target.relative_to(vault_root).as_posix()}


def _handle_capture_idea(vault_root: Path, job: dict[str, Any]) -> dict[str, Any]:
    payload = job["payload"]
    job_id = job["job_id"]
    kind = (payload.get("kind") or "brainstorm").lower()
    if kind == "pulled":
        out_dir = vault_root / "20-workshop" / "pulled"
        note_kind = "pulled"
    else:
        out_dir = vault_root / "20-workshop" / "brainstorms" / "capsules"
        note_kind = "brainstorm"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(payload.get("title", "")) or job_id[:8]
    target = out_dir / f"{job_id[:8]}-{slug}.md"
    fm = {
        "id": job_id,
        "title": payload.get("title", "(untitled)"),
        "kind": note_kind,
        "domain": "general-knowledge",
        "maturity": "seedling",
        "secondary_domains": [],
        "ai-first": True,
        "source": "claude",
        "captured": _now_iso(),
        "confidence": 2,
        "tags": [],
        "source_note_paths": payload.get("source_note_paths", []) or [],
    }
    body = payload.get("body", "")
    target.write_text(_frontmatter(fm) + "\n" + body + "\n", encoding="utf-8")
    return {"path": target.relative_to(vault_root).as_posix()}


def _handle_ingest_url(vault_root: Path, job: dict[str, Any]) -> dict[str, Any]:
    payload = job["payload"]
    job_id = job["job_id"]
    jobs_dir = vault_root / "40-inbox" / "_jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    descriptor = jobs_dir / f"{job_id}.yaml"
    fields = {
        "job_id": job_id,
        "kind": "ingest_url",
        "url": payload.get("url", ""),
        "domain": payload.get("domain", "general-knowledge"),
        "notes": payload.get("notes", ""),
        "enqueued_at": job["enqueued_at"],
        "phase": "5a-stub — Phase 6 will fetch + distill",
    }
    descriptor.write_text(
        "\n".join(f"{k}: {_yaml_escape(str(v))}" for k, v in fields.items()) + "\n",
        encoding="utf-8",
    )
    return {"descriptor": descriptor.relative_to(vault_root).as_posix(), "stub": True}


def _handle_ingest_repo(vault_root: Path, job: dict[str, Any]) -> dict[str, Any]:
    """Phase-6 handler: write the job descriptor, then invoke
    `bin/vault-fetch-repo.py --from-job <job_id>` to drain it.

    The descriptor is written first so `--from-job` can read it; we then
    shell out synchronously and capture the result in `result.fetch_output`.
    """
    import subprocess  # local import; avoids polluting module-level imports

    payload = job["payload"]
    job_id = job["job_id"]
    jobs_dir = vault_root / "40-inbox" / "_jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    descriptor = jobs_dir / f"{job_id}.yaml"
    fields = {
        "job_id": job_id,
        "kind": "ingest_repo",
        "repo_url_or_path": payload.get("repo_url_or_path", ""),
        "target": payload.get("target", ""),
        "enqueued_at": job["enqueued_at"],
        "phase": "6 — vault-fetch-repo orchestrator",
    }
    if payload.get("budget") is not None:
        fields["budget"] = payload["budget"]
    descriptor.write_text(
        "\n".join(f"{k}: {_yaml_escape(str(v))}" for k, v in fields.items()) + "\n",
        encoding="utf-8",
    )

    # Resolve bin/vault-fetch-repo.py relative to the repo root (vault_root is
    # research/; the orchestrator lives at <repo-root>/bin/).
    repo_root = vault_root.parent
    orchestrator = repo_root / "bin" / "vault-fetch-repo.py"
    if not orchestrator.exists():
        return {
            "descriptor": descriptor.relative_to(vault_root).as_posix(),
            "fetch_output": None,
            "error": f"orchestrator missing: {orchestrator}",
        }

    cmd = ["python3", str(orchestrator), "--from-job", job_id, "--vault-root", str(vault_root)]
    if payload.get("budget") is not None:
        cmd.extend(["--budget", str(payload["budget"])])
    if payload.get("target"):
        cmd.extend(["--target", payload["target"]])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=900,  # 15 min hard wall (binding gate is 12 min)
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "descriptor": descriptor.relative_to(vault_root).as_posix(),
            "fetch_output": None,
            "error": "timeout (>15 min) — vault-fetch-repo.py exceeded acceptance ceiling",
        }

    fetch_output: dict[str, Any] | None = None
    if result.stdout:
        try:
            import json as _json
            fetch_output = _json.loads(result.stdout.strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            pass

    return {
        "descriptor": descriptor.relative_to(vault_root).as_posix(),
        "returncode": result.returncode,
        "fetch_output": fetch_output,
        "stderr_tail": result.stderr[-2000:] if result.stderr else "",
    }


_HANDLERS = {
    "append_inbox": _handle_append_inbox,
    "capture_idea": _handle_capture_idea,
    "ingest_url": _handle_ingest_url,
    "ingest_repo": _handle_ingest_repo,
}


def _drain_once(db_path: Path, vault_root: Path) -> int:
    """Drain all currently-queued jobs. Returns count drained."""
    n = 0
    while True:
        job = claim_next_queued(db_path)
        if job is None:
            return n
        handler = _HANDLERS.get(job["kind"])
        if handler is None:
            mark_failed(db_path, job["job_id"], f"unknown_kind:{job['kind']}")
            n += 1
            continue
        try:
            result = handler(vault_root, job)
            mark_done(db_path, job["job_id"], result)
        except Exception as exc:  # noqa: BLE001
            mark_failed(db_path, job["job_id"], f"{type(exc).__name__}:{exc}")
        n += 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="nexus-vault writer daemon (B3 single-writer)")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--vault-root", type=Path, default=None)
    parser.add_argument("--once", action="store_true", help="drain queued jobs and exit")
    parser.add_argument("--skip-lock", action="store_true", help="for tests only")
    args = parser.parse_args(argv)

    config = build_config(
        access_mode="local_stdio",
        vault_root=args.vault_root,
        db_path=args.db,
    )

    lock_fd: int | None = None
    if not args.skip_lock:
        lock_fd = _acquire_lock()
        if lock_fd is None:
            print("nexus-vault writer: another instance is already running", file=sys.stderr)
            return 1

    try:
        if args.once:
            drained = _drain_once(config.db_path, config.vault_root)
            print(f"writer: drained {drained} jobs (once)")
            return 0
        while True:
            _drain_once(config.db_path, config.vault_root)
            time.sleep(POLL_INTERVAL_SEC)
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
