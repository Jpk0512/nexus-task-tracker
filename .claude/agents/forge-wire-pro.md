---
name: "forge-wire-pro"
dispatchable: false
description: "RETIRED persona name (R2-E2 MERGE). forge-wire-pro has been merged into forge-wire as a runtime tier parameter (tier=pro: model=opus, effort=xhigh) rather than a separate hand-authored file — see forge-wire.md. This file is denied by `persona-alias-resolver.sh` (redirects a `forge-wire-pro` dispatch to `forge-wire` at tier=pro) and the base name is absent from broker `registry.py` ALLOWED_PERSONAS. Kept only as a `dispatchable: false` tombstone."
---

`forge-wire-pro` is a RETIRED persona name (R2-E2 MERGE). Dispatch `forge-wire` with `tier=pro` instead — same file, same scope, `model: opus` / `effort: xhigh` override applied at dispatch time. A bare `forge-wire-pro` dispatch is redirected by `persona-alias-resolver.sh` to `forge-wire` at tier=pro, and the name does not appear in `ALLOWED_PERSONAS` in `broker/registry.py` as a distinct persona.
