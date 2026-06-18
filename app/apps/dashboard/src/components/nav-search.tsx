"use client";
import { useMutation } from "@tanstack/react-query";
import { Kbd, KbdGroup } from "@ui/components/ui/kbd";
import { cn } from "@ui/lib/utils";
import { SearchIcon } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { toast } from "sonner";
import { useShortcut } from "@/hooks/use-shortcuts";
import { queryClient, trpc } from "@/utils/trpc";
import { findActionById } from "./global-search/actions-catalogue";
import type { PaletteLinkMode } from "./global-search/global-search-context";
import { GlobalSearchDialog } from "./global-search/global-search-dialog";
import { QuickOpenRing } from "./global-search/quick-open-ring";
import {
	loadLastCommand,
	recordCommand,
} from "./global-search/repeat-last-command";
import type { GlobalSearchItem } from "./global-search/types";
import { useActionDispatcher } from "./global-search/use-action-dispatcher";
import { useUser } from "./user-provider";

export const NavSearch = ({
	placeholder,
	className,
}: {
	placeholder?: string;
	className?: string;
}) => {
	const [open, setOpen] = useState(false);
	const [quickOpen, setQuickOpen] = useState(false);
	const [linkMode, setLinkMode] = useState<PaletteLinkMode | null>(null);
	const user = useUser();
	const dispatch = useActionDispatcher(user?.basePath ?? "");

	// ── iter-10 Round F: link-mode mutations ────────────────────────────────
	// Each of these resolves the correct tRPC mutation for a given
	// (sourceType, entity) pair. We expose them as a stable handler the
	// palette calls when the user picks a result while in link-mode.
	const setPromptProject = useMutation(
		trpc.prompts.setProject.mutationOptions({
			onSettled: () => {
				queryClient.invalidateQueries({
					queryKey: trpc.projects.listLinkedPrompts.pathKey(),
				});
			},
		}),
	);
	const setMilestoneOwner = useMutation(
		trpc.milestones.setOwnerAgent.mutationOptions({
			onSettled: () => {
				queryClient.invalidateQueries({
					queryKey: trpc.milestones.get.pathKey(),
				});
			},
		}),
	);
	const linkKnowledgeToTask = useMutation(
		trpc.tasks.linkKnowledge.mutationOptions({
			onSettled: () => {
				queryClient.invalidateQueries({
					queryKey: trpc.tasks.listKnowledge.pathKey(),
				});
				queryClient.invalidateQueries({
					queryKey: trpc.tasks.getLinkedKnowledgeNotes.pathKey(),
				});
			},
		}),
	);
	const linkSkillToTask = useMutation(
		trpc.tasks.linkSkill.mutationOptions({
			onSettled: () => {
				queryClient.invalidateQueries({
					queryKey: trpc.tasks.listSkills.pathKey(),
				});
			},
		}),
	);

	useHotkeys(
		"ctrl+p, meta+p, ctrl+k, meta+k",
		(e) => {
			e.preventDefault();
			setLinkMode(null);
			setOpen((o) => !o);
		},
		{
			enableOnContentEditable: true,
		},
	);

	// ── Cmd+. — repeat last command (codex delighter #8) ──────────────────
	useShortcut("palette.repeat-last", () => {
		const last = loadLastCommand();
		if (!last) return;
		const target = findActionById(last.id);
		if (!target) return;
		dispatch(target);
		recordCommand(target);
	});

	// ── Cmd+O — quick-open ring (codex delighter #9) ──────────────────────
	useShortcut("palette.quick-open", () => {
		setQuickOpen(true);
	});

	// ── iter-10 Round F: palette.openLink event listener ──────────────────
	// The relationships sidebar (and any other surface needing entity
	// picking) dispatches a CustomEvent rather than threading callbacks
	// through component trees. NavSearch is the canonical mount point so
	// the listener lives here.
	useEffect(() => {
		const onOpenLink = (e: Event) => {
			const detail = (e as CustomEvent<PaletteLinkMode>).detail;
			if (!detail) return;
			setLinkMode(detail);
			setOpen(true);
		};
		window.addEventListener("palette.openLink", onOpenLink);
		return () => window.removeEventListener("palette.openLink", onOpenLink);
	}, []);

	// Resolve which mutation to fire for a given (linkMode, pickedItem) pair.
	const onLinkPick = useCallback(
		async (item: GlobalSearchItem) => {
			if (!linkMode) return;

			try {
				const { sourceType, sourceId, entity } = linkMode;

				if (sourceType === "project" && entity === "prompts") {
					await setPromptProject.mutateAsync({
						promptId: item.id,
						projectId: sourceId,
					});
					toast("Linked prompt to project");
				} else if (sourceType === "project" && entity === "agents") {
					// Project doesn't have a direct agent link; "linking an agent"
					// to a project surface means: ensure the agent owns one of
					// the project's milestones. The picker here surfaces agents
					// but the sidebar's agents section is read-only via the
					// milestone relationship — we route the user to the milestone
					// detail instead. Falling through with a toast is the least
					// confusing UX while the milestone-picker isn't wired.
					toast(
						"To link an agent, assign it as a milestone owner in the project's milestones tab.",
					);
				} else if (sourceType === "task" && entity === "knowledge") {
					await linkKnowledgeToTask.mutateAsync({
						taskId: sourceId,
						noteId: item.id,
					});
					toast("Linked note to task");
				} else if (sourceType === "task" && entity === "skills") {
					await linkSkillToTask.mutateAsync({
						taskId: sourceId,
						skillId: item.id,
					});
					toast("Linked skill to task");
				} else if (sourceType === "agent" && entity === "agents") {
					// Reverse direction handled at the milestone surface.
					toast("Use the milestone detail to assign an owner agent.");
				} else if (entity === "knowledge" && sourceType === "project") {
					// Project -> knowledge runs through the task chain; the
					// closest single mutation is to surface the knowledge note
					// and let the user pick a task. For now we just toast.
					toast(
						"Knowledge notes link to projects via tasks. Open the task and link the note there.",
					);
				} else {
					toast(`Linking ${entity} from ${sourceType} not yet implemented.`);
				}
			} catch (err) {
				toast.error(
					`Failed to link: ${err instanceof Error ? err.message : "unknown error"}`,
				);
			} finally {
				setOpen(false);
				setLinkMode(null);
			}
		},
		[
			linkMode,
			setPromptProject,
			setMilestoneOwner,
			linkKnowledgeToTask,
			linkSkillToTask,
		],
	);

	return (
		<>
			<button
				type="button"
				onClick={() => {
					setLinkMode(null);
					setOpen(true);
				}}
				className={cn(
					"inline-flex h-7 items-center gap-2 rounded-md border border-border bg-white/[0.02] px-2 text-start text-[12px] text-muted-foreground transition-colors hover:bg-white/[0.04] hover:text-foreground",
					className,
				)}
			>
				<SearchIcon className="size-3 shrink-0" />
				<span className="truncate">{placeholder || "Find anything…"}</span>
				<Kbd className="ml-3">
					<KbdGroup>
						<span>⌘</span>
						<span>P</span>
					</KbdGroup>
				</Kbd>
			</button>
			<GlobalSearchDialog
				open={open}
				onOpenChange={(next) => {
					if (!next) setLinkMode(null);
					setOpen(next);
				}}
				linkMode={linkMode}
				onLinkPick={onLinkPick}
			/>
			<QuickOpenRing open={quickOpen} onOpenChange={setQuickOpen} />
		</>
	);
};
