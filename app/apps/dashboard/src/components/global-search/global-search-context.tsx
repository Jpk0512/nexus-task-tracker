"use client";
import {
	createContext,
	type ReactNode,
	useContext,
	useMemo,
	useState,
} from "react";
import type { GlobalSearchItem } from "./types";

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
};

const GlobalSearchContext = createContext<GlobalSearchContextValue | null>(
	null,
);

export const GlobalSearchProvider = ({
	children,
	onOpenChange,
	basePath,
	onSelectItem,
}: {
	children: React.ReactNode;
	onOpenChange: (open: boolean) => void;
	onSelectItem?: (item: GlobalSearchItem) => void;
	basePath: string;
}) => {
	const [preview, setPreview] = useState<ReactNode | null>(null);

	const contextValue = useMemo<GlobalSearchContextValue>(
		() => ({
			onOpenChange,
			onSelectItem,
			basePath,
			preview,
			setPreview,
		}),
		[onOpenChange, onSelectItem, basePath, preview],
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
