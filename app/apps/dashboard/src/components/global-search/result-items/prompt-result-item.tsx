import { MessageSquareTextIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useGlobalSearch } from "../global-search-context";
import type { ResultItemProps } from "../types";
import { BaseResultItem } from "./base-result-item";

export const PromptResultItem = ({ item }: ResultItemProps) => {
	const router = useRouter();
	const { onOpenChange, basePath } = useGlobalSearch();

	// parent_id arrives from the view as "productSlug:promptSlug" so we can
	// route directly without an extra lookup.
	const [productSlug, promptSlug] = (item.parentId || "").split(":");

	const handleSelect = () => {
		if (productSlug && promptSlug) {
			router.push(`${basePath}/prompts/${productSlug}/${promptSlug}`);
		} else {
			router.push(`${basePath}/prompts`);
		}
		onOpenChange(false);
	};

	return (
		<BaseResultItem
			onSelect={handleSelect}
			icon={MessageSquareTextIcon}
			title={item.title}
			item={item}
		>
			<div className="flex min-w-0 flex-1 items-center gap-2">
				<span className="truncate">{item.title}</span>
				{productSlug && (
					<span className="rounded-sm bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground uppercase tracking-wide">
						{productSlug}
					</span>
				)}
			</div>
		</BaseResultItem>
	);
};
