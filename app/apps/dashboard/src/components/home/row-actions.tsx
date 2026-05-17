"use client";

import { Button } from "@ui/components/ui/button";
import { cn } from "@ui/lib/utils";
import Link from "next/link";

/**
 * Shared hover-affordance button + strip for Home rows.
 *
 * Both the Agenda card and the Up Next card surface a small action group
 * (done / defer / open) that fades in on hover or keyboard focus-within.
 * Pulling the chrome into one place keeps the row-level animations,
 * spacing, and Shadcn `<Button>` usage consistent across cards — the
 * previous inline divs drifted between rows because each was hand-rolled.
 */

export const RowActionStrip = ({ children }: { children: React.ReactNode }) => (
	<div
		className={cn(
			"absolute top-1/2 right-1.5 flex -translate-y-1/2 items-center gap-0.5 rounded-md border border-border bg-background/95 px-1 py-0.5 opacity-0 shadow-sm backdrop-blur transition-opacity",
			"group-hover:opacity-100 group-focus-within:opacity-100",
		)}
		onClick={(e) => e.stopPropagation()}
		onKeyDown={(e) => e.stopPropagation()}
	>
		{children}
	</div>
);

export const RowActionButton = ({
	title,
	onClick,
	disabled,
	children,
}: {
	title: string;
	onClick: () => void;
	disabled?: boolean;
	children: React.ReactNode;
}) => (
	<Button
		variant="ghost"
		size="icon"
		type="button"
		title={title}
		aria-label={title}
		disabled={disabled}
		onClick={(e) => {
			e.stopPropagation();
			e.preventDefault();
			if (!disabled) onClick();
		}}
		className="size-6 text-muted-foreground hover:text-foreground"
	>
		{children}
	</Button>
);

export const RowActionLink = ({
	href,
	title,
	children,
}: {
	href: string;
	title: string;
	children: React.ReactNode;
}) => (
	<Button
		variant="ghost"
		size="icon"
		asChild
		title={title}
		aria-label={title}
		className="size-6 text-muted-foreground hover:text-foreground"
	>
		<Link
			href={href}
			onClick={(e) => {
				// Don't bubble to the outer row Link — clicks on the action group
				// should always go to the action, never re-trigger the row navigation.
				e.stopPropagation();
			}}
		>
			{children}
		</Link>
	</Button>
);
