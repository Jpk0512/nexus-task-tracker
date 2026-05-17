"use client";
import { cn } from "@ui/lib/utils";
import { ChevronRightIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useUser } from "@/components/user-provider";
import { getSettingsGroups, type SettingsGroup } from "./nav-list";

const STORAGE_PREFIX = "nexus.settings.sidebar";
const COLLAPSE_SUFFIX = "collapsed";

const storageKeyFor = (groupId: string) =>
	`${STORAGE_PREFIX}.${groupId}.${COLLAPSE_SUFFIX}`;

/**
 * Initial collapse state for a single group. SSR-safe: returns the
 * design default during server render, then re-syncs after mount via the
 * `useEffect` below.
 */
const readInitialCollapsed = (group: SettingsGroup): boolean => {
	if (!group.collapsible) return false;
	if (typeof window === "undefined") return !!group.defaultCollapsed;
	try {
		const raw = window.localStorage.getItem(storageKeyFor(group.id));
		if (raw === null) return !!group.defaultCollapsed;
		return raw === "true";
	} catch {
		return !!group.defaultCollapsed;
	}
};

export const SettingsSidebar = () => {
	const user = useUser();
	const pathname = usePathname();

	const settingsGroups = useMemo(() => {
		return getSettingsGroups(user.basePath);
	}, [user.basePath]);

	const teamScopes = (user?.team?.scopes as string[]) ?? [];

	// Per-group collapsed state. Keyed by group.id so the localStorage entry
	// (`nexus.settings.sidebar.<groupId>.collapsed`) survives label renames.
	// SSR: starts from design defaults; effect below re-syncs after mount.
	const [collapsedById, setCollapsedById] = useState<Record<string, boolean>>(
		() =>
			Object.fromEntries(
				settingsGroups.map((g) => [g.id, !!g.defaultCollapsed && !!g.collapsible]),
			),
	);

	// On mount + on cross-tab storage events, re-read each group's state.
	useEffect(() => {
		const sync = () => {
			setCollapsedById(
				Object.fromEntries(
					settingsGroups.map((g) => [g.id, readInitialCollapsed(g)]),
				),
			);
		};
		sync();
		window.addEventListener("storage", sync);
		return () => window.removeEventListener("storage", sync);
	}, [settingsGroups]);

	const toggleGroup = useCallback((group: SettingsGroup) => {
		if (!group.collapsible) return;
		setCollapsedById((prev) => {
			const next = !prev[group.id];
			try {
				window.localStorage.setItem(storageKeyFor(group.id), String(next));
			} catch {
				// localStorage unavailable (private mode etc.) — keep in-memory.
			}
			return { ...prev, [group.id]: next };
		});
	}, []);

	return (
		<nav
			aria-label="Settings"
			className="sticky top-[68px] flex flex-col gap-3 self-start rounded-sm p-2 text-sm"
		>
			{settingsGroups.map((group, groupIndex) => {
				const visibleLinks = group.links.filter(
					(link) =>
						!link.scopes ||
						link.scopes.every((scope) => teamScopes.includes(scope)),
				);
				if (visibleLinks.length === 0) return null;
				const collapsed = !!collapsedById[group.id];
				const hasDivider = groupIndex > 0;
				const headerId = `settings-sidebar-${group.id}-header`;
				const panelId = `settings-sidebar-${group.id}-panel`;

				return (
					<div
						key={group.id}
						className={cn(
							"flex flex-col gap-0.5",
							hasDivider && "border-border/60 border-t pt-3",
						)}
					>
						{group.collapsible ? (
							<button
								type="button"
								id={headerId}
								aria-expanded={!collapsed}
								aria-controls={panelId}
								onClick={() => toggleGroup(group)}
								className={cn(
									"flex w-full items-center gap-1.5 rounded-sm px-4 py-1 text-left font-medium text-[10px] text-muted-foreground/70 uppercase tracking-wider",
									"hover:text-muted-foreground",
								)}
							>
								<ChevronRightIcon
									className={cn(
										"size-3 shrink-0 transition-transform duration-150",
										!collapsed && "rotate-90",
									)}
								/>
								<span className="flex-1">{group.label}</span>
								<span
									aria-label={`${visibleLinks.length} settings`}
									className="rounded-full bg-muted/60 px-1.5 text-[10px] text-muted-foreground tabular-nums"
								>
									{visibleLinks.length}
								</span>
							</button>
						) : (
							<div
								id={headerId}
								className="px-4 pb-1 font-medium text-[10px] text-muted-foreground/70 uppercase tracking-wider"
							>
								{group.label}
							</div>
						)}

						{!collapsed && (
							<div
								id={panelId}
								role={group.collapsible ? "region" : undefined}
								aria-labelledby={group.collapsible ? headerId : undefined}
								className="flex flex-col gap-0.5"
							>
								{visibleLinks.map((link) => {
									const isActive = pathname === link.to;
									const Icon = link.icon;
									return (
										<Link key={link.to} href={link.to}>
											<div
												className={cn(
													"flex h-7 items-center gap-2 rounded-sm border-l-2 border-transparent px-4 text-xs transition-colors hover:bg-accent dark:hover:bg-accent/30 [&_svg]:opacity-50",
													isActive &&
														"border-l-brand bg-brand/10 text-brand [&_svg]:opacity-100",
												)}
											>
												<Icon className="size-4" />
												{link.label}
											</div>
										</Link>
									);
								})}
							</div>
						)}
					</div>
				);
			})}
		</nav>
	);
};
