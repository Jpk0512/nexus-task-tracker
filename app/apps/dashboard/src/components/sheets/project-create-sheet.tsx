"use client";
import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
} from "@nexus-app/ui/dialog";
import { useProjectParams } from "@/hooks/use-project-params";
import { ProjectForm } from "../forms/project-form/form";

export const ProjectCreateSheet = () => {
	const { createProject, projectSeedName, projectSeedDescription, setParams } =
		useProjectParams();

	const isOpen = Boolean(createProject);

	return (
		<Dialog open={isOpen} onOpenChange={() => setParams(null)}>
			<DialogContent className="sm:min-w-[1000px]">
				<DialogHeader className="sr-only">
					<DialogTitle>Create project</DialogTitle>
				</DialogHeader>
				{/* Dialog content stays mounted while closed, so `ProjectForm`'s
				 internal form would otherwise only ever see the first seed. The
				 `key` forces a fresh mount (and fresh useForm defaults) whenever
				 the seed changes — e.g. a new "convert task to project" call. */}
				<ProjectForm
					key={`${createProject}-${projectSeedName ?? ""}`}
					propertiesLayout="compact"
					defaultValues={{
						name: projectSeedName || undefined,
						description: projectSeedDescription || undefined,
					}}
				/>
			</DialogContent>
		</Dialog>
	);
};
