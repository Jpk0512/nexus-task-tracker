"use client";
import { Kbd, KbdGroup } from "@ui/components/ui/kbd";
import { cn } from "@ui/lib/utils";
import { SearchIcon } from "lucide-react";
import { useState } from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { GlobalSearchDialog } from "./global-search/global-search-dialog";

export const NavSearch = ({
	placeholder,
	className,
}: {
	placeholder?: string;
	className?: string;
}) => {
	const [open, setOpen] = useState(false);

	useHotkeys(
		"ctrl+p, meta+p, ctrl+k, meta+k",
		(e) => {
			e.preventDefault();
			setOpen((o) => !o);
		},
		{
			enableOnContentEditable: true,
		},
	);

	return (
		<>
			<button
				type="button"
				onClick={() => {
					setOpen(true);
				}}
				className={cn(
					"inline-flex h-7 items-center gap-2 rounded-md border border-border bg-white/[0.02] px-2 text-start text-[12px] text-muted-foreground transition-colors hover:bg-white/[0.04] hover:text-foreground",
					className,
				)}
			>
				<SearchIcon className="size-3 shrink-0" />
				<span className="truncate">{placeholder || "Find anything…"}</span>
				<Kbd className="ml-3">
					<KbdGroup>
						<span>⌘</span>
						<span>P</span>
					</KbdGroup>
				</Kbd>
			</button>
			<GlobalSearchDialog open={open} onOpenChange={setOpen} />
		</>
	);
};
