import { KnowledgeView } from "@/components/knowledge/knowledge-view";

/** /notes — product name for Knowledge engine (URL stays Notes). */
export default function NotesPage() {
	return (
		<div className="h-full animate-blur-in">
			<KnowledgeView />
		</div>
	);
}
