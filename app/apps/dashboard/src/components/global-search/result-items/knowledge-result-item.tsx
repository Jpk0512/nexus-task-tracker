import { BrainIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useGlobalSearch } from "../global-search-context";
import type { ResultItemProps } from "../types";
import { BaseResultItem } from "./base-result-item";

export const KnowledgeResultItem = ({ item }: ResultItemProps) => {
	const router = useRouter();
	const { onOpenChange, basePath } = useGlobalSearch();

	const handleSelect = () => {
		// Knowledge view is a single-page editor with a `?note=` query param —
		// passing the id selects the matching row in the left rail.
		router.push(`${basePath}/knowledge?note=${item.id}`);
		onOpenChange(false);
	};

	const relativePath = item.parentId || "";

	return (
		<BaseResultItem
			onSelect={handleSelect}
			icon={BrainIcon}
			title={item.title}
			item={item}
		>
			<div className="flex min-w-0 flex-1 items-baseline gap-2">
				<span className="truncate">{item.title}</span>
				{relativePath && (
					<span className="truncate text-muted-foreground text-xs">
						{relativePath}
					</span>
				)}
			</div>
		</BaseResultItem>
	);
};
