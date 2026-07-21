"use client";
import { useLocaleStore } from "@nexus-app/locale";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@nexus-app/ui/dropdown-menu";
import { Kbd, KbdGroup } from "@nexus-app/ui/kbd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ChevronsUpDownIcon, PlusIcon } from "lucide-react";
import Link from "next/link";
import { useEffect } from "react";
import { toast } from "sonner";
import { useUser } from "@/components/user-provider";
import { useChatParams } from "@/hooks/use-chat-params";
import { useTeamParams } from "@/hooks/use-team-params";
import { trpc } from "@/utils/trpc";

export const TeamSwitcher = () => {
	const user = useUser();
	const { setParams } = useTeamParams();
	const { setParams: setChatParams } = useChatParams();
	const { data: teams } = useQuery(trpc.teams.getAvailable.queryOptions());
	const { setLocale } = useLocaleStore();

	const { mutate: switchTeam } = useMutation(
		trpc.users.switchTeam.mutationOptions({
			onSuccess: (data) => {
				setChatParams(null);
				setParams(null);
				window.location.href = `/team/${data.slug}`;
			},
			onError: () => {
				toast.error("Failed to switch team");
			},
		}),
	);

	useEffect(() => {
		if (user?.team) {
			setLocale({
				locale: user.team.locale,
				timezone: user.team.timezone,
			});
		}
	}, [user?.team]);

	// Register keyboard to switch teams (Cmd + Ctrl + { index })
	useEffect(() => {
		const handleKeyDown = (e: KeyboardEvent) => {
			if (e.metaKey && e.ctrlKey && e.key.length === 1) {
				const pressedKey = Number.parseInt(e.key, 10);
				if (
					!Number.isNaN(pressedKey) &&
					pressedKey > 0 &&
					pressedKey <= teams!.length
				) {
					const team = teams![pressedKey - 1];
					if (!team) return;
					switchTeam({
						slug: team.slug,
					});
				}
			}
		};

		window.addEventListener("keydown", handleKeyDown);
		return () => {
			window.removeEventListener("keydown", handleKeyDown);
		};
	}, [teams]);

	return (
		<DropdownMenu>
			<button
				type="button"
				className="flex h-full w-full items-center justify-between opacity-90 hover:bg-transparent hover:opacity-100 focus:outline-none dark:hover:bg-transparent"
			>
				<Link href={`/team/${user?.team?.slug}`} className="min-w-0 flex-1">
					<span className="block truncate font-medium text-sm tracking-[-0.01em]">
						{user?.team?.name}
					</span>
				</Link>

				<DropdownMenuTrigger asChild>
					<div className="px-2">
						<ChevronsUpDownIcon className="size-4 text-muted-foreground" />
					</div>
				</DropdownMenuTrigger>
			</button>
			<DropdownMenuContent
				className="w-72"
				side="right"
				align="start"
				sideOffset={10}
			>
				{teams?.map((team, index) => (
					<DropdownMenuItem
						key={team.id}
						onClick={() =>
							switchTeam({
								slug: team.slug,
							})
						}
					>
						<span className="flex-1 truncate">{team.name}</span>
						<KbdGroup className="ml-auto">
							<Kbd>⌘ + Ctrl + {index + 1}</Kbd>
						</KbdGroup>
					</DropdownMenuItem>
				))}
				<DropdownMenuSeparator />
				<DropdownMenuItem onClick={() => setParams({ createTeam: true })}>
					<PlusIcon className="size-4" />
					Create Team
				</DropdownMenuItem>
			</DropdownMenuContent>
		</DropdownMenu>
	);
};
