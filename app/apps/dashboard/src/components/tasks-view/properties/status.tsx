"use client";
import type { RouterOutputs } from "@nexus-app/trpc";
import { StatusIcon } from "@/components/status-icon";

type Status = RouterOutputs["statuses"]["get"]["data"][number];

export const TaskPropertyStatus = ({
	status,
	id: _id,
}: {
	status: Status | undefined | null;
	id?: string;
}) => {
	if (!status) return null;

	return (
		<div>
			<time className="flex h-5.5 items-center rounded-sm text-xs">
				<StatusIcon type={status.type} className="size-3.5" />
				<span className="sr-only">{status.name}</span>
			</time>
		</div>
	);
};
