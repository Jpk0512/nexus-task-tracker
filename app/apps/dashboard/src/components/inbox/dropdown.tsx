import { useMutation } from "@tanstack/react-query";
import { Button } from "@ui/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from "@ui/components/ui/dropdown-menu";
import { ArchiveIcon, EllipsisIcon } from "lucide-react";
import { queryClient, trpc } from "@/utils/trpc";
import type { Inbox } from "./use-inbox";

export const InboxDropdown = ({
	className,
	children,
	inbox,
}: {
	className?: string;
	children?: React.ReactNode;
	inbox: Inbox;
}) => {
	const { mutate: update, isPending } = useMutation(
		trpc.inbox.update.mutationOptions({
			onSettled: () => {
				queryClient.invalidateQueries(trpc.inbox.get.infiniteQueryOptions({}));
			},
		}),
	);

	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Button
					variant="ghost"
					size="icon"
					className="size-6"
					aria-label="Inbox item actions"
				>
					<EllipsisIcon />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent>
				<DropdownMenuItem
					disabled={isPending}
					onSelect={() => {
						update({
							id: inbox.id,
							status: "archived",
						});
					}}
				>
					<ArchiveIcon />
					Archive
				</DropdownMenuItem>
				{children}
			</DropdownMenuContent>
		</DropdownMenu>
	);
};
