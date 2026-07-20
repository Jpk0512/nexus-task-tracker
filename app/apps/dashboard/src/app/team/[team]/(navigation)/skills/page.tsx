import { LibraryListView } from "@/components/library/list-view";

/** /skills — Skills-first library surface with square grid. */
export default function SkillsPage() {
	return (
		<div className="flex h-full min-h-0 flex-1 animate-blur-in">
			<LibraryListView
				defaultKind="skill"
				title="Skills"
				description="Agent skills as a square catalog. Disk is source of truth — re-scan after edits."
				defaultView="grid"
			/>
		</div>
	);
}
