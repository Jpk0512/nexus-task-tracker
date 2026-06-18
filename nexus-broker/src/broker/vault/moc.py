"""vault_moc — read a zone's _MOC.md and split into {curated, recent}.

The 'recent' section is delimited by `<!-- BEGIN AUTO -->` / `<!-- END AUTO -->`.
Everything before BEGIN AUTO is treated as curated (human-authored). If the
markers are absent, the whole file is treated as curated and 'recent' is empty.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from broker.vault._server import AppConfig


_BEGIN = "<!-- BEGIN AUTO -->"
_END = "<!-- END AUTO -->"


async def vault_moc_impl(*, config: AppConfig, zone: str) -> dict[str, Any]:
    moc_path = (config.vault_root / zone / "_MOC.md").resolve()
    try:
        moc_path.relative_to(config.vault_root.resolve())
    except ValueError:
        return {"error": "escapes_vault_root", "zone": zone}
    if not moc_path.is_file():
        return {"error": "moc_not_found", "zone": zone, "path": str(moc_path)}
    text = moc_path.read_text(encoding="utf-8", errors="ignore")
    if _BEGIN in text:
        head, _, tail = text.partition(_BEGIN)
        recent_block, _, _ = tail.partition(_END)
        curated = head.rstrip()
        recent = recent_block.strip()
    else:
        curated = text.rstrip()
        recent = ""
    return {
        "zone": zone,
        "path": moc_path.relative_to(config.vault_root).as_posix(),
        "curated": curated,
        "recent": recent,
    }
