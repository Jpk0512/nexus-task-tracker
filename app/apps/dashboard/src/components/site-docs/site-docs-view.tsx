"use client";

/**
 * Site Docs — Notes Option C shell with Sites instead of categories.
 * Disk folders are source of truth; Nexus Maps are app-stored.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@ui/components/ui/button";
import { Input } from "@ui/components/ui/input";
import { cn } from "@ui/lib/utils";
import {
	ChevronDownIcon,
	ChevronRightIcon,
	FileTextIcon,
	FolderIcon,
	GitBranchIcon,
	NetworkIcon,
	PanelRightCloseIcon,
	PanelRightIcon,
	RefreshCwIcon,
	SaveIcon,
	Share2Icon,
	WorkflowIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { BlockEditor } from "@/components/editor/block-editor";
import { useUser } from "@/components/user-provider";
import { trpc } from "@/utils/trpc";

const LIST_WIDTH_KEY = "nexus.siteDocs.listWidth";
const INSPECTOR_OPEN_KEY = "nexus.siteDocs.inspectorOpen";
const LIST_WIDTH_DEFAULT = 300;
const LIST_WIDTH_MIN = 220;
const LIST_WIDTH_MAX = 480;

type TreeNode = {
	name: string;
	relativePath: string;
	type: "file" | "dir";
	children?: TreeNode[];
};

type Selection =
	| { kind: "file"; relativePath: string }
	| { kind: "map"; mapId: string }
	| null;

function MapIcon({ kind }: { kind: string }) {
	if (kind === "flow") return <WorkflowIcon className="size-3.5" />;
	if (kind === "graph") return <NetworkIcon className="size-3.5" />;
	return <GitBranchIcon className="size-3.5" />;
}

export function SiteDocsView() {
	const user = useUser();
	const qc = useQueryClient();
	const [siteId, setSiteId] = useState<string | null>(null);
	const [selection, setSelection] = useState<Selection>(null);
	const [draft, setDraft] = useState("");
	const [sha, setSha] = useState<string | null>(null);
	const [mode, setMode] = useState<"preview" | "edit">("preview");
	const [listWidth, setListWidth] = useState(LIST_WIDTH_DEFAULT);
	const [inspectorOpen, setInspectorOpen] = useState(true);
	const [expanded, setExpanded] = useState<Record<string, boolean>>({});
	const dragRef = useRef<{ startX: number; startW: number } | null>(null);

	useEffect(() => {
		try {
			const w = localStorage.getItem(LIST_WIDTH_KEY);
			if (w)
				setListWidth(
					Math.min(LIST_WIDTH_MAX, Math.max(LIST_WIDTH_MIN, Number(w))),
				);
			const insp = localStorage.getItem(INSPECTOR_OPEN_KEY);
			if (insp != null) setInspectorOpen(insp === "1");
		} catch {}
	}, []);

	const sitesQuery = useQuery(trpc.siteDocs.listSites.queryOptions());
	const sites = sitesQuery.data ?? [];

	useEffect(() => {
		if (!siteId && sites.length > 0) setSiteId(sites[0]!.id);
	}, [sites, siteId]);

	const treeQuery = useQuery({
		...trpc.siteDocs.listTree.queryOptions({ projectId: siteId ?? "" }),
		enabled: !!siteId,
	});

	const ensureMaps = useMutation(
		trpc.siteDocs.ensureDefaultMaps.mutationOptions({
			onSuccess: () => {
				qc.invalidateQueries({ queryKey: [["siteDocs", "listTree"]] });
			},
		}),
	);

	useEffect(() => {
		if (siteId && treeQuery.data && (treeQuery.data.maps?.length ?? 0) === 0) {
			ensureMaps.mutate({ projectId: siteId });
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [siteId, treeQuery.data?.maps?.length]);

	const fileQuery = useQuery({
		...trpc.siteDocs.readFile.queryOptions({
			projectId: siteId ?? "",
			relativePath: selection?.kind === "file" ? selection.relativePath : "",
		}),
		enabled: !!siteId && selection?.kind === "file",
	});

	const mapQuery = useQuery({
		...trpc.siteDocs.getMap.queryOptions({
			id: selection?.kind === "map" ? selection.mapId : "",
		}),
		enabled: selection?.kind === "map",
	});

	useEffect(() => {
		if (selection?.kind === "file" && fileQuery.data) {
			setDraft(fileQuery.data.content);
			setSha(fileQuery.data.sha);
			setMode("preview");
		} else if (selection?.kind === "map" && mapQuery.data) {
			setDraft(mapQuery.data.content);
			setSha(null);
			setMode("preview");
		}
	}, [selection, fileQuery.data, mapQuery.data]);

	const saveFile = useMutation(
		trpc.siteDocs.writeFile.mutationOptions({
			onSuccess: (res) => {
				setSha(res.sha);
				toast.success("Saved to disk");
				qc.invalidateQueries({ queryKey: [["siteDocs", "readFile"]] });
			},
			onError: (err) => toast.error(err.message),
		}),
	);

	const saveMap = useMutation(
		trpc.siteDocs.upsertMap.mutationOptions({
			onSuccess: () => {
				toast.success("Map saved");
				qc.invalidateQueries({ queryKey: [["siteDocs"]] });
			},
			onError: (err) => toast.error(err.message),
		}),
	);

	const activeSite = sites.find((s) => s.id === siteId) ?? null;
	const title = useMemo(() => {
		if (selection?.kind === "file") return selection.relativePath;
		if (selection?.kind === "map" && mapQuery.data) return mapQuery.data.title;
		return null;
	}, [selection, mapQuery.data]);

	const onSave = () => {
		if (!siteId || !selection) return;
		if (selection.kind === "file") {
			saveFile.mutate({
				projectId: siteId,
				relativePath: selection.relativePath,
				content: draft,
				expectedSha: sha ?? undefined,
			});
		} else if (selection.kind === "map" && mapQuery.data) {
			saveMap.mutate({
				id: mapQuery.data.id,
				projectId: siteId,
				kind: mapQuery.data.kind,
				title: mapQuery.data.title,
				content: draft,
			});
		}
	};

	useEffect(() => {
		const onMove = (e: MouseEvent) => {
			if (!dragRef.current) return;
			const next = Math.min(
				LIST_WIDTH_MAX,
				Math.max(
					LIST_WIDTH_MIN,
					dragRef.current.startW + (e.clientX - dragRef.current.startX),
				),
			);
			setListWidth(next);
		};
		const onUp = () => {
			if (!dragRef.current) return;
			dragRef.current = null;
			try {
				localStorage.setItem(LIST_WIDTH_KEY, String(listWidth));
			} catch {}
		};
		window.addEventListener("mousemove", onMove);
		window.addEventListener("mouseup", onUp);
		return () => {
			window.removeEventListener("mousemove", onMove);
			window.removeEventListener("mouseup", onUp);
		};
	}, [listWidth]);

	const toggleDir = (path: string) =>
		setExpanded((prev) => ({ ...prev, [path]: !prev[path] }));

	const renderTree = (nodes: TreeNode[], depth = 0): React.ReactNode =>
		nodes.map((node) => {
			if (node.type === "dir") {
				const open = expanded[node.relativePath] ?? depth < 1;
				return (
					<div key={node.relativePath}>
						<button
							type="button"
							onClick={() => toggleDir(node.relativePath)}
							className="flex w-full items-center gap-1 rounded-md px-2 py-1 text-left text-[12px] text-muted-foreground hover:bg-accent/40 hover:text-foreground"
							style={{ paddingLeft: 8 + depth * 10 }}
						>
							{open ? (
								<ChevronDownIcon className="size-3 shrink-0" />
							) : (
								<ChevronRightIcon className="size-3 shrink-0" />
							)}
							<FolderIcon className="size-3.5 shrink-0" />
							<span className="truncate">{node.name}</span>
						</button>
						{open && node.children
							? renderTree(node.children, depth + 1)
							: null}
					</div>
				);
			}
			const selected =
				selection?.kind === "file" &&
				selection.relativePath === node.relativePath;
			return (
				<button
					key={node.relativePath}
					type="button"
					onClick={() =>
						setSelection({ kind: "file", relativePath: node.relativePath })
					}
					className={cn(
						"flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left text-[12px] hover:bg-accent/40",
						selected && "border border-primary/40 bg-primary/10",
					)}
					style={{ paddingLeft: 8 + depth * 10 }}
				>
					<FileTextIcon className="size-3.5 shrink-0 text-muted-foreground" />
					<span className="truncate">{node.name}</span>
				</button>
			);
		});

	return (
		<div className="flex h-full flex-col">
			<header className="flex h-11 shrink-0 items-center justify-between gap-3 border-border border-b px-3">
				<div className="flex min-w-0 items-center gap-2">
					<h1 className="font-[510] text-[14px] tracking-[-0.015em]">
						Site Docs
					</h1>
					{activeSite ? (
						<span className="truncate text-[11px] text-muted-foreground">
							{activeSite.name}
							{title ? ` · ${title}` : ""}
						</span>
					) : (
						<span className="text-[11px] text-muted-foreground">
							Link a docs folder from Create Project → Existing
						</span>
					)}
				</div>
				<div className="flex items-center gap-1.5">
					<Button
						variant="ghost"
						size="sm"
						className="h-7 px-2 text-[12px]"
						onClick={() => {
							treeQuery.refetch();
							sitesQuery.refetch();
						}}
					>
						<RefreshCwIcon
							className={cn("size-3.5", treeQuery.isFetching && "animate-spin")}
						/>
						<span className="hidden sm:inline">Refresh</span>
					</Button>
					<Button
						variant={inspectorOpen ? "secondary" : "ghost"}
						size="sm"
						className="h-7 px-2 text-[12px]"
						onClick={() => {
							setInspectorOpen((v) => {
								const next = !v;
								try {
									localStorage.setItem(INSPECTOR_OPEN_KEY, next ? "1" : "0");
								} catch {}
								return next;
							});
						}}
					>
						{inspectorOpen ? (
							<PanelRightCloseIcon className="size-3.5" />
						) : (
							<PanelRightIcon className="size-3.5" />
						)}
						<span className="hidden sm:inline">Inspector</span>
					</Button>
					{selection ? (
						<Button
							size="sm"
							className="h-7 px-2.5 text-[12px]"
							onClick={onSave}
							disabled={saveFile.isPending || saveMap.isPending}
						>
							<SaveIcon className="size-3.5" />
							Save
						</Button>
					) : null}
				</div>
			</header>

			<div className="flex min-h-0 grow">
				<aside
					className="relative flex shrink-0 flex-col border-border border-r bg-background"
					style={{ width: listWidth }}
				>
					<div className="space-y-1 border-border border-b p-2.5">
						<div className="px-1 font-[510] text-[10px] text-muted-foreground uppercase tracking-wider">
							Sites
						</div>
						{sites.length === 0 ? (
							<div className="space-y-2 px-1 py-2 text-[12px] text-muted-foreground">
								<p>No sites with a docs folder yet.</p>
								<Button asChild size="sm" variant="outline" className="h-7">
									<Link href={`${user.basePath}/create-project`}>
										Create / link a site
									</Link>
								</Button>
							</div>
						) : (
							<div className="max-h-40 space-y-0.5 overflow-y-auto">
								{sites.map((s) => (
									<button
										key={s.id}
										type="button"
										onClick={() => {
											setSiteId(s.id);
											setSelection(null);
										}}
										className={cn(
											"flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12.5px] hover:bg-accent/40",
											siteId === s.id && "bg-primary/10 text-foreground",
										)}
									>
										<span
											className="size-2 shrink-0 rounded-full"
											style={{ backgroundColor: s.color || "#8f8f8a" }}
										/>
										<span className="truncate font-[510]">{s.name}</span>
									</button>
								))}
							</div>
						)}
					</div>

					<div className="min-h-0 flex-1 overflow-y-auto p-1.5">
						{!siteId ? null : treeQuery.isLoading ? (
							<div className="p-3 text-[12px] text-muted-foreground">
								Loading…
							</div>
						) : (
							<>
								{(treeQuery.data?.maps?.length ?? 0) > 0 ? (
									<div className="mb-2">
										<div className="px-2 py-1 font-[510] text-[10px] text-muted-foreground uppercase tracking-wider">
											Nexus Maps
										</div>
										{treeQuery.data!.maps.map((m) => {
											const selected =
												selection?.kind === "map" && selection.mapId === m.id;
											return (
												<button
													key={m.id}
													type="button"
													onClick={() =>
														setSelection({ kind: "map", mapId: m.id })
													}
													className={cn(
														"flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left text-[12px] hover:bg-accent/40",
														selected &&
															"border border-primary/40 bg-primary/10",
													)}
												>
													<MapIcon kind={m.kind} />
													<span className="truncate">{m.title}</span>
													{m.stale ? (
														<span className="ml-auto text-[9px] text-amber-500">
															draft
														</span>
													) : null}
												</button>
											);
										})}
									</div>
								) : null}
								<div className="px-2 py-1 font-[510] text-[10px] text-muted-foreground uppercase tracking-wider">
									Docs
								</div>
								{(treeQuery.data?.tree?.length ?? 0) === 0 ? (
									<div className="p-3 text-[12px] text-muted-foreground">
										No markdown in this docs folder.
									</div>
								) : (
									renderTree(treeQuery.data!.tree)
								)}
							</>
						)}
					</div>

					<button
						type="button"
						aria-label="Resize rail"
						className="absolute inset-y-0 right-0 w-1 cursor-col-resize hover:bg-primary/30"
						onMouseDown={(e) => {
							dragRef.current = { startX: e.clientX, startW: listWidth };
						}}
					/>
				</aside>

				<section className="flex min-w-0 flex-1 flex-col">
					{!selection ? (
						<div className="flex flex-1 flex-col items-center justify-center gap-2 text-center text-muted-foreground">
							<Share2Icon className="size-8 opacity-40" />
							<p className="text-[13px]">
								Select a site, then a doc or Nexus Map.
							</p>
						</div>
					) : (
						<>
							<div className="flex h-9 items-center gap-2 border-border border-b px-4">
								<div className="inline-flex rounded-md border border-border p-0.5 text-[11px]">
									<button
										type="button"
										className={cn(
											"rounded px-2 py-0.5",
											mode === "preview" && "bg-accent text-foreground",
										)}
										onClick={() => setMode("preview")}
									>
										Preview
									</button>
									<button
										type="button"
										className={cn(
											"rounded px-2 py-0.5 font-mono",
											mode === "edit" && "bg-accent text-foreground",
										)}
										onClick={() => setMode("edit")}
										aria-label="Source"
										title="Source"
									>
										{"</>"}
									</button>
								</div>
								<span className="truncate font-mono text-[11px] text-muted-foreground">
									{selection.kind === "file"
										? selection.relativePath
										: "Nexus Map · app storage"}
								</span>
							</div>
							<div className="min-h-0 flex-1 overflow-y-auto">
								<div className="mx-auto w-full max-w-[46rem] px-6 py-6">
									{mode === "preview" ? (
										<BlockEditor
											key={
												selection.kind === "file"
													? `${selection.relativePath}:${sha}`
													: selection.mapId
											}
											value={draft}
											onChange={setDraft}
											className="editor-xl [&_.tiptap]:min-h-[min(60vh,520px)]"
										/>
									) : (
										<textarea
											value={draft}
											onChange={(e) => setDraft(e.target.value)}
											spellCheck={false}
											className="min-h-[min(60vh,520px)] w-full resize-y rounded-md border border-border/60 bg-transparent px-3 py-2 font-mono text-[12.5px] text-foreground/90 leading-[1.65] outline-none focus-visible:ring-1 focus-visible:ring-ring"
											placeholder="Empty."
										/>
									)}
								</div>
							</div>
						</>
					)}
				</section>

				{inspectorOpen ? (
					<aside className="hidden w-[240px] shrink-0 flex-col border-border border-l bg-background md:flex">
						<div className="border-border border-b px-3 py-2 font-[510] text-[11px] text-muted-foreground uppercase tracking-wider">
							Inspector
						</div>
						<div className="space-y-3 overflow-y-auto p-3 text-[12px]">
							{activeSite ? (
								<>
									<div>
										<div className="text-[10px] text-muted-foreground uppercase">
											Site
										</div>
										<div className="font-[510]">{activeSite.name}</div>
									</div>
									<div>
										<div className="text-[10px] text-muted-foreground uppercase">
											Docs path
										</div>
										<div className="break-all font-mono text-[10.5px] text-muted-foreground">
											{treeQuery.data?.docsPath ?? activeSite.docsPath}
										</div>
									</div>
									{selection?.kind === "map" ? (
										<p className="text-muted-foreground">
											Nexus Map — stored in the app, not in the site docs
											folder.
										</p>
									) : (
										<p className="text-muted-foreground">
											Edits save through to disk at the docs path.
										</p>
									)}
								</>
							) : (
								<p className="text-muted-foreground">No site selected.</p>
							)}
						</div>
					</aside>
				) : null}
			</div>
		</div>
	);
}
