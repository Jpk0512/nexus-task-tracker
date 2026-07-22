"use client";

import { Button } from "@ui/components/ui/button";
import {
	FileTextIcon,
	FolderPlusIcon,
	HardDriveIcon,
	SparklesIcon,
} from "lucide-react";
import Link from "next/link";
import { SoftIcon } from "@/components/ui/soft-icon";
import { useUser } from "@/components/user-provider";

/**
 * Create Project — blank · from idea · existing folder on disk.
 */
export default function CreateProjectPage() {
	const user = useUser();
	const base = user.basePath;

	return (
		<div className="mx-auto flex max-w-4xl flex-col gap-8 px-6 py-10">
			<div>
				<h1 className="font-[510] text-[22px] tracking-[-0.02em]">
					Create a project
				</h1>
				<p className="mt-1 text-[13px] text-muted-foreground">
					Start blank, walk an idea through Starter, or link an existing site
					folder on disk for Site Docs.
				</p>
			</div>

			<div className="grid gap-4 sm:grid-cols-3">
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
							materialize a kanban board.
						</p>
					</div>
					<Button asChild className="mt-auto w-fit">
						<Link href={`${base}/create-project/starter`}>
							Open Project Starter
						</Link>
					</Button>
				</div>

				<div className="flex flex-col gap-3 rounded-xl border border-border/60 bg-card/40 p-5">
					<SoftIcon icon={HardDriveIcon} tone="orange" size="lg" />
					<div>
						<h2 className="font-[510] text-[15px]">Existing on disk</h2>
						<p className="mt-1 text-[12.5px] text-muted-foreground">
							Point at a site folder, name it, pick which folder Site Docs
							mirrors (usually <code className="text-[11px]">docs/</code>).
						</p>
					</div>
					<Button asChild className="mt-auto w-fit" variant="outline">
						<Link href={`${base}/create-project/existing`}>
							Link existing site
						</Link>
					</Button>
				</div>
			</div>

			<div className="rounded-xl border border-border/60 bg-card/30 p-4 text-[12.5px] text-muted-foreground">
				<span className="inline-flex items-center gap-2 font-[510] text-foreground">
					<FileTextIcon className="size-3.5" /> Site Docs
				</span>
				<ul className="mt-2 list-inside list-disc space-y-1">
					<li>Docs live on disk — edits in Nexus write through</li>
					<li>Nexus Maps (architecture / flow / graph) stay in the app</li>
					<li>Open Site Docs from Brain to review any linked site</li>
				</ul>
			</div>
		</div>
	);
}
