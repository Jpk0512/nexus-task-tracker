import { ActiveProjectsCard } from "@/components/home/active-projects-card";
import { ActivityTimeline } from "@/components/home/activity-timeline";
import { InboxPreviewCard } from "@/components/home/inbox-preview-card";
import { MyIssuesCard } from "@/components/home/my-issues-card";
import { RecentDocumentsCard } from "@/components/home/recent-documents-card";
import { trpcClient } from "@/utils/trpc";

type Props = {
	searchParams: Promise<{
		[key: string]: string | string[] | undefined;
	}>;
};

export default async function Page({ searchParams: _searchParams }: Props) {
	const user = await trpcClient.users.getCurrent.query();
	const firstName = user?.name?.split(" ")[0] ?? "there";

	return (
		<div className="flex animate-blur-in flex-col gap-4 p-6">
			<div className="space-y-0.5">
				<h1 className="font-[510] text-[20px] text-foreground tracking-[-0.01em]">
					Jump into your work
				</h1>
				<p className="text-[13px] text-muted-foreground">
					Welcome back, {firstName}.
				</p>
			</div>
			<div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
				<MyIssuesCard />
				<ActiveProjectsCard />
				<RecentDocumentsCard />
				<InboxPreviewCard />
			</div>
			<ActivityTimeline />
		</div>
	);
}
