"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@ui/components/ui/badge";
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
	ArrowLeftIcon,
	ClipboardIcon,
	PencilIcon,
	SaveIcon,
	XIcon,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { BacklinksPanel } from "@/components/backlinks/backlinks-panel";
import { BlockEditor } from "@/components/editor/block-editor";
import { trpc } from "@/utils/trpc";
import { kindColor } from "./kind-color";

type Props = { entryId: string; readOnly?: boolean };

type Tab = "rendered" | "yaml";

function serializeYamlObj(obj: Record<string, unknown>): string {
	const lines: string[] = [];
	for (const [k, v] of Object.entries(obj)) {
		if (Array.isArray(v)) {
			lines.push(`${k}: [${v.map((x) => JSON.stringify(x)).join(", ")}]`);
		} else if (typeof v === "string") {
			lines.push(`${k}: ${/[:#]/.test(v) ? JSON.stringify(v) : v}`);
		} else {
			lines.push(`${k}: ${v}`);
		}
	}
	return lines.join("\n");
}

export function LibraryDetailView({ entryId, readOnly = false }: Props) {
	const { team } = useParams<{ team: string }>();
	const qc = useQueryClient();
	const { data: entry, isLoading } = useQuery(
		trpc.library.getById.queryOptions({ id: entryId }),
	);
	const projectsQuery = useQuery(
		trpc.projects.get.queryOptions({ pageSize: 100 } as any),
	);

	const [tab, setTab] = useState<Tab>("rendered");
	const [editing, setEditing] = useState(false);
	const [yamlDraft, setYamlDraft] = useState("");
	const [bodyDraft, setBodyDraft] = useState("");
	const [newTag, setNewTag] = useState("");

	useEffect(() => {
		if (entry) {
			setYamlDraft(
				serializeYamlObj(
					(entry.frontmatter as Record<string, unknown> | null) ?? {},
				),
			);
			setBodyDraft(entry.body ?? "");
		}
	}, [entry?.id, entry?.fileSha]);

	const refetchEntry = () =>
		qc.invalidateQueries({ queryKey: [["library", "getById"]] });

	const updateMut = useMutation(
		trpc.library.update.mutationOptions({
			onSuccess: () => {
				toast.success("Saved to disk");
				setEditing(false);
				refetchEntry();
			},
			onError: (e) => toast.error(e.message),
		}),
	);
	const addTagMut = useMutation(
		trpc.library.addTag.mutationOptions({
			onSuccess: () => {
				setNewTag("");
				refetchEntry();
			},
			onError: (e) => toast.error(e.message),
		}),
	);
	const removeTagMut = useMutation(
		trpc.library.removeTag.mutationOptions({ onSuccess: refetchEntry }),
	);
	const linkProjectMut = useMutation(
		trpc.library.linkProject.mutationOptions({ onSuccess: refetchEntry }),
	);
	const unlinkProjectMut = useMutation(
		trpc.library.unlinkProject.mutationOptions({ onSuccess: refetchEntry }),
	);

	if (isLoading) {
		return (
			<div className="px-6 py-4 text-muted-foreground text-sm">Loading…</div>
		);
	}
	if (!entry) {
		return (
			<div className="px-6 py-4">
				<Link
					href={`/team/${team}/library`}
					className="inline-flex items-center gap-1 text-muted-foreground text-sm hover:text-foreground"
				>
					<ArrowLeftIcon className="size-3.5" /> Library
				</Link>
				<p className="mt-4">Entry not found.</p>
			</div>
		);
	}

	const fm = (entry.frontmatter as Record<string, unknown> | null) ?? {};
	const fmEntries = Object.entries(fm).filter(
		([k]) => !["name", "description"].includes(k),
	);
	const tags = (entry.tags as string[]) ?? [];
	const linkedProjectIds = new Set(
		(entry.projects as { projectId: string }[] | undefined)?.map(
			(p) => p.projectId,
		) ?? [],
	);
	const allProjects =
		(
			projectsQuery.data as
				| { data: Array<{ id: string; name: string }> }
				| undefined
		)?.data ?? [];
	const availableProjects = allProjects.filter(
		(p) => !linkedProjectIds.has(p.id),
	);

	const copyPath = () => {
		navigator.clipboard?.writeText(entry.absolutePath);
		toast.success("Absolute path copied");
	};

	const save = () => {
		updateMut.mutate({
			id: entry.id,
			yaml: yamlDraft,
			body: bodyDraft,
			expectedSha: entry.fileSha,
		});
	};

	const cancelEdit = () => {
		setYamlDraft(serializeYamlObj(fm));
		setBodyDraft(entry.body ?? "");
		setEditing(false);
	};

	return (
		<div className="flex h-full flex-col">
			<header className="border-border border-b px-6 py-3">
				<Link
					href={`/team/${team}/library`}
					className="inline-flex items-center gap-1 text-muted-foreground text-xs hover:text-foreground"
				>
					<ArrowLeftIcon className="size-3.5" /> Library
				</Link>

				<div className="mt-2 flex items-start justify-between gap-4">
					<div className="min-w-0">
						<div className="flex items-center gap-2">
							<h1 className="truncate font-[510] text-[15px] text-foreground tracking-[-0.012em]">
								{entry.name}
							</h1>
							<LabelBadge
								name={entry.kind}
								color={kindColor(entry.kind)}
								className="h-[20px] font-normal"
							/>
						</div>
						{entry.description && (
							<p className="mt-1 text-muted-foreground text-sm">
								{entry.description}
							</p>
						)}
						<div className="mt-2 flex flex-wrap items-center gap-2 text-muted-foreground text-xs">
							<span className="font-medium">{entry.sourceLabel}</span>
							<span>·</span>
							<code className="rounded bg-muted px-1.5 py-0.5">
								{entry.relativePath}
							</code>
							{entry.lastEditedAt && (
								<>
									<span>·</span>
									<span>last edited {entry.lastEditedAt}</span>
								</>
							)}
						</div>
					</div>
					<div className="flex gap-2">
						<Button variant="outline" size="sm" onClick={copyPath}>
							<ClipboardIcon className="size-3.5" /> Copy path
						</Button>
						{readOnly ? null : !editing ? (
							<Button
								variant="outline"
								size="sm"
								onClick={() => {
									setTab("yaml");
									setEditing(true);
								}}
							>
								<PencilIcon className="size-3.5" /> Edit
							</Button>
						) : (
							<>
								<Button variant="ghost" size="sm" onClick={cancelEdit}>
									<XIcon className="size-3.5" /> Cancel
								</Button>
								<Button size="sm" onClick={save} disabled={updateMut.isPending}>
									<SaveIcon className="size-3.5" />{" "}
									{updateMut.isPending ? "Saving…" : "Save"}
								</Button>
							</>
						)}
					</div>
				</div>

				{/* Tags + project links */}
				<div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
					<span className="text-muted-foreground">tags:</span>
					{tags.length === 0 && (
						<span className="text-muted-foreground italic">none</span>
					)}
					{tags.map((t) => (
						<Badge key={t} variant="outline" className="gap-1 font-normal">
							{t}
							<button
								type="button"
								onClick={() =>
									removeTagMut.mutate({ entryId: entry.id, tag: t })
								}
								className="hover:text-destructive"
							>
								<XIcon className="size-3" />
							</button>
						</Badge>
					))}
					<form
						onSubmit={(e) => {
							e.preventDefault();
							if (newTag.trim())
								addTagMut.mutate({ entryId: entry.id, tag: newTag.trim() });
						}}
						className="flex items-center"
					>
						<Input
							value={newTag}
							onChange={(e) => setNewTag(e.target.value)}
							placeholder="+ tag"
							className="h-6 w-24 text-xs"
						/>
					</form>
				</div>

				<div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
					<span className="text-muted-foreground">projects:</span>
					{(entry.projects ?? []).length === 0 && (
						<span className="text-muted-foreground italic">none</span>
					)}
					{(entry.projects as Array<{ projectId: string }> | undefined)?.map(
						(link) => {
							const proj = allProjects.find((p) => p.id === link.projectId);
							return (
								<Badge
									key={link.projectId}
									variant="outline"
									className="gap-1 font-normal"
								>
									{proj?.name ?? link.projectId}
									<button
										type="button"
										onClick={() =>
											unlinkProjectMut.mutate({
												entryId: entry.id,
												projectId: link.projectId,
											})
										}
										className="hover:text-destructive"
									>
										<XIcon className="size-3" />
									</button>
								</Badge>
							);
						},
					)}
					{availableProjects.length > 0 && (
						<Select
							value=""
							onValueChange={(v) =>
								linkProjectMut.mutate({ entryId: entry.id, projectId: v })
							}
						>
							<SelectTrigger className="h-6 w-32 text-xs">
								<SelectValue placeholder="+ link project" />
							</SelectTrigger>
							<SelectContent>
								{availableProjects.map((p) => (
									<SelectItem key={p.id} value={p.id}>
										{p.name}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					)}
				</div>

				{/* Tab strip */}
				<div className="mt-3 flex border-border border-b">
					<button
						type="button"
						onClick={() => setTab("rendered")}
						className={`-mb-px border-b-2 px-3 py-1.5 text-sm ${
							tab === "rendered"
								? "border-primary text-foreground"
								: "border-transparent text-muted-foreground"
						}`}
					>
						Rendered
					</button>
					<button
						type="button"
						onClick={() => setTab("yaml")}
						className={`-mb-px border-b-2 px-3 py-1.5 text-sm ${
							tab === "yaml"
								? "border-primary text-foreground"
								: "border-transparent text-muted-foreground"
						}`}
					>
						YAML
					</button>
				</div>
			</header>

			<div className="grow overflow-y-auto px-6 py-4">
				{tab === "rendered" ? (
					<>
						{fmEntries.length > 0 && (
							<section className="mb-6 rounded-md border border-border bg-card/40 p-4">
								<h2 className="mb-2 text-muted-foreground text-xs uppercase tracking-wider">
									Frontmatter
								</h2>
								<dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
									{fmEntries.map(([k, v]) => (
										<div key={k} className="contents">
											<dt className="font-mono text-muted-foreground">{k}</dt>
											<dd className="break-words font-mono">
												{typeof v === "string" ? v : JSON.stringify(v, null, 0)}
											</dd>
										</div>
									))}
								</dl>
							</section>
						)}
						<section className="prose prose-sm dark:prose-invert max-w-none">
							<BlockEditor value={entry.body ?? ""} readOnly={true} />
						</section>
					</>
				) : (
					<div className="space-y-4">
						<div>
							<div className="mb-1 text-muted-foreground text-xs uppercase tracking-wider">
								Frontmatter (YAML)
							</div>
							<textarea
								value={yamlDraft}
								onChange={(e) => setYamlDraft(e.target.value)}
								readOnly={!editing}
								spellCheck={false}
								className="h-48 w-full resize-y rounded-md border border-border bg-card/40 p-3 font-mono text-xs"
							/>
						</div>
						<div>
							<div className="mb-1 text-muted-foreground text-xs uppercase tracking-wider">
								Body (markdown)
							</div>
							{editing ? (
								// BlockEditor is markdown-backed (tiptap-markdown plugin) so
								// the YAML-tab save path that reads `bodyDraft` keeps working
								// without any persistence-layer change.
								<div className="min-h-[480px] rounded-md border border-border bg-card/40 px-3 py-2">
									<BlockEditor
										value={bodyDraft}
										onChange={(value) => setBodyDraft(value)}
										placeholder="Body (markdown)…"
									/>
								</div>
							) : (
								<textarea
									value={bodyDraft}
									readOnly
									spellCheck={false}
									className="h-[480px] w-full resize-y rounded-md border border-border bg-card/40 p-3 font-mono text-xs"
								/>
							)}
						</div>
					</div>
				)}
				<BacklinksPanel entityType="library" entityId={entry.id} />
			</div>
		</div>
	);
}
