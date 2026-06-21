import type { RouterOutputs } from "@nexus-app/trpc";
import { cn } from "@ui/lib/utils";
import {
	CircleCheckIcon,
	CircleDashedIcon,
	CircleDotIcon,
	CircleIcon,
	CircleSlashIcon,
} from "lucide-react";

// Iter8 a11y: status icons are semantically meaningful (they communicate
// the workflow state of the task), so they get role="img" + aria-label
// rather than being marked aria-hidden. Screen readers will announce
// "Status: In progress" alongside the task title.
const LABELS: Record<string, string> = {
	backlog: "Status: Backlog",
	to_do: "Status: To do",
	in_progress: "Status: In progress",
	review: "Status: In review",
	done: "Status: Done",
};

export const StatusIcon = ({
	type,
	className,
}: {
	type: RouterOutputs["statuses"]["get"]["data"][number]["type"];
	className?: string;
}) => {
	const label = LABELS[type ?? ""] ?? "Status";
	switch (type) {
		case "backlog":
			return (
				<CircleDotIcon
					role="img"
					aria-label={label}
					className={cn(className, "text-muted-foreground")}
				/>
			);
		case "to_do":
			return (
				<CircleIcon
					role="img"
					aria-label={label}
					className={cn(className, "text-muted-foreground")}
				/>
			);
		case "in_progress":
			return (
				<CircleDashedIcon
					role="img"
					aria-label={label}
					className={cn(className, "text-yellow-400")}
				/>
			);
		case "review":
			return (
				<CircleSlashIcon
					role="img"
					aria-label={label}
					className={cn(className, "text-purple-400")}
				/>
			);
		case "done":
			return (
				<CircleCheckIcon
					role="img"
					aria-label={label}
					className={cn(className, "text-green-400")}
				/>
			);
		default:
			return null;
	}
};
