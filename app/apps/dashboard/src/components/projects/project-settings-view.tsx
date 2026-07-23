"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@ui/components/ui/button";
import { Input } from "@ui/components/ui/input";
import { Label } from "@ui/components/ui/label";
import { Textarea } from "@ui/components/ui/textarea";
import { cn } from "@ui/lib/utils";
import {
	ArchiveIcon,
	ArchiveRestoreIcon,
	SaveIcon,
	SettingsIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import Loader from "@/components/loader";
import { ProjectIcon } from "@/components/project-icon";
import { updateProjectInCache } from "@/hooks/use-data-cache-helpers";
import { runToastAction } from "@/lib/toast-action";
import { queryClient, trpc } from "@/utils/trpc";

type Props = { projectId: string };

const PRESET_COLORS = [
	"#5e6ad2",
	"#d4a373",
	"#e07a5f",
	"#81b29a",
	"#f2cc8f",
	"#6a994e",
	"#c75c5c",
	"#3b82f6",
];

/**
 * Project-scoped Settings tab (FEAT-020 item 1) — the one surface that edits
 * `rootPath`/`docsPath` (the on-disk link consumed by Docs/Library, distinct
 * from the Overview tab's task-management properties) and holds the
 * Archive/Unarchive action. Both go through the same `projects.update`
 * mutation `ProjectForm` already uses elsewhere.
 */
export function ProjectSettingsView({ projectId }: Props) {
	const projectQuery = useQuery(
		trpc.projects.getById.queryOptions({ id: projectId }),
	);
	const project = projectQuery.data;

	const [name, setName] = useState("");
	const [description, setDescription] = useState("");
	const [color, setColor] = useState<string | null>(null);
	const [rootPath, setRootPath] = useState("");
	const [docsPath, setDocsPath] = useState("");
	// Re-keyed on the loaded project's id — guards against clobbering local
	// edits on an in-place refetch, while still re-hydrating when the route
	// actually swaps to a different project.
	const [hydratedFor, setHydratedFor] = useState<string | null>(null);

	useEffect(() => {
		if (!project || hydratedFor === project.id) return;
		setName(project.name ?? "");
		setDescription(project.description ?? "");
		setColor(project.color ?? null);
		setRootPath(project.rootPath ?? "");
		setDocsPath(project.docsPath ?? "");
		setHydratedFor(project.id);
	}, [project, hydratedFor]);

	const updateMutation = useMutation(trpc.projects.update.mutationOptions());

	const invalidateProject = () => {
		queryClient.invalidateQueries(
			trpc.projects.getById.queryOptions({ id: projectId }),
		);
		queryClient.invalidateQueries(trpc.projects.get.infiniteQueryOptions());
		queryClient.invalidateQueries(trpc.projects.get.queryOptions());
		queryClient.invalidateQueries(trpc.projects.getForTimeline.queryOptions());
	};

	const onSave = () => {
		runToastAction(
			() =>
				updateMutation.mutateAsync({
					id: projectId,
					name: name.trim(),
					description: description.trim() || null,
					color,
					rootPath: rootPath.trim() || null,
					docsPath: docsPath.trim() || null,
				}),
			{
				id: `project-settings-save-${projectId}`,
				loading: "Saving settings…",
				success: "Project settings saved",
				error: (err) =>
					err instanceof Error
						? err.message
						: "Failed to save project settings",
			},
		).then((result) => {
			if (!result.ok) return;
			updateProjectInCache(result.data);
			invalidateProject();
		});
	};

	const onToggleArchive = () => {
		if (!project) return;
		const nextArchived = !project.archived;
		runToastAction(
			() =>
				updateMutation.mutateAsync({ id: projectId, archived: nextArchived }),
			{
				id: `project-settings-archive-${projectId}`,
				loading: nextArchived ? "Archiving project…" : "Unarchiving project…",
				success: nextArchived ? "Project archived" : "Project unarchived",
				error: (err) =>
					err instanceof Error ? err.message : "Failed to update project",
			},
		).then((result) => {
			if (!result.ok) return;
			updateProjectInCache(result.data);
			invalidateProject();
		});
	};

	if (!project) {
		return (
			<div className="flex h-full flex-col">
				<header className="border-border border-b px-6 py-3">
					<h1 className="font-[510] text-[15px] text-foreground tracking-[-0.012em]">
						Settings
					</h1>
				</header>
			</div>
		);
	}

	return (
		<div className="flex h-full flex-col">
			<header className="border-border border-b px-6 py-3">
				<div className="flex items-center gap-2">
					<SettingsIcon className="size-4 text-muted-foreground" />
					<h1 className="font-[510] text-[15px] text-foreground tracking-[-0.012em]">
						{project.name} — Settings
					</h1>
				</div>
				<p className="mt-0.5 text-[12px] text-muted-foreground">
					Project identity and the on-disk paths used by Docs + Library.
				</p>
			</header>

			<div className="grow overflow-y-auto px-6 py-4">
				<div className="max-w-xl space-y-5">
					<div className="flex items-center gap-3">
						<ProjectIcon className="size-8" color={color} />
						<div className="flex flex-wrap gap-1.5">
							{PRESET_COLORS.map((c) => (
								<button
									key={c}
									type="button"
									aria-label={`Color ${c}`}
									onClick={() => setColor(c)}
									className={cn(
										"size-5 rounded-full border transition-transform",
										color === c
											? "scale-110 border-foreground/40 ring-2 ring-foreground/20"
											: "border-border/60 hover:scale-105",
									)}
									style={{ backgroundColor: c }}
								/>
							))}
						</div>
					</div>

					<div className="space-y-1.5">
						<Label htmlFor="settings-name">Name</Label>
						<Input
							id="settings-name"
							value={name}
							onChange={(e) => setName(e.target.value)}
						/>
					</div>

					<div className="space-y-1.5">
						<Label htmlFor="settings-description">Description</Label>
						<Textarea
							id="settings-description"
							value={description}
							onChange={(e) => setDescription(e.target.value)}
							rows={3}
						/>
					</div>

					<div className="space-y-1.5">
						<Label htmlFor="settings-root">Root path</Label>
						<Input
							id="settings-root"
							value={rootPath}
							onChange={(e) => setRootPath(e.target.value)}
							placeholder="/host/sites/my-project"
							className="font-mono text-[12px]"
						/>
						<p className="text-[11px] text-muted-foreground">
							Where this project's code lives on disk. Optional — used by
							project-linking features.
						</p>
					</div>

					<div className="space-y-1.5">
						<Label htmlFor="settings-docs">Docs path</Label>
						<Input
							id="settings-docs"
							value={docsPath}
							onChange={(e) => setDocsPath(e.target.value)}
							placeholder="/host/sites/my-project/docs"
							className="font-mono text-[12px]"
						/>
						<p className="text-[11px] text-muted-foreground">
							Markdown docs folder shown on the Docs tab.
						</p>
					</div>

					<div className="flex items-center justify-between border-t pt-4">
						<Button
							type="button"
							onClick={onSave}
							disabled={updateMutation.isPending || !name.trim()}
						>
							{updateMutation.isPending ? <Loader /> : <SaveIcon />}
							Save changes
						</Button>
					</div>

					<div className="rounded-lg border border-border/60 bg-card/40 p-4">
						<div className="flex items-center justify-between gap-4">
							<div>
								<p className="font-[510] text-[13px]">
									{project.archived ? "Archived" : "Active"}
								</p>
								<p className="mt-0.5 text-[12px] text-muted-foreground">
									{project.archived
										? "Hidden from the grid, sidebar, and pickers. Unarchive to bring it back."
										: "Archiving hides this project from the grid, sidebar, and pickers — data is kept, nothing is deleted."}
								</p>
							</div>
							<Button
								type="button"
								variant={project.archived ? "default" : "outline"}
								disabled={updateMutation.isPending}
								onClick={onToggleArchive}
							>
								{project.archived ? <ArchiveRestoreIcon /> : <ArchiveIcon />}
								{project.archived ? "Unarchive" : "Archive"}
							</Button>
						</div>
					</div>
				</div>
			</div>
		</div>
	);
}
