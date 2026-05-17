"use client";

import { useQuery } from "@tanstack/react-query";
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
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@ui/components/ui/tooltip";
import {
	BookOpenIcon,
	BrainIcon,
	CheckSquareIcon,
	ClipboardClockIcon,
	FileTextIcon,
	HomeIcon,
	InboxIcon,
	LayersIcon,
	MessageSquareTextIcon,
	SettingsIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useMemo } from "react";
import { trpc } from "@/utils/trpc";
import { useUser } from "../user-provider";

// Mirror of inbox/use-inbox.tsx::isMention so the sidebar can flag
// mentions without mounting the full InboxProvider.
const MENTION_RE = /(^|\s)@[a-z0-9_-]{2,}/i;
const itemIsMention = (item: {
	source?: string | null;
	display?: string | null;
	subtitle?: string | null;
	content?: string | null;
}) => {
	if (item.source === "mention") return true;
	const haystack = `${item.display ?? ""} ${item.subtitle ?? ""} ${
		item.content ?? ""
	}`;
	return MENTION_RE.test(haystack);
};

export function SidebarWorkspace() {
	const user = useUser();
	const pathname = usePathname();

	const isHomeActive = pathname === user.basePath;
	const isInboxActive = pathname === `${user.basePath}/inbox`;
	const isRecurringActive = pathname === `${user.basePath}/recurring`;
	const isDocumentsActive = pathname.startsWith(`${user.basePath}/documents`);
	const isLibraryActive = pathname.startsWith(`${user.basePath}/library`);
	const isTriageActive = pathname.startsWith(`${user.basePath}/triage`);
	const isTodosActive = pathname.startsWith(`${user.basePath}/todos`);
	const isKnowledgeActive = pathname.startsWith(`${user.basePath}/knowledge`);
	const isPromptsActive = pathname.startsWith(`${user.basePath}/prompts`);
	const isSettingsActive = pathname.startsWith(`${user.basePath}/settings`);

	const { data: inboxUnread } = useQuery(trpc.inbox.count.queryOptions());

	// Lightweight mentions probe: fetch a small page of pending inbox rows and
	// run the same mention heuristic the inbox tab uses. We only need to know
	// whether there's ≥1 unseen mention to render the orange dot.
	const { data: inboxSlice } = useQuery(
		trpc.inbox.get.queryOptions({ status: ["pending"], pageSize: 50 }),
	);
	// Iter8 a11y: track the actual mention count, not just a boolean, so the
	// SR-only fallback can read "3 unread mentions" instead of a generic hint.
	const unreadMentionCount = useMemo(() => {
		const items = (inboxSlice?.data ?? []) as Array<{
			seen?: boolean | null;
			source?: string | null;
			display?: string | null;
			subtitle?: string | null;
			content?: string | null;
		}>;
		return items.filter((i) => !i.seen && itemIsMention(i)).length;
	}, [inboxSlice]);
	const hasUnreadMention = unreadMentionCount > 0;

	return (
		<>
			{/* My work */}
			<SidebarGroup>
				<SidebarGroupLabel>My work</SidebarGroupLabel>
				<SidebarGroupContent>
					<SidebarMenu>
						<SidebarMenuItem>
							<SidebarMenuButton asChild isActive={isHomeActive}>
								<Link href={`${user.basePath}`}>
									<HomeIcon />
									<span>Home</span>
								</Link>
							</SidebarMenuButton>
						</SidebarMenuItem>
						<SidebarMenuItem>
							<SidebarMenuButton asChild isActive={isTodosActive}>
								<Link href={`${user.basePath}/todos`}>
									<CheckSquareIcon />
									<span>To-do</span>
								</Link>
							</SidebarMenuButton>
						</SidebarMenuItem>
						<SidebarMenuItem>
							<SidebarMenuButton asChild isActive={isTriageActive}>
								<Link href={`${user.basePath}/triage`}>
									<LayersIcon />
									<span>Now / Next / Later</span>
								</Link>
							</SidebarMenuButton>
						</SidebarMenuItem>
						<SidebarMenuItem>
							<SidebarMenuButton asChild isActive={isInboxActive}>
								<Link href={`${user.basePath}/inbox`}>
									<InboxIcon />
									<span>Inbox</span>
								</Link>
							</SidebarMenuButton>
							{(!!inboxUnread || hasUnreadMention) && (
								<SidebarMenuBadge className="flex items-center gap-1.5 text-muted-foreground">
									{hasUnreadMention && (
										<Tooltip>
											<TooltipTrigger asChild>
												{/*
												 * Iter8 a11y: pair the decorative amber dot with
												 * an sr-only span so screen-reader users learn
												 * about the mention without relying on the
												 * hover-only tooltip.
												 */}
												<span className="inline-flex items-center">
													<span
														aria-hidden="true"
														className="inline-block size-1.5 rounded-full bg-amber-400 shadow-[0_0_0_2px_var(--sidebar)]"
													/>
													<span className="sr-only">
														{unreadMentionCount} unread mention
														{unreadMentionCount === 1 ? "" : "s"}
													</span>
												</span>
											</TooltipTrigger>
											<TooltipContent side="right" sideOffset={6}>
												{unreadMentionCount} unread mention
												{unreadMentionCount === 1 ? "" : "s"}
											</TooltipContent>
										</Tooltip>
									)}
									{!!inboxUnread && (
										<span title={`${inboxUnread} unread inbox items`}>
											<span className="sr-only">Unread inbox items: </span>
											{inboxUnread}
										</span>
									)}
								</SidebarMenuBadge>
							)}
						</SidebarMenuItem>
					</SidebarMenu>
				</SidebarGroupContent>
			</SidebarGroup>

			{/* Knowledge */}
			<SidebarGroup>
				<SidebarGroupLabel>Knowledge</SidebarGroupLabel>
				<SidebarGroupContent>
					<SidebarMenu>
						<SidebarMenuItem>
							<SidebarMenuButton asChild isActive={isDocumentsActive}>
								<Link href={`${user.basePath}/documents`}>
									<FileTextIcon />
									<span>Documents</span>
								</Link>
							</SidebarMenuButton>
						</SidebarMenuItem>
						<SidebarMenuItem>
							<SidebarMenuButton asChild isActive={isLibraryActive}>
								<Link href={`${user.basePath}/library`}>
									<BookOpenIcon />
									<span>Library</span>
								</Link>
							</SidebarMenuButton>
						</SidebarMenuItem>
						<SidebarMenuItem>
							<SidebarMenuButton asChild isActive={isKnowledgeActive}>
								<Link href={`${user.basePath}/knowledge`}>
									<BrainIcon />
									<span>Knowledge</span>
								</Link>
							</SidebarMenuButton>
						</SidebarMenuItem>
						<SidebarMenuItem>
							<SidebarMenuButton asChild isActive={isPromptsActive}>
								<Link href={`${user.basePath}/prompts`}>
									<MessageSquareTextIcon />
									<span>Prompts</span>
								</Link>
							</SidebarMenuButton>
						</SidebarMenuItem>
					</SidebarMenu>
				</SidebarGroupContent>
			</SidebarGroup>

			{/* System */}
			<SidebarGroup>
				<SidebarGroupLabel>System</SidebarGroupLabel>
				<SidebarGroupContent>
					<SidebarMenu>
						<SidebarMenuItem>
							<SidebarMenuButton asChild isActive={isRecurringActive}>
								<Link href={`${user.basePath}/recurring`}>
									<ClipboardClockIcon />
									<span>Recurring</span>
								</Link>
							</SidebarMenuButton>
						</SidebarMenuItem>
						<SidebarMenuItem>
							<SidebarMenuButton asChild isActive={isSettingsActive}>
								<Link href={`${user.basePath}/settings/general`}>
									<SettingsIcon />
									<span>Settings</span>
								</Link>
							</SidebarMenuButton>
						</SidebarMenuItem>
					</SidebarMenu>
				</SidebarGroupContent>
			</SidebarGroup>
		</>
	);
}
