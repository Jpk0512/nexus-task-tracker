"use client";
import { t } from "@mimir/locale";
import {
	BellIcon,
	BotIcon,
	BrainIcon,
	CableIcon,
	CircleDashedIcon,
	CloudUploadIcon,
	FolderIcon,
	GithubIcon,
	KeyRoundIcon,
	MailIcon,
	MessageSquareIcon,
	ServerIcon,
	SettingsIcon,
	SparklesIcon,
	TagsIcon,
	UserIcon,
	UsersIcon,
} from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";
import {
	NavItem,
	NavItemContent,
	NavItemIcon,
	NavItemIconSecondary,
	NavItemTitle,
} from "@/components/nav/nav-item";
import { useUser } from "@/components/user-provider";

export type SettingsLink = {
	icon: typeof SettingsIcon;
	to: string;
	label: string;
	scopes?: string[];
};

export type SettingsGroup = {
	/** Stable identifier — used as the localStorage key for collapse state. */
	id: string;
	label: string;
	links: SettingsLink[];
	/**
	 * Whether the group renders as a collapsible region in the sidebar.
	 * Workspace / Account / Data are always expanded; Connected apps + Developer
	 * are collapsible (and default-collapsed) per the iter-10 design spec.
	 */
	collapsible?: boolean;
	/** Initial collapse state when no localStorage entry exists yet. */
	defaultCollapsed?: boolean;
};

export const getSettingsGroups = (basePath: string): SettingsGroup[] => [
	{
		id: "workspace",
		label: "Workspace",
		links: [
			{
				icon: SettingsIcon,
				to: `${basePath}/settings/general`,
				label: t("settings.sidebar.general"),
			},
			{
				icon: UsersIcon,
				to: `${basePath}/settings/members`,
				label: t("settings.sidebar.members"),
			},
			{
				icon: CircleDashedIcon,
				to: `${basePath}/settings/statuses`,
				label: t("settings.sidebar.statuses"),
			},
			{
				icon: TagsIcon,
				to: `${basePath}/settings/labels`,
				label: t("settings.sidebar.labels"),
			},
			{
				icon: TagsIcon,
				to: `${basePath}/settings/tags`,
				label: "Tags",
			},
		],
	},
	{
		id: "account",
		label: "Account",
		links: [
			{
				icon: UserIcon,
				to: `${basePath}/settings/profile`,
				label: t("settings.sidebar.profile"),
			},
			{
				icon: BellIcon,
				to: `${basePath}/settings/notifications`,
				label: t("settings.sidebar.notifications"),
			},
			{
				icon: KeyRoundIcon,
				to: `${basePath}/settings/api-keys`,
				label: "API Keys",
			},
		],
	},
	{
		id: "connected-apps",
		label: "Connected apps",
		collapsible: true,
		defaultCollapsed: true,
		links: [
			{
				icon: CableIcon,
				to: `${basePath}/settings/integrations`,
				label: t("settings.sidebar.integrations"),
			},
			{
				icon: GithubIcon,
				to: `${basePath}/settings/integrations/github`,
				label: "GitHub",
			},
			{
				icon: MessageSquareIcon,
				to: `${basePath}/settings/integrations/mattermost`,
				label: "Mattermost",
			},
			{
				icon: MailIcon,
				to: `${basePath}/settings/integrations/smtp`,
				label: "SMTP",
			},
			{
				icon: MessageSquareIcon,
				to: `${basePath}/settings/integrations/whatsapp`,
				label: "WhatsApp",
			},
		],
	},
	{
		id: "developer",
		label: "Developer",
		collapsible: true,
		defaultCollapsed: true,
		links: [
			{
				icon: BotIcon,
				to: `${basePath}/settings/agents`,
				label: "Agents",
				scopes: ["team:write"],
			},
			{
				icon: SparklesIcon,
				to: `${basePath}/settings/autopilot`,
				label: "Autopilot",
				scopes: ["team:write"],
			},
			{
				icon: ServerIcon,
				to: `${basePath}/settings/mcp-servers`,
				label: "MCP Servers",
			},
		],
	},
	{
		id: "data",
		label: "Data",
		links: [
			{
				icon: BrainIcon,
				to: `${basePath}/settings/knowledge`,
				label: "Knowledge",
			},
			{
				icon: CloudUploadIcon,
				to: `${basePath}/settings/import`,
				label: t("settings.sidebar.import"),
			},
		],
	},
];

// Flat list — kept for the legacy NavList grid view below.
export const getSettingsLinks = (basePath: string): SettingsLink[] =>
	getSettingsGroups(basePath).flatMap((g) => g.links);

export const NavList = () => {
	const user = useUser();

	const settingsLinks = useMemo(() => {
		return getSettingsLinks(user.basePath);
	}, [user.basePath]);

	if (!user) return null;

	return (
		<div className="mx-auto h-fit w-full max-w-5xl">
			<ul className="grid grid-cols-5 gap-2">
				{settingsLinks.map(({ to, label, scopes, icon: Icon }) => {
					if (
						scopes &&
						!scopes.every((scope) =>
							(user?.team?.scopes as string[])?.includes(scope),
						)
					)
						return null;
					return (
						<Link href={to} key={to} className="min-w-[100px]">
							<NavItem>
								<NavItemIcon>
									<FolderIcon />
									<NavItemIconSecondary>
										<Icon />
									</NavItemIconSecondary>
								</NavItemIcon>
								<NavItemContent>
									<NavItemTitle>{label}</NavItemTitle>
								</NavItemContent>
							</NavItem>
						</Link>
					);
				})}
			</ul>
		</div>
	);
};
