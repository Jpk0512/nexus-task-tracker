"use client";

import { Skeleton } from "@ui/components/ui/skeleton";
import { cn } from "@ui/lib/utils";
import { ArrowRight } from "lucide-react";
import Link from "next/link";

/**
 * Shared chrome for the Linear-style 2x2 Home grid widgets.
 *
 * Each card renders a header (title + count badge + "view all" arrow link)
 * followed by a compact list body. Visual style follows Linear: hairline
 * border, surface-1 background, 12px radius, 13px header type at weight 510.
 */
export const HomeCard = ({
	title,
	count,
	href,
	isLoading,
	isEmpty,
	emptyState,
	children,
}: {
	title: string;
	count?: number;
	href: string;
	isLoading?: boolean;
	isEmpty?: boolean;
	emptyState?: React.ReactNode;
	children: React.ReactNode;
}) => {
	return (
		<div className="flex flex-col rounded-[12px] border border-border bg-card">
			<div className="flex items-center justify-between gap-2 border-border border-b px-3 py-2">
				<div className="flex items-center gap-1.5">
					<h2 className="font-[510] text-[13px] text-foreground tracking-[-0.005em]">
						{title}
					</h2>
					{typeof count === "number" && count > 0 ? (
						<span className="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-muted px-1.5 font-[510] text-[11px] text-muted-foreground tabular-nums">
							{count}
						</span>
					) : null}
				</div>
				<Link
					href={href}
					className="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[12px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
					aria-label={`View all ${title}`}
				>
					View all
					<ArrowRight className="size-3" />
				</Link>
			</div>
			<div className="flex min-h-[180px] flex-col px-1.5 py-1.5">
				{isLoading ? <HomeCardSkeleton /> : isEmpty ? emptyState : children}
			</div>
		</div>
	);
};

const HomeCardSkeleton = () => (
	<div className="space-y-1.5 p-1.5">
		{Array.from({ length: 5 }).map((_, idx) => (
			// biome-ignore lint/suspicious/noArrayIndexKey: skeleton rows
			<Skeleton key={idx} className="h-7 w-full rounded-md" />
		))}
	</div>
);

/**
 * Compact Linear-style row used inside every Home widget body.
 * 28px tall, leading status/type icon, optional ID prefix, title, trailing meta.
 */
export const HomeCardRow = ({
	href,
	leading,
	id,
	title,
	trailing,
	className,
}: {
	href: string;
	leading?: React.ReactNode;
	id?: React.ReactNode;
	title: React.ReactNode;
	trailing?: React.ReactNode;
	className?: string;
}) => (
	<Link
		href={href}
		className={cn(
			"flex h-7 min-w-0 items-center gap-2 rounded-md px-2 text-[13px] text-foreground transition-colors hover:bg-accent/60",
			className,
		)}
	>
		{leading ? (
			<span className="flex size-4 shrink-0 items-center justify-center text-muted-foreground">
				{leading}
			</span>
		) : null}
		{id ? (
			<span className="shrink-0 text-[12px] text-muted-foreground tabular-nums">
				{id}
			</span>
		) : null}
		<span className="min-w-0 flex-1 truncate">{title}</span>
		{trailing ? (
			<span className="ml-auto flex shrink-0 items-center gap-1.5 text-[11px] text-muted-foreground">
				{trailing}
			</span>
		) : null}
	</Link>
);

export const HomeCardEmpty = ({
	title,
	description,
	ctaHref,
	ctaLabel,
}: {
	title: string;
	description?: string;
	ctaHref?: string;
	ctaLabel?: string;
}) => (
	<div className="flex flex-1 flex-col items-center justify-center gap-1 px-3 py-6 text-center">
		<p className="font-[510] text-[13px] text-foreground">{title}</p>
		{description ? (
			<p className="text-[12px] text-muted-foreground">{description}</p>
		) : null}
		{ctaHref && ctaLabel ? (
			<Link
				href={ctaHref}
				className="mt-1 inline-flex items-center gap-1 rounded-md border border-border bg-background px-2 py-1 text-[12px] text-foreground transition-colors hover:bg-accent"
			>
				{ctaLabel}
				<ArrowRight className="size-3" />
			</Link>
		) : null}
	</div>
);
