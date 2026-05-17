"use client";

import { Button } from "@ui/components/ui/button";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@ui/components/ui/popover";
import { cn } from "@ui/lib/utils";
import {
	CheckSquareIcon,
	FileTextIcon,
	ListPlusIcon,
	PlusIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useTaskParams } from "@/hooks/use-task-params";
import { useUser } from "./user-provider";

// Bottom-right floating command tray: a single FAB that expands into three
// quick actions — New task, New todo, New doc. Hidden whenever a Radix
// dialog/sheet is open so it never overlaps an active modal.
//
// We watch the document for any element with `[role="dialog"][data-state="open"]`,
// which covers Dialog, AlertDialog, and Sheet primitives. A MutationObserver
// catches mount/unmount changes without polling.

const useAnyDialogOpen = () => {
	const [open, setOpen] = useState(false);

	useEffect(() => {
		const check = () => {
			const el = document.querySelector(
				'[role="dialog"][data-state="open"], [role="alertdialog"][data-state="open"]',
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
	const user = useUser();
	const router = useRouter();
	const { setParams } = useTaskParams();
	const [open, setOpen] = useState(false);
	const dialogOpen = useAnyDialogOpen();

	if (dialogOpen) return null;

	const close = () => setOpen(false);

	const actions = [
		{
			label: "New task",
			hint: "c",
			icon: ListPlusIcon,
			onSelect: () => {
				close();
				setParams({ createTask: true });
			},
		},
		{
			label: "New todo",
			hint: "N",
			icon: CheckSquareIcon,
			onSelect: () => {
				close();
				router.push(`${user.basePath}/todos`);
			},
		},
		{
			label: "New doc",
			hint: "",
			icon: FileTextIcon,
			onSelect: () => {
				close();
				router.push(`${user.basePath}/documents/create`);
			},
		},
	];

	return (
		<div className="pointer-events-none fixed right-5 bottom-5 z-40">
			<Popover open={open} onOpenChange={setOpen}>
				<PopoverTrigger asChild>
					<Button
						aria-label="Quick actions"
						className={cn(
							"pointer-events-auto h-10 w-10 rounded-full p-0 shadow-md",
							"bg-foreground text-background hover:bg-foreground/90",
							"transition-transform data-[state=open]:rotate-45",
						)}
					>
						<PlusIcon className="size-4" />
					</Button>
				</PopoverTrigger>
				<PopoverContent
					align="end"
					side="top"
					sideOffset={8}
					className="pointer-events-auto w-48 p-1"
				>
					<div className="flex flex-col">
						{actions.map((a) => (
							<button
								key={a.label}
								type="button"
								onClick={a.onSelect}
								className="flex items-center gap-2 rounded-sm px-2 py-1.5 text-left text-[13px] text-foreground transition-colors hover:bg-accent"
							>
								<a.icon className="size-3.5 text-muted-foreground" />
								<span className="flex-1">{a.label}</span>
								{a.hint && (
									<kbd className="rounded-sm bg-muted px-1 text-[10px] text-muted-foreground">
										{a.hint}
									</kbd>
								)}
							</button>
						))}
					</div>
				</PopoverContent>
			</Popover>
		</div>
	);
};
