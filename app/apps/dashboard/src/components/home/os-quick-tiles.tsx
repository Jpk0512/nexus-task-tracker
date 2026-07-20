"use client";

import {
	BrainIcon,
	BookOpenIcon,
	MicIcon,
	SparklesIcon,
} from "lucide-react";
import Link from "next/link";
import { SoftIcon } from "@/components/ui/soft-icon";
import { useUser } from "@/components/user-provider";

const TILES = [
	{
		href: "notes",
		label: "Notes",
		hint: "Vault + ZenNotes",
		icon: BrainIcon,
		tone: "violet" as const,
	},
	{
		href: "skills",
		label: "Skills",
		hint: "Catalog",
		icon: BookOpenIcon,
		tone: "teal" as const,
	},
	{
		href: "meetings",
		label: "Meetings",
		hint: "Transcripts → tasks",
		icon: MicIcon,
		tone: "pink" as const,
	},
	{
		href: "create-project",
		label: "Start from idea",
		hint: "Project Starter",
		icon: SparklesIcon,
		tone: "blue" as const,
	},
] as const;

/**
 * Soft-icon quick tiles on Home — Dashboard OS lock (content soft icons).
 */
export function OsQuickTiles() {
	const user = useUser();
	const base = user.basePath;

	return (
		<section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
			{TILES.map((t) => (
				<Link
					key={t.href}
					href={`${base}/${t.href}`}
					className="flex items-center gap-3 rounded-xl border border-border/60 bg-card/40 px-3.5 py-3 transition-colors hover:bg-accent/40"
				>
					<SoftIcon icon={t.icon} tone={t.tone} size="md" />
					<div className="min-w-0">
						<div className="truncate font-[510] text-[13px]">{t.label}</div>
						<div className="truncate text-[11.5px] text-muted-foreground">
							{t.hint}
						</div>
					</div>
				</Link>
			))}
		</section>
	);
}
