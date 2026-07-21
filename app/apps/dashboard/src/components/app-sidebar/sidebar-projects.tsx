"use client";

import { useDroppable } from "@dnd-kit/core";
import { useQuery } from "@tanstack/react-query";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@ui/components/ui/collapsible";
import {
	SidebarGroup,
	SidebarGroupAction,
	SidebarGroupContent,
	SidebarGroupLabel,
	SidebarMenu,
	SidebarMenuAction,
	SidebarMenuButton,
	SidebarMenuItem,
	SidebarMenuSub,
	SidebarMenuSubButton,
	SidebarMenuSubItem,
} from "@ui/components/ui/sidebar";
import { cn } from "@ui/lib/utils";
import {
	BookOpenIcon,
	CheckSquareIcon,
	ChevronRightIcon,
	FileTextIcon,
	KanbanIcon,
	LayoutListIcon,
	MegaphoneIcon,
	PlusIcon,
	UsersIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { projectDroppableId } from "@/components/todos/todo-dnd-provider";
import { trpc } from "@/utils/trpc";
import { ProjectIcon } from "../project-icon";
import { ProjectContextMenu } from "../projects/context-menu";
import { useUser } from "../user-provider";

type ProjectSubTab = {
	label: string;
	segment: string;
	icon: React.ComponentType<{ className?: string }>;
};

// Tabs nested under the active project. Coordinated with impl-project-tabs
// (ProjectTabs component) — keep order/labels in sync.
const PROJECT_SUB_TABS: ProjectSubTab[] = [
	{ label: "Board", segment: "", icon: KanbanIcon },
	{ label: "Docs", segment: "docs", icon: FileTextIcon },
	{ label: "Todos", segment: "todos", icon: CheckSquareIcon },
	{ label: "Library", segment: "library", icon: BookOpenIcon },
	{ label: "Updates", segment: "updates", icon: MegaphoneIcon },
	{ label: "Views", segment: "views", icon: LayoutListIcon },
	{ label: "Members", segment: "members", icon: UsersIcon },
];

export function SidebarProjects() {
	const user = useUser();
	const pathname = usePathname();
	const { data: projects } = useQuery(
		trpc.projects.get.queryOptions({
			pageSize: 10,
		}),
	);

	return (
		<SidebarGroup>
			<Link href={`${user.basePath}/projects`}>
				<SidebarGroupLabel>Projects</SidebarGroupLabel>
			</Link>
			<SidebarGroupAction asChild>
				<Link href={`${user.basePath}/create-project`} aria-label="Create project">
					<PlusIcon />
				</Link>
			</SidebarGroupAction>
			<SidebarGroupContent>
				<SidebarMenu>
					{projects?.data.map((project) => {
						const projectBase = `${user.basePath}/projects/${project.id}`;
						const isActive =
							pathname === projectBase ||
							pathname.startsWith(`${projectBase}/`);

						return (
							<ProjectRow
								key={project.id}
								project={project}
								projectBase={projectBase}
								isActive={isActive}
								pathname={pathname}
							/>
						);
					})}
				</SidebarMenu>
			</SidebarGroupContent>
		</SidebarGroup>
	);
}

/**
 * A single project entry in the sidebar. Also acts as a drop target for todos
 * dragged out of the /todos view (see `TodoDndProvider`). The `data` payload
 * carries the project name so the provider can surface "Moved to {name}" in
 * the success toast without a second lookup.
 */
function ProjectRow({
	project,
	projectBase,
	isActive,
	pathname,
}: {
	project: { id: string; name: string } & Record<string, unknown>;
	projectBase: string;
	isActive: boolean;
	pathname: string;
}) {
	const { setNodeRef, isOver } = useDroppable({
		id: projectDroppableId(project.id),
		data: { name: project.name, kind: "project" },
	});

	return (
		<ProjectContextMenu project={project as any}>
			<SidebarMenuItem ref={setNodeRef}>
				<Collapsible
					key={`${project.id}-${isActive ? "active" : "inactive"}`}
					defaultOpen={isActive}
					className="group/project-collapsible"
				>
					<div
						className={cn(
							"relative rounded-md transition-shadow",
							isOver &&
								"ring-2 ring-primary/70 ring-offset-1 ring-offset-sidebar",
						)}
					>
						<SidebarMenuButton
							asChild
							tooltip={project.name}
							isActive={isActive}
						>
							<Link href={projectBase}>
								<ProjectIcon />
								<span>{project.name}</span>
							</Link>
						</SidebarMenuButton>
						{isActive && (
							<CollapsibleTrigger asChild>
								<SidebarMenuAction
									className="group-data-[state=open]/project-collapsible:rotate-90"
									aria-label="Toggle project tabs"
								>
									<ChevronRightIcon className="size-3 transition-transform" />
								</SidebarMenuAction>
							</CollapsibleTrigger>
						)}
					</div>
					{isActive && (
						<CollapsibleContent>
							<SidebarMenuSub>
								{PROJECT_SUB_TABS.map((tab) => {
									const href = tab.segment
										? `${projectBase}/${tab.segment}`
										: projectBase;
									const isTabActive = tab.segment
										? pathname === href || pathname.startsWith(`${href}/`)
										: pathname === projectBase;
									const Icon = tab.icon;
									return (
										<SidebarMenuSubItem key={tab.label}>
											<SidebarMenuSubButton asChild isActive={isTabActive}>
												<Link href={href}>
													<Icon className="size-4 stroke-[1.5]" />
													<span>{tab.label}</span>
												</Link>
											</SidebarMenuSubButton>
										</SidebarMenuSubItem>
									);
								})}
							</SidebarMenuSub>
						</CollapsibleContent>
					)}
				</Collapsible>
			</SidebarMenuItem>
		</ProjectContextMenu>
	);
}
