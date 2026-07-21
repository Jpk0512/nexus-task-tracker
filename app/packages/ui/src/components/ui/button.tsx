import { cn } from "@ui/lib/utils";
import { cva, type VariantProps } from "class-variance-authority";
import { Slot as SlotPrimitive } from "radix-ui";
import type * as React from "react";

// Linear's button system: 8px radius, weight 510, compact padding (8x14),
// near-transparent ghost surfaces, hairline borders on outline, lavender
// primary, scarcely-used semantic colors.
const buttonVariants = cva(
	"inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-[510] text-[13px] tracking-[-0.005em] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-offset-0 disabled:pointer-events-none disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 [&_svg:not([class*='size-'])]:size-3.5 [&_svg]:pointer-events-none [&_svg]:shrink-0",
	{
		variants: {
			variant: {
				// Lavender CTA — primary action only (scarce by spec).
				default:
					"bg-primary text-primary-foreground hover:bg-[#3ec4db] active:bg-[#1fa3ba]",
				destructive:
					"border border-destructive/30 bg-destructive/15 text-destructive hover:bg-destructive/25",
				// Linear ghost button: barely-visible translucent white surface
				// with hairline border. The dominant button shape.
				outline:
					"border border-white/[0.08] bg-white/[0.02] text-foreground hover:border-white/[0.12] hover:bg-white/[0.05]",
				// Surface-1 button — secondary CTAs ("Sign in", "Read changelog").
				secondary: "bg-secondary text-foreground hover:bg-accent",
				// Plain text — no surface at all until hover.
				ghost: "text-foreground hover:bg-accent hover:text-accent-foreground",
				link: "text-primary underline-offset-4 hover:underline",
			},
			size: {
				default: "h-7 px-3.5 has-[>svg]:px-2.5",
				sm: "h-6 gap-1 px-2.5 text-[12px] has-[>svg]:px-2",
				lg: "h-9 px-5 text-sm has-[>svg]:px-3.5",
				xl: "h-12 px-8 text-base has-[>svg]:px-6",
				icon: "size-7",
			},
		},
		defaultVariants: {
			variant: "default",
			size: "default",
		},
	},
);

function Button({
	className,
	variant,
	size,
	asChild = false,
	...props
}: React.ComponentProps<"button"> &
	VariantProps<typeof buttonVariants> & {
		asChild?: boolean;
	}) {
	const Comp = asChild ? SlotPrimitive.Slot : "button";

	return (
		<Comp
			data-slot="button"
			className={cn(buttonVariants({ variant, size, className }))}
			{...props}
		/>
	);
}

export { Button, buttonVariants };
