"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@ui/components/ui/badge";
import { Button } from "@ui/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@ui/components/ui/dropdown-menu";
import { Input } from "@ui/components/ui/input";
import { LabelBadge } from "@ui/components/ui/label-badge";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@ui/components/ui/popover";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@ui/components/ui/select";
import { formatDistanceToNow } from "date-fns";
import {
	ArrowLeftIcon,
	ClipboardIcon,
	CopyIcon,
	EllipsisIcon,
	PencilIcon,
	PlayIcon,
	PlusIcon,
	SearchIcon,
	ShuffleIcon,
	Trash2Icon,
	XIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { tagColor } from "@/lib/project-color";
import { trpc } from "@/utils/trpc";

type Props = { productSlug: string; team: string };

type PromptRow = {
	id: string;
	name: string;
	slug: string;
	tags: string[] | null;
	version: number;
	updatedAt: string;
	projectId: string | null;
	projectName: string | null;
};

// `{{ varName }}` (lenient whitespace) → ordered, deduped variable list.
function extractVars(content: string): string[] {
	const out = new Set<string>();
	const re = /\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g;
	let m: RegExpExecArray | null;
	// biome-ignore lint/suspicious/noAssignInExpressions: standard regex loop
	while ((m = re.exec(content)) !== null) {
		out.add(m[1]!);
	}
	return Array.from(out);
}

// Sample value rotations per variable family. Iter8 added rotations so the
// "Shuffle" affordance in TestPromptPopover can cycle through plausible
// examples instead of repeating the same hardcoded "Alex" / "[team]" pair
// every time. `seed` is just an offset into the rotation array.
const SAMPLE_ROTATIONS: Record<string, string[]> = {
	email: [
		"alex@example.com",
		"jordan@acme.io",
		"sam@kbuddy.ai",
		"lee@mimrai.dev",
	],
	name: ["Alex", "Jordan", "Sam", "Lee", "Robin", "Priya"],
	company: ["Acme", "Globex", "Initech", "Umbrella", "Hooli"],
	project: ["Nexus", "Orchard", "Polaris", "Helios", "Atlas"],
	team: ["Platform", "Growth", "DX", "Infra", "AI Labs"],
	count: ["3", "5", "12", "1", "8"],
	url: [
		"https://example.com",
		"https://docs.mimrai.dev",
		"https://acme.test/specs",
		"https://kbuddy.ai/launch",
	],
};

// Pre-fill sensible sample values for a variable so the user can see the
// shape of the filled prompt without typing — `name` → "Alex", `email` →
// "alex@example.com", etc. Falls back to "[varName]" for anything we don't
// recognise, which keeps the rendered prompt readable.
//
// `seed` lets callers rotate through alternatives (iter8 shuffle button).
function sampleFor(varName: string, seed = 0): string {
	const n = varName.toLowerCase();
	const pick = (key: keyof typeof SAMPLE_ROTATIONS) => {
		const list = SAMPLE_ROTATIONS[key]!;
		return list[Math.abs(seed) % list.length]!;
	};
	if (n.includes("email")) return pick("email");
	if (n.includes("name")) return pick("name");
	if (n.includes("company") || n.includes("org")) return pick("company");
	if (n.includes("project")) return pick("project");
	if (n.includes("team")) return pick("team");
	if (n.includes("date")) return new Date().toISOString().slice(0, 10);
	if (n.includes("count") || n.includes("num")) return pick("count");
	if (n.includes("url") || n.includes("link")) return pick("url");
	return `[${varName}]`;
}

/**
 * Inline Test popover for a prompt row. Loads the prompt's content + vars
 * lazily when the popover opens (the list query intentionally omits content
 * to keep the row payload small), pre-fills sensible sample values, and
 * exposes a "Copy filled" action. The "Open editor" footer link mirrors the
 * old behaviour for users who want the full edit page.
 */
function TestPromptPopover({
	productSlug,
	promptSlug,
	promptName,
	href,
}: {
	productSlug: string;
	promptSlug: string;
	promptName: string;
	href: string;
}) {
	const [open, setOpen] = useState(false);
	const [values, setValues] = useState<Record<string, string>>({});
	// Seed used by `sampleFor` to rotate through sample variations.
	// Bumped each time the user hits the Shuffle button. The very first
	// render uses seed 0 so the default sample stays stable.
	const [seed, setSeed] = useState(0);
	const router = useRouter();

	const { data, isLoading } = useQuery({
		...trpc.prompts.getPromptBySlug.queryOptions({ productSlug, promptSlug }),
		enabled: open,
	});
	const content = (data?.content ?? "") as string;
	const vars = useMemo(() => extractVars(content), [content]);

	// When the prompt finishes loading, seed any unset variables with sample
	// values. Don't overwrite anything the user has already typed.
	useEffect(() => {
		if (!open || vars.length === 0) return;
		setValues((prev) => {
			const next = { ...prev };
			let changed = false;
			for (const v of vars) {
				if (next[v] === undefined) {
					next[v] = sampleFor(v, seed);
					changed = true;
				}
			}
			return changed ? next : prev;
		});
	}, [open, vars, seed]);

	// Shuffle: increments the seed and re-seeds *every* variable with the new
	// rotation value, regardless of whether the user had touched it. This is
	// the explicit "give me different samples" gesture — overwriting is the
	// expected behaviour.
	const shuffle = () => {
		const nextSeed = seed + 1;
		setSeed(nextSeed);
		setValues(() => {
			const next: Record<string, string> = {};
			for (const v of vars) next[v] = sampleFor(v, nextSeed);
			return next;
		});
	};

	const filled = useMemo(() => {
		let out = content;
		for (const [k, v] of Object.entries(values)) {
			out = out.replace(new RegExp(`\\{\\{\\s*${k}\\s*\\}\\}`, "g"), v);
		}
		return out;
	}, [content, values]);

	const copyFilled = () => {
		navigator.clipboard?.writeText(filled);
		toast.success("Filled prompt copied");
	};

	return (
		<Popover open={open} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<Button
					variant="ghost"
					size="sm"
					// Iter8 a11y: button always reveals on keyboard focus so it's
					// not mouse-only. aria-haspopup + aria-expanded wire it up
					// properly to assistive tech.
					className="h-7 px-2 text-[11px] text-muted-foreground opacity-0 transition hover:text-foreground focus-visible:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
					aria-haspopup="dialog"
					aria-expanded={open}
					aria-label={`Test prompt ${promptName} with sample variables`}
					// stopPropagation only (no preventDefault) — Radix needs the
					// native click to flow through to its trigger, but we don't
					// want the row Link href to navigate behind us.
					onClick={(e) => e.stopPropagation()}
					title="Test with sample variables"
				>
					<PlayIcon className="size-3" aria-hidden="true" />
					Test
				</Button>
			</PopoverTrigger>
			<PopoverContent
				align="end"
				className="w-96 p-3"
				onClick={(e) => e.stopPropagation()}
				onOpenAutoFocus={(e) => {
					// Auto-focus the first variable input if there is one; otherwise
					// let Radix focus the popover content so Esc still works.
					if (vars.length === 0) return;
					e.preventDefault();
					requestAnimationFrame(() => {
						const first = document.getElementById(`var-${vars[0]}`);
						(first as HTMLInputElement | null)?.focus();
					});
				}}
			>
				<div className="mb-2 flex items-center justify-between gap-2">
					<h4 className="font-[510] text-[13px]">{promptName}</h4>
					<div className="flex items-center gap-2">
						<span className="text-[11px] text-muted-foreground">
							{vars.length} var{vars.length === 1 ? "" : "s"}
						</span>
						{vars.length > 0 && (
							<button
								type="button"
								onClick={shuffle}
								title="Shuffle sample values"
								aria-label="Shuffle sample variable values"
								className="inline-flex size-5 items-center justify-center rounded text-muted-foreground transition hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/60"
							>
								<ShuffleIcon className="size-3" aria-hidden="true" />
							</button>
						)}
					</div>
				</div>
				{isLoading && (
					<div className="py-4 text-center text-[12px] text-muted-foreground">
						Loading prompt…
					</div>
				)}
				{!isLoading && vars.length === 0 && (
					<div className="rounded-md border border-border/70 border-dashed px-3 py-3 text-[12px] text-muted-foreground italic">
						No variables — this prompt is ready to copy as-is.
					</div>
				)}
				{!isLoading && vars.length > 0 && (
					<div className="space-y-1.5">
						{vars.map((v) => (
							<div key={v} className="flex items-center gap-2">
								<label
									htmlFor={`var-${v}`}
									className="w-24 shrink-0 truncate font-mono text-[11px] text-muted-foreground"
								>
									{v}
								</label>
								<Input
									id={`var-${v}`}
									value={values[v] ?? ""}
									onChange={(e) =>
										setValues((prev) => ({
											...prev,
											[v]: e.target.value,
										}))
									}
									className="h-7 grow text-[12px]"
								/>
							</div>
						))}
					</div>
				)}
				{!isLoading && content && (
					<div className="mt-2">
						<div className="mb-1 text-[10px] text-muted-foreground uppercase tracking-wider">
							Preview
						</div>
						<pre className="max-h-32 overflow-y-auto whitespace-pre-wrap rounded border border-border bg-muted/40 p-2 font-mono text-[11px] leading-snug">
							{filled || "(empty)"}
						</pre>
					</div>
				)}
				<div className="mt-3 flex items-center justify-between gap-2 border-border border-t pt-2">
					<button
						type="button"
						className="text-[11px] text-muted-foreground hover:text-foreground"
						onClick={() => {
							setOpen(false);
							router.push(href);
						}}
					>
						Open editor →
					</button>
					<Button
						size="sm"
						onClick={copyFilled}
						disabled={!content}
						className="h-7"
					>
						<ClipboardIcon className="size-3" />
						Copy filled
					</Button>
				</div>
			</PopoverContent>
		</Popover>
	);
}

export function PromptListView({ productSlug, team }: Props) {
	const router = useRouter();
	const qc = useQueryClient();
	const { data } = useQuery(
		trpc.prompts.getPrompts.queryOptions({ productSlug }),
	);
	// getPrompts narrows `product` to `{ id }`; pull the full-shape product
	// detail (name, description) from getProductBySlug for the header.
	const { data: productDetail } = useQuery(
		trpc.prompts.getProductBySlug.queryOptions({ slug: productSlug }),
	);
	const [showNew, setShowNew] = useState(false);
	const [name, setName] = useState("");
	const [search, setSearch] = useState("");
	const [sortBy, setSortBy] = useState<"updated" | "name">("updated");
	const [projectFilter, setProjectFilter] = useState<string | null>(null);

	const invalidatePrompts = () => {
		qc.invalidateQueries({ queryKey: [["prompts", "getPrompts"]] });
	};

	const createMut = useMutation(
		trpc.prompts.createPrompt.mutationOptions({
			onSuccess: (p) => {
				toast.success(`Created ${(p as { name: string }).name}`);
				setShowNew(false);
				setName("");
				invalidatePrompts();
			},
			onError: (e) => toast.error(e.message),
		}),
	);

	const deleteMut = useMutation(
		trpc.prompts.deletePrompt.mutationOptions({
			onSuccess: () => {
				toast.success("Deleted");
				invalidatePrompts();
			},
			onError: (e) => toast.error(e.message),
		}),
	);

	const product = data?.product;
	const prompts = (data?.prompts ?? []) as PromptRow[];

	const filtered = useMemo(() => {
		const q = search.trim().toLowerCase();
		const list = prompts.filter((p) => {
			if (projectFilter && p.projectId !== projectFilter) return false;
			if (!q) return true;
			if (p.name.toLowerCase().includes(q)) return true;
			return (p.tags ?? []).some((t) => t.toLowerCase().includes(q));
		});
		const sorted = [...list];
		if (sortBy === "name") {
			sorted.sort((a, b) => a.name.localeCompare(b.name));
		} else {
			sorted.sort(
				(a, b) =>
					new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
			);
		}
		return sorted;
	}, [prompts, search, sortBy, projectFilter]);

	const submitInline = () => {
		if (!product) return;
		if (!name.trim()) return;
		createMut.mutate({ productId: product.id, name: name.trim() });
	};

	return (
		<div className="flex h-full flex-col">
			<header className="border-border border-b px-6 py-3">
				<Link
					href={`/team/${team}/prompts`}
					className="inline-flex items-center gap-1 text-muted-foreground text-xs hover:text-foreground"
				>
					<ArrowLeftIcon className="size-3.5" /> Prompt Library
				</Link>
				<div className="mt-2 flex items-baseline justify-between gap-4">
					<div>
						<h1 className="font-[510] text-[15px] text-foreground tracking-[-0.012em]">
							{productDetail?.name ?? productSlug}
						</h1>
						{productDetail?.description && (
							<p className="mt-1 text-[12px] text-muted-foreground">
								{productDetail.description}
							</p>
						)}
					</div>
					<Button
						variant="outline"
						size="sm"
						onClick={() => setShowNew((s) => !s)}
						disabled={!product}
					>
						<PlusIcon className="size-3.5" />{" "}
						{showNew ? "Cancel" : "New prompt"}
					</Button>
				</div>

				{/* Filter bar: search + sort + count chip */}
				<div className="mt-3 flex flex-wrap items-center gap-2">
					<div className="relative">
						<SearchIcon className="absolute top-2.5 left-2.5 size-3.5 text-muted-foreground" />
						<Input
							value={search}
							onChange={(e) => setSearch(e.target.value)}
							placeholder="Search name or tag…"
							className="h-9 w-64 pl-8"
						/>
					</div>
					<Select
						value={sortBy}
						onValueChange={(v) => setSortBy(v as "updated" | "name")}
					>
						<SelectTrigger className="h-9 w-40">
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="updated">Last updated</SelectItem>
							<SelectItem value="name">Name</SelectItem>
						</SelectContent>
					</Select>
					{projectFilter && (
						<button
							type="button"
							onClick={() => setProjectFilter(null)}
							className="project-badge flex h-[22px] items-center gap-1.5 rounded-full border border-primary/50 bg-primary/[0.08] px-2 font-normal text-[11px] text-foreground transition-colors duration-150 ease-out hover:border-destructive/50 hover:bg-destructive/[0.06]"
						>
							<span
								className="inline-block h-[6px] w-[6px] shrink-0 rounded-full"
								style={{ background: tagColor(projectFilter) }}
							/>
							<span className="max-w-[120px] truncate">
								{prompts.find((p) => p.projectId === projectFilter)?.projectName ?? "Project"}
							</span>
							<XIcon className="ml-0.5 size-[12px] text-muted-foreground" />
						</button>
					)}
					<div className="ml-auto flex items-center gap-2 text-muted-foreground text-xs">
						<Badge variant="outline" className="font-normal">
							{filtered.length} prompt{filtered.length === 1 ? "" : "s"}
						</Badge>
					</div>
				</div>
			</header>
			<div className="grow overflow-y-auto px-6 py-4">
				{/* Inline + New prompt affordance at top of list */}
				{product && (
					<div className="mb-2">
						{showNew ? (
							<form
								onSubmit={(e) => {
									e.preventDefault();
									submitInline();
								}}
								className="flex items-center gap-2 rounded-md border border-border bg-card/40 px-3 py-1.5"
							>
								<PlusIcon className="size-3.5 text-muted-foreground" />
								<Input
									autoFocus
									value={name}
									onChange={(e) => setName(e.target.value)}
									placeholder="Prompt name, e.g. 'onboarding-v2'"
									className="h-7 grow border-0 bg-transparent px-0 focus-visible:ring-0"
								/>
								<Button
									type="submit"
									size="sm"
									disabled={!name.trim() || createMut.isPending}
								>
									Create
								</Button>
							</form>
						) : (
							<button
								type="button"
								onClick={() => setShowNew(true)}
								className="flex w-full items-center gap-2 rounded-md border border-border/70 border-dashed px-3 py-1.5 text-left text-[12px] text-muted-foreground transition hover:border-border hover:bg-accent/40 hover:text-foreground"
							>
								<PlusIcon className="size-3.5" />
								New prompt
							</button>
						)}
					</div>
				)}

				{filtered.length === 0 && (
					<div className="py-16 text-center text-muted-foreground text-sm">
						{prompts.length === 0
							? `No prompts yet for ${productDetail?.name ?? productSlug}. Add the first one above.`
							: "No prompts match this search."}
					</div>
				)}

				{/* 3-column LIST: name (+version) | tags | updated + kebab */}
				<ul className="space-y-0.5">
					{filtered.map((p) => {
						const href = `/team/${team}/prompts/${productSlug}/${p.slug}`;
						const tags = p.tags ?? [];
						return (
							<li
								key={p.id}
								className="group relative grid grid-cols-[1fr_auto_auto] items-center gap-4 rounded-md px-3 py-2 transition hover:bg-white/[0.04]"
							>
								<Link
									href={href}
									className="absolute inset-0 rounded-md"
									aria-label={`Open ${p.name}`}
								/>
								{/* col 1: name + version + project badge */}
								<div className="pointer-events-none relative z-10 flex min-w-0 items-center gap-2">
									<span className="truncate font-[510] text-[13px] text-foreground">
										{p.name}
									</span>
									<Badge
										variant="outline"
										className="h-[18px] shrink-0 border-border/70 px-1.5 py-0 font-normal text-[10px] text-muted-foreground"
									>
										v{p.version}
									</Badge>
									{p.projectId && (
										<button
											type="button"
											onClick={(e) => {
												e.preventDefault();
												e.stopPropagation();
												setProjectFilter(
													projectFilter === p.projectId ? null : p.projectId,
												);
											}}
											className={`project-badge pointer-events-auto relative z-10 flex h-[18px] cursor-pointer items-center gap-1 rounded-full border px-[7px] font-normal text-[10px] transition-colors duration-150 ease-out focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring/50 ${
												projectFilter === p.projectId
													? "border-primary/50 bg-primary/[0.08] text-foreground"
													: "border-border bg-transparent text-muted-foreground hover:border-primary/50 hover:bg-primary/[0.06] hover:text-foreground"
											}`}
											aria-pressed={projectFilter === p.projectId}
											aria-label={`Filter by project ${p.projectName ?? "Unknown project"}`}
										>
											<span
												className="inline-block h-[5px] w-[5px] shrink-0 rounded-full"
												style={{ background: tagColor(p.projectId) }}
											/>
											<span className="max-w-[80px] truncate">
												{p.projectName ?? "Unknown project"}
											</span>
										</button>
									)}
								</div>
								{/* col 2: tags */}
								<div className="pointer-events-none relative z-10 hidden items-center gap-1 sm:flex">
									{tags.length === 0 ? (
										<span className="text-[11px] text-muted-foreground/60">
											—
										</span>
									) : (
										tags
											.slice(0, 3)
											.map((t) => (
												<LabelBadge
													key={t}
													color={tagColor(t)}
													name={t}
													className="h-[18px] px-1.5 py-0 text-[10px]"
												/>
											))
									)}
									{tags.length > 3 && (
										<span className="text-[11px] text-muted-foreground">
											+{tags.length - 3}
										</span>
									)}
								</div>
								{/* col 3: updated + actions */}
								<div className="relative z-10 flex items-center gap-1">
									<span
										className="pointer-events-none text-[11px] text-muted-foreground tabular-nums"
										title={new Date(p.updatedAt).toLocaleString()}
									>
										{formatDistanceToNow(new Date(p.updatedAt), {
											addSuffix: true,
										})}
									</span>
									<TestPromptPopover
										productSlug={productSlug}
										promptSlug={p.slug}
										promptName={p.name}
										href={href}
									/>
									<DropdownMenu>
										<DropdownMenuTrigger asChild>
											<Button
												variant="ghost"
												size="icon"
												className="size-6 text-muted-foreground hover:text-foreground"
												onClick={(e) => e.stopPropagation()}
											>
												<EllipsisIcon className="size-3.5" />
											</Button>
										</DropdownMenuTrigger>
										<DropdownMenuContent align="end" className="w-40">
											<DropdownMenuItem onSelect={() => router.push(href)}>
												<PencilIcon className="size-3.5" />
												Edit
											</DropdownMenuItem>
											<DropdownMenuItem
												onSelect={() => {
													if (!product) return;
													createMut.mutate({
														productId: product.id,
														name: `${p.name} (copy)`,
													});
												}}
											>
												<CopyIcon className="size-3.5" />
												Duplicate
											</DropdownMenuItem>
											<DropdownMenuSeparator />
											<DropdownMenuItem
												className="text-destructive focus:text-destructive"
												onSelect={() => {
													if (confirm(`Delete "${p.name}"?`)) {
														deleteMut.mutate({ id: p.id });
													}
												}}
											>
												<Trash2Icon className="size-3.5" />
												Delete
											</DropdownMenuItem>
										</DropdownMenuContent>
									</DropdownMenu>
								</div>
							</li>
						);
					})}
				</ul>
			</div>
		</div>
	);
}
