"use client";
import { Button } from "@ui/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@ui/components/ui/dropdown-menu";
import { RotatingText } from "@ui/components/ui/rotating-text";
import {
	BoxIcon,
	ChevronDownIcon,
	CirclePlusIcon,
	PlusIcon,
} from "lucide-react";
import { useCreateActions } from "@/hooks/use-create-actions";
import { useProjectParams } from "@/hooks/use-project-params";
import { useStatusParams } from "@/hooks/use-status-params";

export const CreateButton = () => {
	const { setParams: setStatusParams } = useStatusParams();
	const { setParams: setProjectParams } = useProjectParams();
	// Shared with the FAB CommandTray and the global "c" hotkey (see
	// use-create-actions.ts, FEAT-007 item 4) — Task/Todo/Doc/Idea keep
	// identical wording, icons, and project/milestone context across all
	// three entrypoints. Status and the quick blank-project sheet below stay
	// sidebar-only: neither has a FAB equivalent in the audit brief, and the
	// sheet is a distinct, already-wired "quick create" the wizard's own
	// "blank project" choice redirects back to — not a duplicate to collapse.
	const { task, todo, doc, idea } = useCreateActions();

	return (
		<div>
			<DropdownMenu>
				<DropdownMenuTrigger asChild>
					<Button
						type="button"
						className="w-full justify-between overflow-hidden group-data-[collapsible=icon]:h-7! group-data-[collapsible=icon]:p-2.5!"
					>
						<div className="flex items-center gap-2">
							<PlusIcon />
							<RotatingText
								text={["Create Task", "Create Status", "Create Project"]}
								duration={4000}
								y={-20}
								transition={{ duration: 0.2, ease: "easeInOut" }}
							/>
						</div>
						<ChevronDownIcon />
					</Button>
				</DropdownMenuTrigger>
				<DropdownMenuContent align="start" className="w-[236px]">
					<DropdownMenuItem onClick={task.onSelect}>
						<task.icon />
						{task.label}
					</DropdownMenuItem>
					<DropdownMenuItem
						onClick={() => setStatusParams({ createStatus: true })}
					>
						<CirclePlusIcon />
						Status
					</DropdownMenuItem>
					<DropdownMenuItem
						onClick={() => setProjectParams({ createProject: true })}
					>
						<BoxIcon />
						Project
					</DropdownMenuItem>
					<DropdownMenuSeparator />
					<DropdownMenuItem onClick={todo.onSelect}>
						<todo.icon />
						{todo.label}
					</DropdownMenuItem>
					<DropdownMenuItem onClick={doc.onSelect}>
						<doc.icon />
						{doc.label}
					</DropdownMenuItem>
					<DropdownMenuItem onClick={idea.onSelect}>
						<idea.icon />
						{idea.label}
					</DropdownMenuItem>
				</DropdownMenuContent>
			</DropdownMenu>
		</div>
	);
};
