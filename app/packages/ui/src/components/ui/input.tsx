import { cn } from "@ui/lib/utils";
import { cva, type VariantProps } from "class-variance-authority";
import type * as React from "react";

// Linear inputs: surface-1 background, hairline border, 8px radius, weight
// 400 body, 13px text. Focus ring is 2px lavender-tinted at half opacity.
const inputVariants = cva("", {
	variants: {
		variant: {
			outline:
				"flex h-8 w-full min-w-0 rounded-md border border-border bg-white/[0.02] px-2.5 py-1 text-[13px] tracking-[-0.005em] outline-none transition-colors selection:bg-primary/40 selection:text-foreground file:inline-flex file:h-6 file:border-0 file:bg-transparent file:font-[510] file:text-[12px] file:text-foreground placeholder:text-muted-foreground hover:border-[#34343a] focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20",
			ghost:
				"flex h-8 w-full min-w-0 rounded-md bg-transparent px-2.5 py-1 text-[13px] tracking-[-0.005em] outline-none transition-colors selection:bg-primary/40 selection:text-foreground placeholder:text-muted-foreground hover:bg-white/[0.03] focus:bg-white/[0.04] focus-visible:ring-2 focus-visible:ring-ring/30 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
		},
	},
	defaultVariants: {
		variant: "outline",
	},
});

function Input({
	className,
	type,
	variant,
	...props
}: React.ComponentProps<"input"> & VariantProps<typeof inputVariants>) {
	return (
		<input
			type={type}
			data-slot="input"
			className={cn(
				inputVariants({
					className,
					variant,
				}),
			)}
			{...props}
		/>
	);
}

export { Input };
