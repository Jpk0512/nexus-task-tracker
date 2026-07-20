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
	ActivityIcon,
	BookOpenIcon,
	BrainIcon,
	FileTextIcon,
	HeartPulseIcon,
	MessageSquareTextIcon,
	MicIcon,
	SettingsIcon,
	ShieldCheckIcon,
	SparklesIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useUser } from "../user-provider";

/**
 * Brain · Insight · Ops clusters (Dashboard OS IA).
 * Soft icons stay out of the sidebar — plain lucide strokes only.
 * Scrollbar hidden via global CSS on [data-sidebar=content].
 */
export function SidebarWorkspace() {
	const user = useUser();
	const pathname = usePathname();
	const base = user.basePath;

	const brain = [
		{
			href: `${base}/notes`,
			label: "Notes",
			icon: BrainIcon,
			// alias knowledge until full rename
			active:
				pathname.startsWith(`${base}/notes`) ||
				pathname.startsWith(`${base}/knowledge`),
		},
		{
			href: `${base}/skills`,
			label: "Skills",
			icon: BookOpenIcon,
			active:
				pathname.startsWith(`${base}/skills`) ||
				pathname.startsWith(`${base}/library`),
		},
		{
			href: `${base}/meetings`,
			label: "Meetings",
			icon: MicIcon,
			active: pathname.startsWith(`${base}/meetings`),
		},
		{
			href: `${base}/prompts`,
			label: "Prompts",
			icon: MessageSquareTextIcon,
			active: pathname.startsWith(`${base}/prompts`),
		},
		{
			href: `${base}/documents`,
			label: "Documents",
			icon: FileTextIcon,
			active: pathname.startsWith(`${base}/documents`),
		},
	] as const;

	const insight = [
		{
			href: `${base}/health`,
			label: "Health",
			icon: HeartPulseIcon,
			active: pathname.startsWith(`${base}/health`),
		},
		{
			href: `${base}/activity`,
			label: "Activity",
			icon: ActivityIcon,
			active: pathname.startsWith(`${base}/activity`),
		},
		{
			href: `${base}/rituals`,
			label: "Rituals",
			icon: SparklesIcon,
			active: pathname.startsWith(`${base}/rituals`),
		},
	] as const;

	const ops = [
		{
			href: `${base}/vault`,
			label: "Vault",
			icon: ShieldCheckIcon,
			active:
				pathname.startsWith(`${base}/vault`) ||
				pathname.startsWith(`${base}/mcps`) ||
				pathname.startsWith(`${base}/secrets`) ||
				pathname.startsWith(`${base}/settings/mcp-servers`) ||
				pathname.startsWith(`${base}/settings/api-keys`),
		},
		{
			href: `${base}/settings/general`,
			label: "Settings",
			icon: SettingsIcon,
			active: pathname.startsWith(`${base}/settings`),
		},
	] as const;

	const render = (
		items: readonly {
			href: string;
			label: string;
			icon: typeof BrainIcon;
			active: boolean;
		}[],
	) =>
		items.map((item) => (
			<SidebarMenuItem key={item.href}>
				<SidebarMenuButton asChild isActive={item.active} tooltip={item.label}>
					<Link href={item.href}>
						<item.icon />
						<span>{item.label}</span>
					</Link>
				</SidebarMenuButton>
			</SidebarMenuItem>
		));

	return (
		<>
			<SidebarGroup>
				<SidebarGroupLabel>Brain</SidebarGroupLabel>
				<SidebarGroupContent>
					<SidebarMenu>{render(brain)}</SidebarMenu>
				</SidebarGroupContent>
			</SidebarGroup>

			<SidebarGroup>
				<SidebarGroupLabel>Insight</SidebarGroupLabel>
				<SidebarGroupContent>
					<SidebarMenu>{render(insight)}</SidebarMenu>
				</SidebarGroupContent>
			</SidebarGroup>

			<SidebarGroup>
				<SidebarGroupLabel>Ops</SidebarGroupLabel>
				<SidebarGroupContent>
					<SidebarMenu>
						{ops.map((item) => (
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
		</>
	);
}
