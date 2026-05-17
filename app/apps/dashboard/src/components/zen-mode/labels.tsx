import { TagIcon } from "lucide-react";

export const ZenModeLabels = ({
	labels,
}: {
	labels: { id: string; name: string; color: string }[];
}) => {
	if (!labels || labels.length === 0) {
		return null;
	}

	return (
		<div className="flex flex-wrap items-center gap-2 text-muted-foreground text-sm">
			<TagIcon className="size-3.5" />
			{labels[0]!.name}
			{labels.length > 1 && (
				<span className="ml-1 text-xs opacity-70">+{labels.length - 1}</span>
			)}
		</div>
	);
};
