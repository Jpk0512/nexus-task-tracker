import { CommandItem } from "@ui/components/ui/command";
import { ChevronRight, type LucideIcon } from "lucide-react";
import { useEffect, useRef } from "react";
import { useGlobalSearch } from "../global-search-context";
import type { GlobalSearchItem } from "../types";

export type BaseResultItemProps = {
	onSelect: () => void;
	icon: LucideIcon;
	preview?: React.ReactNode;
	iconColor?: string;
	title: string;
	children?: React.ReactNode;
	/**
	 * Optional underlying search item. When the consuming result-item passes
	 * this through, BaseResultItem notifies the GlobalSearch context's
	 * `onSelectItem` callback on every selection (keyboard + mouse), so the
	 * palette can record the entity in its recent-items list. Result items
	 * that haven't been updated yet simply skip the notification — backwards
	 * compatible.
	 */
	item?: GlobalSearchItem;
};

export const BaseResultItem = ({
	onSelect,
	preview,
	icon: Icon,
	iconColor,
	title,
	children,
	item,
}: BaseResultItemProps) => {
	const { setPreview, onSelectItem, linkMode, onLinkPick } = useGlobalSearch();
	const itemRef = useRef<HTMLDivElement>(null);
	const isSelectedRef = useRef(false);

	useEffect(() => {
		const element = itemRef.current;
		if (!element) return;

		const updatePreview = () => {
			const isSelected = element.getAttribute("data-selected") === "true";
			if (isSelectedRef.current === isSelected) return;
			isSelectedRef.current = isSelected;
			if (isSelected) {
				setPreview(preview);
			}
		};

		// Check initial state
		updatePreview();

		const observer = new MutationObserver((mutations) => {
			for (const mutation of mutations) {
				if (
					mutation.type === "attributes" &&
					mutation.attributeName === "data-selected"
				) {
					updatePreview();
				}
			}
		});

		observer.observe(element, {
			attributes: true,
			attributeFilter: ["data-selected"],
		});

		return () => {
			observer.disconnect();
		};
	}, [preview]);

	return (
		<CommandItem
			ref={itemRef}
			className="group flex w-full cursor-pointer items-center rounded-sm px-4 py-2 text-sm transition-colors duration-200 hover:bg-accent hover:text-accent-foreground dark:hover:bg-accent/30"
			onSelect={() => {
				// iter-10 Round F: link-mode intercepts the navigate path.
				// onSelectItem (recent-items) still fires so muscle memory
				// of "I just used X" is preserved.
				if (linkMode && item && onLinkPick) {
					onSelectItem?.(item);
					onLinkPick(item);
					return;
				}
				if (item) onSelectItem?.(item);
				onSelect();
			}}
		>
			<Icon
				className="mr-2 size-4 text-muted-foreground"
				style={{
					color: iconColor || "inherit",
				}}
			/>
			{children || title}
			<div className="ml-auto inline opacity-0 transition-opacity duration-200 group-hover:opacity-100">
				<ChevronRight className="size-4 text-muted-foreground" />
			</div>
		</CommandItem>
	);
};
