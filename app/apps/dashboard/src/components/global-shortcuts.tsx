"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useUndoLastOptimistic } from "@/hooks/use-optimistic-action";
import { useShortcut } from "@/hooks/use-shortcuts";
import { useTaskParams } from "@/hooks/use-task-params";
import { ProjectSwitcher } from "./project-switcher";
import { ShortcutsOverlay } from "./shortcuts-overlay";
import { useUser } from "./user-provider";

/**
 * Global keyboard shortcuts (Linear parity).
 *
 *   c          → open the centered create-task dialog
 *   ?          → toggle the keyboard-shortcuts cheatsheet overlay
 *   Cmd/Ctrl+Shift+P → open the project switcher
 *   Cmd/Ctrl+J      → open the brain-dump modal
 *
 * Ignores keystrokes when the focus is inside an input, textarea, select,
 * contenteditable surface, or while a modifier key is held (except for the
 * explicit Cmd/Ctrl+J chord) — so it never fights the user's typing.
 */
export const GlobalShortcuts = () => {
	const { setParams } = useTaskParams();
	const router = useRouter();
	const user = useUser();
	const base = user.basePath;
	const [shortcutsOpen, setShortcutsOpen] = useState(false);
	const [projectSwitcherOpen, setProjectSwitcherOpen] = useState(false);
	// True for the one keydown right after a bare "g" — lets the bare-key
	// handlers below tell a standalone "c" apart from the second key of the
	// registry's "g c" (nav.chat) sequence. react-hotkeys-hook's own sequence
	// match (wired via useShortcut above) never calls preventDefault/
	// stopPropagation on completion, so without this guard the same "c"
	// keydown that completes "g c" also falls through to this window listener
	// and pops open the create-task dialog. The 1000ms window mirrors
	// react-hotkeys-hook's default `sequenceTimeoutMs` so the two independent
	// listeners time out in sync.
	const pendingGRef = useRef(false);

	// Wire the registry's `g <key>` go-to shortcuts (Navigate section). These
	// were defined in the registry but had no handlers — now they jump to their
	// destinations from anywhere. Chords come from the registry (single source).
	const go = (path: string) => () => router.push(`${base}${path}`);
	useShortcut("nav.projects", go("/projects"));
	useShortcut("nav.todos", go("/todos"));
	useShortcut("nav.documents", go("/documents"));
	useShortcut("nav.inbox", go("/inbox"));
	useShortcut("nav.triage", go("/triage"));
	useShortcut("nav.chat", go("/chat"));
	useShortcut("nav.notes", go("/notes"));
	// "Knowledge" is the product name for the Notes engine — URL stays /notes
	// (see notes/page.tsx) — so this chord targets the same route as nav.notes.
	useShortcut("nav.knowledge", go("/notes"));
	useShortcut("nav.settings.alt", go("/settings"));

	// Wire Cmd+Z → revert the most-recent optimistic action (iter-10 codex
	// amendment #6). The hook is a no-op when the undo stack is empty.
	useUndoLastOptimistic();

	useEffect(() => {
		const isEditableTarget = (target: EventTarget | null): boolean => {
			if (!(target instanceof HTMLElement)) return false;
			const tag = target.tagName;
			if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
				return true;
			}
			if (target.isContentEditable) return true;
			// Tiptap / ProseMirror editors expose role="textbox".
			if (target.getAttribute("role") === "textbox") return true;
			return false;
		};

		const handler = (e: KeyboardEvent) => {
			// Cmd/Ctrl+Shift+P — project switcher (matches registry action
			// `project.switch`). Allowed even while typing (explicit chord).
			if (
				(e.metaKey || e.ctrlKey) &&
				e.shiftKey &&
				!e.altKey &&
				(e.key === "p" || e.key === "P")
			) {
				e.preventDefault();
				setProjectSwitcherOpen((prev) => !prev);
				return;
			}

			// `?` is Shift+/ on US layouts. We accept it even while Shift is held,
			// but still bail on Cmd/Ctrl/Alt so we don't fight system chords.
			if (e.metaKey || e.ctrlKey || e.altKey) return;
			if (isEditableTarget(e.target)) return;

			if (e.key === "g" || e.key === "G") {
				pendingGRef.current = true;
				window.setTimeout(() => {
					pendingGRef.current = false;
				}, 1000);
				return;
			}

			// Consume the pending flag on this keydown regardless of which key it
			// is — a "g" followed by anything other than the intended next key
			// (e.g. the help-overlay "?") should not let some later "c" still
			// read as a completed chord, matching react-hotkeys-hook's own
			// sequence-array reset on a non-matching intermediate key.
			const completesGChord = pendingGRef.current;
			pendingGRef.current = false;

			if (e.key === "?") {
				e.preventDefault();
				setShortcutsOpen((prev) => !prev);
				return;
			}

			if (e.key === "c" || e.key === "C") {
				if (completesGChord) {
					// This "c" is the second key of "g c" -> nav.chat (wired via
					// useShortcut above), not a request to open the create-task
					// dialog — let the navigation handler own it.
					return;
				}
				e.preventDefault();
				setParams({ createTask: true });
			}
		};

		window.addEventListener("keydown", handler);
		return () => window.removeEventListener("keydown", handler);
	}, [setParams]);

	return (
		<>
			<ShortcutsOverlay open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
			<ProjectSwitcher
				open={projectSwitcherOpen}
				onOpenChange={setProjectSwitcherOpen}
			/>
		</>
	);
};
