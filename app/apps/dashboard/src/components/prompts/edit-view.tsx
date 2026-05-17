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
import { cn } from "@ui/lib/utils";
import { formatDistanceToNowStrict } from "date-fns";
import {
	ArrowLeftIcon,
	ClipboardIcon,
	HistoryIcon,
	SaveIcon,
	Trash2Icon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { BacklinksPanel } from "@/components/backlinks/backlinks-panel";
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

	// Past versions — populated as users hit "Save as new version". Older
	// prompts created before the snapshot landed will simply have an empty
	// list until the first new bump.
	const versionsQuery = useQuery({
		...trpc.prompts.getVersions.queryOptions({ promptId: prompt?.id ?? "" }),
		enabled: !!prompt?.id,
	});
	const versions = (versionsQuery.data ?? []) as PromptVersion[];

	useEffect(() => {
		if (prompt) {
			setContent(prompt.content);
			setNotes(prompt.notes ?? "");
			// Reset var values to empty strings when prompt loads
			const vars = extractVars(prompt.content);
			setVarValues(Object.fromEntries(vars.map((v) => [v, ""])));
		}
	}, [prompt?.id]);

	const vars = useMemo(() => extractVars(content), [content]);

	const updateMut = useMutation(
		trpc.prompts.updatePrompt.mutationOptions({
			onSuccess: (p, vars) => {
				toast.success(vars.bumpVersion ? "Saved as new version" : "Saved");
				qc.invalidateQueries({
					queryKey: [["prompts", "getPromptBySlug"]],
				});
				qc.invalidateQueries({ queryKey: [["prompts", "getPrompts"]] });
				if (vars.bumpVersion) {
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
					<textarea
						value={notes}
						onChange={(e) => setNotes(e.target.value)}
						placeholder="Context, usage tips, when to reach for this…"
						className="h-32 resize-y rounded-md border border-border bg-card/40 p-3 text-sm outline-none"
					/>
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
