"use client";

import { Button } from "@ui/components/ui/button";
import { Input } from "@ui/components/ui/input";
import { Textarea } from "@ui/components/ui/textarea";
import { cn } from "@ui/lib/utils";
import {
	ArrowRightIcon,
	CheckIcon,
	CompassIcon,
	FileTextIcon,
	FolderKanbanIcon,
	LightbulbIcon,
	MapIcon,
	PaletteIcon,
	RocketIcon,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { SoftIcon } from "@/components/ui/soft-icon";
import { useUser } from "@/components/user-provider";

const PHASES = [
	{ id: "seed", label: "Seed", icon: LightbulbIcon, tone: "yellow" as const },
	{ id: "concept", label: "Concept", icon: FileTextIcon, tone: "blue" as const },
	{
		id: "architecture",
		label: "Architecture",
		icon: MapIcon,
		tone: "teal" as const,
	},
	{ id: "ux", label: "UX", icon: PaletteIcon, tone: "pink" as const },
	{
		id: "handoff",
		label: "Handoff",
		icon: CompassIcon,
		tone: "violet" as const,
	},
	{
		id: "board",
		label: "Board",
		icon: FolderKanbanIcon,
		tone: "green" as const,
	},
] as const;

/**
 * Project Starter thin workshop — seed step interactive now;
 * later phases are progressive disclosure shells (FEAT-003).
 */
export default function StarterWorkshopPage() {
	const user = useUser();
	const base = user.basePath;
	const [phaseIdx, setPhaseIdx] = useState(0);
	const [name, setName] = useState("");
	const [idea, setIdea] = useState("");
	const [drivers, setDrivers] = useState("");
	const [seeded, setSeeded] = useState(false);

	const phase = PHASES[phaseIdx]!;
	const canAdvanceSeed = name.trim().length >= 2 && idea.trim().length >= 8;

	const seedSummary = useMemo(
		() => ({
			name: name.trim(),
			idea: idea.trim(),
			drivers: drivers
				.split("\n")
				.map((s) => s.trim())
				.filter(Boolean),
		}),
		[name, idea, drivers],
	);

	const sealSeed = () => {
		if (!canAdvanceSeed) return;
		try {
			localStorage.setItem(
				"nexus.starter.seed",
				JSON.stringify({ ...seedSummary, at: new Date().toISOString() }),
			);
		} catch {
			/* ignore */
		}
		setSeeded(true);
		setPhaseIdx(1);
	};

	return (
		<div className="mx-auto flex w-full max-w-4xl flex-col gap-6 px-6 py-8">
			<div className="flex flex-wrap items-start justify-between gap-3">
				<div>
					<h1 className="font-[510] text-[22px] tracking-[-0.02em]">
						Project Starter
					</h1>
					<p className="mt-1 max-w-xl text-[13px] text-muted-foreground">
						Idea → sealed handoff → board coding agents can execute. Host agent
						runtime (Claude / Codex OAuth) wires in after seed.
					</p>
				</div>
				<Button asChild variant="outline" size="sm">
					<Link href={`${base}/create-project`}>Exit</Link>
				</Button>
			</div>

			{/* Phase rail */}
			<ol className="flex flex-wrap gap-2">
				{PHASES.map((p, i) => {
					const done = i < phaseIdx || (i === 0 && seeded);
					const active = i === phaseIdx;
					return (
						<li key={p.id}>
							<button
								type="button"
								onClick={() => setPhaseIdx(i)}
								className={cn(
									"inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-[12px] transition-colors",
									active && "border-primary/40 bg-primary/10 text-foreground",
									!active &&
										done &&
										"border-border/60 text-muted-foreground",
									!active &&
										!done &&
										"border-border/40 text-muted-foreground/70",
								)}
							>
								{done && !active ? (
									<CheckIcon className="size-3 text-green-400" />
								) : (
									<span className="font-[510] text-[11px] tabular-nums">
										{i + 1}
									</span>
								)}
								{p.label}
							</button>
						</li>
					);
				})}
			</ol>

			<div className="rounded-xl border border-border/60 bg-card/40 p-5">
				<div className="mb-4 flex items-center gap-3">
					<SoftIcon icon={phase.icon} tone={phase.tone} size="md" />
					<div>
						<h2 className="font-[510] text-[15px]">{phase.label}</h2>
						<p className="text-[12px] text-muted-foreground">
							{phase.id === "seed" && "Name the work and capture the spark."}
							{phase.id === "concept" &&
								"Grill-with-docs — CONTEXT.md + ADRs (agent-backed next)."}
							{phase.id === "architecture" &&
								"Wayfinder decisions → architecture map."}
							{phase.id === "ux" && "Prototype gallery + flow locks."}
							{phase.id === "handoff" &&
								"Seal a handoff pack agents can resume."}
							{phase.id === "board" &&
								"Materialize vertical-slice tasks on a kanban board."}
						</p>
					</div>
				</div>

				{phase.id === "seed" ? (
					<div className="space-y-4">
						<label className="block space-y-1.5">
							<span className="text-[12px] font-[510]">Project name</span>
							<Input
								value={name}
								onChange={(e) => setName(e.target.value)}
								placeholder="e.g. Voice Agent Studio"
								className="max-w-md"
							/>
						</label>
						<label className="block space-y-1.5">
							<span className="text-[12px] font-[510]">Idea in one breath</span>
							<Textarea
								value={idea}
								onChange={(e) => setIdea(e.target.value)}
								placeholder="What are we building, for whom, and why now?"
								className="min-h-[100px]"
							/>
						</label>
						<label className="block space-y-1.5">
							<span className="text-[12px] font-[510]">
								Drivers (one per line, optional)
							</span>
							<Textarea
								value={drivers}
								onChange={(e) => setDrivers(e.target.value)}
								placeholder={"Ship MVP in 2 weeks\nReuse existing MCP stack"}
								className="min-h-[72px]"
							/>
						</label>
						<div className="flex flex-wrap gap-2 pt-1">
							<Button disabled={!canAdvanceSeed} onClick={sealSeed}>
								Seal seed
								<ArrowRightIcon className="ml-1.5 size-3.5" />
							</Button>
							<Button asChild variant="ghost">
								<Link href={`${base}/projects?createProject=true`}>
									Skip to blank project
								</Link>
							</Button>
						</div>
					</div>
				) : (
					<div className="space-y-3 text-[13px] text-muted-foreground">
						{seeded ? (
							<div className="rounded-lg border border-border/50 bg-background/40 p-3 text-foreground">
								<p className="font-[510] text-[12px] text-muted-foreground">
									Seed locked
								</p>
								<p className="mt-1 font-[510]">{seedSummary.name || "—"}</p>
								<p className="mt-0.5 text-[12.5px] text-muted-foreground">
									{seedSummary.idea || "—"}
								</p>
							</div>
						) : (
							<p>
								Seal a seed first so later phases have a named idea to grill.
							</p>
						)}
						<p>
							Full agent workshop (Claude Agent SDK streaming + Codex resume)
							connects here next — host OAuth, no API keys in the Starter path.
						</p>
						<div className="flex flex-wrap gap-2 pt-2">
							{phaseIdx > 0 ? (
								<Button
									variant="outline"
									size="sm"
									onClick={() => setPhaseIdx((i) => Math.max(0, i - 1))}
								>
									Back
								</Button>
							) : null}
							{phaseIdx < PHASES.length - 1 ? (
								<Button
									size="sm"
									disabled={!seeded}
									onClick={() =>
										setPhaseIdx((i) => Math.min(PHASES.length - 1, i + 1))
									}
								>
									Continue
									<ArrowRightIcon className="ml-1.5 size-3.5" />
								</Button>
							) : (
								<Button asChild size="sm" disabled={!seeded}>
									<Link href={`${base}/projects?createProject=true`}>
										<RocketIcon className="mr-1.5 size-3.5" />
										Open board create
									</Link>
								</Button>
							)}
						</div>
					</div>
				)}
			</div>
		</div>
	);
}
