"use client";
import { StatusIcon } from "@/components/status-icon";

type StatusShape = {
	type: "done" | "backlog" | "to_do" | "in_progress" | "review";
	name: string;
};

export const TaskPropertyStatus = ({
	status,
	id: _id,
}: {
	status: StatusShape | undefined | null;
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
