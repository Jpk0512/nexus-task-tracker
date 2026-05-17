"use client";

/**
 * Relationships sidebar — iter-10 Round F (wired to real backlinks).
 *
 * Three sections — Prompts, Agents, Knowledge — fetch via tRPC and render
 * tiny cards (name + last-touched timestamp). The `+` button on each header
 * dispatches a `palette.openLink` event which the command palette listens
 * for and switches into "link mode" (iter-10 Task 6).
 *
 * Layout: collapsible right rail mounted by the project detail layout.
 *   - expanded: 320px wide
 *   - collapsed: 32px strip with a chevron toggle
 *
 * Collapsed state is persisted to localStorage
 * (`nexus.project.relationships.collapsed`) so the choice survives reloads.
 *
 * Performance (codex amendment #7): each section caps at 50 results
 * server-side and React Query caches per-project; we mount the sidebar
 * lazily and only fire queries while it is expanded so the project surface
 * stays cheap for projects without backlinks.
 */

import { useQuery } from "@tanstack/react-query";
import { Badge } from "@ui/components/ui/badge";
import { Button } from "@ui/components/ui/button";
import { cn } from "@ui/lib/utils";
import { formatDistanceToNowStrict } from "date-fns";
import {
	BrainIcon,
	ChevronLeftIcon,
	ChevronRightIcon,
	NetworkIcon,
	PlusIcon,
	SparklesIcon,
	WandSparklesIcon,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useUser } from "@/components/user-provider";
import { trpc } from "@/utils/trpc";

const STORAGE_KEY = "nexus.project.relationships.collapsed";

/**
 * Public link-mode event. The command palette listens for this on `window`
 * and switches into entity-picker mode. Kept as a custom event (rather than
 * a global zustand store) so the sidebar stays independent of the palette
 * implementation — the palette could be swapped without breaking the
 * sidebar contract.
 */
export type PaletteLinkEntity = "prompts" | "agents" | "knowledge" | "skills";
export interface PaletteOpenLinkDetail {
	entity: PaletteLinkEntity;
	sourceType: "project" | "task" | "note" | "agent";
	sourceId: string;
}

export function dispatchOpenLink(detail: PaletteOpenLinkDetail): void {
	if (typeof window === "undefined") return;
	window.dispatchEvent(
		new CustomEvent<PaletteOpenLinkDetail>("palette.openLink", { detail }),
	);
}

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

function formatRelative(value: string | null | undefined): string {
	if (!value) return "";
	try {
		return formatDistanceToNowStrict(new Date(value), { addSuffix: true });
	} catch {
		return "";
	}
}

interface Props {
	projectId: string;
	className?: string;
}

export function ProjectRelationshipsSidebar({
	projectId,
	className,
}: Props) {
	const [collapsed, setCollapsed] = useState<boolean>(false);

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
				<PromptsSection projectId={projectId} />
				<AgentsSection projectId={projectId} />
				<KnowledgeSection projectId={projectId} />
			</div>
		</aside>
	);
}

// ─── Sections ─────────────────────────────────────────────────────────────
//
// Each section is its own component so its tRPC query is only registered
// when the sidebar is expanded (the parent gates rendering above). React
// Query handles caching + revalidation; we don't manually invalidate here
// because link mutations live in the palette which calls
// queryClient.invalidateQueries on its own.

interface SectionShellProps {
	title: string;
	icon: typeof WandSparklesIcon;
	count: number;
	emptyTitle: string;
	emptyAction: string;
	onLink: () => void;
	children: React.ReactNode;
}

function SectionShell({
	title,
	icon: Icon,
	count,
	emptyTitle,
	emptyAction,
	onLink,
	children,
}: SectionShellProps) {
	return (
		<section className="flex flex-col gap-2 border-border border-b px-4 py-3 last:border-b-0">
			<div className="flex items-center justify-between">
				<div className="flex items-center gap-1.5 text-foreground text-sm">
					<Icon className="size-3.5 text-muted-foreground" />
					<span className="font-medium">{title}</span>
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
					onClick={onLink}
					aria-label={emptyAction}
					className="size-6 p-0 text-muted-foreground hover:text-foreground"
				>
					<PlusIcon className="size-3.5" />
				</Button>
			</div>
			{count === 0 ? (
				<div className="rounded-md border border-dashed border-border bg-background/40 px-3 py-3 text-center">
					<p className="text-muted-foreground text-xs">{emptyTitle}</p>
					<Button
						type="button"
						variant="ghost"
						size="sm"
						onClick={onLink}
						className="mt-1 h-6 gap-1 px-2 text-brand text-xs hover:bg-brand/10 hover:text-brand"
					>
						<PlusIcon className="size-3" />
						{emptyAction}
					</Button>
				</div>
			) : (
				<ul className="flex flex-col gap-1">{children}</ul>
			)}
		</section>
	);
}

