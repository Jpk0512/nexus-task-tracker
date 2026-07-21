"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge } from "@ui/components/ui/badge";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@ui/components/ui/card";
import { Input } from "@ui/components/ui/input";
import { LabelBadge } from "@ui/components/ui/label-badge";
import {
	BookOpenIcon,
	ListChecksIcon,
	SearchIcon,
	SparklesIcon,
	TagIcon,
	TagsIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useDebounceValue } from "usehooks-ts";
import { trpc } from "@/utils/trpc";

// Unified tag taxonomy view. Read-only by design — see
// trpc.tags.list (apps/api/src/trpc/routers/tags.ts) for the union logic.
// This page mirrors the visibility of every tag-bearing surface so users
// can answer "where is tag X actually used?" without four round trips.

type TagRow = {
	tag: string;
	color: string | null;
	count: number;
	sources: string[];
};

const SOURCE_META: Record<
	string,
	{ label: string; icon: typeof TagIcon; tint: string }
> = {
	labels: {
		label: "Labels",
		icon: TagIcon,
		tint: "bg-cyan-500/10 text-cyan-600 dark:text-cyan-300",
	},
	todos: {
		label: "Todos",
		icon: ListChecksIcon,
		tint: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-300",
	},
	library: {
		label: "Library",
		icon: BookOpenIcon,
		tint: "bg-amber-500/10 text-amber-600 dark:text-amber-300",
	},
	prompts: {
		label: "Prompts",
		icon: SparklesIcon,
		tint: "bg-sky-500/10 text-sky-600 dark:text-sky-300",
	},
};

export default function TagsSettingsPage() {
	const [search, setSearch] = useState("");
	const [debouncedSearch] = useDebounceValue(search, 250);

	const { data, isLoading } = useQuery(
		trpc.tags.list.queryOptions({ search: debouncedSearch || undefined }),
	);

	const rows = (data ?? []) as TagRow[];

	const totalsBySource = useMemo(() => {
		const out: Record<string, number> = {
			labels: 0,
			todos: 0,
			library: 0,
			prompts: 0,
		};
		for (const r of rows) {
			for (const s of r.sources) {
				out[s] = (out[s] ?? 0) + 1;
			}
		}
		return out;
	}, [rows]);

	return (
		<Card>
			<CardHeader>
				<div className="flex items-start justify-between gap-4">
					<div>
						<CardTitle className="flex items-center gap-2">
							<TagsIcon className="size-4 text-cyan-500" />
							Tags
						</CardTitle>
						<CardDescription>
							Read-only union of every tag in this workspace — labels, todo
							tags, library tags, and prompt tags. Use this page to see where a
							tag is actually in play. Editing happens on the specific entity (a
							label still lives in /settings/labels).
						</CardDescription>
					</div>
					<div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
						{Object.entries(totalsBySource).map(([src, n]) => {
							const meta = SOURCE_META[src];
							if (!meta) return null;
							const Icon = meta.icon;
							return (
								<Badge
									key={src}
									variant="outline"
									className="gap-1 font-normal"
								>
									<Icon className="size-3" />
									{meta.label}: {n}
								</Badge>
							);
						})}
					</div>
				</div>
				<div className="relative mt-3 max-w-sm">
					<SearchIcon className="-translate-y-1/2 absolute top-1/2 left-2 size-3.5 text-muted-foreground" />
					<Input
						value={search}
						onChange={(e) => setSearch(e.target.value)}
						placeholder="Filter tags…"
						className="h-8 pl-7"
						aria-label="Filter tags"
					/>
				</div>
			</CardHeader>
			<CardContent>
				{isLoading && (
					<p className="py-8 text-center text-muted-foreground text-sm">
						Loading…
					</p>
				)}
				{!isLoading && rows.length === 0 && (
					<div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
						<TagsIcon className="size-8 text-muted-foreground" />
						<p className="text-muted-foreground text-sm">
							{debouncedSearch
								? `No tags match "${debouncedSearch}".`
								: "No tags found across labels, todos, library, or prompts yet."}
						</p>
					</div>
				)}
				{rows.length > 0 && (
					<div className="rounded-md border border-border">
						<table className="w-full text-sm">
							<thead>
								<tr className="border-border border-b bg-muted/40 text-[11px] text-muted-foreground uppercase tracking-wider">
									<th className="px-3 py-2 text-left font-[510]">Tag</th>
									<th className="px-3 py-2 text-left font-[510]">Sources</th>
									<th className="px-3 py-2 text-right font-[510]">
										Occurrences
									</th>
								</tr>
							</thead>
							<tbody>
								{rows.map((row) => (
									<tr
										key={row.tag}
										className="border-border border-b last:border-b-0 hover:bg-accent/30"
									>
										<td className="px-3 py-2">
											<LabelBadge
												name={row.tag}
												color={row.color ?? "#9ca3af"}
												className="font-normal"
											/>
										</td>
										<td className="px-3 py-2">
											<div className="flex flex-wrap gap-1">
												{row.sources.map((s) => {
													const meta = SOURCE_META[s];
													if (!meta) return null;
													const Icon = meta.icon;
													return (
														<span
															key={s}
															className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] ${meta.tint}`}
														>
															<Icon className="size-3" />
															{meta.label}
														</span>
													);
												})}
											</div>
										</td>
										<td className="px-3 py-2 text-right font-mono text-muted-foreground tabular-nums">
											{row.count}
										</td>
									</tr>
								))}
							</tbody>
						</table>
					</div>
				)}
			</CardContent>
		</Card>
	);
}
