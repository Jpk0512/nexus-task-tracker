import { ActivityFeed } from "@/components/home/activity-feed";

export default function ActivityPage() {
	return (
		<div className="flex h-full min-h-0 flex-col">
			<div className="border-border/60 border-b px-4 py-3">
				<h1 className="font-[510] text-[18px] tracking-[-0.01em]">Activity</h1>
				<p className="text-[13px] text-muted-foreground">
					Cross-surface timeline — what changed since you were last here.
				</p>
			</div>
			<div className="min-h-0 flex-1 overflow-auto p-4">
				<div className="mx-auto max-w-2xl">
					<ActivityFeed enableBulkActions />
				</div>
			</div>
		</div>
	);
}
