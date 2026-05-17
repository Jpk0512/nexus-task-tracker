"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@ui/components/ui/button";
import { cn } from "@ui/lib/utils";
import { addDays, format, nextDay, parse } from "date-fns";
import { CommandIcon, PlusIcon, SparklesIcon } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { useUser } from "@/components/user-provider";
import { useProjects } from "@/hooks/use-data";
import { useShortcut } from "@/hooks/use-shortcuts";
import { trpc } from "@/utils/trpc";

/**
 * Quick-capture bar (codex delighter #1, Raycast-style).
 *
 * One input on the Home page that turns natural-language strings into tasks
 * without opening a modal. The parser is intentionally simple — regex + a few
 * date keywords. Power users open the full Create Task dialog from the +
 * button if they need rich fields (description, labels, assignee picker).
 *
 * Supported tokens:
 *   - @project-slug-or-name     → project assignment (substring match)
 *   - #label                    → label (raw string for now)
 *   - !priority                 → priority (urgent | high | medium | low)
 *   - :date                     → due date (today / tomorrow / next-mon /
 *                                 mon / 1/15 / 2026-01-15)
 *   - <plain text>              → task title (everything left over)
 *
 * Keyboard:
 *   - Cmd+N (or Ctrl+N) → focus the bar from anywhere on Home
 *   - Esc                → blur + clear the bar
 *   - Enter              → create task, optimistic
 */

type Priority = "low" | "medium" | "high" | "urgent";

type ParsedCapture = {
	title: string;
	projectQuery: string | null;
	labels: string[];
	priority: Priority | null;
	dueDate: Date | null;
};

const PRIORITY_VALUES: Priority[] = ["urgent", "high", "medium", "low"];
const PRIORITY_SET = new Set<string>(PRIORITY_VALUES);

const DAY_NAMES: Record<string, 0 | 1 | 2 | 3 | 4 | 5 | 6> = {
	sun: 0,
	sunday: 0,
	mon: 1,
	monday: 1,
	tue: 2,
	tues: 2,
	tuesday: 2,
	wed: 3,
	wednesday: 3,
	thu: 4,
	thurs: 4,
	thursday: 4,
	fri: 5,
	friday: 5,
	sat: 6,
	saturday: 6,
};

/**
 * Resolve a date token like "today" / "tomorrow" / "next-mon" / "1/15" /
 * "2026-01-15". Returns null if we can't make sense of it — the caller leaves
 * the token in the title rather than guessing wrong.
 */
function parseDateToken(raw: string, now: Date): Date | null {
	const cleaned = raw.toLowerCase().replace(/^next-/, "next ").trim();
	if (cleaned === "today") return now;
	if (cleaned === "tomorrow") return addDays(now, 1);
	const nextMatch = cleaned.match(/^next\s+(\w+)$/);
	if (nextMatch) {
		const day = DAY_NAMES[nextMatch[1] as keyof typeof DAY_NAMES];
		if (day !== undefined) return nextDay(now, day);
	}
	const dayOnly = DAY_NAMES[cleaned as keyof typeof DAY_NAMES];
	if (dayOnly !== undefined) return nextDay(now, dayOnly);
	// M/D — defaults to current year, rolls forward if already past.
	const slashMatch = raw.match(/^(\d{1,2})\/(\d{1,2})$/);
	if (slashMatch) {
		const month = Number.parseInt(slashMatch[1], 10);
		const day = Number.parseInt(slashMatch[2], 10);
		const year = now.getFullYear();
		const candidate = new Date(year, month - 1, day);
		if (candidate.getTime() < now.getTime() - 1000 * 60 * 60 * 24) {
			return new Date(year + 1, month - 1, day);
		}
		return candidate;
	}
	// YYYY-MM-DD
	const isoMatch = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
	if (isoMatch) {
		const parsed = parse(raw, "yyyy-MM-dd", now);
		if (!Number.isNaN(parsed.getTime())) return parsed;
	}
	return null;
}

