"use client";
import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
} from "@nexus-app/ui/dialog";
import { useQuery } from "@tanstack/react-query";
import { useShareableParams } from "@/hooks/use-shareable-params";
import { trpc } from "@/utils/trpc";
import { ShareableForm } from "../forms/shareable-form";

type ShareableResourceType = "task" | "project";

const VALID_RESOURCE_TYPES = new Set<string>(["task", "project"]);

function toResourceType(v: string | null): ShareableResourceType | null {
	if (v && VALID_RESOURCE_TYPES.has(v)) return v as ShareableResourceType;
	return null;
}

export const ShareableCreateSheet = () => {
	const {
		createShareable,
		shareableId,
		shareableResourceId,
		shareableResourceType: rawResourceType,
		setParams,
	} = useShareableParams();

	const shareableResourceType = toResourceType(rawResourceType);
	const isOpen = Boolean(createShareable);

	const { data: shareable, isFetched } = useQuery(
		trpc.shareable.getByResourceId.queryOptions(
			{
				resourceId: shareableResourceId!,
				resourceType: shareableResourceType!,
			},
			{
				enabled: Boolean(shareableResourceId && shareableResourceType),
			},
		),
	);

	return (
		<Dialog
			open={isOpen}
			onOpenChange={() => setParams({ createShareable: null })}
		>
			<DialogContent
				showCloseButton={true}
				className="max-h-[85vh] overflow-y-auto"
			>
				<DialogHeader>
					<DialogTitle>Share</DialogTitle>
				</DialogHeader>
				<div className="pt-0">
					{(isFetched || !shareableId) && (
						<ShareableForm
							defaultValues={{
								id: shareable?.id,
								authorizedEmails: shareable?.authorizedEmails
									? shareable.authorizedEmails.join(", ")
									: "",
								policy: shareable?.policy || "private",
								resourceId: shareableResourceId ?? "",
								resourceType: shareableResourceType ?? "task",
							}}
						/>
					)}
				</div>
			</DialogContent>
		</Dialog>
	);
};
