import { Suspense } from "react";
import { FocusShell } from "@/components/focus/focus-shell";

/**
 * /focus — Attention surface: Do now (Lens) + Needs you (Inbox).
 */
export default function FocusPage() {
	return (
		<Suspense fallback={<div className="p-4 text-[13px] text-muted-foreground">Loading Focus…</div>}>
			<FocusShell />
		</Suspense>
	);
}
