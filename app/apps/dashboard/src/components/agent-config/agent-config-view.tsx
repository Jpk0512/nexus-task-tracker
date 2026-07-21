"use client";

/**
 * Agent Config — Option B: chip-filtered multi-root library.
 * Disk folders are source of truth; Claude includes Code + Desktop roots.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@ui/components/ui/button";
import { Input } from "@ui/components/ui/input";
import { cn } from "@ui/lib/utils";
import {
	ChevronDownIcon,
	ChevronRightIcon,
	FilePlusIcon,
	FileTextIcon,
	FolderIcon,
	FolderPlusIcon,
	PanelRightCloseIcon,
	PanelRightIcon,
	RefreshCwIcon,
	SaveIcon,
	Settings2Icon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { BlockEditor } from "@/components/editor/block-editor";
import { trpc } from "@/utils/trpc";

const LIST_WIDTH_KEY = "nexus.agentConfig.listWidth";
const INSPECTOR_OPEN_KEY = "nexus.agentConfig.inspectorOpen";
const LIST_WIDTH_DEFAULT = 300;
const LIST_WIDTH_MIN = 240;
const LIST_WIDTH_MAX = 480;

type AgentFilter = "all" | "claude" | "codex" | "cursor" | "pi" | "oh" | "custom";

type TreeNode = {
	name: string;
	relativePath: string;
	type: "file" | "dir";
	children?: TreeNode[];
	expandable?: boolean;
};

type Selection = {
	rootId: string;
	relativePath: string;
} | null;

const FILTERS: { id: AgentFilter; label: string; tone?: string }[] = [
	{ id: "all", label: "All" },
	{ id: "claude", label: "Claude", tone: "#f2a65a" },
	{ id: "codex", label: "Codex", tone: "#4cb782" },
	{ id: "cursor", label: "Cursor", tone: "#26b5ce" },
	{ id: "pi", label: "Pi", tone: "#9b8afb" },
	{ id: "oh", label: "Oh", tone: "#e879a9" },
];

const AGENT_TONE: Record<string, string> = {
	claude: "#f2a65a",
	codex: "#4cb782",
	cursor: "#26b5ce",
	pi: "#9b8afb",
	oh: "#e879a9",
	custom: "#e6c35c",
};

function isMarkdown(path: string) {
	return /\.(md|mdx|markdown)$/i.test(path);
}

export function AgentConfigView() {
	const qc = useQueryClient();
	const [filter, setFilter] = useState<AgentFilter>("all");
	const [selection, setSelection] = useState<Selection>(null);
	const [draft, setDraft] = useState("");
	const [sha, setSha] = useState<string | null>(null);
	const [mode, setMode] = useState<"preview" | "edit">("preview");
	const [listWidth, setListWidth] = useState(LIST_WIDTH_DEFAULT);
	const [inspectorOpen, setInspectorOpen] = useState(true);
	const [expanded, setExpanded] = useState<Record<string, boolean>>({});
	const [childrenCache, setChildrenCache] = useState<
		Record<string, TreeNode[]>
	>({});
	const [loadingDirs, setLoadingDirs] = useState<Record<string, boolean>>({});
	const [revealSecrets, setRevealSecrets] = useState(false);
	const [addOpen, setAddOpen] = useState(false);
	const [addFileOpen, setAddFileOpen] = useState(false);
	const [addPath, setAddPath] = useState("");
	const [addLabel, setAddLabel] = useState("");
	const [addAgent, setAddAgent] = useState<AgentFilter>("custom");
	const [newFileRootId, setNewFileRootId] = useState<string>("");
	const [newFilePath, setNewFilePath] = useState("");
	const [search, setSearch] = useState("");
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

	const rootsQuery = useQuery(
		trpc.agentConfig.listRoots.queryOptions({
			agent: filter === "all" ? "all" : filter,
		}),
	);
	const roots = (rootsQuery.data ?? []).filter((r) => r.enabled);

	const trees = useQuery({
		queryKey: ["agentConfig", "trees", roots.map((r) => r.id).join(",")],
		enabled: roots.length > 0,
		queryFn: async () => {
			const out: Record<
				string,
				{ path: string; exists: boolean; tree: TreeNode[] }
			> = {};
			await Promise.all(
				roots.map(async (r) => {
					const data = await qc.fetchQuery(
						trpc.agentConfig.listTree.queryOptions({ rootId: r.id }),
					);
					out[r.id] = data as {
						path: string;
						exists: boolean;
						tree: TreeNode[];
					};
				}),
			);
			return out;
		},
	});

	useEffect(() => {
		if (!trees.data) return;
		setChildrenCache((prev) => {
			const next = { ...prev };
			for (const [rootId, data] of Object.entries(trees.data)) {
				next[`${rootId}:`] = data.tree;
			}
			return next;
		});
	}, [trees.data]);

	useEffect(() => {
		// Reset directory cache when agent filter changes
		setChildrenCache({});
		setExpanded({});
	}, [filter]);

	const fileQuery = useQuery({
		...trpc.agentConfig.readFile.queryOptions({
			rootId: selection?.rootId ?? "",
			relativePath: selection?.relativePath ?? "",
			revealSecrets,
		}),
		enabled: !!selection,
	});

	useEffect(() => {
		if (fileQuery.data) {
			setDraft(fileQuery.data.content);
			setSha(fileQuery.data.sha);
			setMode("preview");
		}
	}, [fileQuery.data]);

	const saveFile = useMutation(
		trpc.agentConfig.writeFile.mutationOptions({
			onSuccess: (res) => {
				setSha(res.sha);
				toast.success("Saved to disk");
				qc.invalidateQueries({ queryKey: [["agentConfig", "readFile"]] });
			},
			onError: (err) => toast.error(err.message),
		}),
	);

	const addRoot = useMutation(
		trpc.agentConfig.addRoot.mutationOptions({
			onSuccess: () => {
				toast.success("Folder added");
				setAddOpen(false);
				setAddPath("");
				setAddLabel("");
				qc.invalidateQueries({ queryKey: [["agentConfig"]] });
			},
			onError: (err) => toast.error(err.message),
		}),
	);

	const createFile = useMutation(
		trpc.agentConfig.createFile.mutationOptions({
			onSuccess: async (res, vars) => {
				toast.success(`Created ${res.relativePath}`);
				setAddFileOpen(false);
				setNewFilePath("");
				const parent = res.relativePath.includes("/")
					? res.relativePath.split("/").slice(0, -1).join("/")
					: "";
				setChildrenCache((prev) => {
					const next = { ...prev };
					delete next[`${vars.rootId}:${parent}`];
					delete next[`${vars.rootId}:`];
					return next;
				});
				await trees.refetch();
				if (parent) {
					setExpanded((prev) => ({
						...prev,
						[`root:${vars.rootId}`]: true,
						[`${vars.rootId}:${parent}`]: true,
					}));
					try {
						const data = await qc.fetchQuery(
							trpc.agentConfig.listChildren.queryOptions({
								rootId: vars.rootId,
								relativePath: parent,
							}),
						);
						setChildrenCache((p) => ({
							...p,
							[`${vars.rootId}:${parent}`]: data.children as TreeNode[],
						}));
					} catch {}
				}
				setSelection({
					rootId: vars.rootId,
					relativePath: res.relativePath,
				});
				setDraft(vars.content ?? "");
				setSha(res.sha);
				setMode("edit");
			},
			onError: (err) => toast.error(err.message),
		}),
	);

	const openAddFile = () => {
		setAddOpen(false);
		const preferred =
			selection?.rootId && roots.some((r) => r.id === selection.rootId)
				? selection.rootId
				: (roots[0]?.id ?? "");
		setNewFileRootId(preferred);
		let starter = "";
		if (selection?.relativePath) {
			const parts = selection.relativePath.split("/");
			if (parts.length > 1) starter = `${parts.slice(0, -1).join("/")}/`;
		}
		setNewFilePath(starter);
		setAddFileOpen((v) => !v);
	};

	const removeRoot = useMutation(
		trpc.agentConfig.removeRoot.mutationOptions({
			onSuccess: () => {
				toast.success("Root removed");
				setSelection(null);
				qc.invalidateQueries({ queryKey: [["agentConfig"]] });
			},
			onError: (err) => toast.error(err.message),
		}),
	);

	const activeRoot = useMemo(
		() => roots.find((r) => r.id === selection?.rootId) ?? null,
		[roots, selection],
	);

	const onSave = () => {
		if (!selection) return;
		const secret = fileQuery.data?.secret;
		saveFile.mutate({
			rootId: selection.rootId,
			relativePath: selection.relativePath,
			content: draft,
			expectedSha: sha ?? undefined,
			allowSecretWrite: secret ? true : undefined,
		});
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

	const matchesSearch = (name: string, path: string) => {
		if (!search.trim()) return true;
		const q = search.trim().toLowerCase();
		return name.toLowerCase().includes(q) || path.toLowerCase().includes(q);
	};

	const cacheKey = (rootId: string, relativePath: string) =>
		`${rootId}:${relativePath}`;

	const loadChildren = async (rootId: string, relativePath: string) => {
		const key = cacheKey(rootId, relativePath);
		if (childrenCache[key] || loadingDirs[key]) return;
		setLoadingDirs((p) => ({ ...p, [key]: true }));
		try {
			const data = await qc.fetchQuery(
				trpc.agentConfig.listChildren.queryOptions({
					rootId,
					relativePath,
				}),
			);
			setChildrenCache((p) => ({ ...p, [key]: data.children as TreeNode[] }));
		} catch (err) {
			toast.error((err as Error).message ?? "Failed to list folder");
		} finally {
			setLoadingDirs((p) => ({ ...p, [key]: false }));
		}
	};

	const toggleDir = (rootId: string, relativePath: string) => {
		const key = cacheKey(rootId, relativePath);
		const nextOpen = !(expanded[key] ?? false);
		setExpanded((prev) => ({ ...prev, [key]: nextOpen }));
		if (nextOpen) void loadChildren(rootId, relativePath);
	};

	const renderTree = (rootId: string, nodes: TreeNode[], depth: number) =>
		nodes.map((node) => {
			if (node.type === "dir") {
				const key = cacheKey(rootId, node.relativePath);
				const open = expanded[key] ?? false;
				const kids = childrenCache[key] ?? node.children ?? [];
				const loading = !!loadingDirs[key];
				if (
					search.trim() &&
					!matchesSearch(node.name, node.relativePath) &&
					!kids.some((c) => matchesSearch(c.name, c.relativePath))
				) {
					return null;
				}
				return (
					<div key={key}>
						<button
							type="button"
							onClick={() => toggleDir(rootId, node.relativePath)}
							className="flex w-full items-center gap-1 rounded-md px-2 py-1 text-left text-[12px] hover:bg-accent/40"
							style={{ paddingLeft: 8 + depth * 10 }}
						>
							{open ? (
								<ChevronDownIcon className="size-3.5 shrink-0 text-muted-foreground" />
							) : (
								<ChevronRightIcon className="size-3.5 shrink-0 text-muted-foreground" />
							)}
							<FolderIcon className="size-3.5 shrink-0 text-muted-foreground" />
							<span className="truncate">{node.name}</span>
							{loading ? (
								<span className="ml-auto text-[10px] text-muted-foreground">
									…
								</span>
							) : null}
						</button>
						{open ? renderTree(rootId, kids, depth + 1) : null}
					</div>
				);
			}
			if (!matchesSearch(node.name, node.relativePath)) return null;
			const selected =
				selection?.rootId === rootId &&
				selection.relativePath === node.relativePath;
			return (
				<button
					key={`${rootId}:${node.relativePath}`}
					type="button"
					onClick={() => {
						setRevealSecrets(false);
						setSelection({ rootId, relativePath: node.relativePath });
					}}
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
						Agent Config
					</h1>
					<span className="truncate text-[11px] text-muted-foreground">
						{roots.length} root{roots.length === 1 ? "" : "s"}
						{filter !== "all" ? ` · ${filter}` : ""}
						{selection ? ` · ${selection.relativePath}` : ""}
					</span>
				</div>
				<div className="flex items-center gap-1.5">
					<Button
						variant="ghost"
						size="sm"
						className="h-7 px-2 text-[12px]"
						onClick={() => {
							setAddFileOpen(false);
							setAddOpen((v) => !v);
						}}
					>
						<FolderPlusIcon className="size-3.5" />
						<span className="hidden sm:inline">Add folder</span>
					</Button>
					<Button
						variant="ghost"
						size="sm"
						className="h-7 px-2 text-[12px]"
						onClick={openAddFile}
						disabled={roots.length === 0}
					>
						<FilePlusIcon className="size-3.5" />
						<span className="hidden sm:inline">Add file</span>
					</Button>
					<Button
						variant="ghost"
						size="sm"
						className="h-7 px-2 text-[12px]"
						onClick={() => {
							rootsQuery.refetch();
							trees.refetch();
							if (selection) fileQuery.refetch();
						}}
					>
						<RefreshCwIcon
							className={cn(
								"size-3.5",
								(rootsQuery.isFetching || trees.isFetching) && "animate-spin",
							)}
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
					</Button>
					{selection ? (
						<Button
							size="sm"
							className="h-7 px-2.5 text-[12px]"
							onClick={onSave}
							disabled={saveFile.isPending || fileQuery.data?.masked}
						>
							<SaveIcon className="size-3.5" />
							{saveFile.isPending ? "Saving…" : "Save"}
						</Button>
					) : null}
				</div>
			</header>

			{addOpen ? (
				<div className="flex flex-wrap items-end gap-2 border-border border-b bg-card/30 px-3 py-2">
					<div className="min-w-[140px] flex-1 space-y-1">
						<label className="text-[10px] text-muted-foreground uppercase">
							Path
						</label>
						<Input
							value={addPath}
							onChange={(e) => setAddPath(e.target.value)}
							placeholder="~/my-agent or /host/home/…"
							className="h-8 font-mono text-[12px]"
						/>
					</div>
					<div className="w-[120px] space-y-1">
						<label className="text-[10px] text-muted-foreground uppercase">
							Label
						</label>
						<Input
							value={addLabel}
							onChange={(e) => setAddLabel(e.target.value)}
							placeholder="Name"
							className="h-8 text-[12px]"
						/>
					</div>
					<div className="w-[110px] space-y-1">
						<label className="text-[10px] text-muted-foreground uppercase">
							Agent
						</label>
						<select
							value={addAgent === "all" ? "custom" : addAgent}
							onChange={(e) => setAddAgent(e.target.value as AgentFilter)}
							className="flex h-8 w-full rounded-md border border-border bg-background px-2 text-[12px]"
						>
							{FILTERS.filter((f) => f.id !== "all").map((f) => (
								<option key={f.id} value={f.id}>
									{f.label}
								</option>
							))}
							<option value="custom">Custom</option>
						</select>
					</div>
					<Button
						size="sm"
						className="h-8"
						disabled={!addPath.trim() || !addLabel.trim() || addRoot.isPending}
						onClick={() =>
							addRoot.mutate({
								path: addPath.trim(),
								label: addLabel.trim(),
								agent: (addAgent === "all" ? "custom" : addAgent) as
									| "claude"
									| "codex"
									| "cursor"
									| "pi"
									| "oh"
									| "custom",
							})
						}
					>
						Add
					</Button>
				</div>
			) : null}

			{addFileOpen ? (
				<div className="flex flex-wrap items-end gap-2 border-border border-b bg-card/30 px-3 py-2">
					<div className="w-[200px] space-y-1">
						<label className="text-[10px] text-muted-foreground uppercase">
							Root
						</label>
						<select
							value={newFileRootId}
							onChange={(e) => setNewFileRootId(e.target.value)}
							className="flex h-8 w-full rounded-md border border-border bg-background px-2 text-[12px]"
						>
							{roots.map((r) => (
								<option key={r.id} value={r.id}>
									{r.agent === "claude"
										? `${r.label} · Claude`
										: r.label}
								</option>
							))}
						</select>
					</div>
					<div className="min-w-[200px] flex-1 space-y-1">
						<label className="text-[10px] text-muted-foreground uppercase">
							Relative path
						</label>
						<Input
							value={newFilePath}
							onChange={(e) => setNewFilePath(e.target.value)}
							placeholder="agents/my-agent.md or settings.local.json"
							className="h-8 font-mono text-[12px]"
							onKeyDown={(e) => {
								if (e.key === "Enter" && newFileRootId && newFilePath.trim()) {
									createFile.mutate({
										rootId: newFileRootId,
										relativePath: newFilePath.trim(),
										content: "",
									});
								}
							}}
						/>
					</div>
					<Button
						size="sm"
						className="h-8"
						disabled={
							!newFileRootId || !newFilePath.trim() || createFile.isPending
						}
						onClick={() =>
							createFile.mutate({
								rootId: newFileRootId,
								relativePath: newFilePath.trim(),
								content: "",
							})
						}
					>
						{createFile.isPending ? "Creating…" : "Create"}
					</Button>
				</div>
			) : null}

			<div className="flex min-h-0 flex-1">
				<aside
					className="flex shrink-0 flex-col border-border border-r bg-background"
					style={{ width: listWidth }}
				>
					<div className="space-y-2 border-border border-b p-2.5">
						<div className="flex flex-wrap gap-1">
							{FILTERS.map((f) => (
								<button
									key={f.id}
									type="button"
									onClick={() => {
										setFilter(f.id);
										setSelection(null);
									}}
									className={cn(
										"inline-flex h-6 items-center gap-1.5 rounded-full border px-2.5 text-[11px]",
										filter === f.id
											? "border-primary/40 bg-primary/10 text-foreground"
											: "border-border text-muted-foreground hover:bg-accent/40",
									)}
								>
									{f.tone ? (
										<span
											className="size-1.5 rounded-full"
											style={{ background: f.tone }}
										/>
									) : null}
									{f.label}
								</button>
							))}
						</div>
						<Input
							value={search}
							onChange={(e) => setSearch(e.target.value)}
							placeholder="settings, mcp, hooks…"
							className="h-7 text-[12px]"
						/>
					</div>
					<div className="min-h-0 flex-1 overflow-y-auto p-1.5">
						{rootsQuery.isError ? (
							<div className="px-2 py-6 text-center text-[12px] text-destructive">
								Failed to load roots.
							</div>
						) : roots.length === 0 && !rootsQuery.isLoading ? (
							<div className="px-2 py-6 text-center text-[12px] text-muted-foreground">
								{filter === "oh"
									? "Oh has no folder yet — use Add folder."
									: "No roots for this filter."}
							</div>
						) : rootsQuery.isLoading || (roots.length > 0 && trees.isLoading) ? (
							<div className="px-2 py-6 text-center text-[12px] text-muted-foreground">
								Loading…
							</div>
						) : (
							roots.map((root) => {
								const tree = trees.data?.[root.id];
								const rootKey = `root:${root.id}`;
								const open = expanded[rootKey] ?? true;
								const topNodes =
									childrenCache[`${root.id}:`] ?? tree?.tree ?? [];
								return (
									<div key={root.id} className="mb-1">
										<button
											type="button"
											onClick={() =>
												setExpanded((prev) => ({
													...prev,
													[rootKey]: !open,
												}))
											}
											className="flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-[12px] hover:bg-accent/40"
										>
											{open ? (
												<ChevronDownIcon className="size-3.5 text-muted-foreground" />
											) : (
												<ChevronRightIcon className="size-3.5 text-muted-foreground" />
											)}
											<span
												className="size-2 shrink-0 rounded-full"
												style={{
													background:
														AGENT_TONE[root.agent] ?? AGENT_TONE.custom,
												}}
											/>
											<span className="min-w-0 flex-1 truncate font-[510]">
												{root.agent === "claude"
													? `${root.label} · Claude`
													: root.label}
											</span>
											{!root.exists ? (
												<span className="text-[10px] text-destructive">
													missing
												</span>
											) : (
												<span className="text-[10px] text-muted-foreground">
													{topNodes.length}
												</span>
											)}
										</button>
										{open && topNodes.length > 0 ? (
											<div>{renderTree(root.id, topNodes, 1)}</div>
										) : null}
										{open && tree && !tree.exists ? (
											<p className="px-3 py-1 font-mono text-[10px] text-muted-foreground">
												{tree.path}
											</p>
										) : null}
										{open && tree?.exists && topNodes.length === 0 ? (
											<p className="px-3 py-1 text-[11px] text-muted-foreground">
												No config files at this root.
											</p>
										) : null}
									</div>
								);
							})
						)}
					</div>
				</aside>

				<div
					role="separator"
					aria-orientation="vertical"
					className="w-1 shrink-0 cursor-col-resize bg-transparent hover:bg-primary/30"
					onMouseDown={(e) => {
						dragRef.current = { startX: e.clientX, startW: listWidth };
					}}
				/>

				<section className="flex min-w-0 flex-1 flex-col">
					{!selection ? (
						<div className="flex flex-1 flex-col items-center justify-center gap-2 text-center text-muted-foreground">
							<Settings2Icon className="size-8 opacity-40" />
							<p className="text-[13px]">
								Filter by agent, then open a config file.
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
									{selection.relativePath}
								</span>
								{fileQuery.data?.masked ? (
									<Button
										variant="ghost"
										size="sm"
										className="ml-auto h-6 px-2 text-[11px]"
										onClick={() => setRevealSecrets(true)}
									>
										Reveal secrets
									</Button>
								) : null}
							</div>
							<div className="min-h-0 flex-1 overflow-y-auto">
								<div className="mx-auto w-full max-w-[46rem] px-6 py-6">
									{mode === "preview" &&
									isMarkdown(selection.relativePath) ? (
										<BlockEditor
											key={`${selection.rootId}:${selection.relativePath}:${sha}`}
											value={draft}
											onChange={setDraft}
											className="editor-xl [&_.tiptap]:min-h-[min(60vh,520px)]"
										/>
									) : mode === "preview" ? (
										<pre className="whitespace-pre-wrap break-words rounded-md border border-border/50 bg-muted/20 p-4 font-mono text-[12px] leading-[1.6] text-foreground/90">
											{draft || "Empty."}
										</pre>
									) : (
										<textarea
											value={draft}
											onChange={(e) => setDraft(e.target.value)}
											spellCheck={false}
											disabled={!!fileQuery.data?.masked}
											className="min-h-[min(60vh,520px)] w-full resize-y rounded-md border border-border/60 bg-transparent px-3 py-2 font-mono text-[12.5px] leading-[1.65] text-foreground/90 outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-60"
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
							{activeRoot ? (
								<>
									<div>
										<div className="text-[10px] text-muted-foreground uppercase">
											Agent
										</div>
										<div className="font-[510] capitalize">{activeRoot.agent}</div>
									</div>
									<div>
										<div className="text-[10px] text-muted-foreground uppercase">
											Root
										</div>
										<div className="font-[510]">{activeRoot.label}</div>
									</div>
									<div>
										<div className="text-[10px] text-muted-foreground uppercase">
											Path
										</div>
										<div className="break-all font-mono text-[10.5px] text-muted-foreground">
											{activeRoot.resolved ?? activeRoot.path}
										</div>
									</div>
									{fileQuery.data?.secret ? (
										<p className="text-amber-500/90">
											Secret file — content masked until revealed. Save requires
											confirm.
										</p>
									) : (
										<p className="text-muted-foreground">
											Edits save through to disk at this root.
										</p>
									)}
									<Button
										variant="ghost"
										size="sm"
										className="h-7 w-full justify-start px-2 text-[11px] text-destructive"
										onClick={() => {
											if (
												confirm(
													`Remove root “${activeRoot.label}” from Agent Config? Disk files are not deleted.`,
												)
											) {
												removeRoot.mutate({ id: activeRoot.id });
											}
										}}
									>
										Remove root
									</Button>
								</>
							) : (
								<p className="text-muted-foreground">
									Select a file to inspect its root. Claude filter shows both
									Code and Desktop.
								</p>
							)}
						</div>
					</aside>
				) : null}
			</div>
		</div>
	);
}
