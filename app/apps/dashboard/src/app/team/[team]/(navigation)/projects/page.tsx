import { ProjectsGrid } from "@/components/projects/projects-grid";

export default function Page() {
	return (
		<div className="h-full animate-blur-in">
			<ProjectsGrid pageSize={30} />
		</div>
	);
}
