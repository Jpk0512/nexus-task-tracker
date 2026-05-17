"use client";

/**
 * Relationships sidebar — iter-10 Round E Task 4.
 *
 * UI shell only. Three sections — Prompts, Agents, Knowledge — each rendering
 * an empty state today. The `+` button on each header opens the command
 * palette in "link mode" (a stub that toasts a "wire-up coming in iter 8"
 * message); iter-8 wires the join tables + populates counts.
 *
 * Layout: collapsible right rail mounted by the project detail layout.
 *   - expanded: 320px wide
 *   - collapsed: 32px strip with a chevron toggle
 *
 * Collapsed state is persisted to localStorage
 * (`nexus.project.relationships.collapsed`) so the choice survives reloads
 * and route changes within the project surface.
 *
 * Performance budget (codex amendment #5): the sidebar renders 3 fixed
 * sections regardless of project size, so the heavy lifting (relevance
 * ranking, dedupe, paging) happens at the query layer in iter-8. The shell
 * stays cheap.
 */

import { Badge } from "@ui/components/ui/badge";
import { Button } from "@ui/components/ui/button";
import { cn } from "@ui/lib/utils";
import {
	BrainIcon,
	ChevronLeftIcon,
	ChevronRightIcon,
	NetworkIcon,
	PlusIcon,
	SparklesIcon,
	WandSparklesIcon,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

const STORAGE_KEY = "nexus.project.relationships.collapsed";

function readCollapsed(): boolean {
	if (typeof window === "undefined") return false;
	try {
		return window.localStorage.getItem(STORAGE_KEY) === "1";
	} catch {
		return false;
	}
}

function writeCollapsed(collapsed: boolean): void {
	if (typeof window === "undefined") return;
	try {
		window.localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0");
	} catch {
		// ignore
	}
}

interface SectionDef {
	id: "prompts" | "agents" | "knowledge";
	label: string;
	icon: typeof BrainIcon;
	emptyTitle: string;
	emptyAction: string;
}

const SECTIONS: SectionDef[] = [
	{
		id: "prompts",
		label: "Prompts",
		icon: WandSparklesIcon,
		emptyTitle: "No prompts linked yet.",
		emptyAction: "Add prompt",
	},
	{
		id: "agents",
		label: "Agents",
		icon: SparklesIcon,
		emptyTitle: "No agents linked yet.",
		emptyAction: "Add agent",
	},
	{
		id: "knowledge",
		label: "Knowledge notes",
		icon: BrainIcon,
		emptyTitle: "No notes linked yet.",
		emptyAction: "Add note",
	},
];

interface Props {
	projectId: string;
	className?: string;
}

export function ProjectRelationshipsSidebar({ projectId: _projectId, className }: Props) {
	const [collapsed, setCollapsed] = useState<boolean>(false);

	// Restore persisted state on mount. We pay one render of the default
	// (expanded) to keep server + client markup identical for hydration.
	useEffect(() => {
		setCollapsed(readCollapsed());
	}, []);

	const toggleCollapsed = useCallback(() => {
		setCollapsed((prev) => {
			const next = !prev;
			writeCollapsed(next);
			return next;
		});
	}, []);

	const onLink = useCallback((section: SectionDef) => {
		// Iter-8 wires this to the command palette in link mode; today we
		// surface a stub so the affordance is discoverable but inert.
		toast(`Linking ${section.label.toLowerCase()} — wire-up coming in iter 8.`);
	}, []);

	if (collapsed) {
		return (
			<aside
				aria-label="Project relationships"
				className={cn(
					"sticky top-12 flex h-[calc(100vh-3rem)] w-8 shrink-0 flex-col items-center border-border border-l bg-card",
					className,
				)}
			>
				<button
					type="button"
					onClick={toggleCollapsed}
					aria-label="Expand relationships panel"
					aria-expanded={false}
					className="flex h-10 w-full items-center justify-center text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
				>
					<ChevronLeftIcon className="size-4" />
				</button>
				<div className="flex flex-col items-center gap-3 pt-3 text-muted-foreground">
					<NetworkIcon className="size-3.5" />
				</div>
			</aside>
		);
	}

	return (
		<aside
			aria-label="Project relationships"
			className={cn(
				"sticky top-12 flex h-[calc(100vh-3rem)] w-80 shrink-0 flex-col gap-0 overflow-y-auto border-border border-l bg-card",
				className,
			)}
		>
			<header className="flex items-center justify-between gap-2 border-border border-b px-4 py-2">
				<div className="flex items-center gap-1.5 text-muted-foreground text-xs uppercase tracking-wide">
					<NetworkIcon className="size-3.5" />
					<span className="font-semibold">Relationships</span>
				</div>
				<button
					type="button"
					onClick={toggleCollapsed}
					aria-label="Collapse relationships panel"
					aria-expanded={true}
					className="inline-flex size-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
				>
					<ChevronRightIcon className="size-3.5" />
				</button>
			</header>

			<div className="flex flex-col">
				{SECTIONS.map((section) => {
					const Icon = section.icon;
					const count = 0; // iter-8: wire to backlinks query
					return (
						<section
							key={section.id}
							className="flex flex-col gap-2 border-border border-b px-4 py-3 last:border-b-0"
						>
							<div className="flex items-center justify-between">
								<div className="flex items-center gap-1.5 text-foreground text-sm">
									<Icon className="size-3.5 text-muted-foreground" />
									<span className="font-medium">{section.label}</span>
									<Badge
										variant="secondary"
										className="h-4 px-1.5 text-[10px]"
									>
										{count}
									</Badge>
								</div>
								<Button
									type="button"
									variant="ghost"
									size="sm"
									onClick={() => onLink(section)}
									aria-label={section.emptyAction}
									className="size-6 p-0 text-muted-foreground hover:text-foreground"
								>
									<PlusIcon className="size-3.5" />
								</Button>
							</div>
							<div className="rounded-md border border-dashed border-border bg-background/40 px-3 py-3 text-center">
								<p className="text-muted-foreground text-xs">
									{section.emptyTitle}
								</p>
								<Button
									type="button"
									variant="ghost"
									size="sm"
									onClick={() => onLink(section)}
									className="mt-1 h-6 gap-1 px-2 text-brand text-xs hover:bg-brand/10 hover:text-brand"
								>
									<PlusIcon className="size-3" />
									{section.emptyAction}
								</Button>
							</div>
						</section>
					);
				})}
			</div>
		</aside>
	);
}
