"use client";

import {
	SidebarGroup,
	SidebarGroupContent,
	SidebarGroupLabel,
	SidebarMenu,
	SidebarMenuBadge,
	SidebarMenuButton,
	SidebarMenuItem,
} from "@ui/components/ui/sidebar";
import {
	FolderKanbanIcon,
	HomeIcon,
	InboxIcon,
	PlusCircleIcon,
	TargetIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useUser } from "../user-provider";

/**
 * Workspace cluster — Dashboard OS IA lock:
 * Home · Focus · Capture · Projects · Create Project
 * (Chat demoted — available via header/⌘K, not peer of Home.)
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
			href: `${base}/capture`,
			label: "Capture",
			icon: InboxIcon,
			active:
				pathname.startsWith(`${base}/capture`) ||
				pathname.startsWith(`${base}/todos`) ||
				pathname.startsWith(`${base}/inbox`),
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
							<SidebarMenuButton asChild isActive={item.active} tooltip={item.label}>
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
