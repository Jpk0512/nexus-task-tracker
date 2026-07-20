/**
 * Home dashboard configuration model (codex amendment #3).
 *
 * Persistence precedence:
 *   1. URL `?config=<base64-json>` — wins for shareable / link-anchored layouts
 *      (think "here's how I have my home set up").
 *   2. `localStorage["nexus.home.config"]` — the user's saved layout.
 *   3. Built-in defaults below — first paint for a brand-new user.
 *
 * The shape is intentionally narrow: one entry per card with `id`, `enabled`,
 * and an implicit order = array index. No nested groups, no per-card prefs.
 * That keeps the config small (so URL-encoding stays cheap), forward-
 * compatible (new cards default to off), and trivial to migrate (just
 * upsert + filter on read).
 */

export type HomeCardId =
	| "greeting"
	| "do-now"
	| "agenda"
	| "up-next"
	| "active-projects"
	| "stale-digest"
	| "eod-recap"
	| "activity-feed";

export interface HomeCardConfig {
	id: HomeCardId;
	enabled: boolean;
}

export interface HomeConfig {
	cards: HomeCardConfig[];
}

/**
 * Default surface: the four high-signal cards visible above the fold, with
 * the three optional cards hidden until the user toggles them on. Order
 * mirrors the designer-meta §5 sketch.
 */
export const DEFAULT_HOME_CONFIG: HomeConfig = {
	cards: [
		{ id: "greeting", enabled: true },
		{ id: "do-now", enabled: true },
		{ id: "agenda", enabled: true },
		{ id: "up-next", enabled: true },
		{ id: "active-projects", enabled: true },
		{ id: "stale-digest", enabled: false },
		{ id: "eod-recap", enabled: false },
		{ id: "activity-feed", enabled: false },
	],
};

export const HOME_CARD_LABELS: Record<HomeCardId, string> = {
	greeting: "Greeting",
	"do-now": "Do now",
	agenda: "My agenda",
	"up-next": "Up next",
	"active-projects": "Active projects",
	"stale-digest": "Stale commitments",
	"eod-recap": "End-of-day recap",
	"activity-feed": "Activity feed",
};

export const HOME_CARD_DESCRIPTIONS: Record<HomeCardId, string> = {
	greeting: "Time-of-day greeting and day brief.",
	"do-now": "Attention Graph top 5 — overdue, today, in progress.",
	agenda: "Tasks due today or overdue, assigned to you.",
	"up-next": "Tasks currently in progress or in review.",
	"active-projects": "Top active projects with progress bars.",
	"stale-digest": "Tasks with no status change in 7+ days.",
	"eod-recap": "Granola-style end-of-day deltas.",
	"activity-feed": "Last 10 events across your team.",
};

const STORAGE_KEY = "nexus.home.config";

/**
 * Merge a (potentially partial / outdated) saved config with the defaults
 * so missing cards default to off-and-appended. Idempotent.
 */
function reconcile(saved: Partial<HomeConfig> | null): HomeConfig {
	if (!saved?.cards || !Array.isArray(saved.cards)) return DEFAULT_HOME_CONFIG;
	const seen = new Set<HomeCardId>();
	const cards: HomeCardConfig[] = [];
	for (const c of saved.cards) {
		if (!c || typeof c.id !== "string") continue;
		if (seen.has(c.id as HomeCardId)) continue;
		// reject ids we don't know about (forward-compat: old config from a
		// future version with a card we no longer ship).
		if (!(c.id in HOME_CARD_LABELS)) continue;
		seen.add(c.id as HomeCardId);
		cards.push({ id: c.id as HomeCardId, enabled: c.enabled !== false });
	}
	// Append any default cards that weren't in the saved list (new cards
	// added since the user last saved), defaulted to disabled.
	for (const def of DEFAULT_HOME_CONFIG.cards) {
		if (seen.has(def.id)) continue;
		cards.push({ id: def.id, enabled: false });
	}
	return { cards };
}

export function loadHomeConfig(urlConfigParam?: string | null): HomeConfig {
	// URL override wins. Base64-encoded JSON because the slugged tokens we'd
	// otherwise expose are noisier and harder to evolve.
	if (urlConfigParam) {
		try {
			const decoded = JSON.parse(
				typeof window !== "undefined"
					? atob(urlConfigParam)
					: Buffer.from(urlConfigParam, "base64").toString("utf-8"),
			);
			return reconcile(decoded);
		} catch {
			// Fall through to localStorage.
		}
	}
	if (typeof window === "undefined") return DEFAULT_HOME_CONFIG;
	try {
		const raw = window.localStorage.getItem(STORAGE_KEY);
		if (!raw) return DEFAULT_HOME_CONFIG;
		return reconcile(JSON.parse(raw));
	} catch {
		return DEFAULT_HOME_CONFIG;
	}
}

export function saveHomeConfig(config: HomeConfig): void {
	if (typeof window === "undefined") return;
	try {
		window.localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
	} catch {
		// Quota or disabled storage — silently no-op. The runtime defaults
		// take over on the next page load.
	}
}

export function resetHomeConfig(): HomeConfig {
	if (typeof window !== "undefined") {
		try {
			window.localStorage.removeItem(STORAGE_KEY);
		} catch {
			// no-op
		}
	}
	return DEFAULT_HOME_CONFIG;
}

/**
 * Encode a config to a URL-safe string the caller can append as `?config=`.
 * Used by the "share my layout" affordance in the configurator modal.
 */
export function encodeHomeConfigToUrl(config: HomeConfig): string {
	const json = JSON.stringify(config);
	if (typeof window !== "undefined") return btoa(json);
	return Buffer.from(json, "utf-8").toString("base64");
}