/**
 * Tokenize the input string. The implementation walks once, peeling off
 * recognized tokens (each must be a separate whitespace-delimited word).
 * Everything else stacks into the title — punctuation preserved.
 */
export function parseQuickCapture(input: string, now = new Date()): ParsedCapture {
	const out: ParsedCapture = {
		title: "",
		projectQuery: null,
		labels: [],
		priority: null,
		dueDate: null,
	};
	const titleWords: string[] = [];
	const tokens = input.split(/\s+/).filter(Boolean);
	for (const tok of tokens) {
		if (tok.startsWith("@") && tok.length > 1) {
			out.projectQuery = tok.slice(1);
			continue;
		}
		if (tok.startsWith("#") && tok.length > 1) {
			out.labels.push(tok.slice(1));
			continue;
		}
		if (tok.startsWith("!") && tok.length > 1) {
			const p = tok.slice(1).toLowerCase();
			if (PRIORITY_SET.has(p)) {
				out.priority = p as Priority;
				continue;
			}
		}
		if (tok.startsWith(":") && tok.length > 1) {
			const date = parseDateToken(tok.slice(1), now);
			if (date) {
				out.dueDate = date;
				continue;
			}
		}
		titleWords.push(tok);
	}
	out.title = titleWords.join(" ").trim();
	return out;
}

