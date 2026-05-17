import { AgendaCard } from "@/components/home/agenda-card";
import { GreetingCard } from "@/components/home/greeting-card";
import { UpNextCard } from "@/components/home/up-next-card";

type Props = {
	searchParams: Promise<{
		[key: string]: string | string[] | undefined;
	}>;
};

/**
 * Home — iter-10 redesign.
 *
 * Above-the-fold layout (1280px desktop):
 *   row 1: GreetingCard (time-of-day + day brief)
 *   row 2: AgendaCard (due today / overdue) | UpNextCard (Triage Now slice)
 *
 * Subsequent commits in this iteration layer in:
 *   - ActiveProjectsRail (horizontal scroll, commit 2)
 *   - ActivityFeed + EodRecap + StaleDigest (commit 3)
 *   - DashboardConfigModal — toggles visibility + reorders cards (commit 4)
 *   - QuickCapture bar above the greeting (commit 6)
 */
export default async function Page({ searchParams: _searchParams }: Props) {
	return (
		<div className="flex animate-blur-in flex-col gap-4 p-6">
			<GreetingCard />
			<div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
				<AgendaCard />
				<UpNextCard />
			</div>
		</div>
	);
}
