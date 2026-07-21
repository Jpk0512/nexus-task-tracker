# Nexus Desktop (Electron)

Thin native shell around the local Nexus dashboard.

## Prerequisites

- API + dashboard running (Docker compose local stack)
- Dashboard reachable at `http://localhost:5179`

## Dev

```bash
cd app/apps/desktop
npm install
npm start
```

Optional env:

- `NEXUS_DESKTOP_URL` — default `http://localhost:5179`
- `NEXUS_DESKTOP_PATH` — default `/team/local-dev`

## Package / installers

```bash
npm run package   # unpacked .app under out/
npm run make      # zip (+ dmg when maker-dmg is installed)
```

Open the packaged app:

```bash
open "out/Nexus-darwin-arm64/Nexus.app"
```

## Notes

- Renamed from the old mimrai desktop skeleton.
- External links open in the system browser.
- Menu **Go** shortcuts: Home / Site Docs / Agent Config / Chat.
