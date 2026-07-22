"use client";

import { useMutation } from "@tanstack/react-query";
import {
	ContextMenu,
	ContextMenuContent,
	ContextMenuItem,
	ContextMenuTrigger,
} from "@ui/components/ui/context-menu";
import { CopyPlusIcon, EditIcon, TrashIcon } from "lucide-react";
import { useRef } from "react";
import { toast } from "sonner";
import {
	addProjectToCache,
	removeProjectFromCache,
} from "@/hooks/use-data-cache-helpers";
import {
	TOAST_WINDOW_MS,
	useOptimisticAction,
} from "@/hooks/use-optimistic-action";
import { useProjectParams } from "@/hooks/use-project-params";
import { queryClient, trpc } from "@/utils/trpc";
import type { Project } from "./list";

export const ProjectContextMenu = ({
	project,
	children,
}: {
	project: Project;
	children: React.ReactNode;
}) => {
	const { setParams } = useProjectParams();

	const { mutateAsync: deleteProjectMutation } = useMutation(
		trpc.projects.delete.mutationOptions(),
	);
	const pendingDeleteTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

	// Deferred delete: the project disappears from the grid immediately, but
	// the real `projects.delete` call waits out the undo window — a plain
	// cache rollback can't un-delete a row that already hit the database.
	const deleteProjectWithUndo = useOptimisticAction<Project, Project>({
		action: "project.delete",
		optimisticUpdate: (p) => {
			removeProjectFromCache(p.id);
			return p;
		},
		mutateFn: (p) =>
			new Promise((resolve, reject) => {
				const timer = setTimeout(() => {
					pendingDeleteTimer.current = null;
					deleteProjectMutation({ id: p.id }).then(resolve, reject);
				}, TOAST_WINDOW_MS);
				pendingDeleteTimer.current = timer;
			}),
		rollback: (p) => {
			if (pendingDeleteTimer.current) {
				clearTimeout(pendingDeleteTimer.current);
				pendingDeleteTimer.current = null;
			}
			addProjectToCache(p);
		},
		toastLabel: "Project deleted",
	});

	const { mutate: cloneProject } = useMutation(
		trpc.projects.clone.mutationOptions({
			onMutate: () => {
				toast.loading("Cloning project...", { id: "clone-project" });
			},
			onSuccess: (project) => {
				queryClient.invalidateQueries(trpc.projects.get.infiniteQueryOptions());
				queryClient.invalidateQueries(trpc.projects.get.queryOptions());
				toast.success("Project cloned successfully", { id: "clone-project" });
				setParams({ projectId: project.id });
			},
			onError: (_error) => {
				toast.error("Failed to clone project", { id: "clone-project" });
			},
		}),
	);

	return (
		<ContextMenu>
			<ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
			<ContextMenuContent>
				<ContextMenuItem
					onClick={() => {
						setParams({
							projectId: project.id,
						});
					}}
				>
					<EditIcon />
					Edit
				</ContextMenuItem>
				<ContextMenuItem
					onClick={() => {
						cloneProject({ id: project.id });
					}}
				>
					<CopyPlusIcon />
					Clone
				</ContextMenuItem>
				<ContextMenuItem
					variant="destructive"
					onClick={() => deleteProjectWithUndo.run(project)}
				>
					<TrashIcon />
					Delete
				</ContextMenuItem>
			</ContextMenuContent>
		</ContextMenu>
	);
};
