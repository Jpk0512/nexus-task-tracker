import { Skeleton } from "@ui/components/ui/skeleton";

/**
 * Route-level loading skeleton for the Projects grid — prevents the
 * flash-of-blank while the list query resolves.
 */
export default function ProjectsLoading() {
	return (
		<div className="grid auto-rows-min gap-4 p-6 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
			{Array.from({ length: 8 }).map((_, i) => (
				<div
					key={`proj-skel-${i}`}
					className="rounded-lg border border-border bg-card p-4"
				>
					<div className="flex items-center gap-2">
						<Skeleton className="size-4 rounded" />
						<Skeleton className="h-4 w-1/2" />
					</div>
					<Skeleton className="mt-3 h-8 w-full" />
					<Skeleton className="mt-3 h-1.5 w-full rounded-full" />
					<div className="mt-2 flex justify-between">
						<Skeleton className="h-3 w-16" />
						<Skeleton className="h-3 w-10" />
					</div>
				</div>
			))}
		</div>
	);
}
