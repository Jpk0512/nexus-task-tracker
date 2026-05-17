import { HomeShell } from "@/components/home/home-shell";

type Props = {
	searchParams: Promise<{
		[key: string]: string | string[] | undefined;
	}>;
};

/**
 * Home — iter-10 redesign.
 *
 * Server component is intentionally thin: the configurator (localStorage,
 * drag-reorder, gear icon) lives in `HomeShell`, a client component. Keeping
 * the route a server component preserves the existing render flow (auth,
 * teamLayout chrome) while letting the shell own all configurable state.
 *
 * Cards (server-paint defaults, fully configurable in the modal):
 *   - GreetingCard (time-of-day + day brief)         [on]
 *   - AgendaCard (due today / overdue)               [on]
 *   - UpNextCard (Triage Now slice)                  [on]
 *   - ActiveProjectsRail (horizontal scroll)         [on]
 *   - StaleCommitmentDigest (cron-style nag)         [off]
 *   - EndOfDayRecap (granola-style)                  [off]
 *   - ActivityFeed (last 10 events)                  [off]
 *
 * Quick-capture bar lands in commit 6.
 */
export default async function Page({ searchParams: _searchParams }: Props) {
	return <HomeShell />;
}
