"use client";

/**
 * Shared dispatcher for command-style palette actions.
 *
 * Lifted out so the **repeat-last** flow (codex delighter #8) can re-fire a
 * command without going through the palette UI — `useShortcut('palette.repeat-last')`
 * resolves the stored last command, finds it in `ACTIONS`, and calls
 * `dispatch(target)` directly.
 *
 * Keeping the action logic in one place also stops the renderer + the
 * keyboard handler from drifting: every new `action:*` id only needs a
 * single branch in `dispatch`.
 */

import { useRouter } from "next/navigation";
import { useCallback } from "react";
import { useProjectParams } from "@/hooks/use-project-params";
import { useTaskParams } from "@/hooks/use-task-params";
import type { GlobalSearchItem } from "./types";

export type ActionDispatcher = (target: GlobalSearchItem) => void;

export function useActionDispatcher(basePath: string): ActionDispatcher {
	const router = useRouter();
	const { setParams: setTaskParams } = useTaskParams();
	const { setParams: setProjectParams } = useProjectParams();

	return useCallback(
		(target: GlobalSearchItem) => {
			switch (target.id) {
				case "action:view-projects": {
					router.push(`${basePath}/projects`);
					break;
				}
				case "action:create-task":
				case "action:new-task": {
					setTaskParams({ createTask: true });
					break;
				}
				case "action:create-project":
				case "action:new-project": {
					setProjectParams({ createProject: true });
					break;
				}
				default: {
					if (target.href) router.push(`${basePath}${target.href}`);
					break;
				}
			}
		},
		[basePath, router, setTaskParams, setProjectParams],
	);
}
