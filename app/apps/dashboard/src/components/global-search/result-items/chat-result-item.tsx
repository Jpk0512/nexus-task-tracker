import { MessageCircleIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useGlobalSearch } from "../global-search-context";
import type { ResultItemProps } from "../types";
import { BaseResultItem } from "./base-result-item";

/**
 * Renders a recently-visited chat conversation in the palette's Recent
 * section (FEAT-006 item 4). Chats aren't part of the searchable entity
 * catalogue — this type only ever appears via the shared recent-items store.
 */
export const ChatResultItem = ({ item }: ResultItemProps) => {
	const router = useRouter();
	const { onOpenChange, basePath } = useGlobalSearch();

	const handleSelect = () => {
		router.push(`${basePath}/chat/${item.id}`);
		onOpenChange(false);
	};

	return (
		<BaseResultItem
			onSelect={handleSelect}
			icon={MessageCircleIcon}
			iconColor={item.color}
			title={item.title}
			item={item}
		/>
	);
};
