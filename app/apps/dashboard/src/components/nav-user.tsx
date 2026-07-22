"use client";

import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@ui/components/ui/dropdown-menu";
import { isPast, isToday } from "date-fns";
import {
	CheckSquareIcon,
	LogOut,
	Settings,
	TargetIcon,
	User,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo } from "react";
import { useInboxCounts } from "@/components/inbox/use-inbox-counts";
import { useUser } from "@/components/user-provider";
import { useTasks } from "@/hooks/use-data";
import { authClient } from "@/lib/auth-client";
import { AssigneeAvatar } from "./asignee-avatar";

export function NavUser() {
	const user = useUser();
	const router = useRouter();
	const { tasks } = useTasks(
		{
			assigneeId: user?.id ? [user.id] : undefined,
			statusType: ["to_do", "in_progress", "review"],
			pageSize: 100,
		},
		{ enabled: !!user?.id },
	);
	const { tabCounts } = useInboxCounts();

	const stats = useMemo(() => {
		const overdue = tasks.filter((t) => {
			if (!t.dueDate) return false;
			const d = new Date(t.dueDate);
			return isPast(d) && !isToday(d);
		}).length;
		const today = tasks.filter(
			(t) => t.dueDate && isToday(new Date(t.dueDate)),
		).length;
		return {
			open: tasks.length,
			overdue,
			today,
			unread: tabCounts?.unread ?? 0,
		};
	}, [tasks, tabCounts]);

	if (!user) return <div className="size-7 rounded-full bg-secondary/50" />;

	return (
		<DropdownMenu>
			<DropdownMenuTrigger className="rounded-full outline-none ring-offset-background focus-visible:ring-2 focus-visible:ring-ring">
				<div className="relative">
					<AssigneeAvatar
						name={user.name}
						email={user.email}
						image={user.image}
						className="size-7"
					/>
					{/* Attention dot — red when overdue, else subtle */}
					{stats.overdue > 0 ? (
						<span className="-right-0.5 -top-0.5 absolute size-2.5 rounded-full border-2 border-background bg-red-500" />
					) : stats.unread > 0 ? (
						<span className="-right-0.5 -top-0.5 absolute size-2.5 rounded-full border-2 border-background bg-primary" />
					) : null}
				</div>
			</DropdownMenuTrigger>
			<DropdownMenuContent
				className="min-w-60"
				side="bottom"
				align="end"
				sideOffset={8}
			>
				{/* Identity */}
				<DropdownMenuLabel className="p-0 font-normal">
					<div className="flex items-center gap-2.5 px-2 py-2 text-left">
						<AssigneeAvatar
							name={user.name}
							email={user.email}
							image={user.image}
							className="size-8"
						/>
						<div className="grid min-w-0 flex-1 text-left text-sm leading-tight">
							<span className="truncate font-medium">{user.name}</span>
							<span className="truncate text-muted-foreground text-xs">
								{user.email}
							</span>
						</div>
					</div>
				</DropdownMenuLabel>

				{/* Today snapshot */}
				<div className="mx-1 grid grid-cols-3 gap-1 rounded-lg border border-border/60 bg-card/40 p-1.5 text-center">
					<TodayStat
						label="Open"
						value={stats.open}
						href={`${user.basePath}/todos`}
					/>
					<TodayStat
						label="Today"
						value={stats.today}
						href={`${user.basePath}/focus`}
						tone={stats.today > 0 ? "orange" : undefined}
					/>
					<TodayStat
						label="Overdue"
						value={stats.overdue}
						href={`${user.basePath}/focus`}
						tone={stats.overdue > 0 ? "red" : undefined}
					/>
				</div>

				<DropdownMenuSeparator />

				<Link href={`${user.basePath}/todos`}>
					<DropdownMenuItem>
						<CheckSquareIcon /> To-do
					</DropdownMenuItem>
				</Link>
				<Link href={`${user.basePath}/focus`}>
					<DropdownMenuItem>
						<TargetIcon /> Focus
						{stats.unread > 0 ? (
							<span className="ml-auto rounded-full bg-primary/20 px-1.5 text-[10px] text-primary">
								{stats.unread}
							</span>
						) : null}
					</DropdownMenuItem>
				</Link>
				<Link href={`${user.basePath}/settings/profile`}>
					<DropdownMenuItem>
						<User /> Profile
					</DropdownMenuItem>
				</Link>
				<Link href={`${user.basePath}/settings`}>
					<DropdownMenuItem>
						<Settings /> Settings
					</DropdownMenuItem>
				</Link>

				<DropdownMenuSeparator />
				<DropdownMenuItem
					onClick={() => {
						authClient.signOut({
							fetchOptions: {
								onSuccess: () => {
									router.push("/");
								},
							},
						});
					}}
				>
					<LogOut /> Log out
				</DropdownMenuItem>
			</DropdownMenuContent>
		</DropdownMenu>
	);
}

function TodayStat({
	label,
	value,
	href,
	tone,
}: {
	label: string;
	value: number;
	href: string;
	tone?: "red" | "orange";
}) {
	const color =
		tone === "red"
			? "text-red-400"
			: tone === "orange"
				? "text-orange-300"
				: "text-foreground";
	return (
		<Link
			href={href}
			className="flex flex-col items-center gap-0.5 rounded-md px-1 py-1 transition-colors hover:bg-accent/50"
		>
			<span className={`font-[510] text-[15px] tabular-nums ${color}`}>
				{value}
			</span>
			<span className="text-[10px] text-muted-foreground">{label}</span>
		</Link>
	);
}
