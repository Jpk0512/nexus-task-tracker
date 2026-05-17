"use client";

import { useQuery } from "@tanstack/react-query";
import { LabelBadge } from "@ui/components/ui/label-badge";
import { BookOpenIcon, BotIcon, NetworkIcon } from "lucide-react";
import { trpc } from "@/utils/trpc";
import { kindColor, type LibraryKind } from "./kind-color";

type Props = {
	entryId: string | null;
};

const KIND_ICON = {
	skill: BookOpenIcon,
	agent: BotIcon,
	orchestration: NetworkIcon,
} as const;

// Right-rail inline preview rendered when a Library row is hovered. Lazy —
// only fires the trpc query when entryId is set. Sticky inside the scroll
// container so it tracks the cursor without jumping. Hidden < 1024px.
export function LibraryPreviewPanel({ entryId }: Props) {
	const { data, isFetching } = useQuery({
		...trpc.library.getById.queryOptions({ id: entryId ?? "" }),
		enabled: !!entryId,
		staleTime: 30_000,
	});

	if (!entryId) {
		return (
			<aside className="flex h-full w-[320px] flex-col rounded-md border border-border/60 bg-card/30 p-4">
				<p className="text-[12px] text-muted-foreground">
					Hover a row to preview its contents here.
				</p>
			</aside>
		);
	}

	const Icon =
		(data?.kind && KIND_ICON[data.kind as LibraryKind]) || BookOpenIcon;
	const body = data?.body ?? "";
	const preview = body ? body.slice(0, 600) : (data?.description ?? "");
	const truncated = body && body.length > 600;

	return (
		<aside className="flex h-full w-[320px] flex-col overflow-hidden rounded-md border border-border/60 bg-card/40">
			<div className="border-border/60 border-b p-3">
				<div className="flex items-start gap-2">
					<div
						className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded"
						style={{
							backgroundColor: data?.kind
								? `${kindColor(data.kind)}22`
								: undefined,
							color: data?.kind ? kindColor(data.kind) : undefined,
						}}
					>
						<Icon className="size-3.5" />
					</div>
					<div className="min-w-0 grow">
						<h3 className="truncate font-[510] text-[13px] text-foreground">
							{data?.name ?? (isFetching ? "Loading…" : "Untitled")}
						</h3>
						{data?.kind && (
							<div className="mt-1 flex items-center gap-1.5">
								<LabelBadge
									name={data.kind}
									color={kindColor(data.kind)}
									className="h-[18px] px-1.5 text-[10px]"
								/>
							</div>
						)}
					</div>
				</div>
				{data?.relativePath && (
					<p
						className="mt-2 truncate font-mono text-[10.5px] text-muted-foreground/70"
						title={data.relativePath}
					>
						{data.relativePath}
					</p>
				)}
			</div>
			<div className="grow overflow-y-auto p-3">
				{isFetching && !data ? (
					<div className="space-y-2">
						<div className="h-2 w-3/4 animate-pulse rounded bg-muted/60" />
						<div className="h-2 w-full animate-pulse rounded bg-muted/60" />
						<div className="h-2 w-5/6 animate-pulse rounded bg-muted/60" />
						<div className="h-2 w-2/3 animate-pulse rounded bg-muted/60" />
					</div>
				) : preview ? (
					<pre className="whitespace-pre-wrap break-words font-sans text-[12px] text-foreground/80 leading-[1.55]">
						{preview}
						{truncated && <span className="text-muted-foreground/70">…</span>}
					</pre>
				) : (
					<p className="text-[12px] text-muted-foreground">
						No preview content.
					</p>
				)}
			</div>
		</aside>
	);
}
