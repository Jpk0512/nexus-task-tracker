import { Slot } from "@radix-ui/react-slot";
import { cn } from "@ui/lib/utils";
import { cva, type VariantProps } from "class-variance-authority";
import type * as React from "react";

// Linear badges: pill-rounded, 11px text, weight 510, very compact padding.
// Default is the "neutral pill" — transparent surface, hairline border, muted
// text — what Linear uses for labels, filter chips, status indicators.
const badgeVariants = cva(
	"inline-flex w-fit shrink-0 items-center justify-center gap-1 overflow-hidden whitespace-nowrap rounded-full border px-2 py-[1px] font-[510] text-[11px] leading-[1.4] tracking-[-0.005em] transition-colors focus-visible:ring-2 focus-visible:ring-ring/50 [&>svg]:pointer-events-none [&>svg]:size-2.5",
	{
		variants: {
			variant: {
				default:
					"border-transparent bg-primary/15 text-primary [a&]:hover:bg-primary/25",
				secondary:
					"border-border bg-secondary text-muted-foreground [a&]:hover:bg-accent",
				destructive:
					"border-transparent bg-destructive/15 text-destructive [a&]:hover:bg-destructive/25",
				// The dominant Linear pill: transparent + hairline border + muted text.
				outline:
					"border-border bg-transparent text-muted-foreground [a&]:hover:bg-accent/40 [a&]:hover:text-foreground",
			},
		},
		defaultVariants: {
			variant: "outline",
		},
	},
);

function Badge({
	className,
	variant,
	asChild = false,
	...props
}: React.ComponentProps<"span"> &
	VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
	const Comp = asChild ? Slot : "span";

	return (
		<Comp
			data-slot="badge"
			className={cn(badgeVariants({ variant }), className)}
			{...props}
		/>
	);
}

export { Badge, badgeVariants };
