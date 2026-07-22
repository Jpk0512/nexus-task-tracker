"use client";

import { Button } from "@ui/components/ui/button";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@ui/components/ui/popover";
import { cn } from "@ui/lib/utils";
import { PlusIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { useCreateActions } from "@/hooks/use-create-actions";

// Bottom-right floating command tray: a single FAB that expands into quick
// create actions. Hidden whenever a real Dialog/Sheet/AlertDialog is open so
// it never overlaps an active modal.
//
// IMPORTANT: Radix Popover.Content itself exposes role="dialog" +
// data-state="open", so the detection selector MUST exclude our own popover
// (tagged `data-command-tray`) — otherwise opening the tray makes the hook
// see a "dialog", the component returns null, the popover unmounts, the state
// flips back, and it re-renders into an infinite loop (= app freeze).

const useAnyDialogOpen = () => {
	const [open, setOpen] = useState(false);

	useEffect(() => {
		const check = () => {
			// Any open dialog/alertdialog EXCEPT the command tray's own popover.
			const el = document.querySelector(
				'[role="dialog"][data-state="open"]:not([data-command-tray]), [role="alertdialog"][data-state="open"]',
			);
			setOpen(!!el);
		};
		check();
		const obs = new MutationObserver(check);
		obs.observe(document.body, {
			subtree: true,
			childList: true,
			attributes: true,
			attributeFilter: ["data-state"],
		});
		return () => obs.disconnect();
	}, []);

	return open;
};

export const CommandTray = () => {
	const [open, setOpen] = useState(false);
	const dialogOpen = useAnyDialogOpen();
	// Shared with the sidebar CreateButton and the global "c" hotkey (see
	// use-create-actions.ts) so all three create entrypoints agree on
	// wording, icons, and ambient project/milestone context (FEAT-007).
	const { list: actions } = useCreateActions();

	if (dialogOpen) return null;

	const close = () => setOpen(false);

	return (
		<div className="pointer-events-none fixed right-5 bottom-5 z-40">
			<Popover open={open} onOpenChange={setOpen}>
				<PopoverTrigger asChild>
					<Button
						aria-label="Quick actions"
						className={cn(
							"pointer-events-auto h-11 w-11 rounded-full p-0 shadow-md",
							"bg-foreground text-background hover:bg-foreground/90",
							"transition-transform data-[state=open]:rotate-45",
						)}
					>
						<PlusIcon className="size-5" />
					</Button>
				</PopoverTrigger>
				<PopoverContent
					data-command-tray
					align="end"
					side="top"
					sideOffset={8}
					className="pointer-events-auto w-52 p-1"
				>
					<div className="flex flex-col">
						{actions.map((a) => (
							<button
								key={a.id}
								type="button"
								onClick={() => {
									close();
									a.onSelect();
								}}
								className="flex items-center gap-2 rounded-sm px-2 py-1.5 text-left text-[13px] text-foreground transition-colors hover:bg-accent"
							>
								<a.icon className="size-3.5 text-muted-foreground" />
								<span className="flex-1">{a.label}</span>
								{a.hint ? (
									<kbd className="rounded-sm bg-muted px-1 text-[10px] text-muted-foreground">
										{a.hint}
									</kbd>
								) : null}
							</button>
						))}
					</div>
				</PopoverContent>
			</Popover>
		</div>
	);
};
