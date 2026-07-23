/**
 * Ambient bridge exposed by the Electron shell's preload script
 * (`app/apps/desktop/src/preload.js`, via `contextBridge.exposeInMainWorld`).
 * Absent entirely in a plain browser tab — every consumer must
 * feature-detect with `window.nexusDesktop?.selectFolder` rather than assume
 * presence (FEAT-020 item 4b).
 *
 * Plain `.ts` (not `.d.ts`) — the dashboard's `.gitignore` excludes
 * `/src/**\/*.d.ts` to guard against stale tsc-emitted build artifacts,
 * which would silently drop a hand-authored declaration file under that
 * extension.
 */
export {};

declare global {
	interface Window {
		nexusDesktop?: {
			isDesktop: true;
			platform: string;
			version: string;
			/**
			 * Opens the native folder picker, starting at `defaultPath` if given.
			 * Resolves to the chosen absolute path, or `null` if cancelled.
			 */
			selectFolder: (defaultPath?: string) => Promise<string | null>;
		};
	}
}
