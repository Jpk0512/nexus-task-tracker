"use client";
import {
	createContext,
	type ReactNode,
	useContext,
	useMemo,
	useState,
} from "react";
import type { GlobalSearchItem } from "./types";

/**
 * Link-mode descriptor — iter-10 Round F.
 *
 * When set, the palette is acting as an entity picker for a backlinks
 * sidebar (project / task / note detail). Result-items honour
 * `onLinkPick` instead of navigating away, and the dialog title shifts
 * to explain the operation.
 */
export type PaletteLinkMode = {
	/** Entity kind the picker should constrain to. */
	entity: "prompts" | "agents" | "knowledge" | "skills" | "documents";
	/** Where the link originates from. Mostly informational for the UI. */
	sourceType: "project" | "task" | "note" | "agent";
	sourceId: string;
};

type GlobalSearchContextValue = {
	onOpenChange: (open: boolean) => void;
	/**
	 * Called by every result-item just before it navigates / closes the
	 * palette. Used to record entity selections in the recent-items list
	 * (codex amendment #4 / palette tabs round).
	 *
	 * Result-items should call this *before* `onOpenChange(false)` so the
	 * recent list reflects the user's most-recent intent even when the
	 * subsequent close handler clears local search state.
	 */
	onSelectItem?: (item: GlobalSearchItem) => void;
	basePath: string;
	preview: ReactNode | null;
	setPreview: (preview: ReactNode | null) => void;
	/**
	 * iter-10 Round F: when in link mode, selection fires this handler
	 * instead of navigating. Result-items branch on `linkMode != null` to
	 * decide which behaviour to apply.
	 */
	linkMode: PaletteLinkMode | null;
	onLinkPick?: (item: GlobalSearchItem) => void;
};

const GlobalSearchContext = createContext<GlobalSearchContextValue | null>(
	null,
);

export const GlobalSearchProvider = ({
	children,
	onOpenChange,
	basePath,
	onSelectItem,
	linkMode = null,
	onLinkPick,
}: {
	children: React.ReactNode;
	onOpenChange: (open: boolean) => void;
	onSelectItem?: (item: GlobalSearchItem) => void;
	basePath: string;
	linkMode?: PaletteLinkMode | null;
	onLinkPick?: (item: GlobalSearchItem) => void;
}) => {
	const [preview, setPreview] = useState<ReactNode | null>(null);

	const contextValue = useMemo<GlobalSearchContextValue>(
		() => ({
			onOpenChange,
			onSelectItem,
			basePath,
			preview,
			setPreview,
			linkMode,
			onLinkPick,
		}),
		[onOpenChange, onSelectItem, basePath, preview, linkMode, onLinkPick],
	);

	return (
		<GlobalSearchContext.Provider value={contextValue}>
			{children}
		</GlobalSearchContext.Provider>
	);
};

export const useGlobalSearch = () => {
	const context = useContext(GlobalSearchContext);
	if (!context) {
		throw new Error("useGlobalSearch must be used within GlobalSearchProvider");
	}
	return context;
};
