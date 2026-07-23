"use client";

import {
	Sidebar,
	SidebarContent,
	SidebarHeader,
	SidebarTrigger,
	useSidebar,
} from "@ui/components/ui/sidebar";
import { cn } from "@ui/lib/utils";
import type { LucideIcon } from "lucide-react";
import { Logo } from "@/components/logo";
import { TeamSwitcher } from "../team-switcher";
import { SidebarFocus } from "./sidebar-focus";
import { SidebarProjects } from "./sidebar-projects";
import { SidebarWorkspace } from "./sidebar-workspace";

export type NavItem = {
	title: string;
	url: string;
	icon: LucideIcon;
};

export function AppSidebar() {
	const { open } = useSidebar();

	return (
		<Sidebar collapsible="icon" className="">
			<SidebarHeader className="h-12 border-sidebar-border border-b p-0">
				<div
					className={cn(
						"group/header relative flex h-full items-center justify-between px-2",
						{
							"justify-center": !open,
						},
					)}
				>
					<div className="flex items-center gap-2 px-2">
						{/* <Logo
							className={cn("size-8", {
								"opacity-100 transition-opacity group-hover/header:opacity-0":
									!open,
							})}
						/> */}
						<TeamSwitcher />
						{/* <span
							className={cn("font-header font-medium text-foreground", {
								hidden: !open,
							})}
						>
							NEXUS
						</span> */}
					</div>
					<SidebarTrigger
						className={cn({
							"absolute inset-0 opacity-0 transition-opacity group-hover/header:opacity-100":
								!open,
						})}
					/>
				</div>
			</SidebarHeader>
			{/* scrollbar-none: Dashboard OS lock — no visible scrollbar on side panel */}
			<SidebarContent className="scrollbar-none pb-12 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
				<SidebarFocus />
				<SidebarProjects />
				<SidebarWorkspace />
			</SidebarContent>
		</Sidebar>
	);
}

export const AppSidebarWrapper = ({
	children,
}: {
	children: React.ReactNode;
}) => {
	const { open } = useSidebar();

	return (
		<div
			className={cn(
				// min-h-0 lets this flex item actually shrink to the max-height
				// budget instead of growing to fit tall page content; overflow-y-auto
				// makes THIS pane (not the window) the scroll owner for any page
				// that doesn't manage its own internal scroll.
				"relative flex max-h-[calc(100vh-48px)] min-h-0 flex-1 flex-col overflow-y-auto rounded-lg bg-background p-4 [&:has([data-slot=sidebar-wrapper])]:p-0",
				{
					"md:max-w-[calc(100vw-240px)]": open,
				},
			)}
		>
			{children}
		</div>
	);
};
