"use client";
import { Kbd, KbdGroup } from "@ui/components/ui/kbd";
import { cn } from "@ui/lib/utils";
import { SearchIcon } from "lucide-react";
import { useState } from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { useShortcut } from "@/hooks/use-shortcuts";
import { findActionById } from "./global-search/actions-catalogue";
import { GlobalSearchDialog } from "./global-search/global-search-dialog";
import {
	loadLastCommand,
	recordCommand,
} from "./global-search/repeat-last-command";
import { useActionDispatcher } from "./global-search/use-action-dispatcher";
import { useUser } from "./user-provider";

export const NavSearch = ({
	placeholder,
	className,
}: {
	placeholder?: string;
	className?: string;
}) => {
	const [open, setOpen] = useState(false);
	const user = useUser();
	const dispatch = useActionDispatcher(user?.basePath ?? "");

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

	// ─── Cmd+. — repeat last command (codex delighter #8) ──────────────────
	// Resolves the persisted last command from localStorage and re-fires it
	// through the shared action dispatcher. No palette open — Linear's
	// repeat-last is intentionally invisible: hit the chord and the side-
	// effect happens. If nothing is recorded yet we no-op (the user has
	// nothing to repeat).
	useShortcut("palette.repeat-last", () => {
		const last = loadLastCommand();
		if (!last) return;
		const target = findActionById(last.id);
		if (!target) return;
		dispatch(target);
		// Re-record so the "last command" stays sticky across rapid repeats.
		recordCommand(target);
	});

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
