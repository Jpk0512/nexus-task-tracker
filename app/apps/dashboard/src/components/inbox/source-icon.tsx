import { cn } from "@ui/lib/utils";
import {
	AtSignIcon,
	BellIcon,
	CalendarIcon,
	GitBranchIcon,
	GithubIcon,
	LayersIcon,
	MessageSquareIcon,
} from "lucide-react";

const inboxSourceIcons: Record<string, React.ReactNode> = {
	github: <GithubIcon />,
	git: <GitBranchIcon />,
	mention: <AtSignIcon />,
	slack: <MessageSquareIcon />,
	task: <LayersIcon />,
	intake: <LayersIcon />,
	calendar: <CalendarIcon />,
	notification: <BellIcon />,
	gmail: (
		<svg
			xmlns="http://www.w3.org/2000/svg"
			aria-label="Gmail"
			role="img"
			viewBox="0 0 512 512"
			className="size-full"
		>
			<rect width="512" height="512" rx="15%" fill="#ffffff" />
			<path d="M158 391v-142l-82-63V361q0 30 30 30" fill="#4285f4" />
			<path d="M 154 248l102 77l102-77v-98l-102 77l-102-77" fill="#ea4335" />
			<path d="M354 391v-142l82-63V361q0 30-30 30" fill="#34a853" />
			<path d="M76 188l82 63v-98l-30-23c-27-21-52 0-52 26" fill="#c5221f" />
			<path d="M436 188l-82 63v-98l30-23c27-21 52 0 52 26" fill="#fbbc04" />
		</svg>
	),
};

export const InboxSourceIcon = ({
	source,
	className,
}: {
	source: string;
	className?: string;
}) => {
	const IconComponent = inboxSourceIcons[source];
	if (!IconComponent) {
		// Fallback so unknown sources still get a glyph (Linear style).
		return (
			<span
				className={cn(
					"inline-flex items-center justify-center text-muted-foreground",
					className,
				)}
			>
				<BellIcon className="size-full" />
			</span>
		);
	}
	return (
		<span
			className={cn(
				"inline-flex size-4 items-center justify-center text-muted-foreground",
				className,
			)}
		>
			{IconComponent}
		</span>
	);
};
