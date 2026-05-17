"use client";

// BacklinksPanel — universal "Referenced by N items" affordance, mounted on
// every detail surface (task, document, knowledge, library, prompt, todo).
// Fed by a single trpc.references.list endpoint that already gracefully
// degrades to empty arrays for entity types that don't have real join-table
// adjacency yet, so this component never explodes — it just shows fewer
// groups.

import { useQuery } from "@tanstack/react-query";
import {
	CheckSquareIcon,
	FileTextIcon,
	InboxIcon,
	LinkIcon,
	ListChecksIcon,
} from "lucide-react";
import Link from "next/link";
import { useUser } from "@/components/user-provider";
import { trpc } from "@/utils/trpc";

export type BacklinksEntityType =
	| "task"
	| "document"
	| "knowledge"
	| "library"
	| "prompt"
	| "todo";

type BacklinksPanelProps = {
	entityType: BacklinksEntityType;
	entityId: string;
	className?: string;
};

export function BacklinksPanel({
	entityType,
	entityId,
	className,
}: BacklinksPanelProps) {
	const user = useUser();
	const basePath = user?.basePath ?? "/team";

	const { data, isLoading } = useQuery(
		trpc.references.list.queryOptions(
			{ entityType, entityId },
			{ enabled: Boolean(entityId), staleTime: 30_000 },
		),
	);

	if (!entityId) return null;
	if (isLoading) return null;
	if (!data) return null;

	const todos = data.todos ?? [];
	const tasks = data.tasks ?? [];
	const documents = data.documents ?? [];
	const inboxItems = data.inbox ?? [];

	const total =
		todos.length + tasks.length + documents.length + inboxItems.length;

	if (total === 0) {
		// Render a small muted hint so the affordance is at least discoverable on
		// every detail page (Linear-style "Referenced by — None"). Caller can
		// alternatively pass null by checking total themselves.
		return (
			<section
				className={joinClass(
					"mt-6 border-border border-t pt-4 text-[12px] text-muted-foreground",
					className,
				)}
				aria-label="Referenced by"
			>
				<div className="flex items-center gap-2">
					<LinkIcon className="size-3.5" />
					<span className="font-[510] tracking-[-0.005em]">Referenced by</span>
					<span className="italic">No references yet</span>
				</div>
			</section>
		);
	}

	return (
		<section
			className={joinClass("mt-6 border-border border-t pt-4", className)}
			aria-label="Referenced by"
		>
			<header className="mb-2 flex items-center gap-2 text-[12px] text-foreground">
				<LinkIcon className="size-3.5 text-muted-foreground" />
				<span className="font-[510] tracking-[-0.005em]">
					Referenced by {total} {total === 1 ? "item" : "items"}
				</span>
			</header>

			<div className="space-y-3">
				{tasks.length > 0 && (
					<BacklinkGroup
						icon={
							<CheckSquareIcon className="size-3.5 text-muted-foreground" />
						}
						label="Tasks"
						count={tasks.length}
					>
						{tasks.map((t) => (
							<BacklinkRow
								key={t.id}
								href={`${basePath}/tasks/${t.id}`}
								primary={t.title}
								prefix={t.permalinkId}
								chip={t.projectName}
							/>
						))}
					</BacklinkGroup>
				)}

				{todos.length > 0 && (
					<BacklinkGroup
						icon={<ListChecksIcon className="size-3.5 text-muted-foreground" />}
						label="Todos"
						count={todos.length}
					>
						{todos.map((t) => (
							<BacklinkRow
								key={t.id}
								href={`${basePath}/todos?todoId=${t.id}`}
								primary={t.content}
								chip={t.projectName ?? undefined}
								strike={t.checked}
							/>
						))}
					</BacklinkGroup>
				)}

				{documents.length > 0 && (
					<BacklinkGroup
						icon={<FileTextIcon className="size-3.5 text-muted-foreground" />}
						label="Documents"
						count={documents.length}
					>
						{documents.map((d) => (
							<BacklinkRow
								key={d.id}
								href={`${basePath}/documents/${d.id}`}
								primary={`${d.icon ? `${d.icon} ` : ""}${d.name}`}
							/>
						))}
					</BacklinkGroup>
				)}

				{inboxItems.length > 0 && (
					<BacklinkGroup
						icon={<InboxIcon className="size-3.5 text-muted-foreground" />}
						label="Inbox"
						count={inboxItems.length}
					>
						{inboxItems.map((n) => (
							<BacklinkRow
								key={n.id}
								href={`${basePath}/inbox?selectedInboxId=${n.id}`}
								primary={n.display}
								chip={n.source}
								secondary={n.subtitle ?? undefined}
							/>
						))}
					</BacklinkGroup>
				)}
			</div>
		</section>
	);
}

function BacklinkGroup({
	icon,
	label,
	count,
	children,
}: {
	icon: React.ReactNode;
	label: string;
	count: number;
	children: React.ReactNode;
}) {
	return (
		<div>
			<div className="mb-1 flex items-center gap-1.5 text-[11px] text-muted-foreground uppercase tracking-wider">
				{icon}
				<span>{label}</span>
				<span className="text-muted-foreground/70">({count})</span>
			</div>
			<ul className="space-y-0.5">{children}</ul>
		</div>
	);
}

function BacklinkRow({
	href,
	primary,
	prefix,
	chip,
	secondary,
	strike,
}: {
	href: string;
	primary: string;
	prefix?: string;
	chip?: string | null;
	secondary?: string;
	strike?: boolean;
}) {
	return (
		<li>
			<Link
				href={href}
				className="-mx-1.5 flex items-center gap-2 rounded-md px-1.5 py-1 text-[12px] hover:bg-muted/60"
			>
				{prefix && (
					<span className="shrink-0 font-mono text-[10px] text-muted-foreground">
						{prefix}
					</span>
				)}
				<span
					className={joinClass(
						"min-w-0 truncate text-foreground",
						strike && "text-muted-foreground line-through",
					)}
				>
					{primary}
				</span>
				{secondary && (
					<span className="min-w-0 truncate text-[11px] text-muted-foreground">
						— {secondary}
					</span>
				)}
				{chip && (
					<span className="ml-auto shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
						{chip}
					</span>
				)}
			</Link>
		</li>
	);
}

function joinClass(...parts: Array<string | undefined | false>): string {
	return parts.filter(Boolean).join(" ");
}
