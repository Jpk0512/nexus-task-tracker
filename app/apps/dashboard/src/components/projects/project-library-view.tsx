"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@ui/components/ui/badge";
import { Button } from "@ui/components/ui/button";
import { Input } from "@ui/components/ui/input";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@ui/components/ui/popover";
import {
	BookOpenIcon,
	BotIcon,
	NetworkIcon,
	PinIcon,
	SearchIcon,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { trpc } from "@/utils/trpc";

type Props = { projectId: string; team: string };

type Kind = "skill" | "agent" | "orchestration";

const KIND_ICON: Record<Kind, typeof BookOpenIcon> = {
	skill: BookOpenIcon,
	agent: BotIcon,
	orchestration: NetworkIcon,
};

const KIND_COLOR: Record<Kind, string> = {
	skill: "bg-violet-500/10 text-violet-600 dark:text-violet-300",
	agent: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-300",
	orchestration: "bg-amber-500/10 text-amber-600 dark:text-amber-300",
};

/**
 * Popover that lets the user pick an entry from the global Library and pin it
 * to the current project. Debounced search across all sources/kinds; clicking
 * a row attaches it via `trpc.library.linkProject` (writes a row to the
 * `libraryEntryProjects` join table) and invalidates the project's library
 * query so the new entry appears immediately.
 */
function PinEntryPopover({
	projectId,
	pinnedIds,
	onPinned,
}: {
	projectId: string;
	pinnedIds: Set<string>;
	onPinned: () => void;
}) {
	const [open, setOpen] = useState(false);
	const [search, setSearch] = useState("");

	const allEntriesQuery = useQuery({
		...trpc.library.get.queryOptions({
			pageSize: 50,
			cursor: 0,
			search: search || undefined,
		}),
		enabled: open,
	});

	const linkMut = useMutation(
		trpc.library.linkProject.mutationOptions({
			onSuccess: () => {
				toast.success("Pinned to project");
				onPinned();
			},
			onError: (e) => toast.error(e.message),
		}),
	);

	const candidates = useMemo(() => {
		const rows = (allEntriesQuery.data?.data ?? []) as Array<{
			id: string;
			name: string;
			kind: string;
			sourceLabel: string;
		}>;
		return rows.filter((r) => !pinnedIds.has(r.id));
	}, [allEntriesQuery.data, pinnedIds]);

	return (
		<Popover open={open} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<Button variant="outline" size="sm">
					<PinIcon className="size-3.5" />
					Pin entry
				</Button>
			</PopoverTrigger>
			<PopoverContent
				align="end"
				className="w-80 p-2"
				onOpenAutoFocus={(e) => {
					// Focus the search input, not the first row, so the user can
					// start typing immediately — same UX as Linear's command bar.
					e.preventDefault();
				}}
			>
				<div className="relative">
					<SearchIcon className="-translate-y-1/2 absolute top-1/2 left-2 size-3.5 text-muted-foreground" />
					<Input
						autoFocus
						value={search}
						onChange={(e) => setSearch(e.target.value)}
						placeholder="Search library…"
						className="h-8 pl-7"
					/>
				</div>
				<ul className="mt-2 max-h-72 overflow-y-auto">
					{allEntriesQuery.isLoading && (
						<li className="px-2 py-2 text-[12px] text-muted-foreground">
							Loading…
						</li>
					)}
					{!allEntriesQuery.isLoading && candidates.length === 0 && (
						<li className="px-2 py-2 text-[12px] text-muted-foreground italic">
							{pinnedIds.size > 0
								? "Everything matching is already pinned."
								: "No library entries match."}
						</li>
					)}
					{candidates.map((e) => {
						const kind = e.kind as Kind;
						const Icon = KIND_ICON[kind] ?? BookOpenIcon;
						return (
							<li key={e.id}>
								<button
									type="button"
									onClick={() => {
										linkMut.mutate({
											entryId: e.id,
											projectId,
										});
									}}
									disabled={linkMut.isPending}
									className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-[13px] text-foreground hover:bg-accent/60 disabled:opacity-60"
								>
									<div
										className={`flex size-5 shrink-0 items-center justify-center rounded ${KIND_COLOR[kind] ?? ""}`}
									>
										<Icon className="size-3" />
									</div>
									<span className="truncate font-medium">{e.name}</span>
									<span className="ml-auto shrink-0 text-[11px] text-muted-foreground">
										{e.sourceLabel}
									</span>
								</button>
							</li>
						);
					})}
				</ul>
			</PopoverContent>
		</Popover>
	);
}

/**
 * Project-scoped Library tab — renders every library entry linked to this
 * project via the `libraryEntryProjects` join table. Mirrors the row UX of
 * /library so users have one mental model regardless of entry point.
 */
export function ProjectLibraryView({ projectId, team }: Props) {
	const qc = useQueryClient();
	const projectQuery = useQuery(
		trpc.projects.getById.queryOptions({ id: projectId } as any),
	);
	const entriesQuery = useQuery(
		trpc.library.get.queryOptions({
			projectId,
			pageSize: 200,
			cursor: 0,
		}),
	);
	const project = projectQuery.data as { name?: string } | undefined;
	const entries = entriesQuery.data?.data ?? [];
	const pinnedIds = useMemo(() => new Set(entries.map((e) => e.id)), [entries]);

	const onPinned = () => {
		qc.invalidateQueries({ queryKey: [["library", "get"]] });
	};

	return (
		<div className="flex h-full flex-col">
			<header className="border-border border-b px-6 py-3">
				<div className="flex items-baseline justify-between gap-4">
					<div>
						<h1 className="font-[510] text-[15px] text-foreground tracking-[-0.012em]">
							{project?.name ?? "Project"} — Library
						</h1>
						<p className="mt-0.5 text-[12px] text-muted-foreground">
							Skills, agents, and orchestration configs pinned to this project.
							Pin more from the global /library page.
						</p>
					</div>
					<PinEntryPopover
						projectId={projectId}
						pinnedIds={pinnedIds}
						onPinned={onPinned}
					/>
				</div>
			</header>
			<div className="grow overflow-y-auto px-6 py-4">
				{entriesQuery.isLoading && (
					<div className="text-[12px] text-muted-foreground">Loading…</div>
				)}
				{entries.length === 0 && !entriesQuery.isLoading && (
					<div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
						<BookOpenIcon className="size-10 text-muted-foreground" />
						<div>
							<p className="font-[510] text-foreground text-sm">
								Nothing pinned yet
							</p>
							<p className="mt-0.5 text-[12px] text-muted-foreground">
								Pin a skill, agent, or orchestration from your global{" "}
								<Link
									href={`/team/${team}/library`}
									className="text-primary underline"
								>
									Library
								</Link>{" "}
								to surface it here.
							</p>
						</div>
						<div className="mt-1">
							<PinEntryPopover
								projectId={projectId}
								pinnedIds={pinnedIds}
								onPinned={onPinned}
							/>
						</div>
					</div>
				)}
				<ul className="space-y-1">
					{entries.map((e) => {
						const kind = e.kind as Kind;
						const Icon = KIND_ICON[kind] ?? BookOpenIcon;
						return (
							<li key={e.id}>
								<Link
									href={`/team/${team}/library/${e.id}`}
									className="group flex items-start gap-3 rounded-md border border-transparent px-3 py-2 transition hover:border-border hover:bg-accent/40"
								>
									<div
										className={`mt-0.5 flex size-7 shrink-0 items-center justify-center rounded ${KIND_COLOR[kind] ?? ""}`}
									>
										<Icon className="size-3.5" />
									</div>
									<div className="min-w-0 grow">
										<div className="flex items-center gap-2">
											<span className="truncate font-medium text-sm">
												{e.name}
											</span>
											<Badge variant="outline" className="font-normal text-xs">
												{e.kind}
											</Badge>
											<span className="truncate text-muted-foreground text-xs">
												{e.sourceLabel}
											</span>
										</div>
										{e.description && (
											<p className="mt-0.5 line-clamp-1 text-muted-foreground text-xs">
												{e.description}
											</p>
										)}
									</div>
									<span className="hidden text-muted-foreground text-xs sm:inline">
										{e.relativePath}
									</span>
								</Link>
							</li>
						);
					})}
				</ul>
			</div>
		</div>
	);
}
