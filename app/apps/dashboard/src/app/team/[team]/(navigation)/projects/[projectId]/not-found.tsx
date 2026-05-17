"use client";

import { Button } from "@ui/components/ui/button";
import { FolderXIcon } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

export default function ProjectNotFound() {
	const { team, projectId } = useParams<{ team: string; projectId: string }>();
	return (
		<div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
			<FolderXIcon className="size-8 text-muted-foreground" />
			<h2 className="font-[510] text-[15px] text-foreground tracking-[-0.012em]">
				Tab not found
			</h2>
			<p className="max-w-md text-[12px] text-muted-foreground">
				The page you tried to open doesn't exist for this project. Try the
				board, docs, updates, or views tabs instead.
			</p>
			<Link href={`/team/${team}/projects/${projectId}`}>
				<Button variant="outline" size="sm">
					Back to project board
				</Button>
			</Link>
		</div>
	);
}
