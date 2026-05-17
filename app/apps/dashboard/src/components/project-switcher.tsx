"use client";

import { useQuery } from "@tanstack/react-query";
import {
	Command,
	CommandEmpty,
	CommandGroup,
	CommandInput,
	CommandItem,
	CommandList,
} from "@ui/components/ui/command";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogTitle,
} from "@ui/components/ui/dialog";
import { useRouter } from "next/navigation";
import { useCallback } from "react";
import { ProjectIcon } from "@/components/project-icon";
import { useUser } from "@/components/user-provider";
import { trpc } from "@/utils/trpc";

/**
 * Linear-style quick project switcher (Cmd+J / Ctrl+J).
 *
 * Small command-palette popover listing every project. Arrow keys to walk
 * the list, Enter to jump straight to that project's board. Mounted globally
 * via `GlobalShortcuts`.
 */
export const ProjectSwitcher = ({
	open,
	onOpenChange,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
}) => {
	const user = useUser();
	const router = useRouter();

	const { data, isLoading } = useQuery(
		trpc.projects.get.queryOptions(
			{ pageSize: 50 },
			{
				staleTime: 5 * 60 * 1000,
				enabled: open,
			},
		),
	);

	const projects = (data?.data ?? [])
		.filter((p) => !p.archived)
		.slice()
		.sort((a, b) => {
			const aTime = a.updatedAt ? new Date(a.updatedAt).getTime() : 0;
			const bTime = b.updatedAt ? new Date(b.updatedAt).getTime() : 0;
			return bTime - aTime;
		});

	const handleSelect = useCallback(
		(projectId: string) => {
			onOpenChange(false);
			router.push(`${user.basePath}/projects/${projectId}`);
		},
		[onOpenChange, router, user.basePath],
	);

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent
				className="top-[18%] max-h-[60vh] w-full max-w-md translate-y-0 gap-0 overflow-hidden p-0"
				showCloseButton={false}
			>
				<DialogTitle className="sr-only">Switch project</DialogTitle>
				<DialogDescription className="sr-only">
					Jump to a project board. Use arrow keys and Enter.
				</DialogDescription>
				<Command shouldFilter={true}>
					<div className="border-border/60 border-b px-3 py-2.5">
						<CommandInput
							placeholder="Switch project…"
							className="border-0 px-0 text-[13px]"
							containerClassName="border-0 p-0 h-7"
						/>
					</div>
					<CommandList className="max-h-[360px] px-1 py-1.5">
						<CommandEmpty className="py-6 text-center text-[12.5px] text-muted-foreground">
							{isLoading ? "Loading projects…" : "No projects found."}
						</CommandEmpty>
						<CommandGroup
							heading="Projects"
							className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1 [&_[cmdk-group-heading]]:font-[510] [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:text-muted-foreground [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-[0.06em]"
						>
							{projects.map((project) => (
								<CommandItem
									key={project.id}
									value={`${project.name} ${project.prefix ?? ""}`}
									onSelect={() => handleSelect(project.id)}
									className="flex items-center gap-2 px-2 py-1.5 text-[13px]"
								>
									<ProjectIcon
										className="size-4 shrink-0"
										color={project.color}
									/>
									<span className="flex-1 truncate font-[510] text-foreground tracking-[-0.005em]">
										{project.name}
									</span>
									{project.prefix ? (
										<span className="shrink-0 rounded-sm border border-border/60 px-1 py-0.5 text-[10px] text-muted-foreground tabular-nums">
											{project.prefix}
										</span>
									) : null}
								</CommandItem>
							))}
						</CommandGroup>
					</CommandList>
					<div className="border-border/60 border-t bg-muted/20 px-3 py-2 text-[11px] text-muted-foreground">
						<span>Press </span>
						<kbd className="rounded-sm border border-border/80 bg-background px-1 py-0.5 font-mono text-[10px]">
							↵
						</kbd>
						<span> to open · </span>
						<kbd className="rounded-sm border border-border/80 bg-background px-1 py-0.5 font-mono text-[10px]">
							Esc
						</kbd>
						<span> to close</span>
					</div>
				</Command>
			</DialogContent>
		</Dialog>
	);
};
