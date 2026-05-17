import { ActiveProjectsRail } from "@/components/home/active-projects-rail";
import { ActivityFeed } from "@/components/home/activity-feed";
import { AgendaCard } from "@/components/home/agenda-card";
import { EndOfDayRecap } from "@/components/home/end-of-day-recap";
import { GreetingCard } from "@/components/home/greeting-card";
import { StaleCommitmentDigest } from "@/components/home/stale-commitment-digest";
import { UpNextCard } from "@/components/home/up-next-card";

type Props = {
	searchParams: Promise<{
		[key: string]: string | string[] | undefined;
	}>;
};

/**
 * Home — iter-10 redesign.
 *
 * Layout (1280px desktop):
 *   row 1: GreetingCard (time-of-day + day brief)
 *   row 2: AgendaCard (due today / overdue) | UpNextCard (Triage Now slice)
 *   row 3: ActiveProjectsRail (horizontal scroll)
 *   row 4: StaleCommitmentDigest (cron-style nag) | EndOfDayRecap (granola)
 *   row 5: ActivityFeed (last 10 events)
 *
 * All cards become configurable in commit 4 (DashboardConfigModal). Quick-
 * capture bar lands in commit 6.
 */
export default async function Page({ searchParams: _searchParams }: Props) {
	return (
		<div className="flex animate-blur-in flex-col gap-4 p-6">
			<GreetingCard />
			<div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
				<AgendaCard />
				<UpNextCard />
			</div>
			<ActiveProjectsRail />
			<div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
				<StaleCommitmentDigest />
				<EndOfDayRecap />
			</div>
			<ActivityFeed />
		</div>
	);
}
