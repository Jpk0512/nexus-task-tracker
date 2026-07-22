"use client";

import {
	SidebarGroup,
	SidebarGroupContent,
	SidebarGroupLabel,
	SidebarMenu,
	SidebarMenuButton,
	SidebarMenuItem,
} from "@ui/components/ui/sidebar";
import {
	CheckSquareIcon,
	FolderKanbanIcon,
	HomeIcon,
	PlusCircleIcon,
	TargetIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useUser } from "../user-provider";

/**
 * Workspace cluster — Dashboard OS IA lock:
 * Home · To-do · Focus · Projects · Create Project
 *
 * To-do is the primary work surface (prominent, second slot).
 * Brain dump lives in the global header Dump modal (⌘J), not a nav page.
 */
export function SidebarFocus() {
	const user = useUser();
	const pathname = usePathname();
	const base = user.basePath;

	const items = [
		{
			href: base,
			label: "Home",
			icon: HomeIcon,
			active: pathname === base || pathname === `${base}/`,
		},
		{
			href: `${base}/todos`,
			label: "To-do",
			icon: CheckSquareIcon,
			active: pathname.startsWith(`${base}/todos`),
		},
		{
			href: `${base}/focus`,
			label: "Focus",
			icon: TargetIcon,
			active:
				pathname.startsWith(`${base}/focus`) ||
				pathname.startsWith(`${base}/lens`) ||
				pathname.startsWith(`${base}/my-tasks`) ||
				pathname.startsWith(`${base}/views/my-tasks`) ||
				pathname.startsWith(`${base}/triage`),
		},
		{
			href: `${base}/projects`,
			label: "Projects",
			icon: FolderKanbanIcon,
			active:
				pathname.startsWith(`${base}/projects`) &&
				!pathname.includes("/create-project"),
		},
		{
			href: `${base}/create-project`,
			label: "Create Project",
			icon: PlusCircleIcon,
			active:
				pathname.startsWith(`${base}/create-project`) ||
				pathname.startsWith(`${base}/starter`),
		},
	] as const;

	return (
		<SidebarGroup>
			<SidebarGroupLabel>Workspace</SidebarGroupLabel>
			<SidebarGroupContent>
				<SidebarMenu>
					{items.map((item) => (
						<SidebarMenuItem key={item.href}>
							<SidebarMenuButton
								asChild
								isActive={item.active}
								tooltip={item.label}
							>
								<Link href={item.href}>
									<item.icon />
									<span>{item.label}</span>
								</Link>
							</SidebarMenuButton>
						</SidebarMenuItem>
					))}
				</SidebarMenu>
			</SidebarGroupContent>
		</SidebarGroup>
	);
}
