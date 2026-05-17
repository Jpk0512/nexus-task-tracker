"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@ui/components/ui/button";
import { Input } from "@ui/components/ui/input";
import { LabelBadge } from "@ui/components/ui/label-badge";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@ui/components/ui/select";
import {
	BookOpenIcon,
	BotIcon,
	NetworkIcon,
	RefreshCwIcon,
	SearchIcon,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { JkHint } from "@/components/jk-hint";
import { useJkNavigation } from "@/hooks/use-jk-navigation";
import { trpc } from "@/utils/trpc";
import { kindColor, kindIconBg, type LibraryKind } from "./kind-color";
import { LibraryPreviewPanel } from "./preview-panel";

const KIND_ICON: Record<LibraryKind, typeof BookOpenIcon> = {
	skill: BookOpenIcon,
	agent: BotIcon,
	orchestration: NetworkIcon,
};

const HOVER_GRACE_MS = 200;

export function LibraryListView() {
	const { team } = useParams<{ team: string }>();
	const router = useRouter();
	const [kind, setKind] = useState<LibraryKind | "all">("all");
	const [sourceId, setSourceId] = useState<string>("all");
	const [search, setSearch] = useState("");
	const [hoveredId, setHoveredId] = useState<string | null>(null);
	const leaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

	const sourcesQuery = useQuery(
		trpc.library.getSources.queryOptions(undefined),
	);
	const entriesQuery = useQuery(
		trpc.library.get.queryOptions({
			kind: kind === "all" ? undefined : kind,
			sourceId: sourceId === "all" ? undefined : sourceId,
			search: search || undefined,
			pageSize: 100,
			cursor: 0,
		}),
	);

	const scanMutation = useMutation(
		trpc.library.scan.mutationOptions({
			onSuccess: (data) => {
				const total = data.results.reduce(
					(n, r) => n + r.inserted + r.updated + r.deleted,
					0,
				);
				toast.success(
					total === 0
						? "Library is up to date — nothing changed."
						: `Library re-scanned — ${total} change${total === 1 ? "" : "s"}`,
				);
				entriesQuery.refetch();
				sourcesQuery.refetch();
			},
			onError: (e) => toast.error(`Scan failed: ${e.message}`),
		}),
	);

	const entries = entriesQuery.data?.data ?? [];
	const sources = sourcesQuery.data ?? [];

	const jkIds = useMemo(() => entries.map((e) => e.id), [entries]);
	const entryById = useMemo(() => {
		const m = new Map<string, (typeof entries)[number]>();
		for (const e of entries) m.set(e.id, e);
		return m;
	}, [entries]);
	const jk = useJkNavigation({
		ids: jkIds,
		onOpen: (id) => router.push(`/team/${team}/library/${id}`),
		toastLabel: (id) => {
			const e = entryById.get(id);
			if (!e) return null;
			return `Opened ${(e as { name?: string }).name ?? "entry"}`;
		},
	});

	const totalByKind = entries.reduce<Record<string, number>>((acc, e) => {
		acc[e.kind] = (acc[e.kind] ?? 0) + 1;
		return acc;
	}, {});

	// Group rows by source when the user is browsing "All sources".
	// Preserves the order returned by the API (already name-sorted within
	// the source partition) by walking the array once.
	const groups = useMemo(() => {
		if (sourceId !== "all") {
			return [{ sourceId: null, sourceLabel: "", rows: entries }];
		}
		const buckets = new Map<
			string,
			{ sourceId: string; sourceLabel: string; rows: typeof entries }
		>();
		for (const e of entries) {
			const existing = buckets.get(e.sourceId);
			if (existing) {
				existing.rows.push(e);
			} else {
				buckets.set(e.sourceId, {
					sourceId: e.sourceId,
					sourceLabel: e.sourceLabel,
					rows: [e],
				});
			}
		}
		return Array.from(buckets.values());
	}, [entries, sourceId]);

	const handleEnter = (id: string) => {
		if (leaveTimerRef.current) {
			clearTimeout(leaveTimerRef.current);
			leaveTimerRef.current = null;
		}
		setHoveredId(id);
	};

	const handleLeave = () => {
		if (leaveTimerRef.current) clearTimeout(leaveTimerRef.current);
		leaveTimerRef.current = setTimeout(() => {
			setHoveredId(null);
			leaveTimerRef.current = null;
		}, HOVER_GRACE_MS);
	};

	// Iter8 a11y: when a row gets keyboard focus (Tab through the list),
	// drive the preview the same way mouse hover does. The grace timer
	// pattern still applies so the preview doesn't disappear the instant
	// focus jumps to a sibling link inside the same row.
	const handleFocus = (id: string) => handleEnter(id);
	const handleBlur = handleLeave;

	// Mirror keyboard focus to the j/k focused row so the highlight ring is
	// consistent regardless of whether the user used Tab or j/k. This also
	// keeps the preview pane in sync because j/k drives `jk.focusedId` only,
	// while Tab drives `hoveredId` via handleFocus above.

	return (
		<div className="flex h-full w-full flex-col">
			{/* Header */}
			<header className="border-border border-b px-6 py-3">
				<div className="flex items-baseline justify-between gap-4">
					<div>
						<h1 className="font-[510] text-[15px] text-foreground tracking-[-0.012em]">
							Library
						</h1>
						<p className="mt-0.5 text-[12px] text-muted-foreground">
							Skills, agents, and orchestration configs across all your
							projects. Disk is source of truth.
						</p>
					</div>
					<div className="flex items-center gap-3">
						<JkHint />
						<Button
							variant="outline"
							size="sm"
							onClick={() => scanMutation.mutate(undefined)}
							disabled={scanMutation.isPending}
						>
							<RefreshCwIcon
								className={`size-3.5 ${scanMutation.isPending ? "animate-spin" : ""}`}
							/>
							{scanMutation.isPending ? "Scanning…" : "Re-scan"}
						</Button>
					</div>
				</div>

				{/* Filters */}
				<div className="mt-4 flex flex-wrap items-center gap-2">
					<div className="relative">
						<SearchIcon className="absolute top-2.5 left-2.5 size-3.5 text-muted-foreground" />
						<Input
							value={search}
							onChange={(e) => setSearch(e.target.value)}
							placeholder="Search name or description…"
							className="h-9 w-64 pl-8"
						/>
					</div>
					<Select
						value={kind}
						onValueChange={(v) => setKind(v as LibraryKind | "all")}
					>
						<SelectTrigger className="h-9 w-40">
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="all">All kinds</SelectItem>
							<SelectItem value="skill">Skills</SelectItem>
							<SelectItem value="agent">Agents</SelectItem>
							<SelectItem value="orchestration">Orchestration</SelectItem>
						</SelectContent>
					</Select>
					<Select value={sourceId} onValueChange={setSourceId}>
						<SelectTrigger className="h-9 w-56">
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="all">
								All sources ({sources.length})
							</SelectItem>
							{sources.map((s) => (
								<SelectItem key={s.id} value={s.id}>
									{s.label} ({s.entryCount})
								</SelectItem>
							))}
						</SelectContent>
					</Select>
					<div className="ml-auto flex items-center gap-2 text-muted-foreground text-xs">
						{Object.entries(totalByKind).map(([k, n]) => (
							<LabelBadge
								key={k}
								name={`${k}: ${n}`}
								color={kindColor(k)}
								className="h-[20px] font-normal"
							/>
						))}
						<span>· {entries.length} total</span>
					</div>
				</div>
			</header>

			{/* List body + always-visible right-rail preview.
			    The preview pane is a sibling of the scroll container so it
			    stays pinned in view regardless of how far the list scrolls. */}
			<div className="flex min-h-0 grow">
				<div className="relative min-w-0 grow overflow-y-auto px-6 py-4">
					{entriesQuery.isLoading && (
						<div className="mt-0.5 text-[12px] text-muted-foreground">
							Loading…
						</div>
					)}
					{entries.length === 0 && !entriesQuery.isLoading && (
						<div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
							<BookOpenIcon className="size-10 text-muted-foreground" />
							<p className="text-muted-foreground">
								No entries match these filters.
							</p>
						</div>
					)}
					{groups.map((group, gIdx) => (
						<section
							key={group.sourceId ?? `g-${gIdx}`}
							className={gIdx > 0 ? "mt-6" : ""}
						>
							{sourceId === "all" && (
								<div className="mb-2 flex items-baseline gap-2 px-3">
									<span className="font-[510] text-[11px] text-muted-foreground uppercase tracking-[0.06em]">
										{group.sourceLabel}
									</span>
									<span className="text-[11px] text-muted-foreground/70">
										· {group.rows.length} entr
										{group.rows.length === 1 ? "y" : "ies"}
									</span>
								</div>
							)}
							<ul className="space-y-1">
								{group.rows.map((e) => {
									const Icon = KIND_ICON[e.kind as LibraryKind] ?? BookOpenIcon;
									const focused = jk.isFocused(e.id);
									return (
										<li
											key={e.id}
											data-jk-row={e.id}
											onMouseEnter={() => handleEnter(e.id)}
											onMouseLeave={handleLeave}
										>
											<Link
												href={`/team/${team}/library/${e.id}`}
												onFocus={() => handleFocus(e.id)}
												onBlur={handleBlur}
												className={`group flex items-start gap-3 rounded-md border px-3 py-2 transition hover:border-border hover:bg-accent/40 focus-visible:border-violet-400/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/40 ${
													focused
														? "border-violet-400/70 ring-2 ring-violet-400/40"
														: "border-transparent"
												}`}
											>
												<div
													className={`mt-0.5 flex size-7 shrink-0 items-center justify-center rounded ${kindIconBg(e.kind)}`}
												>
													<Icon className="size-3.5" />
												</div>
												<div className="min-w-0 grow">
													<div className="flex items-center gap-2">
														<span className="truncate font-medium text-sm">
															{e.name}
														</span>
														<LabelBadge
															name={e.kind}
															color={kindColor(e.kind)}
															className="h-[18px] px-1.5 font-normal text-[10px]"
														/>
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
						</section>
					))}
				</div>
				{entries.length > 0 && (
					<div className="hidden shrink-0 px-4 py-4 lg:block">
						<LibraryPreviewPanel entryId={hoveredId} />
					</div>
				)}
			</div>
		</div>
	);
}