interface CardItemProps {
	href: string;
	title: string;
	subtitle: string;
}

function CardItem({ href, title, subtitle }: CardItemProps) {
	return (
		<li>
			<Link
				href={href}
				className="flex flex-col gap-0.5 rounded-md border border-transparent px-2 py-1.5 text-xs transition-colors hover:border-border hover:bg-muted"
				title={title}
			>
				<span className="truncate font-medium text-foreground">{title}</span>
				{subtitle ? (
					<span className="truncate text-muted-foreground text-[11px]">
						{subtitle}
					</span>
				) : null}
			</Link>
		</li>
	);
}

function PromptsSection({ projectId }: { projectId: string }) {
	const user = useUser();
	const basePath = user?.basePath || "";
	const promptsQuery = useQuery(
		trpc.projects.listLinkedPrompts.queryOptions({ projectId, limit: 50 }),
	);
	const items = promptsQuery.data ?? [];

	const onLink = useCallback(() => {
		dispatchOpenLink({
			entity: "prompts",
			sourceType: "project",
			sourceId: projectId,
		});
	}, [projectId]);

	return (
		<SectionShell
			title="Prompts"
			icon={WandSparklesIcon}
			count={items.length}
			emptyTitle="No prompts linked yet."
			emptyAction="Add prompt"
			onLink={onLink}
		>
			{items.map((p) => (
				<CardItem
					key={p.id}
					href={`${basePath}/prompts/${p.productSlug}/${p.slug}`}
					title={p.name}
					subtitle={`Updated ${formatRelative(p.updatedAt)}`}
				/>
			))}
		</SectionShell>
	);
}

function AgentsSection({ projectId }: { projectId: string }) {
	const user = useUser();
	const basePath = user?.basePath || "";
	// Agents owning milestones in this project. We start from milestones,
	// dedupe by agentId, then resolve agent metadata.
	const milestonesQuery = useQuery(
		trpc.milestones.get.queryOptions({ projectId }),
	);
	const agentsQuery = useQuery(trpc.agents.get.queryOptions({}));

	const ownedAgentIds = useMemo(() => {
		const ids = new Set<string>();
		for (const m of (milestonesQuery.data ?? []) as Array<{
			ownerAgentId?: string | null;
		}>) {
			if (m.ownerAgentId) ids.add(m.ownerAgentId);
		}
		return ids;
	}, [milestonesQuery.data]);

	const agents = useMemo(() => {
		const rows = (agentsQuery.data ?? []) as Array<{
			id: string;
			name: string;
			description?: string | null;
			updatedAt?: string;
		}>;
		return rows.filter((a) => ownedAgentIds.has(a.id));
	}, [agentsQuery.data, ownedAgentIds]);

	const onLink = useCallback(() => {
		dispatchOpenLink({
			entity: "agents",
			sourceType: "project",
			sourceId: projectId,
		});
	}, [projectId]);

	return (
		<SectionShell
			title="Agents"
			icon={SparklesIcon}
			count={agents.length}
			emptyTitle="No agents owning milestones yet."
			emptyAction="Assign agent"
			onLink={onLink}
		>
			{agents.map((a) => (
				<CardItem
					key={a.id}
					href={`${basePath}/agents/${a.id}`}
					title={a.name}
					subtitle={a.description ?? "Owns a milestone in this project"}
				/>
			))}
		</SectionShell>
	);
}

function KnowledgeSection({ projectId }: { projectId: string }) {
	const user = useUser();
	const basePath = user?.basePath || "";
	const knowledgeQuery = useQuery(
		trpc.projects.listLinkedKnowledge.queryOptions({ projectId, limit: 50 }),
	);
	const items = knowledgeQuery.data ?? [];

	const onLink = useCallback(() => {
		dispatchOpenLink({
			entity: "knowledge",
			sourceType: "project",
			sourceId: projectId,
		});
	}, [projectId]);

	return (
		<SectionShell
			title="Knowledge notes"
			icon={BrainIcon}
			count={items.length}
			emptyTitle="No notes linked via this project's tasks yet."
			emptyAction="Link a note from a task"
			onLink={onLink}
		>
			{items.map((n) => (
				<CardItem
					key={n.id}
					href={`${basePath}/knowledge/${encodeURIComponent(n.relativePath)}`}
					title={n.name}
					subtitle={
						n.lastEditedAt
							? `Edited ${formatRelative(n.lastEditedAt)}`
							: (n.parentDir ?? "")
					}
				/>
			))}
		</SectionShell>
	);
}
