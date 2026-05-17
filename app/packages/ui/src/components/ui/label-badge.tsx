import type React from "react";
import { cn } from "@/lib/utils";
import { Badge } from "./badge";

// Linear-style label: filled colored dot + muted ink text on transparent
// surface with hairline border. The colored dot IS the visual cue — text
// stays grey for readability across many colors at once.
export const LabelBadge = ({
	color,
	name,
	variant = "outline",
	className,
}: {
	color: string;
	name: string;
	variant?: "default" | "secondary" | "outline";
	className?: string;
}) => {
	return (
		<Badge
			variant={variant}
			className={cn(
				"gap-1.5 rounded-full border-border/80 bg-transparent text-muted-foreground",
				className,
			)}
		>
			<span
				aria-hidden
				className="inline-block size-[6px] shrink-0 rounded-full"
				style={{ backgroundColor: color }}
			/>
			{name}
		</Badge>
	);
};
