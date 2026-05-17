import { BookOpenIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useGlobalSearch } from "../global-search-context";
import type { ResultItemProps } from "../types";
import { BaseResultItem } from "./base-result-item";

export const LibraryResultItem = ({ item }: ResultItemProps) => {
	const router = useRouter();
	const { onOpenChange, basePath } = useGlobalSearch();

	const handleSelect = () => {
		router.push(`${basePath}/library/${item.id}`);
		onOpenChange(false);
	};

	const kind = item.parentId || "";

	return (
		<BaseResultItem
			onSelect={handleSelect}
			icon={BookOpenIcon}
			title={item.title}
			item={item}
		>
			<div className="flex min-w-0 flex-1 items-center gap-2">
				<span className="truncate">{item.title}</span>
				{kind && (
					<span className="rounded-sm bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground uppercase tracking-wide">
						{kind}
					</span>
				)}
			</div>
		</BaseResultItem>
	);
};
