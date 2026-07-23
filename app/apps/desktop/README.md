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

## Window behavior

- Minimum size is enforced at 1024×680 (`minWidth`/`minHeight`); drag-to-resize is
  Electron's `BrowserWindow` default and is never disabled.
- Size and position persist across restarts. State is written to
  `window-state.json` under Electron's per-OS `userData` directory (e.g.
  `~/Library/Application Support/Nexus/window-state.json` on macOS) — on
  resize/move (debounced ~500ms) and on window close. No extra dependency: it's
  a small JSON file read/written with `node:fs`.
- If the saved position no longer overlaps any currently connected display (e.g.
  an external monitor was unplugged since the last run), the saved size is kept
  but the position is dropped in favor of the platform default placement — this
  prevents the window from reopening off-screen with no way to drag it back.
- Maximized state is remembered too: the window re-maximizes on launch, and the
  underlying (non-maximized) size/position is what gets saved, so un-maximizing
  later doesn't leave you with the maximized dimensions as the "restored" size.

## IPC

`window.nexusDesktop` (exposed via `contextBridge`, safe under
`contextIsolation`/`sandbox`) additionally offers:

- `selectFolder(defaultPath?: string): Promise<string | null>` — opens the
  native folder picker (`openDirectory` + `createDirectory`), starting at
  `defaultPath` if given (falls back to the user's home directory), and
  resolves to the chosen absolute path or `null` if the dialog is cancelled.
  `defaultPath` must be a string or `undefined`; anything else rejects. No
  other IPC channel is exposed — this stays a minimal, single-purpose bridge,
  not a generic `invoke` passthrough.

## Notes

- Renamed from the old mimrai desktop skeleton.
- External links open in the system browser.
- Menu **Go** shortcuts: Home / Site Docs / Agent Config / Chat.
