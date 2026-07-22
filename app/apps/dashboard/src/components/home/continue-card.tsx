"use client";

import {
	BoxIcon,
	ClockIcon,
	FileTextIcon,
	MessageCircleIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { loadRecent } from "@/components/global-search/recent-items";
import type { GlobalSearchItem } from "@/components/global-search/types";
import { useUser } from "@/components/user-provider";

// FEAT-006 item 4 — "Continue" card: the Home-page half of the same
// recently-visited store the command palette's Recent row and Cmd+O ring
// read from, narrowed to the three entity kinds the audit calls out.
const CONTINUE_TYPES = new Set(["project", "chat", "document"]);
const CONTINUE_MAX = 3;

const TYPE_ICON: Record<string, typeof BoxIcon> = {
	project: BoxIcon,
	chat: MessageCircleIcon,
	document: FileTextIcon,
};

const TYPE_LABEL: Record<string, string> = {
	project: "Project",
	chat: "Chat",
	document: "Doc",
};

function hrefFor(basePath: string, item: GlobalSearchItem): string {
	if (item.href) return `${basePath}${item.href}`;
	if (item.type === "project") return `${basePath}/projects/${item.id}`;
	if (item.type === "document") return `${basePath}/documents/${item.id}`;
	return basePath;
}

export function ContinueCard() {
	const user = useUser();
	const [items, setItems] = useState<GlobalSearchItem[]>([]);

	// Read once on mount — same "storage is the source of truth" approach the
	// palette's own Recent section and the quick-open ring already use.
	useEffect(() => {
		setItems(
			loadRecent()
				.filter((item) => CONTINUE_TYPES.has(item.type))
				.slice(0, CONTINUE_MAX),
		);
	}, []);

	if (items.length === 0) return null;

	return (
		<div className="rounded-[12px] border border-border bg-card p-3">
			<div className="mb-2 flex items-center gap-1.5 px-1">
				<ClockIcon className="size-3.5 text-muted-foreground" />
				<h2 className="font-[510] text-[13px] tracking-[-0.005em]">Continue</h2>
			</div>
			<div className="flex flex-col gap-0.5">
				{items.map((item) => {
					const Icon = TYPE_ICON[item.type] ?? BoxIcon;
					return (
						<Link
							key={`${item.type}-${item.id}`}
							href={hrefFor(user.basePath, item)}
							className="flex h-8 items-center gap-2 rounded-md px-2 text-[13px] transition-colors hover:bg-accent/60"
						>
							<Icon className="size-3.5 shrink-0 text-muted-foreground" />
							<span className="min-w-0 flex-1 truncate">{item.title}</span>
							<span className="shrink-0 text-[11px] text-muted-foreground">
								{TYPE_LABEL[item.type] ?? item.type}
							</span>
						</Link>
					);
				})}
			</div>
		</div>
	);
}
