"use client";
import { cn } from "@ui/lib/utils";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useMemo } from "react";
import { useUser } from "@/components/user-provider";
import { getSettingsGroups } from "./nav-list";

export const SettingsSidebar = () => {
	const user = useUser();
	const pathname = usePathname();

	const settingsGroups = useMemo(() => {
		return getSettingsGroups(user.basePath);
	}, [user.basePath]);

	const teamScopes = (user?.team?.scopes as string[]) ?? [];

	return (
		<div className="sticky top-[68px] flex flex-col gap-4 self-start rounded-sm p-2 text-sm">
			{settingsGroups.map((group) => {
				const visibleLinks = group.links.filter(
					(link) =>
						!link.scopes ||
						link.scopes.every((scope) => teamScopes.includes(scope)),
				);
				if (visibleLinks.length === 0) return null;

				return (
					<div key={group.label} className="flex flex-col gap-0.5">
						<div className="px-4 pb-1 font-medium text-[10px] text-muted-foreground/70 uppercase tracking-wider">
							{group.label}
						</div>
						{visibleLinks.map((link) => {
							const isActive = pathname === link.to;
							const Icon = link.icon;
							return (
								<Link key={link.to} href={link.to}>
									<div
										className={cn(
											"flex h-7 items-center gap-2 rounded-sm px-4 text-xs transition-colors hover:bg-accent dark:hover:bg-accent/30 [&_svg]:opacity-50",
											{
												"bg-accent [&_svg]:opacity-100": isActive,
											},
										)}
									>
										<Icon className="size-4" />
										{link.label}
									</div>
								</Link>
							);
						})}
					</div>
				);
			})}
		</div>
	);
};
