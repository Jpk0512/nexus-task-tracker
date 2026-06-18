"""vault_get_note + vault_related impls.

vault_get_note: frontmatter + body + outbound [[wikilinks]] + inbound backlinks.
vault_related : semantic neighbours via _recall_fast using note title or TL;DR.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from broker.vault import policy as policy_mod
from broker.vault.search import _recall_fast

if TYPE_CHECKING:
    from broker.vault._server import AppConfig


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")


def _resolve_note(vault_root: Path, raw_path: str) -> Path | None:
    p = Path(raw_path)
    # Reject absolute paths — they bypass vault containment.
    if p.is_absolute():
        return None
    candidates: list[Path] = [vault_root / raw_path]
    if not raw_path.endswith(".md"):
        candidates.append(vault_root / (raw_path + ".md"))
    resolved_root = vault_root.resolve()
    for c in candidates:
        resolved = c.resolve()
        try:
            resolved.relative_to(resolved_root)
        except ValueError:
            continue
        if resolved.is_file():
            return resolved
    return None


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_block = m.group(1)
    body = text[m.end():]
    fm: dict[str, Any] = {}
    for line in fm_block.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key or value.startswith("- "):
            continue
        if value.startswith("[") and value.endswith("]"):
            items = [v.strip().strip("'\"") for v in value[1:-1].split(",")]
            fm[key] = [i for i in items if i]
        elif value.lower() in {"true", "false"}:
            fm[key] = value.lower() == "true"
        elif value.startswith(("'", '"')) and value.endswith(("'", '"')):
            fm[key] = value[1:-1]
        else:
            fm[key] = value
    return fm, body


def _outbound_links(body: str) -> list[str]:
    return sorted({m.group(1).strip() for m in WIKILINK_RE.finditer(body) if m.group(1).strip()})


def _inbound_backlinks(vault_root: Path, note_path: Path) -> list[str]:
    """Cheap walk: find any .md whose body contains [[<stem>]]."""
    stem = note_path.stem
    needle = f"[[{stem}"  # tolerates [[stem]], [[stem|alias]], [[stem#anchor]]
    backlinks: list[str] = []
    for md in vault_root.rglob("*.md"):
        if md == note_path:
            continue
        try:
            text = md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if needle in text:
            backlinks.append(md.relative_to(vault_root).as_posix())
    return sorted(backlinks)


async def vault_get_note_impl(
    *, config: AppConfig, path: str, include_body: bool
) -> dict[str, Any]:
    note = _resolve_note(config.vault_root, path)
    if note is None:
        return {"error": "not_found", "path": path}
    text = note.read_text(encoding="utf-8", errors="ignore")
    fm, body = _parse_frontmatter(text)

    # Privacy fence on the resolved note's domain.
    domain = fm.get("domain")
    fenced = policy_mod.domains_filter_includes_fenced(domain)
    if fenced is not None:
        decision = policy_mod.enforce("vault_get_note", fenced, config.access_mode)
        if decision in ("return_empty", "deny"):
            return {
                "path": note.relative_to(config.vault_root).as_posix(),
                "frontmatter": {},
                "body": "",
                "outbound_links": [],
                "backlinks": [],
                "fenced": True,
            }

    rel = note.relative_to(config.vault_root).as_posix()
    outbound = _outbound_links(body)
    backlinks = _inbound_backlinks(config.vault_root, note)
    result: dict[str, Any] = {
        "path": rel,
        "frontmatter": fm,
        "outbound_links": outbound,
        "backlinks": backlinks,
        "fenced": False,
    }
    if include_body:
        result["body"] = body
    return result


async def vault_related_impl(
    *, config: AppConfig, path: str, limit: int
) -> dict[str, Any]:
    note = _resolve_note(config.vault_root, path)
    if note is None:
        return {"error": "not_found", "path": path, "hits": []}
    text = note.read_text(encoding="utf-8", errors="ignore")
    fm, body = _parse_frontmatter(text)

    # Privacy fence — use vault_related's own tool name, not vault_get_note.
    domain = fm.get("domain")
    fenced = policy_mod.domains_filter_includes_fenced(domain)
    if fenced is not None:
        decision = policy_mod.enforce("vault_related", fenced, config.access_mode)
        if decision in ("return_empty", "deny"):
            return {"hits": [], "fenced": True}

    seed = fm.get("title") or note.stem
    snippet = body[:600].strip()
    query = f"{seed}\n{snippet}" if snippet else seed

    rel = note.relative_to(config.vault_root).as_posix()
    hits = _recall_fast(
        db_path=config.db_path,
        vault_root=config.vault_root,
        query=query,
        kind=None,
        domain=None,
        min_confidence=1,
        exclude_maturity=("archived",),
        limit=limit + 5,
    )
    hits = [h for h in hits if (h.get("ref_id") or "").split("#", 1)[0] != rel][:limit]
    # VAULT-2: post-filter fenced neighbours via canonical hit_visible predicate.
    rules = policy_mod.load_rules(config.vault_root)
    if not rules.can_read_fenced(config.access_mode):
        hits = [h for h in hits if policy_mod.hit_visible(h.get("domain"), config.access_mode, rules)]
    return {"hits": hits, "fenced": False, "count": len(hits)}
