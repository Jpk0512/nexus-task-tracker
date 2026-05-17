"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@ui/components/ui/badge";
import { Button } from "@ui/components/ui/button";
import { Input } from "@ui/components/ui/input";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@ui/components/ui/select";
import { PlusIcon, RefreshCwIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { trpc } from "@/utils/trpc";

type Kind = "skill" | "agent" | "orchestration";

export function LibrarySettingsView() {
	const qc = useQueryClient();
	const sourcesQuery = useQuery(
		trpc.library.getSources.queryOptions(undefined),
	);
	const [label, setLabel] = useState("");
	const [rootPath, setRootPath] = useState("");
	const [kindHint, setKindHint] = useState<Kind | "auto">("auto");

	const refetch = () =>
		qc.invalidateQueries({ queryKey: [["library", "getSources"]] });

	const addMut = useMutation(
		trpc.library.addSource.mutationOptions({
			onSuccess: (s) => {
				toast.success(`Added source "${(s as { label: string }).label}"`);
				setLabel("");
				setRootPath("");
				setKindHint("auto");
				refetch();
			},
			onError: (e) => toast.error(e.message),
		}),
	);
	const removeMut = useMutation(
		trpc.library.removeSource.mutationOptions({
			onSuccess: () => {
				toast.success("Source removed");
				refetch();
			},
			onError: (e) => toast.error(e.message),
		}),
	);
	const scanMut = useMutation(
		trpc.library.scan.mutationOptions({
			onSuccess: (data) => {
				const total = (
					data as {
						results: Array<{
							inserted: number;
							updated: number;
							deleted: number;
						}>;
					}
				).results.reduce((n, r) => n + r.inserted + r.updated + r.deleted, 0);
				toast.success(
					total === 0
						? "Up to date"
						: `Re-scanned (${total} change${total === 1 ? "" : "s"})`,
				);
				refetch();
			},
			onError: (e) => toast.error(e.message),
		}),
	);

	const sources = sourcesQuery.data ?? [];

	return (
		<div className="px-6 py-4">
			<header className="mb-6">
				<h1 className="font-[510] text-[15px] text-foreground tracking-[-0.012em]">
					Library Sources
				</h1>
				<p className="mt-1 text-muted-foreground text-sm">
					Directories the library scanner walks. Disk is source of truth.
					Bind-mount roots are limited to whatever your api container exposes
					(see `LIBRARY_ALLOWED_ROOT`).
				</p>
			</header>

			<section className="mb-8 rounded-md border border-border bg-card/40 p-4">
				<h2 className="mb-3 font-medium text-sm">Add source</h2>
				<form
					onSubmit={(e) => {
						e.preventDefault();
						if (!label.trim() || !rootPath.trim()) return;
						addMut.mutate({
							label: label.trim(),
							rootPath: rootPath.trim(),
							kindHint: kindHint === "auto" ? null : kindHint,
						});
					}}
					className="grid grid-cols-[1fr_2fr_max-content_max-content] items-end gap-3"
				>
					<div>
						<label className="mb-1 block text-muted-foreground text-xs">
							Label
						</label>
						<Input
							value={label}
							onChange={(e) => setLabel(e.target.value)}
							placeholder="e.g. user-global skills"
							className="h-8"
						/>
					</div>
					<div>
						<label className="mb-1 block text-muted-foreground text-xs">
							Root path (inside the api container)
						</label>
						<Input
							value={rootPath}
							onChange={(e) => setRootPath(e.target.value)}
							placeholder="e.g. /host/some-extra-mount"
							className="h-8 font-mono text-xs"
						/>
					</div>
					<div>
						<label className="mb-1 block text-muted-foreground text-xs">
							Kind hint
						</label>
						<Select
							value={kindHint}
							onValueChange={(v) => setKindHint(v as Kind | "auto")}
						>
							<SelectTrigger className="h-8 w-36">
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								<SelectItem value="auto">Auto-classify</SelectItem>
								<SelectItem value="skill">Skill</SelectItem>
								<SelectItem value="agent">Agent</SelectItem>
								<SelectItem value="orchestration">Orchestration</SelectItem>
							</SelectContent>
						</Select>
					</div>
					<Button
						type="submit"
						size="sm"
						disabled={addMut.isPending || !label || !rootPath}
					>
						<PlusIcon className="size-3.5" /> Add
					</Button>
				</form>
				<p className="mt-2 text-muted-foreground text-xs">
					To expose a new host directory, add a bind-mount in{" "}
					<code className="rounded bg-muted px-1">
						app/docker-compose.local.yaml
					</code>{" "}
					under the api service&apos;s <code>volumes</code> list, then enter the
					in-container path here.
				</p>
			</section>

			<section className="rounded-md border border-border">
				<div className="grid grid-cols-[2fr_3fr_max-content_max-content_max-content_max-content] items-center gap-3 border-border border-b bg-muted/40 px-4 py-2 text-muted-foreground text-xs uppercase tracking-wider">
					<div>Label</div>
					<div>Root path</div>
					<div>Kind</div>
					<div>Entries</div>
					<div>Last scan</div>
					<div />
				</div>
				{sources.length === 0 && (
					<div className="px-4 py-6 text-center text-muted-foreground text-sm">
						No sources yet. Add one above.
					</div>
				)}
				{sources.map((s) => (
					<div
						key={s.id}
						className="grid grid-cols-[2fr_3fr_max-content_max-content_max-content_max-content] items-center gap-3 border-border border-b px-4 py-2 text-sm last:border-b-0"
					>
						<div className="font-medium">{s.label}</div>
						<code className="break-all text-muted-foreground text-xs">
							{s.rootPath}
						</code>
						<Badge variant="outline" className="font-normal">
							{s.kindHint ?? "auto"}
						</Badge>
						<div className="tabular-nums">{s.entryCount}</div>
						<div className="whitespace-nowrap text-muted-foreground text-xs">
							{s.lastScannedAt
								? new Date(
										s.lastScannedAt as unknown as string,
									).toLocaleString()
								: "never"}
						</div>
						<div className="flex gap-1">
							<Button
								variant="ghost"
								size="sm"
								onClick={() => scanMut.mutate({ sourceId: s.id })}
								disabled={scanMut.isPending}
							>
								<RefreshCwIcon
									className={`size-3.5 ${scanMut.isPending ? "animate-spin" : ""}`}
								/>
							</Button>
							<Button
								variant="ghost"
								size="sm"
								onClick={() => {
									if (
										confirm(
											`Remove "${s.label}"? Entries will be deleted from the index (files on disk remain untouched).`,
										)
									) {
										removeMut.mutate({ id: s.id });
									}
								}}
								className="text-muted-foreground hover:text-destructive"
							>
								<Trash2Icon className="size-3.5" />
							</Button>
						</div>
					</div>
				))}
			</section>
		</div>
	);
}
