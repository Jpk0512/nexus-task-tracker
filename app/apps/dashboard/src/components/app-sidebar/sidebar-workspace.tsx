"use client";

import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@ui/components/ui/collapsible";
import {
	SidebarGroup,
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
import {
	ActivityIcon,
	BookOpenIcon,
	BrainIcon,
	ChevronRightIcon,
	FileTextIcon,
	HeartPulseIcon,
	KeyRoundIcon,
	LibraryIcon,
	MessageCircleIcon,
	MessageSquareTextIcon,
	MicIcon,
	ServerIcon,
	Settings2Icon,
	SettingsIcon,
	ShieldCheckIcon,
	SparklesIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useUser } from "../user-provider";

type SubNavItem = {
	href: string;
	label: string;
	icon: typeof BrainIcon;
	active: boolean;
};

type NavItem = {
	href: string;
	label: string;
	icon: typeof BrainIcon;
	active: boolean;
	/**
	 * Real destinations folded into this nav row's `active` check but not
	 * reachable from its own href — e.g. "Vault" also covers the separate
	 * MCP Servers / API Keys admin pages. Surfaced as an inline sub-tab
	 * disclosure (FEAT-006 item 3) instead of leaving them one-click-away
	 * only via direct URL or a second Settings hop.
	 */
	subItems?: SubNavItem[];
};

/**
 * Brain · Insight · Ops clusters (Dashboard OS IA).
 * Soft icons stay out of the sidebar — plain lucide strokes only.
 * Scrollbar hidden via global CSS on [data-sidebar=content].
 */
export function SidebarWorkspace() {
	const user = useUser();
	const pathname = usePathname();
	const base = user.basePath;

	const brain: NavItem[] = [
		{
			href: `${base}/chat`,
			label: "Chat",
			icon: MessageCircleIcon,
			active: pathname.startsWith(`${base}/chat`),
		},
		{
			href: `${base}/notes`,
			label: "Notes",
			icon: BrainIcon,
			// alias knowledge until full rename
			active:
				pathname.startsWith(`${base}/notes`) ||
				pathname.startsWith(`${base}/knowledge`),
			subItems: [
				{
					href: `${base}/knowledge`,
					label: "Knowledge",
					icon: BrainIcon,
					active: pathname.startsWith(`${base}/knowledge`),
				},
			],
		},
		{
			href: `${base}/skills`,
			label: "Skills",
			icon: BookOpenIcon,
			active:
				pathname.startsWith(`${base}/skills`) ||
				pathname.startsWith(`${base}/library`),
			subItems: [
				{
					href: `${base}/library`,
					label: "Library",
					icon: LibraryIcon,
					active: pathname.startsWith(`${base}/library`),
				},
			],
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
			label: "Site Docs",
			icon: FileTextIcon,
			active: pathname.startsWith(`${base}/documents`),
		},
		{
			href: `${base}/agent-config`,
			label: "Agent Config",
			icon: Settings2Icon,
			active: pathname.startsWith(`${base}/agent-config`),
		},
	];

	const insight: NavItem[] = [
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
	];

	const ops: NavItem[] = [
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
			subItems: [
				{
					href: `${base}/settings/mcp-servers`,
					label: "MCP Servers",
					icon: ServerIcon,
					active: pathname.startsWith(`${base}/settings/mcp-servers`),
				},
				{
					href: `${base}/settings/api-keys`,
					label: "API Keys",
					icon: KeyRoundIcon,
					active: pathname.startsWith(`${base}/settings/api-keys`),
				},
			],
		},
		{
			href: `${base}/settings/general`,
			label: "Settings",
			icon: SettingsIcon,
			active: pathname.startsWith(`${base}/settings`),
		},
	];

	const render = (items: readonly NavItem[]) =>
		items.map((item) => <NavRow key={item.href} item={item} />);

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
					<SidebarMenu>{render(ops)}</SidebarMenu>
				</SidebarGroupContent>
			</SidebarGroup>
		</>
	);
}

function NavRow({ item }: { item: NavItem }) {
	const hasSubItems = Boolean(item.subItems?.length);

	if (!hasSubItems) {
		return (
			<SidebarMenuItem>
				<SidebarMenuButton asChild isActive={item.active} tooltip={item.label}>
					<Link href={item.href}>
						<item.icon />
						<span>{item.label}</span>
					</Link>
				</SidebarMenuButton>
			</SidebarMenuItem>
		);
	}

	return (
		<SidebarMenuItem>
			<Collapsible
				key={`${item.href}-${item.active ? "active" : "inactive"}`}
				defaultOpen={item.active}
				className="group/nav-collapsible"
			>
				<SidebarMenuButton asChild isActive={item.active} tooltip={item.label}>
					<Link href={item.href}>
						<item.icon />
						<span>{item.label}</span>
					</Link>
				</SidebarMenuButton>
				<CollapsibleTrigger asChild>
					<SidebarMenuAction
						className="group-data-[state=open]/nav-collapsible:rotate-90"
						aria-label={`Show destinations grouped under ${item.label}`}
					>
						<ChevronRightIcon className="size-3 transition-transform" />
					</SidebarMenuAction>
				</CollapsibleTrigger>
				<CollapsibleContent>
					<SidebarMenuSub>
						{item.subItems?.map((sub) => (
							<SidebarMenuSubItem key={sub.href}>
								<SidebarMenuSubButton asChild isActive={sub.active}>
									<Link href={sub.href}>
										<sub.icon className="size-4 stroke-[1.5]" />
										<span>{sub.label}</span>
									</Link>
								</SidebarMenuSubButton>
							</SidebarMenuSubItem>
						))}
					</SidebarMenuSub>
				</CollapsibleContent>
			</Collapsible>
		</SidebarMenuItem>
	);
}
