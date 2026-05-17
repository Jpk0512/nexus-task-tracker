"use client";

import { useQuery } from "@tanstack/react-query";
import { FileTextIcon } from "lucide-react";
import Link from "next/link";
import { trpc } from "@/utils/trpc";

type Props = { projectId: string; team: string };

export function ProjectDocsView({ projectId, team }: Props) {
	const projectQuery = useQuery(
		trpc.projects.getById.queryOptions({ id: projectId } as any),
	);
	const docsQuery = useQuery(
		trpc.documents.get.queryOptions({
			projectId,
			tree: false,
			pageSize: 100,
		} as any),
	);
	const project = projectQuery.data as
		| { name?: string; prefix?: string }
		| undefined;
	const docs = ((docsQuery.data as { data?: Array<any> } | undefined)?.data ??
		[]) as Array<{
		id: string;
		name: string;
		icon: string | null;
		content: string | null;
		updatedAt: string;
	}>;

	return (
		<div className="flex h-full flex-col">
			<header className="border-border border-b px-6 py-3">
				<div className="flex items-baseline justify-between gap-4">
					<div>
						<h1 className="font-[510] text-[15px] text-foreground tracking-[-0.012em]">
							{project?.name ?? "Project"} — Docs
						</h1>
						<p className="mt-0.5 text-[12px] text-muted-foreground">
							Markdown + Mermaid docs pinned to this project. ({docs.length})
						</p>
					</div>
				</div>
			</header>
			<div className="grow overflow-y-auto px-6 py-4">
				{docs.length === 0 && (
					<div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
						<FileTextIcon className="size-10 text-muted-foreground" />
						<p className="text-muted-foreground">
							No docs pinned to this project yet.
						</p>
					</div>
				)}
				<ul className="space-y-1">
					{docs.map((d) => (
						<li key={d.id}>
							<Link
								href={`/team/${team}/documents/${d.id}`}
								className="group flex items-start gap-3 rounded-md border border-transparent px-3 py-2 transition hover:border-border hover:bg-accent/40"
							>
								<span className="text-lg leading-none">{d.icon ?? "📄"}</span>
								<div className="min-w-0 grow">
									<div className="font-medium text-sm">{d.name}</div>
									{d.content && (
										<p className="mt-0.5 line-clamp-2 text-muted-foreground text-xs">
											{d.content.slice(0, 240).replace(/^#+\s+/gm, "")}
										</p>
									)}
								</div>
								<span className="hidden text-muted-foreground text-xs sm:inline">
									{new Date(d.updatedAt).toLocaleDateString()}
								</span>
							</Link>
						</li>
					))}
				</ul>
			</div>
		</div>
	);
}
