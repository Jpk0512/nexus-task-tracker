"use client";

import { cn } from "@ui/lib/utils";
import { SettingsIcon } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { WeeklyRollover } from "@/components/lens/weekly-rollover";
import { ActiveProjectsRail } from "./active-projects-rail";
import { ActivityFeed } from "./activity-feed";
import { AgendaCard } from "./agenda-card";
import { DashboardConfigModal } from "./dashboard-config-modal";
import { EndOfDayRecap } from "./end-of-day-recap";
import { GreetingCard } from "./greeting-card";
import {
	type HomeCardId,
	type HomeConfig,
	loadHomeConfig,
	resetHomeConfig,
	saveHomeConfig,
} from "./home-config";
import { CompanionStub } from "@/components/ai/companion-stub";
import { DoNowCard } from "./do-now-card";
import { HealthStrip } from "./health-strip";
import { OsQuickTiles } from "./os-quick-tiles";
import { QuickCapture } from "./quick-capture";
import { TodosCard } from "./todos-card";
import { StarterContinueCard } from "./starter-continue-card";
import { StaleCommitmentDigest } from "./stale-commitment-digest";
import { UpNextCard } from "./up-next-card";

/**
 * Client shell for the Home page. Reads the user's HomeConfig from
 * localStorage (or `?config=` if present), renders enabled cards in saved
 * order, and exposes the configurator modal via a gear icon docked to the
 * upper-right.
 *
 * Cards that span the full row (greeting, rail, activity feed) render as
 * standalone sections. Cards designed for the 2-column grid (agenda,
 * up-next, stale-digest, eod-recap) are grouped into adjacent pairs so they
 * sit side-by-side when both are enabled — preserves the designer-meta §5
 * layout while still respecting user reorder intent.
 */

const FULL_WIDTH_CARDS: HomeCardId[] = [
	"greeting",
	"active-projects",
	"activity-feed",
];

function renderCard(id: HomeCardId): React.ReactNode {
	switch (id) {
		case "greeting":
			return <GreetingCard />;
		case "do-now":
			return <DoNowCard />;
		case "todos":
			return <TodosCard />;
		case "agenda":
			return <AgendaCard />;
		case "up-next":
			return <UpNextCard />;
		case "active-projects":
			return <ActiveProjectsRail />;
		case "stale-digest":
			return <StaleCommitmentDigest />;
		case "eod-recap":
			return <EndOfDayRecap />;
		case "activity-feed":
			return <ActivityFeed />;
	}
}

export const HomeShell = () => {
	const searchParams = useSearchParams();
	const urlConfig = searchParams?.get("config") ?? null;

	const [config, setConfig] = useState<HomeConfig | null>(null);
	const [open, setOpen] = useState(false);

	// Hydrate after mount so SSR + initial paint match (avoids hydration
	// mismatch when localStorage diverges from the server defaults).
	useEffect(() => {
		setConfig(loadHomeConfig(urlConfig));
	}, [urlConfig]);

	const handleChange = (next: HomeConfig) => {
		setConfig(next);
		saveHomeConfig(next);
	};

	const handleReset = () => {
		const next = resetHomeConfig();
		setConfig(next);
	};

	// Group enabled cards into runs of grid-pairable cards vs full-width
	// breakouts. A 2-col grid wraps any consecutive run of non-full-width
	// cards so user-reorders within those runs lay out as pairs.
	const layout = useMemo(() => {
		if (!config) return null;
		const enabled = config.cards.filter((c) => c.enabled);
		const groups: Array<{ kind: "full" | "grid"; ids: HomeCardId[] }> = [];
		let currentGrid: HomeCardId[] = [];
		const flushGrid = () => {
			if (currentGrid.length) {
				groups.push({ kind: "grid", ids: currentGrid });
				currentGrid = [];
			}
		};
		for (const c of enabled) {
			if (FULL_WIDTH_CARDS.includes(c.id)) {
				flushGrid();
				groups.push({ kind: "full", ids: [c.id] });
			} else {
				currentGrid.push(c.id);
			}
		}
		flushGrid();
		return groups;
	}, [config]);

	return (
		<div className="flex animate-blur-in flex-col gap-4 p-6">
			{/* Weekly rollover banner (codex delighter #3) — self-managed: only
				 renders Monday-of-a-new-ISO-week before the user dismisses, no-ops
				 the rest of the week. Mounted at the top so it's the first thing
				 seen on home. */}
			<WeeklyRollover />
			{/* Quick-capture bar + configurator gear share the top row. The bar
			 *  is always rendered (it's the primary CTA on Home and doesn't make
			 *  sense to hide); the gear sits flush right.
			 */}
			<div className="flex items-start gap-2">
				<div className="flex-1">
					<QuickCapture />
				</div>
				<button
					type="button"
					onClick={() => setOpen(true)}
					title="Customize home"
					aria-label="Customize home"
					className={cn(
						"inline-flex size-9 shrink-0 items-center justify-center rounded-[12px] border border-border bg-card text-muted-foreground transition-colors",
						"hover:bg-accent hover:text-foreground",
					)}
				>
					<SettingsIcon className="size-3.5" />
				</button>
			</div>
			<HealthStrip />
			{layout === null ? (
				// Initial paint before hydration — render defaults to avoid layout
				// shift. The useEffect will replace this with the persisted layout
				// on next tick.
				<DefaultHomeFallback />
			) : (
				layout.map((group, idx) => {
					if (group.kind === "full") {
						return (
							<section key={`full-${group.ids[0]}-${idx}`}>
								{renderCard(group.ids[0])}
							</section>
						);
					}
					if (group.ids.length === 1) {
						return (
							<section key={`solo-${group.ids[0]}-${idx}`}>
								{renderCard(group.ids[0])}
							</section>
						);
					}
					return (
						<div
							key={`grid-${idx}-${group.ids.join("-")}`}
							className="grid grid-cols-1 gap-3 lg:grid-cols-2"
						>
							{group.ids.map((id) => (
								<div key={id}>{renderCard(id)}</div>
							))}
						</div>
					);
				})
			)}
			<StarterContinueCard />
			{/* Dashboard OS quick tiles — Notes / Skills / Meetings / Starter */}
			<OsQuickTiles />
			<CompanionStub compact />
			{config ? (
				<DashboardConfigModal
					open={open}
					onOpenChange={setOpen}
					config={config}
					onChange={handleChange}
					onReset={handleReset}
				/>
			) : null}
		</div>
	);
};

/**
 * Server-paint fallback identical to the default config so first paint
 * looks correct even before localStorage hydration. Once HomeShell mounts
 * and reads localStorage, this is replaced with the real layout — same
 * content if the user hasn't customized anything, so no jank.
 */
function DefaultHomeFallback() {
	return (
		<>
			<GreetingCard />
			<div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
				<DoNowCard />
				<TodosCard />
				<AgendaCard />
				<UpNextCard />
			</div>
			<ActiveProjectsRail />
			<StarterContinueCard />
			<OsQuickTiles />
		</>
	);
}
