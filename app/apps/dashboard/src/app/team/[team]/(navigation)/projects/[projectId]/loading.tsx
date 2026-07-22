import { Skeleton } from "@ui/components/ui/skeleton";

/**
 * Loading skeleton for a single project's overview/board so the layout
 * doesn't pop in when deep-linking into a project.
 */
export default function ProjectDetailLoading() {
	return (
		<div className="flex h-full flex-col gap-4 p-4">
			<div className="flex items-center gap-3">
				<Skeleton className="size-6 rounded" />
				<Skeleton className="h-5 w-40" />
				<Skeleton className="ml-auto h-8 w-24 rounded-md" />
			</div>
			<div className="flex gap-2">
				{Array.from({ length: 5 }).map((_, i) => (
					<Skeleton key={`tab-${i}`} className="h-8 w-20 rounded-md" />
				))}
			</div>
			<div className="grid flex-1 gap-4 md:grid-cols-3">
				<Skeleton className="rounded-lg md:col-span-2" />
				<Skeleton className="rounded-lg" />
			</div>
		</div>
	);
}
