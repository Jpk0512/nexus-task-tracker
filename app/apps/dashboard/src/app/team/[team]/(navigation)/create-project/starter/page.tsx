"use client";

import { useMutation } from "@tanstack/react-query";
import { Button } from "@ui/components/ui/button";
import { Input } from "@ui/components/ui/input";
import { Label } from "@ui/components/ui/label";
import { Textarea } from "@ui/components/ui/textarea";
import { cn } from "@ui/lib/utils";
import {
	ArrowRightIcon,
	CheckIcon,
	FileTextIcon,
	FolderKanbanIcon,
	LightbulbIcon,
	MessageSquareIcon,
	RocketIcon,
	SparklesIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import {
	StarterInterview,
	type StarterSeed,
} from "@/components/starter/starter-interview";
import { SoftIcon } from "@/components/ui/soft-icon";
import { useUser } from "@/components/user-provider";
import { runToastAction } from "@/lib/toast-action";
import { queryClient, trpc } from "@/utils/trpc";

const PHASES = [
	{ id: "seed", label: "Seed", icon: LightbulbIcon, tone: "yellow" as const },
	{
		id: "interview",
		label: "Interview",
		icon: MessageSquareIcon,
		tone: "blue" as const,
	},
	{ id: "prd", label: "PRD", icon: FileTextIcon, tone: "violet" as const },
] as const;

const PROJECT_COLORS = [
	"#5e6ad2",
	"#d4a373",
	"#e07a5f",
	"#81b29a",
	"#f2cc8f",
	"#6a994e",
	"#c75c5c",
	"#3b82f6",
];

/**
 * Project Starter workshop — in-app realization of FEAT-003.
 *
 * Seed (name/idea/drivers) → guided AI interview (one question at a time) →
 * finalized PRD review → create the project + a linked PRD document in the
 * dashboard. No host runtime required; the interview runs on the app's AI
 * layer.
 */
export default function StarterWorkshopPage() {
	const user = useUser();
	const router = useRouter();
	const base = user.basePath;

	const [phaseIdx, setPhaseIdx] = useState(0);
	const [name, setName] = useState("");
	const [idea, setIdea] = useState("");
	const [drivers, setDrivers] = useState("");
	const [color, setColor] = useState<string>(PROJECT_COLORS[0]!);
	const [prd, setPrd] = useState("");

	const phase = PHASES[phaseIdx]!;
	const canAdvanceSeed = name.trim().length >= 2 && idea.trim().length >= 8;

	const seed: StarterSeed = useMemo(
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
				JSON.stringify({ ...seed, color, at: new Date().toISOString() }),
			);
		} catch {
			/* ignore */
		}
		setPhaseIdx(1);
	};

	const createProject = useMutation(trpc.projects.create.mutationOptions());
	const createDoc = useMutation(
		trpc.documents.create.mutationOptions({
			onError: () =>
				toast.error("Project created, but the PRD document failed to save."),
		}),
	);

	// Specific server error via toast (FEAT-020 item 3) — mirrors the
	// ProjectForm/projects-grid create call sites: the tRPC error's own
	// message surfaces (e.g. a duplicate-name conflict) instead of a generic
	// failure string, and the grid only invalidates once creation actually
	// succeeds so the new project is never silently missing from it.
	const onCreate = async () => {
		if (!prd.trim() || !name.trim()) return;
		const result = await runToastAction(
			() =>
				createProject.mutateAsync({
					name: name.trim(),
					// Short summary only — the full PRD lives in the linked document.
					description: idea.trim().slice(0, 300) || null,
					color,
					visibility: "team",
				}),
			{
				id: "starter-create",
				loading: "Creating project…",
				success: "Project created",
				error: (err) =>
					err instanceof Error ? err.message : "Failed to create project",
			},
		);
		if (!result.ok) return;
		const project = result.data;
		try {
			await createDoc.mutateAsync({
				name: "PRD",
				content: prd.trim(),
				projectId: project.id,
			});
		} catch {
			/* project still created; surfaced by createDoc onError toast */
		}
		queryClient.invalidateQueries(trpc.projects.get.infiniteQueryOptions());
		queryClient.invalidateQueries(trpc.projects.get.queryOptions());
		router.push(`${base}/projects/${project.id}/overview`);
	};

	return (
		<div className="mx-auto flex w-full max-w-4xl flex-col gap-6 px-6 py-8">
			<div className="flex flex-wrap items-start justify-between gap-3">
				<div>
					<h1 className="font-[510] text-[22px] tracking-[-0.02em]">
						Project Starter
					</h1>
					<p className="mt-1 max-w-xl text-[13px] text-muted-foreground">
						Walk an idea through a guided interview, finalize a full PRD, then
						create the project with the PRD attached.
					</p>
				</div>
				<Button asChild variant="outline" size="sm">
					<Link href={`${base}/create-project`}>Exit</Link>
				</Button>
			</div>

			{/* Phase rail */}
			<ol className="flex flex-wrap gap-2">
				{PHASES.map((p, i) => {
					const done = i < phaseIdx;
					const active = i === phaseIdx;
					return (
						<li key={p.id}>
							<button
								type="button"
								disabled={i > phaseIdx}
								onClick={() => setPhaseIdx(i)}
								className={cn(
									"inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-[12px] transition-colors",
									active && "border-primary/40 bg-primary/10 text-foreground",
									!active && done && "border-border/60 text-muted-foreground",
									!active &&
										!done &&
										"border-border/40 text-muted-foreground/70",
								)}
							>
								{done ? (
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
							{phase.id === "interview" &&
								"Answer one question at a time — the agent locks decisions toward a PRD."}
							{phase.id === "prd" &&
								"Review the generated PRD, then create the project."}
						</p>
					</div>
				</div>

				{/* Seed */}
				{phase.id === "seed" ? (
					<div className="space-y-4">
						<div className="space-y-1.5">
							<Label htmlFor="starter-name">Project name</Label>
							<Input
								id="starter-name"
								value={name}
								onChange={(e) => setName(e.target.value)}
								placeholder="e.g. Voice Agent Studio"
								className="max-w-md"
							/>
						</div>
						<div className="space-y-1.5">
							<Label htmlFor="starter-idea">Idea in one breath</Label>
							<Textarea
								id="starter-idea"
								value={idea}
								onChange={(e) => setIdea(e.target.value)}
								placeholder="What are we building, for whom, and why now?"
								className="min-h-[100px]"
							/>
						</div>
						<div className="space-y-1.5">
							<Label htmlFor="starter-drivers">
								Drivers (one per line, optional)
							</Label>
							<Textarea
								id="starter-drivers"
								value={drivers}
								onChange={(e) => setDrivers(e.target.value)}
								placeholder={"Ship MVP in 2 weeks\nReuse existing MCP stack"}
								className="min-h-[72px]"
							/>
						</div>
						<div className="space-y-1.5">
							<span className="font-[510] text-[12px]">Project color</span>
							<div className="flex flex-wrap gap-2">
								{PROJECT_COLORS.map((c) => (
									<button
										key={c}
										type="button"
										aria-label={`Color ${c}`}
										onClick={() => setColor(c)}
										className={cn(
											"size-6 rounded-full border transition-transform",
											color === c
												? "scale-110 border-foreground/40 ring-2 ring-foreground/20"
												: "border-border/60 hover:scale-105",
										)}
										style={{ backgroundColor: c }}
									/>
								))}
							</div>
						</div>
						<div className="flex flex-wrap gap-2 pt-1">
							<Button disabled={!canAdvanceSeed} onClick={sealSeed}>
								Start interview
								<ArrowRightIcon className="ml-1.5 size-3.5" />
							</Button>
							<Button asChild variant="ghost">
								<Link href={`${base}/projects?createProject=true`}>
									Skip to blank project
								</Link>
							</Button>
						</div>
					</div>
				) : null}

				{/* Interview */}
				{phase.id === "interview" ? (
					<StarterInterview
						seed={seed}
						onPrd={(p) => {
							setPrd(p);
							setPhaseIdx(2);
						}}
						onBack={() => setPhaseIdx(0)}
					/>
				) : null}

				{/* PRD review + create */}
				{phase.id === "prd" ? (
					<div className="space-y-4">
						<div className="flex flex-wrap items-end gap-4">
							<div className="space-y-1.5">
								<Label htmlFor="starter-prd-name">Project name</Label>
								<Input
									id="starter-prd-name"
									value={name}
									onChange={(e) => setName(e.target.value)}
									className="max-w-xs"
								/>
							</div>
							<div className="space-y-1.5">
								<span className="font-[510] text-[12px]">Color</span>
								<div className="flex flex-wrap gap-2">
									{PROJECT_COLORS.map((c) => (
										<button
											key={c}
											type="button"
											aria-label={`Color ${c}`}
											onClick={() => setColor(c)}
											className={cn(
												"size-6 rounded-full border transition-transform",
												color === c
													? "scale-110 border-foreground/40 ring-2 ring-foreground/20"
													: "border-border/60 hover:scale-105",
											)}
											style={{ backgroundColor: c }}
										/>
									))}
								</div>
							</div>
						</div>
						<div className="space-y-1.5">
							<Label htmlFor="starter-prd">
								Product Requirements Document (editable)
							</Label>
							<Textarea
								id="starter-prd"
								value={prd}
								onChange={(e) => setPrd(e.target.value)}
								className="min-h-[360px] font-mono text-[12.5px] leading-relaxed"
							/>
						</div>
						<div className="flex flex-wrap items-center gap-2 pt-1">
							<Button
								onClick={onCreate}
								disabled={
									!prd.trim() || !name.trim() || createProject.isPending
								}
							>
								<RocketIcon className="mr-1.5 size-3.5" />
								{createProject.isPending ? "Creating…" : "Create project"}
							</Button>
							<Button variant="outline" onClick={() => setPhaseIdx(1)}>
								Back to interview
							</Button>
							<span className="inline-flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
								<SparklesIcon className="size-3" />
								The full PRD is saved as a document on the new project.
							</span>
						</div>
					</div>
				) : null}
			</div>

			<p className="flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
				<FolderKanbanIcon className="size-3" />
				On create, the project opens on its board — the PM agent may plan
				milestones from the idea.
			</p>
		</div>
	);
}
