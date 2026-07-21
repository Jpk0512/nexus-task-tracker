import { cn } from "@ui/lib/utils";
import { BoxIcon } from "lucide-react";

/**
 * Project identity mark — plain BoxIcon stroke (site convention for projects:
 * chat context, quick-open, task toolbar). Optional `color` tints the stroke;
 * never filled — matches Linear-style sidebar/content icons.
 */
export const ProjectIcon = ({
	color,
	hasTasks: _hasTasks,
	className,
}: {
	color?: string | null;
	/** Kept for call-site compat; unused (both states share BoxIcon). */
	hasTasks?: boolean;
	className?: string;
}) => {
	return (
		<BoxIcon
			className={cn("stroke-[1.5]", className)}
			style={color ? { color } : undefined}
		/>
	);
};
