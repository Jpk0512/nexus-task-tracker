"use client";

/**
 * Small "j/k →" keyboard hint chip. Used in page headers on list views
 * (Todos, Triage, Library, Documents, Inbox) where useJkNavigation is
 * wired up. Keeps the affordance discoverable without adding chrome.
 */
export function JkHint({ className }: { className?: string }) {
	return (
		<span
			className={
				"inline-flex items-center gap-1 text-[11px] text-muted-foreground" +
				(className ?? "")
			}
		>
			<kbd className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[10px]">
				j
			</kbd>
			<kbd className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[10px]">
				k
			</kbd>
			<span>→ navigate</span>
		</span>
	);
}
