import { useMutation } from "@tanstack/react-query";
import { Button } from "@ui/components/ui/button";
import { Form } from "@ui/components/ui/form";
import { cn } from "@ui/lib/utils";
import { SaveIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import Loader from "@/components/loader";
import { updateProjectInCache } from "@/hooks/use-data-cache-helpers";
import { useProjectParams } from "@/hooks/use-project-params";
import { useFormAutoSave, useZodForm } from "@/hooks/use-zod-form";
import { runToastAction } from "@/lib/toast-action";
import { queryClient, trpc } from "@/utils/trpc";
import { ProjectColorPicker } from "./color-picker";
import { Description } from "./description";
import { type ProjectFormValues, projectFormSchema } from "./form-type";
import { ProjectLeadSelect } from "./lead";
import { ProjectMembersSelect } from "./members";
import { RangeInput } from "./range-input";
import { ProjectStatusSelect } from "./status";
import { Name } from "./title";
import { ProjectVisibilitySelect } from "./visibility";

export type PropertiesLayout = "compact" | "expanded";

const colors = [
	"#d4a373",
	"#e07a5f",
	"#4852ad",
	"#81b29a",
	"#f2cc8f",
	"#6a994e",
	"#f4a261",
	"#2a9d8f",
	"#e76f51",
];

export const ProjectForm = ({
	defaultValues,
	propertiesLayout,
	descriptionVariant,
}: {
	defaultValues?: Partial<ProjectFormValues>;
	propertiesLayout?: PropertiesLayout;
	descriptionVariant?: "compact" | "page";
}) => {
	const [lastSavedAt, setLastSavedAt] = useState<Date | null>(
		defaultValues?.updatedAt ? new Date(defaultValues.updatedAt) : null,
	);
	const { setParams } = useProjectParams();
	const form = useZodForm(projectFormSchema, {
		defaultValues: {
			name: "",
			description: "",
			color: colors[Math.floor(Math.random() * colors.length)],
			...defaultValues,
		},
	});

	const { mutate: updateProject, isPending: isUpdating } = useMutation(
		trpc.projects.update.mutationOptions({
			onMutate: () => {
				toast.loading("Updating project...", { id: "update-project" });
			},
			onSuccess: (updated) => {
				if (!updated) return;

				queryClient.invalidateQueries(
					trpc.projects.getForTimeline.queryOptions(),
				);

				updateProjectInCache(updated);
				setLastSavedAt(new Date());
				toast.success("Project updated successfully", { id: "update-project" });
			},
			onError: (error) => {
				toast.error("Failed to update project", { id: "update-project" });
			},
		}),
	);

	const { mutateAsync: createProjectAsync, isPending: isCreating } =
		useMutation(trpc.projects.create.mutationOptions());

	// Specific server error via toast (FEAT-020 item 3) — `error` reads the
	// tRPC error's own message (mapped server-side by `toProjectCreateError`,
	// e.g. a duplicate-name conflict) rather than a generic failure string,
	// and the grid is invalidated only once creation actually succeeds so a
	// created project is never silently missing from it.
	const createProject = (values: ProjectFormValues) => {
		runToastAction(() => createProjectAsync(values), {
			id: "create-project",
			loading: "Creating project…",
			success: (project) => `Project "${project.name}" created`,
			error: (err) =>
				err instanceof Error ? err.message : "Failed to create project",
		}).then((result) => {
			if (!result.ok) return;
			queryClient.invalidateQueries(trpc.projects.get.infiniteQueryOptions());
			queryClient.invalidateQueries(trpc.projects.get.queryOptions());
			setParams(null);
		});
	};

	const handleSubmit = (values: ProjectFormValues) => {
		if (defaultValues?.id) {
			updateProject({
				...values,
				id: defaultValues.id,
			});
		} else {
			createProject(values);
		}
	};

	useFormAutoSave(form, handleSubmit, {
		enabled: Boolean(defaultValues?.id),
	});

	const isLoading = isCreating || isUpdating;

	return (
		<div className="h-full space-y-4">
			<Form {...form}>
				<form onSubmit={form.handleSubmit(handleSubmit)} className="h-full">
					<div className="w-full space-y-4">
						<div className="space-y-1">
							<div className="flex items-center gap-4">
								<ProjectColorPicker />
								<Name />
							</div>
							{descriptionVariant === "page" ? (
								<>
									<div
										className={cn(
											propertiesLayout === "compact"
												? "flex flex-wrap items-center gap-x-6 gap-y-2 border-b py-3"
												: "space-y-4",
										)}
									>
										<RangeInput variant={propertiesLayout} />
										<ProjectStatusSelect variant={propertiesLayout} />
										<ProjectVisibilitySelect variant={propertiesLayout} />
										<ProjectLeadSelect variant={propertiesLayout} />
										<ProjectMembersSelect variant={propertiesLayout} />
									</div>
									<Description variant={descriptionVariant} />
								</>
							) : (
								<>
									<Description variant={descriptionVariant} />
									<div
										className={cn(
											propertiesLayout === "compact"
												? "flex flex-wrap items-center gap-6"
												: "space-y-4",
										)}
									>
										<RangeInput variant={propertiesLayout} />
										<ProjectStatusSelect variant={propertiesLayout} />
										<ProjectVisibilitySelect variant={propertiesLayout} />
										<ProjectLeadSelect variant={propertiesLayout} />
										<ProjectMembersSelect variant={propertiesLayout} />
									</div>
								</>
							)}
						</div>
					</div>

					{!defaultValues?.id && (
						<div className="flex justify-end">
							<Button
								type="submit"
								disabled={isLoading}
								size="sm"
								variant="default"
							>
								{isLoading ? <Loader /> : <SaveIcon />}
								Save Project
							</Button>
						</div>
					)}
				</form>
			</Form>
		</div>
	);
};
