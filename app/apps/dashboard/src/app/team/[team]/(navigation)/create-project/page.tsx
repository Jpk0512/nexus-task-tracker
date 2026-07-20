"use client";

import { Button } from "@ui/components/ui/button";
import { FileTextIcon, FolderPlusIcon, SparklesIcon } from "lucide-react";
import Link from "next/link";
import { SoftIcon } from "@/components/ui/soft-icon";
import { useUser } from "@/components/user-provider";

/**
 * Dedicated Create Project tab — entry to blank project + Project Starter (FEAT-003).
 */
export default function CreateProjectPage() {
	const user = useUser();
	const base = user.basePath;

	return (
		<div className="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-10">
			<div>
				<h1 className="font-[510] text-[22px] tracking-[-0.02em]">
					Create a project
				</h1>
				<p className="mt-1 text-[13px] text-muted-foreground">
					Start blank, or walk an idea through concept → architecture → handoff
					→ board.
				</p>
			</div>

			<div className="grid gap-4 sm:grid-cols-2">
				<div className="flex flex-col gap-3 rounded-xl border border-border/60 bg-card/40 p-5">
					<SoftIcon icon={FolderPlusIcon} tone="green" size="lg" />
					<div>
						<h2 className="font-[510] text-[15px]">Blank project</h2>
						<p className="mt-1 text-[12.5px] text-muted-foreground">
							Name it, pick a color, open a board. You bring the plan.
						</p>
					</div>
					<Button asChild className="mt-auto w-fit" variant="outline">
						<Link href={`${base}/projects?createProject=true`}>
							Create blank
						</Link>
					</Button>
				</div>

				<div className="flex flex-col gap-3 rounded-xl border border-primary/30 bg-gradient-to-br from-primary/10 to-card/40 p-5">
					<SoftIcon icon={SparklesIcon} tone="blue" size="lg" />
					<div>
						<h2 className="font-[510] text-[15px]">Start from an idea</h2>
						<p className="mt-1 text-[12.5px] text-muted-foreground">
							Project Starter: grill, wayfind, mock UX, seal a handoff, then
							materialize a kanban board coding agents can execute.
						</p>
					</div>
					<Button asChild className="mt-auto w-fit">
						<Link href={`${base}/create-project/starter`}>
							Open Project Starter
						</Link>
					</Button>
				</div>
			</div>

			<div className="rounded-xl border border-border/60 bg-card/30 p-4 text-[12.5px] text-muted-foreground">
				<span className="inline-flex items-center gap-2 font-[510] text-foreground">
					<FileTextIcon className="size-3.5" /> What you get from Starter
				</span>
				<ul className="mt-2 list-inside list-disc space-y-1">
					<li>CONTEXT.md + ADRs from grill-with-docs</li>
					<li>Architecture map (wayfinder decisions)</li>
					<li>Handoff pack + board of vertical-slice tasks</li>
					<li>Project notebook scaffold under Notes</li>
				</ul>
			</div>
		</div>
	);
}
