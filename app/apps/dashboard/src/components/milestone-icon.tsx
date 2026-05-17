import { TargetIcon } from "lucide-react";

export const MilestoneIcon = ({
	color,
	className,
}: {
	color?: string | null;
	className?: string;
}) => {
	return (
		<TargetIcon className={className} style={{ color: color || "inherit" }} />
	);
};
