"use client";

import { Button } from "@ui/components/ui/button";
import { RocketIcon, SparklesIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { SoftIcon } from "@/components/ui/soft-icon";
import { useUser } from "@/components/user-provider";

type Seed = { name?: string; idea?: string; at?: string };

/**
 * Home Continue card when a Project Starter seed is in localStorage.
 */
export function StarterContinueCard() {
	const user = useUser();
	const [seed, setSeed] = useState<Seed | null>(null);

	useEffect(() => {
		try {
			const raw = localStorage.getItem("nexus.starter.seed");
			if (raw) setSeed(JSON.parse(raw) as Seed);
		} catch {
			/* ignore */
		}
	}, []);

	if (!seed?.name) return null;

	return (
		<div className="flex flex-col gap-3 rounded-[12px] border border-primary/25 bg-gradient-to-br from-primary/10 to-card p-4 sm:flex-row sm:items-center sm:justify-between">
			<div className="flex items-start gap-3">
				<SoftIcon icon={SparklesIcon} tone="blue" size="md" />
				<div>
					<p className="text-[11px] font-[510] uppercase tracking-wider text-muted-foreground">
						Continue starter
					</p>
					<p className="font-[510] text-[14px]">{seed.name}</p>
					{seed.idea ? (
						<p className="mt-0.5 line-clamp-2 text-[12px] text-muted-foreground">
							{seed.idea}
						</p>
					) : null}
				</div>
			</div>
			<Button asChild size="sm" className="shrink-0 gap-1.5">
				<Link href={`${user.basePath}/create-project/starter`}>
					<RocketIcon className="size-3.5" />
					Resume workshop
				</Link>
			</Button>
		</div>
	);
}
