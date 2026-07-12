---
name: "forge-ui-pro"
dispatchable: false
description: "RETIRED persona name (R2-E2 MERGE). forge-ui-pro has been merged into forge-ui as a runtime tier parameter (tier=pro: model=opus, effort=xhigh) rather than a separate hand-authored file — see forge-ui.md. This file is denied by `persona-alias-resolver.sh` (redirects a `forge-ui-pro` dispatch to `forge-ui` at tier=pro) and the base name is absent from broker `registry.py` ALLOWED_PERSONAS. Kept only as a `dispatchable: false` tombstone."
---

`forge-ui-pro` is a RETIRED persona name (R2-E2 MERGE). Dispatch `forge-ui` with `tier=pro` instead — same file, same scope, `model: opus` / `effort: xhigh` override applied at dispatch time. A bare `forge-ui-pro` dispatch is redirected by `persona-alias-resolver.sh` to `forge-ui` at tier=pro, and the name does not appear in `ALLOWED_PERSONAS` in `broker/registry.py` as a distinct persona.
