"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@ui/components/ui/badge";
import { Button } from "@ui/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
} from "@ui/components/ui/dialog";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@ui/components/ui/dropdown-menu";
import { Input } from "@ui/components/ui/input";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@ui/components/ui/popover";
import { Skeleton } from "@ui/components/ui/skeleton";
import { cn } from "@ui/lib/utils";
import { formatDistanceToNowStrict } from "date-fns";
import {
	ArrowLeftIcon,
	CheckIcon,
	ChevronDownIcon,
	ClipboardIcon,
	FolderIcon,
	HistoryIcon,
	MinusIcon,
	SaveIcon,
	SearchIcon,
	Trash2Icon,
	XIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { BacklinksPanel } from "@/components/backlinks/backlinks-panel";
import { BlockEditor } from "@/components/editor/block-editor";
import { tagColor } from "@/lib/project-color";
import { trpc } from "@/utils/trpc";

type Props = { productSlug: string; promptSlug: string; team: string };

function extractVars(content: string): string[] {
	const out = new Set<string>();
	const re = /\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g;
	let m: RegExpExecArray | null;
	// biome-ignore lint/suspicious/noAssignInExpressions: standard regex loop
	while ((m = re.exec(content)) !== null) {
		out.add(m[1]);
	}
	return Array.from(out);
}

type PromptVersion = {
	id: string;
	version: number;
	content: string;
	notes: string | null;
	createdAt: string | Date;
	createdBy: string | null;
};

export function PromptEditView({ productSlug, promptSlug, team }: Props) {
	const qc = useQueryClient();
	const { data: prompt, isLoading } = useQuery(
		trpc.prompts.getPromptBySlug.queryOptions({ productSlug, promptSlug }),
	);

	const [content, setContent] = useState("");
	const [notes, setNotes] = useState("");
	const [varValues, setVarValues] = useState<Record<string, string>>({});
	const [diffVersion, setDiffVersion] = useState<PromptVersion | null>(null);
	const [projectId, setProjectId] = useState<string | null>(null);
	const [pickerOpen, setPickerOpen] = useState(false);
	const [projectSearch, setProjectSearch] = useState("");

	// Past versions — populated as users hit "Save as new version". Older
	// prompts created before the snapshot landed will simply have an empty
	// list until the first new bump.
	const versionsQuery = useQuery({
		...trpc.prompts.getVersions.queryOptions({ promptId: prompt?.id ?? "" }),
		enabled: !!prompt?.id,
	});
	const versions = (versionsQuery.data ?? []) as PromptVersion[];

	// biome-ignore lint/correctness/useExhaustiveDependencies: intentional — reset all editable fields only when the prompt identity changes, not on every field update
	useEffect(() => {
		if (prompt) {
			setContent(prompt.content);
			setNotes(prompt.notes ?? "");
			// prompt.projectId is returned by the server but absent from the inferred client type
			setProjectId(
				((prompt as unknown as Record<string, unknown>).projectId as
					| string
					| null
					| undefined) ?? null,
			);
			const vars = extractVars(prompt.content);
			setVarValues(Object.fromEntries(vars.map((v) => [v, ""])));
		}
	}, [prompt?.id]);

	const vars = useMemo(() => extractVars(content), [content]);

	const updateMut = useMutation(
		trpc.prompts.updatePrompt.mutationOptions({
			onSuccess: (_data, vars) => {
				const bumped =
					typeof vars === "object" &&
					vars !== null &&
					vars.bumpVersion === true;
				toast.success(bumped ? "Saved as new version" : "Saved");
				qc.invalidateQueries({
					queryKey: [["prompts", "getPromptBySlug"]],
				});
				qc.invalidateQueries({ queryKey: [["prompts", "getPrompts"]] });
				if (bumped) {
					qc.invalidateQueries({
						queryKey: [["prompts", "getVersions"]],
					});
				}
			},
			onError: (e) => toast.error(e.message),
		}),
	);
	const deleteMut = useMutation(
		trpc.prompts.deletePrompt.mutationOptions({
			onSuccess: () => {
				toast.success("Deleted");
				if (typeof window !== "undefined") {
					window.location.href = `/team/${team}/prompts/${productSlug}`;
				}
			},
		}),
	);

	const setProjectMut = useMutation(
		trpc.prompts.setProject.mutationOptions({
			onSuccess: () => {
				qc.invalidateQueries({ queryKey: [["prompts", "getPromptBySlug"]] });
				qc.invalidateQueries({ queryKey: [["prompts", "getPrompts"]] });
			},
			onError: (e) => toast.error(e.message),
		}),
	);

	const projectsQuery = useQuery(trpc.projects.get.queryOptions());
	const allProjects = (projectsQuery.data ?? []) as Array<{
		id: string;
		name: string;
	}>;

	const selectedProject = allProjects.find((p) => p.id === projectId) ?? null;

	const filteredProjects = projectSearch.trim()
		? allProjects.filter((p) =>
				p.name.toLowerCase().includes(projectSearch.toLowerCase()),
			)
		: allProjects;

	const handleSelectProject = (id: string | null) => {
		setProjectId(id);
		setPickerOpen(false);
		setProjectSearch("");
		if (prompt) {
			setProjectMut.mutate({ promptId: prompt.id, projectId: id });
		}
	};

	if (isLoading)
		return (
			<div className="px-6 py-4 text-muted-foreground text-sm">Loading…</div>
		);
	if (!prompt)
		return (
			<div className="px-6 py-4">
				<Link
					href={`/team/${team}/prompts/${productSlug}`}
					className="inline-flex items-center gap-1 text-muted-foreground text-sm hover:text-foreground"
				>
					<ArrowLeftIcon className="size-3.5" /> Back
				</Link>
				<p className="mt-4">Prompt not found.</p>
			</div>
		);

	const fillTemplate = (raw: string) => {
		let out = raw;
		for (const [k, v] of Object.entries(varValues)) {
			out = out.replaceAll(`{{${k}}}`, v);
			out = out.replace(new RegExp(`\\{\\{\\s*${k}\\s*\\}\\}`, "g"), v);
		}
		return out;
	};

	const copyFilled = () => {
		const filled = fillTemplate(content);
		navigator.clipboard?.writeText(filled);
		toast.success("Filled prompt copied");
	};

	return (
		<div className="flex h-full flex-col">
			<header className="border-border border-b px-6 py-3">
				<Link
					href={`/team/${team}/prompts/${productSlug}`}
					className="inline-flex items-center gap-1 text-muted-foreground text-xs hover:text-foreground"
				>
					<ArrowLeftIcon className="size-3.5" /> {prompt.productName}
				</Link>
				<div className="mt-2 flex items-start justify-between gap-4">
					<div>
						<h1 className="font-[510] text-[15px] text-foreground tracking-[-0.012em]">
							{prompt.name}
						</h1>
						<div className="mt-1 flex items-center gap-2 text-muted-foreground text-xs">
							<Badge variant="outline" className="gap-1 font-normal">
								<HistoryIcon className="size-3" /> v{prompt.version}
							</Badge>
							<span>
								· updated {new Date(prompt.updatedAt).toLocaleString()}
							</span>
						</div>
					</div>
					<div className="flex gap-2">
						<Button variant="outline" size="sm" onClick={copyFilled}>
							<ClipboardIcon className="size-3.5" /> Copy filled
						</Button>
						<DropdownMenu>
							<DropdownMenuTrigger asChild>
								<Button
									variant="outline"
									size="sm"
									disabled={versionsQuery.isLoading || versions.length === 0}
									title={
										versions.length === 0
											? "No prior versions yet — bump to start history"
											: "Browse prior versions"
									}
								>
									<HistoryIcon className="size-3.5" /> Versions
									{versions.length > 0 ? ` (${versions.length})` : ""}
								</Button>
							</DropdownMenuTrigger>
							<DropdownMenuContent align="end" className="w-72">
								<DropdownMenuLabel className="flex items-center justify-between text-[11px] uppercase tracking-wider">
									<span>History</span>
									<span className="text-muted-foreground">
										Current v{prompt.version}
									</span>
								</DropdownMenuLabel>
								<DropdownMenuSeparator />
								{versions.length === 0 && (
									<div className="px-2 py-3 text-center text-muted-foreground text-xs">
										No prior versions. Hit "Save as new version" to start.
									</div>
								)}
								{versions.map((v) => (
									<DropdownMenuItem
										key={v.id}
										onSelect={(e) => {
											e.preventDefault();
											setDiffVersion(v);
										}}
										className="flex items-start justify-between gap-2"
									>
										<div className="flex flex-col">
											<span className="font-[510]">v{v.version}</span>
											<span className="text-[11px] text-muted-foreground">
												{formatDistanceToNowStrict(new Date(v.createdAt), {
													addSuffix: true,
												})}
											</span>
										</div>
										<Badge
											variant="outline"
											className="self-center font-normal text-[10px]"
										>
											Diff →
										</Badge>
									</DropdownMenuItem>
								))}
							</DropdownMenuContent>
						</DropdownMenu>
						<Button
							variant="outline"
							size="sm"
							onClick={() =>
								updateMut.mutate({
									id: prompt.id,
									content,
									notes,
									bumpVersion: true,
								})
							}
							disabled={updateMut.isPending}
						>
							<HistoryIcon className="size-3.5" /> Save as new version
						</Button>
						<Button
							size="sm"
							onClick={() =>
								updateMut.mutate({ id: prompt.id, content, notes })
							}
							disabled={updateMut.isPending}
						>
							<SaveIcon className="size-3.5" />{" "}
							{updateMut.isPending ? "Saving…" : "Save"}
						</Button>
						<Button
							variant="ghost"
							size="sm"
							onClick={() => {
								if (confirm(`Delete "${prompt.name}"?`)) {
									deleteMut.mutate({ id: prompt.id });
								}
							}}
							className="text-muted-foreground hover:text-destructive"
						>
							<Trash2Icon className="size-3.5" />
						</Button>
					</div>
				</div>
			</header>

			<div className="grid grow grid-cols-1 gap-4 overflow-hidden px-6 py-4 lg:grid-cols-[1fr_320px]">
				<div className="flex min-h-0 flex-col gap-2">
					<div className="text-muted-foreground text-xs uppercase tracking-wider">
						Prompt
					</div>
					<textarea
						value={content}
						onChange={(e) => setContent(e.target.value)}
						spellCheck={false}
						placeholder="Write your prompt. Use {{variable_name}} for placeholders."
						className="grow resize-none rounded-md border border-border bg-card/40 p-3 font-mono text-sm leading-relaxed outline-none"
					/>
					<div className="text-muted-foreground text-xs uppercase tracking-wider">
						Notes
					</div>
					{/*
					 * Notes is prose — context / when-to-reach-for guidance. The prompt
					 * `content` field above stays a plain textarea because the
					 * variable-extraction regex (`/\{\{(\w+)\}\}/g`) runs on raw text
					 * and Tiptap would wrap braces in spans on render. Iter10 brief
					 * targets the body editor specifically.
					 */}
					<div className="min-h-32 resize-y rounded-md border border-border bg-card/40 px-3 py-2">
						<BlockEditor
							value={notes}
							onChange={(value) => setNotes(value)}
							placeholder="Context, usage tips, when to reach for this…"
						/>
					</div>
				</div>

				<aside className="flex min-w-0 flex-col gap-3 overflow-y-auto rounded-md border border-border bg-card/40 p-3">
					<div className="text-muted-foreground text-xs uppercase tracking-wider">
						Variables ({vars.length})
					</div>
					{vars.length === 0 && (
						<p className="text-muted-foreground text-xs italic">
							No <code className="rounded bg-muted px-1">{"{{var}}"}</code>{" "}
							placeholders in the prompt yet.
						</p>
					)}
					{vars.map((v) => (
						<div key={v}>
							<label className="mb-1 block text-muted-foreground text-xs">
								<code className="rounded bg-muted px-1">{`{{${v}}}`}</code>
							</label>
							<Input
								value={varValues[v] ?? ""}
								onChange={(e) =>
									setVarValues({ ...varValues, [v]: e.target.value })
								}
								placeholder={`value for ${v}…`}
								className="h-8 text-xs"
							/>
						</div>
					))}
					{vars.length > 0 && (
						<div className="border-border border-t pt-3">
							<div className="mb-1 text-muted-foreground text-xs uppercase tracking-wider">
								Preview
							</div>
							<pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-muted p-2 font-mono text-xs">
								{fillTemplate(content)}
							</pre>
						</div>
					)}
					<div className="border-border border-t pt-3">
						<div className="mb-1.5 font-[600] text-[10px] text-muted-foreground uppercase tracking-[0.06em]">
							Project
						</div>
						{projectsQuery.isLoading ? (
							<Skeleton className="h-[30px] w-full rounded-md" />
						) : (
							<Popover open={pickerOpen} onOpenChange={setPickerOpen}>
								<PopoverTrigger asChild>
									<button
										type="button"
										disabled={setProjectMut.isPending}
										className={cn(
											"flex h-[30px] w-full items-center gap-1.5 rounded-sm border px-2 text-[12px] outline-none transition-[border-color,background-color,color] duration-150 ease-out",
											"focus:border-primary focus:ring-[3px] focus:ring-primary/25",
											selectedProject
												? "border-border bg-accent/30 text-foreground hover:border-border hover:bg-accent/50"
												: "border-transparent bg-transparent text-muted-foreground hover:border-border/80 hover:bg-accent/50 hover:text-foreground",
											setProjectMut.isPending &&
												"pointer-events-none opacity-50",
										)}
									>
										{selectedProject ? (
											<>
												<span
													className="inline-block h-[7px] w-[7px] shrink-0 rounded-full"
													style={{ background: tagColor(selectedProject.id) }}
												/>
												<span className="min-w-0 flex-1 truncate text-left">
													{selectedProject.name}
												</span>
												<button
													type="button"
													onClick={(e) => {
														e.stopPropagation();
														handleSelectProject(null);
													}}
													className={cn(
														"flex size-[14px] shrink-0 items-center justify-center rounded-[3px] text-muted-foreground outline-none transition-[background-color,color] duration-100 ease-out",
														"hover:bg-destructive/12 hover:text-destructive",
														"focus:ring-1 focus:ring-destructive/50",
													)}
													aria-label="Clear project"
												>
													<XIcon className="size-3" />
												</button>
											</>
										) : (
											<>
												<FolderIcon className="size-3 shrink-0 opacity-50" />
												<span className="flex-1 text-left">Set project…</span>
												<ChevronDownIcon className="ml-auto size-3 opacity-40" />
											</>
										)}
									</button>
								</PopoverTrigger>
								<PopoverContent
									align="start"
									className="data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 w-[--radix-popover-trigger-width] min-w-[200px] rounded-lg border border-border bg-popover p-1 shadow-[0_8px_24px_rgba(0,0,0,.5)] data-[state=closed]:animate-out data-[state=open]:animate-in"
								>
									<div className="flex items-center gap-1.5 border-border/70 border-b px-2 py-1.5">
										<SearchIcon className="size-3 shrink-0 text-muted-foreground" />
										<input
											value={projectSearch}
											onChange={(e) => setProjectSearch(e.target.value)}
											placeholder="Search projects…"
											className="flex-1 border-0 bg-transparent text-[12px] text-foreground outline-none placeholder:text-muted-foreground"
										/>
									</div>
									<button
										type="button"
										onClick={() => handleSelectProject(null)}
										className="flex w-full items-center gap-2 rounded-[5px] px-2 py-1.5 text-[12px] text-muted-foreground transition-[background-color,color] duration-100 ease-out hover:bg-accent hover:text-foreground"
									>
										<MinusIcon className="size-3" />
										No project
									</button>
									{filteredProjects.length > 0 && (
										<>
											<div className="my-1 border-border/60 border-t" />
											<div className="px-2 py-1 font-[600] text-[10px] text-muted-foreground uppercase tracking-[0.06em]">
												Projects
											</div>
											{filteredProjects.map((proj) => (
												<button
													key={proj.id}
													type="button"
													onClick={() => handleSelectProject(proj.id)}
													className={cn(
														"flex w-full cursor-pointer items-center gap-2 rounded-[5px] px-2 py-1.5 text-[13px] text-foreground transition-[background-color] duration-100 ease-out hover:bg-accent",
														projectId === proj.id && "bg-primary/[0.08]",
													)}
												>
													<span
														className="inline-block h-[7px] w-[7px] shrink-0 rounded-full"
														style={{ background: tagColor(proj.id) }}
													/>
													<span className="min-w-0 flex-1 truncate text-left">
														{proj.name}
													</span>
													{projectId === proj.id && (
														<CheckIcon className="ml-auto size-3 text-primary" />
													)}
												</button>
											))}
										</>
									)}
									{filteredProjects.length === 0 &&
										allProjects.length === 0 && (
											<div className="px-2 py-3 text-center text-[12px] text-muted-foreground">
												No projects yet
											</div>
										)}
									{filteredProjects.length === 0 && allProjects.length > 0 && (
										<div className="px-2 py-3 text-center text-[12px] text-muted-foreground">
											No projects match
										</div>
									)}
								</PopoverContent>
							</Popover>
						)}
					</div>
					<BacklinksPanel entityType="prompt" entityId={prompt.id} />
				</aside>
			</div>

			{diffVersion && (
				<VersionDiffDialog
					currentVersion={prompt.version}
					currentContent={prompt.content}
					version={diffVersion}
					onClose={() => setDiffVersion(null)}
					onRestore={() => {
						setContent(diffVersion.content);
						setNotes(diffVersion.notes ?? "");
						setDiffVersion(null);
						toast.message(
							`Loaded v${diffVersion.version} into the editor — Save to apply.`,
						);
					}}
				/>
			)}
		</div>
	);
}

// ─── Side-by-side diff dialog ──────────────────────────────────────────────
//
// Simple line-by-line diff. We use the longest-common-subsequence (LCS)
// algorithm — small inputs, no need for an external library. Renders
// both sides aligned, with red/green tints for removed/added lines and a
// neutral line for unchanged context.
//
// For now this is the "basic dropdown that loads the prior content" the
// brief says is acceptable as a minimum; the diff view itself is the
// bonus polish we squeezed in.

type DiffOp =
	| { type: "equal"; a: string; b: string }
	| { type: "add"; b: string }
	| { type: "remove"; a: string };

function computeLineDiff(a: string, b: string): DiffOp[] {
	const aLines = a.split("\n");
	const bLines = b.split("\n");
	const m = aLines.length;
	const n = bLines.length;

	// LCS table — O(m*n) memory but prompts are small enough.
	const dp: number[][] = Array.from({ length: m + 1 }, () =>
		new Array(n + 1).fill(0),
	);
	for (let i = m - 1; i >= 0; i--) {
		for (let j = n - 1; j >= 0; j--) {
			if (aLines[i] === bLines[j]) dp[i][j] = dp[i + 1][j + 1] + 1;
			else dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
		}
	}

	const out: DiffOp[] = [];
	let i = 0,
		j = 0;
	while (i < m && j < n) {
		if (aLines[i] === bLines[j]) {
			out.push({ type: "equal", a: aLines[i], b: bLines[j] });
			i++;
			j++;
		} else if (dp[i + 1][j] >= dp[i][j + 1]) {
			out.push({ type: "remove", a: aLines[i] });
			i++;
		} else {
			out.push({ type: "add", b: bLines[j] });
			j++;
		}
	}
	while (i < m) out.push({ type: "remove", a: aLines[i++] });
	while (j < n) out.push({ type: "add", b: bLines[j++] });
	return out;
}

function VersionDiffDialog({
	currentVersion,
	currentContent,
	version,
	onClose,
	onRestore,
}: {
	currentVersion: number;
	currentContent: string;
	version: PromptVersion;
	onClose: () => void;
	onRestore: () => void;
}) {
	const ops = useMemo(
		() => computeLineDiff(version.content, currentContent),
		[version.content, currentContent],
	);

	return (
		<Dialog open onOpenChange={(o) => !o && onClose()}>
			<DialogContent className="max-w-5xl">
				<DialogHeader>
					<DialogTitle className="flex items-center justify-between gap-4">
						<span>
							v{version.version} → v{currentVersion}
						</span>
						<Button size="sm" variant="outline" onClick={onRestore}>
							Load v{version.version} into editor
						</Button>
					</DialogTitle>
				</DialogHeader>
				<div className="grid max-h-[60vh] grid-cols-2 gap-2 overflow-hidden">
					<div className="flex min-w-0 flex-col">
						<div className="border-border border-b px-2 py-1.5 font-[510] text-[11px] text-muted-foreground uppercase tracking-wider">
							v{version.version} ·{" "}
							{formatDistanceToNowStrict(new Date(version.createdAt), {
								addSuffix: true,
							})}
						</div>
						<pre className="grow overflow-auto bg-muted/30 p-2 font-mono text-[12px] leading-relaxed">
							{ops.map((op, idx) => {
								if (op.type === "equal")
									return (
										<div key={idx} className="px-2 text-muted-foreground">
											{op.a || " "}
										</div>
									);
								if (op.type === "remove")
									return (
										<div
											key={idx}
											className="bg-red-500/10 px-2 text-red-600 dark:text-red-300"
										>
											− {op.a || " "}
										</div>
									);
								return (
									<div key={idx} className="px-2 opacity-30">
										{" "}
									</div>
								);
							})}
						</pre>
					</div>
					<div className="flex min-w-0 flex-col">
						<div className="border-border border-b px-2 py-1.5 font-[510] text-[11px] text-muted-foreground uppercase tracking-wider">
							v{currentVersion} · current
						</div>
						<pre className="grow overflow-auto bg-muted/30 p-2 font-mono text-[12px] leading-relaxed">
							{ops.map((op, idx) => {
								if (op.type === "equal")
									return (
										<div key={idx} className="px-2 text-muted-foreground">
											{op.b || " "}
										</div>
									);
								if (op.type === "add")
									return (
										<div
											key={idx}
											className={cn(
												"bg-emerald-500/10 px-2 text-emerald-600 dark:text-emerald-300",
											)}
										>
											+ {op.b || " "}
										</div>
									);
								return (
									<div key={idx} className="px-2 opacity-30">
										{" "}
									</div>
								);
							})}
						</pre>
					</div>
				</div>
				{version.notes && (
					<div className="border-border border-t pt-2 text-[12px] text-muted-foreground">
						<span className="font-[510]">Notes at v{version.version}:</span>{" "}
						{version.notes}
					</div>
				)}
			</DialogContent>
		</Dialog>
	);
}
