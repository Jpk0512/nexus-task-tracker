import { CheckSquareIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useGlobalSearch } from "../global-search-context";
import type { ResultItemProps } from "../types";
import { BaseResultItem } from "./base-result-item";

export const TodoResultItem = ({ item }: ResultItemProps) => {
	const router = useRouter();
	const { onOpenChange, basePath } = useGlobalSearch();

	const handleSelect = () => {
		// Todos have no dedicated detail page. Route to the scoped list when we
		// know the project, otherwise the workspace list. The id is appended as
		// a hash so the receiving view can scroll the row into focus later.
		const path = item.parentId
			? `${basePath}/projects/${item.parentId}/todos#${item.id}`
			: `${basePath}/todos#${item.id}`;
		router.push(path);
		onOpenChange(false);
	};

	return (
		<BaseResultItem
			onSelect={handleSelect}
			icon={CheckSquareIcon}
			title={item.title}
		>
			<span className="line-clamp-1 min-w-0 flex-1">{item.title}</span>
		</BaseResultItem>
	);
};
