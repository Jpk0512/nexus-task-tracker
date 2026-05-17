"use client";

import {
	SidebarGroup,
	SidebarGroupContent,
	SidebarMenu,
	SidebarMenuButton,
	SidebarMenuItem,
} from "@ui/components/ui/sidebar";
import { LayersIcon, MessagesSquareIcon, SunIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useUser } from "../user-provider";

export function SidebarFocus() {
	const user = useUser();
	const pathname = usePathname();

	const isMyTasksActive = pathname === `${user.basePath}/views/my-tasks`;
	const isLensActive = pathname.startsWith(`${user.basePath}/lens`);
	const isChatActive = pathname.startsWith(`${user.basePath}/chat`);

	return (
		<SidebarGroup>
			<SidebarGroupContent>
				<SidebarMenu>
					<SidebarMenuItem>
						<SidebarMenuButton asChild isActive={isMyTasksActive}>
							<Link href={`${user.basePath}/views/my-tasks`}>
								<LayersIcon />
								<span>My Tasks</span>
							</Link>
						</SidebarMenuButton>
					</SidebarMenuItem>
					{/* Codex delighter #2 — Things-style personal lens. Sits directly
						  under "My Tasks" so the personal-overview cluster stays
						  contiguous. */}
					<SidebarMenuItem>
						<SidebarMenuButton asChild isActive={isLensActive}>
							<Link href={`${user.basePath}/lens`}>
								<SunIcon />
								<span>Lens</span>
							</Link>
						</SidebarMenuButton>
					</SidebarMenuItem>
				</SidebarMenu>
			</SidebarGroupContent>
			<SidebarGroupContent>
				<SidebarMenu>
					<SidebarMenuItem>
						<SidebarMenuButton asChild isActive={isChatActive}>
							<Link href={`${user.basePath}/chat`}>
								<MessagesSquareIcon />
								<span>Chat</span>
							</Link>
						</SidebarMenuButton>
					</SidebarMenuItem>
				</SidebarMenu>
			</SidebarGroupContent>
		</SidebarGroup>
	);
}
