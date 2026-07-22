"use client";

import { Button } from "@ui/components/ui/button";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@ui/components/ui/popover";
import { cn } from "@ui/lib/utils";
import {
	BoxIcon,
	CheckSquareIcon,
	FileTextIcon,
	ListPlusIcon,
	PlusIcon,
	SparklesIcon,
} from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useTaskParams } from "@/hooks/use-task-params";
import { useUser } from "./user-provider";

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
	const user = useUser();
	const router = useRouter();
	const pathname = usePathname();
	const { setParams } = useTaskParams();
	const [open, setOpen] = useState(false);
	const dialogOpen = useAnyDialogOpen();

	// Context-awareness: if we're inside a project, "New task" pre-fills it.
	const inProject = /\/projects\/[^/]+/.test(pathname);

	if (dialogOpen) return null;

	const close = () => setOpen(false);
	const base = user.basePath;

	const actions = [
		{
			label: inProject ? "New task in project" : "New task",
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
				router.push(`${base}/todos`);
			},
		},
		{
			label: "New doc",
			hint: "",
			icon: FileTextIcon,
			onSelect: () => {
				close();
				router.push(`${base}/documents/create`);
			},
		},
		{
			label: "New project",
			hint: "",
			icon: BoxIcon,
			onSelect: () => {
				close();
				router.push(`${base}/create-project`);
			},
		},
		{
			label: "Start from an idea",
			hint: "",
			icon: SparklesIcon,
			onSelect: () => {
				close();
				router.push(`${base}/create-project/starter`);
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
								key={a.label}
								type="button"
								onClick={a.onSelect}
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
