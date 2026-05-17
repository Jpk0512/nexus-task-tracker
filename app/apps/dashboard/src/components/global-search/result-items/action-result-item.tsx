import { CornerDownLeftIcon, RotateCwIcon } from "lucide-react";
import { findActionById } from "../actions-catalogue";
import { useGlobalSearch } from "../global-search-context";
import { loadLastCommand, recordCommand } from "../repeat-last-command";
import type { ResultItemProps } from "../types";
import { useActionDispatcher } from "../use-action-dispatcher";
import { BaseResultItem } from "./base-result-item";

export const ActionResultItem = ({ item }: ResultItemProps) => {
	const { onOpenChange, basePath } = useGlobalSearch();
	const dispatch = useActionDispatcher(basePath);

	const handleSelect = () => {
		if (item.id === "action:repeat-last") {
			const last = loadLastCommand();
			const target = last ? findActionById(last.id) : undefined;
			if (target) {
				dispatch(target);
				// Recording the repeat keeps the "last command" sticky — the
				// user can chain Cmd+. forever without it drifting back to an
				// older command.
				recordCommand(target);
			}
		} else {
			dispatch(item);
		}
		onOpenChange(false);
	};

	const isRepeat = item.id === "action:repeat-last";

	return (
		<BaseResultItem
			onSelect={handleSelect}
			icon={isRepeat ? RotateCwIcon : CornerDownLeftIcon}
			iconColor={item.color}
			title={item.title}
		/>
	);
};