export const QuickCapture = () => {
	const user = useUser();
	const qc = useQueryClient();
	const inputRef = useRef<HTMLInputElement | null>(null);
	const [value, setValue] = useState("");

	const { data: projectsData } = useProjects();
	const parsed = useMemo(() => parseQuickCapture(value), [value]);

	// Resolve the project the @token refers to. Substring match on
	// name / slug / prefix. Falls back to the user's first project so capture
	// always succeeds even without a token.
	const project = useMemo(() => {
		// biome-ignore lint/suspicious/noExplicitAny: tRPC return is unknown
		const list = (((projectsData as any)?.data ?? []) as Array<any>).filter(
			(p) => !p.archived,
		);
		if (!list.length) return null;
		if (!parsed.projectQuery) return list[0];
		const q = parsed.projectQuery.toLowerCase();
		return (
			list.find(
				(p) =>
					(p.slug && p.slug.toLowerCase() === q) ||
					(p.prefix && p.prefix.toLowerCase() === q) ||
					(p.name && p.name.toLowerCase().includes(q)),
			) ?? list[0]
		);
	}, [projectsData, parsed.projectQuery]);

	// Resolve the default "to_do" status for that project so the create call
	// has a non-empty statusId (the schema requires min: 1).
	const { data: todoStatus } = useQuery(
		trpc.statuses.get.queryOptions(
			// biome-ignore lint/suspicious/noExplicitAny: tRPC input shape evolves
			{
				type: ["to_do"],
				pageSize: 1,
				projectId: project?.id ?? null,
			} as any,
			{
				// biome-ignore lint/suspicious/noExplicitAny: select narrows the type
				select: (data: any) => data?.data?.[0] as { id: string } | undefined,
				refetchOnWindowFocus: false,
				enabled: !!project?.id,
			},
		),
	);

	const createMut = useMutation(
		trpc.tasks.create.mutationOptions({
			onSuccess: () => {
				toast.success("Task created", { id: "quick-capture" });
				qc.invalidateQueries({ queryKey: [["tasks"]] });
			},
			onError: (err: { message?: string } | unknown) => {
				const message =
					typeof err === "object" && err && "message" in err
						? String((err as { message: unknown }).message)
						: "Could not create task";
				toast.error(message, { id: "quick-capture" });
			},
		}),
	);

	// Cmd+N focuses the bar from anywhere on Home. Uses the existing shortcut
	// registry so the binding is remappable via Settings → Shortcuts.
	useShortcut(
		"capture.focus",
		(e) => {
			e.preventDefault?.();
			inputRef.current?.focus();
		},
		{ enabled: true },
	);

	const submit = () => {
		if (!parsed.title) {
			toast.error("Type a title to create a task.", { id: "quick-capture" });
			return;
		}
		if (!project) {
			toast.error("No project available. Create a project first.", {
				id: "quick-capture",
			});
			return;
		}
		// biome-ignore lint/suspicious/noExplicitAny: tRPC select escape hatch — see queryOptions above
		const resolvedStatus = todoStatus as { id?: string } | undefined;
		if (!resolvedStatus?.id) {
			toast.error("No 'to-do' status configured for this project.", {
				id: "quick-capture",
			});
			return;
		}
		toast.loading("Creating task…", { id: "quick-capture" });
		createMut.mutate({
			title: parsed.title,
			projectId: project.id,
			statusId: resolvedStatus.id,
			assigneeId: user?.id || null,
			priority: parsed.priority ?? "low",
			labels: parsed.labels.length ? parsed.labels : undefined,
			dueDate: parsed.dueDate ? parsed.dueDate.toISOString() : null,
			// biome-ignore lint/suspicious/noExplicitAny: tRPC input shape includes mentions etc.
		} as any);
		setValue("");
	};

	const handleKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
		if (e.key === "Enter") {
			e.preventDefault();
			submit();
		}
		if (e.key === "Escape") {
			e.preventDefault();
			setValue("");
			inputRef.current?.blur();
		}
	};

	// Build a tiny "chips preview" so the user sees how their string parsed —
	// drops below the input as soon as any token resolves. Cheap reassurance
	// that the parser saw what they meant.
	const previewChips: Array<{ label: string; tone: string }> = [];
	if (project) previewChips.push({ label: `@${project.name}`, tone: "violet" });
	if (parsed.priority)
		previewChips.push({ label: `!${parsed.priority}`, tone: "amber" });
	if (parsed.dueDate)
		previewChips.push({
			label: `:${format(parsed.dueDate, "MMM d")}`,
			tone: "sky",
		});
	for (const l of parsed.labels)
		previewChips.push({ label: `#${l}`, tone: "emerald" });

	return (
		<section
			className={cn(
				"rounded-[12px] border border-border bg-card px-3 py-2.5",
				"focus-within:border-[color:var(--brand,#6e7bff)] focus-within:ring-2 focus-within:ring-[color:var(--brand,#6e7bff)]/30",
			)}
		>
			<div className="flex items-center gap-2">
				<SparklesIcon className="size-4 shrink-0 text-violet-500" />
				<input
					ref={inputRef}
					type="text"
					value={value}
					onChange={(e) => setValue(e.target.value)}
					onKeyDown={handleKey}
					placeholder="What's on your mind?  @project  #label  !urgent  :tomorrow"
					className={cn(
						"min-w-0 flex-1 bg-transparent text-[13px] text-foreground outline-none placeholder:text-muted-foreground",
					)}
				/>
				<span className="hidden items-center gap-0.5 text-[11px] text-muted-foreground sm:flex">
					<CommandIcon className="size-3" />
					<span>N</span>
				</span>
				<Button
					size="sm"
					variant="ghost"
					onClick={submit}
					disabled={!parsed.title || createMut.isPending}
					className="gap-1 text-[12px]"
				>
					<PlusIcon className="size-3.5" />
					Add
				</Button>
			</div>
			{previewChips.length > 0 ? (
				<div className="mt-1.5 flex flex-wrap items-center gap-1 pl-6">
					{previewChips.map((c) => (
						<span
							key={`${c.tone}-${c.label}`}
							className={cn(
								"inline-flex h-[18px] items-center rounded-full px-1.5 font-[510] text-[10px] uppercase tracking-wide",
								c.tone === "violet" && "bg-violet-500/15 text-violet-500",
								c.tone === "amber" && "bg-amber-500/15 text-amber-500",
								c.tone === "sky" && "bg-sky-500/15 text-sky-500",
								c.tone === "emerald" && "bg-emerald-500/15 text-emerald-500",
							)}
						>
							{c.label}
						</span>
					))}
				</div>
			) : null}
		</section>
	);
};

