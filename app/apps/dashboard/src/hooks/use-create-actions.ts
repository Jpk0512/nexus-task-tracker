import type { LucideIcon } from "lucide-react";
import {
	BoxIcon,
	CheckSquareIcon,
	FileTextIcon,
	ListPlusIcon,
	SparklesIcon,
} from "lucide-react";
import { useParams, usePathname, useRouter } from "next/navigation";
import { useMemo } from "react";
import { useUser } from "@/components/user-provider";
import { useMilestoneParams } from "./use-milestone-params";
import { useTaskParams } from "./use-task-params";

export interface CreateAction {
	id: "task" | "todo" | "doc" | "project" | "idea";
	label: string;
	/** Keyboard hint shown next to the label (FAB popover only). */
	hint?: string;
	icon: LucideIcon;
	onSelect: () => void;
}

export interface CreateActions {
	task: CreateAction;
	todo: CreateAction;
	doc: CreateAction;
	project: CreateAction;
	idea: CreateAction;
	/** All five, in the order the FAB (CommandTray) renders them. */
	list: CreateAction[];
	inProject: boolean;
	inDocs: boolean;
}

/**
 * Single source of truth for the "create new…" actions shared by the sidebar
 * CreateButton, the FAB CommandTray, and the global "c" hotkey (FEAT-007
 * item 4) — one label/icon/handler per action, computed once, so a task
 * created from any of the three entrypoints pre-fills identically instead of
 * each entrypoint hand-rolling its own context detection.
 *
 * Context detection:
 *   - `inProject` comes from the Next.js dynamic-segment match
 *     (`useParams().projectId`), not a pathname regex — the sibling static
 *     `/projects/timeline` route has no `[projectId]` segment, so it never
 *     false-positives the way a `/\/projects\/[^/]+/` regex would.
 *   - the milestone context is only applied when a specific milestone is
 *     open for edit (see `MilestonesCard`'s inline form in
 *     `projects/overview/milestones-card.tsx`) — never during the separate
 *     "create a new milestone" flow, which has no milestone id yet.
 *
 * `project` (the FAB's wizard-hub "New project", `/create-project`) is
 * deliberately NOT rendered by the sidebar CreateButton — that surface keeps
 * its own quick blank-project sheet (`createProject` param), a distinct,
 * already-wired flow the wizard's own "blank project" choice redirects back
 * to. Collapsing them would remove a working shortcut, not unify one.
 */
export function useCreateActions(): CreateActions {
	const user = useUser();
	const router = useRouter();
	const pathname = usePathname();
	const routeParams = useParams<{ projectId?: string }>();
	const { setParams: setTaskParams } = useTaskParams();
	const { milestoneId, milestoneProjectId } = useMilestoneParams();

	const base = user.basePath;
	const inProject = typeof routeParams?.projectId === "string";
	const inDocs = /\/documents(\/|$)/.test(pathname);
	const contextProjectId =
		routeParams?.projectId ?? milestoneProjectId ?? undefined;
	const contextMilestoneId = inProject ? (milestoneId ?? undefined) : undefined;

	return useMemo(() => {
		const task: CreateAction = {
			id: "task",
			label: inProject ? "New task in this project" : "New task",
			hint: "c",
			icon: ListPlusIcon,
			onSelect: () => {
				setTaskParams({
					createTask: true,
					taskProjectId: contextProjectId ?? null,
					taskMilestoneId: contextMilestoneId ?? null,
				});
			},
		};
		const todo: CreateAction = {
			id: "todo",
			label: "New todo",
			hint: "N",
			icon: CheckSquareIcon,
			onSelect: () => router.push(`${base}/todos`),
		};
		const doc: CreateAction = {
			id: "doc",
			label: inDocs ? "New doc here" : "New doc",
			icon: FileTextIcon,
			onSelect: () => router.push(`${base}/documents/create`),
		};
		const project: CreateAction = {
			id: "project",
			label: "New project",
			icon: BoxIcon,
			onSelect: () => router.push(`${base}/create-project`),
		};
		const idea: CreateAction = {
			id: "idea",
			label: "Start from an idea",
			icon: SparklesIcon,
			onSelect: () => router.push(`${base}/create-project/starter`),
		};

		return {
			task,
			todo,
			doc,
			project,
			idea,
			list: [task, todo, doc, project, idea],
			inProject,
			inDocs,
		};
	}, [
		inProject,
		inDocs,
		contextProjectId,
		contextMilestoneId,
		setTaskParams,
		router,
		base,
	]);
}
