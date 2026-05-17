"use client";

import { Button } from "@ui/components/ui/button";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@ui/components/ui/tooltip";
import { formatDistanceToNowStrict } from "date-fns";
import { ClockIcon, Share2Icon, UserIcon } from "lucide-react";
import { toast } from "sonner";

/**
 * Linear-style properties strip rendered above the document body.
 * Mirrors Linear's issue header: small chips for author / last-edited / share.
 *
 * Keeps the API surface narrow so it can be slotted into the existing
 * `DocumentForm` without prop-drilling concerns.
 */
export function DocumentProperties({
	creatorName,
	updatedAt,
}: {
	creatorName?: string | null;
	updatedAt?: string | Date | null;
}) {
	const updated = updatedAt ? new Date(updatedAt) : null;

	const handleShare = async () => {
		try {
			await navigator.clipboard.writeText(window.location.href);
			toast.success("Link copied to clipboard");
		} catch {
			toast.error("Could not copy link");
		}
	};

	return (
		<div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b px-4 py-2 text-muted-foreground text-xs">
			{creatorName ? (
				<span className="inline-flex items-center gap-1.5">
					<UserIcon className="size-3.5" />
					{creatorName}
				</span>
			) : null}
			{updated ? (
				<Tooltip>
					<TooltipTrigger asChild>
						<span className="inline-flex items-center gap-1.5">
							<ClockIcon className="size-3.5" />
							Edited {formatDistanceToNowStrict(updated, { addSuffix: true })}
						</span>
					</TooltipTrigger>
					<TooltipContent>{updated.toLocaleString()}</TooltipContent>
				</Tooltip>
			) : null}
			<div className="ml-auto">
				<Button
					type="button"
					variant="ghost"
					size="sm"
					className="h-6 px-2 text-xs"
					onClick={handleShare}
				>
					<Share2Icon className="size-3.5" />
					Share
				</Button>
			</div>
		</div>
	);
}
