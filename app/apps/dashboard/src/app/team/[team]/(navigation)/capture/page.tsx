import { TodosView } from "@/components/todos/todos-view";

/**
 * /capture — Dump | Todos | Outline.
 * Phase C wires Dump + safe promote; v1 mounts Todos as the primary process surface.
 */
export default function CapturePage() {
	return (
		<div className="flex h-full min-h-0 flex-col">
			<div className="border-border/60 border-b px-4 py-3">
				<h1 className="font-[510] text-[18px] tracking-[-0.01em]">Capture</h1>
				<p className="text-[13px] text-muted-foreground">
					Dump thoughts here — promote to Todo, Task, or Note when ready. Inbox
					(Needs you) is attention-only, not a scratch pad.
				</p>
			</div>
			<div className="min-h-0 flex-1">
				<TodosView />
			</div>
		</div>
	);
}
