"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@ui/components/ui/badge";
import { Button } from "@ui/components/ui/button";
import { Skeleton } from "@ui/components/ui/skeleton";
import {
	AlertCircleIcon,
	ArrowLeftIcon,
	BrainIcon,
	CheckIcon,
	RefreshCwIcon,
	SaveIcon,
	Trash2Icon,
} from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { BacklinksPanel } from "@/components/backlinks/backlinks-panel";
import { BlockEditor } from "@/components/editor/block-editor";
import { trpc } from "@/utils/trpc";

// Single-note focus mode (GWT#7) for /team/[team]/knowledge/[noteId]. Trades
// the KnowledgeView left rail for a right backlinks panel, giving the note's
// content the dominant column. Reuses the BlockEditor + 500ms blur auto-save +
// last-write-wins conflict handling (DEC-010) — palette spec §4.

type AutoSaveState = "idle" | "dirty" | "saving" | "saved" | "conflict";

export function KnowledgeFocusView({ noteId }: { noteId: string }) {
	const qc = useQueryClient();
	const router = useRouter();
	const { team } = useParams<{ team: string }>();

	const [draft, setDraft] = useState("");
	const [autoSaveState, setAutoSaveState] = useState<AutoSaveState>("idle");
	const draftRef = useRef("");
	const shaRef = useRef<string | null>(null);
	const autoSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	// Single-flight mutex: skip if a save is already in progress so rapid
	// blur/refocus cannot enqueue two concurrent knowledge.update mutations.
	const saveInFlight = useRef(false);
	// Stable ref to autoSave so the trailing-edge setTimeout closure always
	// invokes the latest version without a circular useCallback dep.
	const autoSaveRef = useRef<() => Promise<void>>(async () => {});

	const noteQuery = useQuery({
		...trpc.knowledge.getById.queryOptions({ id: noteId }),
		enabled: !!noteId,
	});

	useEffect(() => {
		if (!noteQuery.data) return;
		const fm = noteQuery.data.frontmatter as Record<string, unknown> | null;
		const lines: string[] = [];
		if (fm && Object.keys(fm).length > 0) {
			lines.push("---");
			for (const [k, v] of Object.entries(fm)) {
				if (Array.isArray(v)) {
					lines.push(`${k}: [${v.map((x) => JSON.stringify(x)).join(", ")}]`);
				} else if (typeof v === "string") {
					lines.push(`${k}: ${/[:#]/.test(v) ? JSON.stringify(v) : v}`);
				} else {
					lines.push(`${k}: ${v}`);
				}
			}
			lines.push("---", "");
		}
		lines.push(noteQuery.data.content ?? "");
		const next = lines.join("\n");
		setDraft(next);
		draftRef.current = next;
		shaRef.current = noteQuery.data.fileSha;
		setAutoSaveState("idle");
	}, [noteQuery.data?.id, noteQuery.data?.fileSha]);

	const refetchNote = useCallback(
		() => qc.invalidateQueries({ queryKey: [["knowledge", "getById"]] }),
		[qc],
	);

	const updateMut = useMutation(
		trpc.knowledge.update.mutationOptions({
			onSuccess: () => {
				toast.success("Saved to disk");
				refetchNote();
			},
			onError: (e) => toast.error(e.message),
		}),
	);
	const deleteMut = useMutation(
		trpc.knowledge.delete.mutationOptions({
			onSuccess: () => {
				toast.success("Deleted");
				router.push(`/team/${team}/knowledge`);
			},
			onError: (e) => toast.error(e.message),
		}),
	);

	const updateAsync = updateMut.mutateAsync;
	// Auto-save: blur → ≤500ms debounce → update. CONFLICT → re-fetch fresh sha
	// via getById and re-save (last-write-wins). No modal, no conflict toast.
	const autoSave = useCallback(async () => {
		if (saveInFlight.current) return;
		saveInFlight.current = true;
		const content = draftRef.current;
		setAutoSaveState("saving");
		try {
			await updateAsync({
				id: noteId,
				content,
				expectedSha: shaRef.current ?? "",
			});
			setAutoSaveState("saved");
			refetchNote();
		} catch (err) {
			const isConflict = err instanceof Error && /CONFLICT/i.test(err.message);
			if (!isConflict) {
				setAutoSaveState("idle");
				toast.error(err instanceof Error ? err.message : "Save failed");
				saveInFlight.current = false;
				return;
			}
			setAutoSaveState("conflict");
			try {
				const fresh = await qc.fetchQuery(
					trpc.knowledge.getById.queryOptions({ id: noteId }),
				);
				const freshSha = (fresh as { fileSha?: string } | undefined)?.fileSha;
				shaRef.current = freshSha ?? null;
				await updateAsync({
					id: noteId,
					content: draftRef.current,
					expectedSha: freshSha ?? "",
				});
				setAutoSaveState("saved");
				refetchNote();
			} catch {
				setAutoSaveState("idle");
			}
		} finally {
			saveInFlight.current = false;
			// Trailing-edge guard: if the user edited while this save was in-flight
			// the early-return above silently dropped that content. Re-arm a save so
			// the newest draft is never silently discarded.
			if (draftRef.current !== content) {
				setAutoSaveState("dirty");
				if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current);
				autoSaveTimer.current = setTimeout(() => {
					void autoSaveRef.current();
				}, 500);
			}
		}
	}, [noteId, updateAsync, qc, refetchNote]);
	autoSaveRef.current = autoSave;

	const scheduleAutoSave = useCallback(() => {
		if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current);
		autoSaveTimer.current = setTimeout(() => {
			void autoSave();
		}, 500);
	}, [autoSave]);

	useEffect(() => {
		if (autoSaveState !== "saved") return;
		const t = setTimeout(() => setAutoSaveState("idle"), 2000);
		return () => clearTimeout(t);
	}, [autoSaveState]);

	useEffect(
		() => () => {
			if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current);
		},
		[],
	);

	const goBack = () => router.push(`/team/${team}/knowledge?note=${noteId}`);

	if (noteQuery.isLoading) {
		return (
			<div className="flex h-full flex-col">
				<header className="flex items-center gap-3 border-border border-b px-6 py-3">
					<Skeleton className="h-5 w-48 rounded" />
				</header>
				<div className="grow px-8 py-8">
					<div className="mx-auto max-w-[760px] space-y-3">
						<Skeleton className="h-4 w-3/4 rounded" />
						<Skeleton className="h-4 w-5/6 rounded" />
						<Skeleton className="h-4 w-2/3 rounded" />
						<Skeleton className="h-4 w-4/5 rounded" />
					</div>
				</div>
			</div>
		);
	}

	if (!noteQuery.data) {
		return (
			<div className="flex h-full flex-col items-center justify-center gap-4 text-center">
				<BrainIcon className="size-10 text-muted-foreground/60" />
				<p className="font-[510] text-[15px] tracking-[-0.012em]">
					Note not found
				</p>
				<Button variant="default" size="sm" onClick={goBack}>
					<ArrowLeftIcon className="size-3.5" /> Back to Knowledge
				</Button>
			</div>
		);
	}

	const fm = noteQuery.data.frontmatter as Record<string, unknown> | null;
	const titleFromFm =
		typeof fm?.title === "string" && fm.title.trim() ? fm.title : null;

	return (
		<div className="flex h-full animate-blur-in flex-col bg-background">
			<header className="flex items-center justify-between border-border border-b px-6 py-3">
				<div className="flex min-w-0 items-center gap-3">
					<Button variant="ghost" size="sm" onClick={goBack}>
						<ArrowLeftIcon className="size-3.5" /> Back
					</Button>
					<div className="min-w-0">
						<h1 className="truncate font-[510] text-[17px] tracking-[-0.015em]">
							{titleFromFm || noteQuery.data.name}
						</h1>
						<div className="mt-0.5 flex items-center gap-2 text-[12px] text-muted-foreground">
							<Badge variant="outline" className="font-normal">
								{noteQuery.data.vaultLabel}
							</Badge>
							<code className="rounded bg-muted px-1.5 py-0.5 text-[11px]">
								{noteQuery.data.relativePath}
							</code>
						</div>
					</div>
				</div>
				<div className="flex items-center gap-2">
					<AutoSaveIndicator state={autoSaveState} />
					<Button
						size="sm"
						onClick={() =>
							updateMut.mutate({
								id: noteId,
								content: draft,
								expectedSha: shaRef.current ?? "",
							})
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
							if (
								confirm(
									`Delete "${noteQuery.data!.name}" from disk? This cannot be undone.`,
								)
							) {
								deleteMut.mutate({ id: noteId });
							}
						}}
						className="text-muted-foreground hover:text-destructive"
					>
						<Trash2Icon className="size-3.5" />
					</Button>
				</div>
			</header>
			<div className="flex grow overflow-hidden">
				<div className="grow overflow-y-auto">
					<div className="mx-auto min-h-[320px] max-w-[760px] rounded-lg px-8 py-8 transition-shadow duration-150 focus-within:ring-1 focus-within:ring-border/60">
						<BlockEditor
							key={`${noteId}:${noteQuery.data.fileSha}`}
							value={draft}
							onChange={(value) => {
								draftRef.current = value;
								setDraft(value);
								setAutoSaveState((s) => (s === "saving" ? s : "dirty"));
							}}
							onBlur={scheduleAutoSave}
						/>
					</div>
				</div>
				<aside className="sticky top-0 h-full w-72 shrink-0 overflow-y-auto border-border border-l px-4 py-4">
					<BacklinksPanel entityType="knowledge" entityId={noteId} />
				</aside>
			</div>
		</div>
	);
}

function AutoSaveIndicator({ state }: { state: AutoSaveState }) {
	if (state === "idle") return null;
	if (state === "dirty") {
		return (
			<span className="text-[11px] text-muted-foreground opacity-60">
				Unsaved
			</span>
		);
	}
	if (state === "saving") {
		return (
			<span className="flex items-center gap-1 text-[11px] text-muted-foreground">
				<RefreshCwIcon className="size-3 animate-spin" />
				Saving…
			</span>
		);
	}
	if (state === "conflict") {
		return (
			<span className="flex items-center gap-1 text-[11px] text-destructive">
				<AlertCircleIcon className="size-3" />
				Conflict — reloading
			</span>
		);
	}
	return (
		<span className="flex items-center gap-1 text-[11px] text-[var(--color-success)] transition-opacity duration-500">
			<CheckIcon className="size-3" />
			Saved
		</span>
	);
}
