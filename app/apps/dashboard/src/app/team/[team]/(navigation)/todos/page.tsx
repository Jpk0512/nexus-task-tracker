import { TodosView } from "@/components/todos/todos-view";

/**
 * /todos — dedicated, prominent todos surface (the workhorse).
 * Keeps tags + notes/attachments; constrained to a centered column so it
 * doesn't sprawl edge-to-edge.
 */
export default function TodosPage() {
	return (
		<div className="mx-auto h-full w-full max-w-3xl animate-blur-in">
			<TodosView />
		</div>
	);
}
