"use client";

import { useEffect, useState } from "react";
import { useUndoLastOptimistic } from "@/hooks/use-optimistic-action";
import { useTaskParams } from "@/hooks/use-task-params";
import { ProjectSwitcher } from "./project-switcher";
import { ShortcutsOverlay } from "./shortcuts-overlay";

/**
 * Global keyboard shortcuts (Linear parity).
 *
 *   c          → open the centered create-task dialog
 *   ?          → toggle the keyboard-shortcuts cheatsheet overlay
 *   Cmd/Ctrl+J → open the project switcher
 *
 * Ignores keystrokes when the focus is inside an input, textarea, select,
 * contenteditable surface, or while a modifier key is held (except for the
 * explicit Cmd/Ctrl+J chord) — so it never fights the user's typing.
 */
export const GlobalShortcuts = () => {
	const { setParams } = useTaskParams();
	const [shortcutsOpen, setShortcutsOpen] = useState(false);
	const [projectSwitcherOpen, setProjectSwitcherOpen] = useState(false);

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
			// Cmd/Ctrl+J — project switcher. Allowed even while typing in an
			// input (it's an explicit chord, not a stray keystroke).
			if (
				(e.metaKey || e.ctrlKey) &&
				!e.altKey &&
				!e.shiftKey &&
				(e.key === "j" || e.key === "J")
			) {
				e.preventDefault();
				setProjectSwitcherOpen((prev) => !prev);
				return;
			}

			// `?` is Shift+/ on US layouts. We accept it even while Shift is held,
			// but still bail on Cmd/Ctrl/Alt so we don't fight system chords.
			if (e.metaKey || e.ctrlKey || e.altKey) return;
			if (isEditableTarget(e.target)) return;

			if (e.key === "?") {
				e.preventDefault();
				setShortcutsOpen((prev) => !prev);
				return;
			}

			if (e.key === "c" || e.key === "C") {
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
