"use client";

import { cn } from "@ui/lib/utils";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useInboxCounts } from "@/components/inbox/use-inbox-counts";
import { InboxView } from "@/components/inbox/view";
import { PersonalLens } from "@/components/lens/personal-lens";

type Tab = "focus" | "needs-you";

/**
 * Focus surface — merges Lens work-list + Needs you (attention inbox).
 * Supports ?tab=needs-you for legacy /inbox redirects.
 */
export function FocusShell() {
	const searchParams = useSearchParams();
	const initial =
		searchParams.get("tab") === "needs-you" ||
		searchParams.get("needs") === "you"
			? "needs-you"
			: "focus";
	const [tab, setTab] = useState<Tab>(initial);
	const { tabCounts } = useInboxCounts();
	const unread = tabCounts?.unread ?? 0;

	useEffect(() => {
		if (
			searchParams.get("tab") === "needs-you" ||
			searchParams.get("needs") === "you"
		) {
			setTab("needs-you");
		}
	}, [searchParams]);

	return (
		<div className="flex h-full min-h-0 flex-col">
			<div className="flex items-center justify-between gap-3 border-border/60 border-b px-4 py-2.5">
				<div>
					<h1 className="font-[510] text-[15px] tracking-[-0.01em]">Focus</h1>
					<p className="text-[12px] text-muted-foreground">
						Work list + attention. Dump lives under Capture.
					</p>
				</div>
				<div className="inline-flex rounded-lg border border-border/60 bg-card/40 p-0.5">
					{(
						[
							["focus", "Do now"],
							["needs-you", "Needs you"],
						] as const
					).map(([id, label]) => (
						<button
							key={id}
							type="button"
							onClick={() => setTab(id)}
							className={cn(
								"inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12.5px] font-[510] transition-colors",
								tab === id
									? "bg-accent text-foreground"
									: "text-muted-foreground hover:text-foreground",
							)}
						>
							{label}
							{id === "needs-you" && unread > 0 ? (
								<span className="rounded-full bg-primary/20 px-1.5 text-[10px] tabular-nums text-primary">
									{unread}
								</span>
							) : null}
						</button>
					))}
				</div>
			</div>
			<div className="min-h-0 flex-1 overflow-hidden">
				{tab === "focus" ? <PersonalLens /> : <InboxView />}
			</div>
		</div>
	);
}
