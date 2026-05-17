"use client";

import { useQuery } from "@tanstack/react-query";
import { Button } from "@ui/components/ui/button";
import { Input } from "@ui/components/ui/input";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@ui/components/ui/popover";
import { cn } from "@ui/lib/utils";
import { differenceInSeconds, format } from "date-fns";
import {
	CoffeeIcon,
	PauseIcon,
	PlayIcon,
	SquareIcon,
	TargetIcon,
	XIcon,
} from "lucide-react";
import {
	useCallback,
	useEffect,
	useMemo,
	useRef,
	useState,
	useSyncExternalStore,
} from "react";
import { toast } from "sonner";
import { useShortcut } from "@/hooks/use-shortcuts";
import { trpc } from "@/utils/trpc";

/**
 * Focus session widget — codex delighter #10 (Notion/Things-style focus).
 *
 * 25-minute timer anchored bottom-right, expandable to a tray. State machine:
 *
 *   idle → running → paused → running → completed
 *                  ↘ deferred (with reason) → idle
 *                  ↘ stopped → idle
 *   completed → break (5 min) → idle
 *
 * Persistence: live session state is held in a module-level store backed by
 * localStorage so it survives soft navigation (the global mount sees the same
 * store). Completed sessions are logged to localStorage `nexus.focus.log` as a
 * compact ring (cap 100). A future commit can swap the log writer for a tRPC
 * mutation; the call-site is centralised in `logCompleted`.
 *
 * Reduced motion: the pulsing ring on the trigger button respects
 * `prefers-reduced-motion: reduce`.
 *
 * Mounted globally via the navigation layout so the widget survives across
 * route changes.
 */

const FOCUS_DEFAULT_SEC = 25 * 60;
const BREAK_SEC = 5 * 60;
const STATE_LS_KEY = "nexus.focus.state";
const LOG_LS_KEY = "nexus.focus.log";

type FocusStatus =
	| "idle"
	| "running"
	| "paused"
	| "break"
	| "completed";

interface FocusState {
	status: FocusStatus;
	durationSec: number;
	/** Epoch ms when the timer began counting in its current "running" stretch. */
	startedAt: number | null;
	/** Seconds elapsed in prior running stretches (paused/resume). */
	priorElapsed: number;
	taskId: string | null;
	taskTitle: string | null;
	openTray: boolean;
}

interface FocusLogEntry {
	completedAt: number;
	durationSec: number;
	actualSec: number;
	taskId: string | null;
	taskTitle: string | null;
	outcome: "completed" | "deferred" | "stopped";
	deferReason?: string;
}

const DEFAULT_STATE: FocusState = {
	status: "idle",
	durationSec: FOCUS_DEFAULT_SEC,
	startedAt: null,
	priorElapsed: 0,
	taskId: null,
	taskTitle: null,
	openTray: false,
};

// ──────────────────────────────────────────────────────────────────────────
// Module-level store — survives soft navigation because the widget mounts at
// the layout level. We expose a useSyncExternalStore subscription so React
// updates are explicit and SSR-safe.

let _state: FocusState = DEFAULT_STATE;
const _listeners = new Set<() => void>();

function loadFromLocalStorage(): FocusState {
	if (typeof window === "undefined") return DEFAULT_STATE;
	try {
		const raw = window.localStorage.getItem(STATE_LS_KEY);
		if (!raw) return DEFAULT_STATE;
		const parsed = JSON.parse(raw);
		return { ...DEFAULT_STATE, ...parsed };
	} catch {
		return DEFAULT_STATE;
	}
}

function saveToLocalStorage(s: FocusState) {
	if (typeof window === "undefined") return;
	try {
		window.localStorage.setItem(STATE_LS_KEY, JSON.stringify(s));
	} catch {
		// fail open
	}
}

function setState(updater: (prev: FocusState) => FocusState) {
	_state = updater(_state);
	saveToLocalStorage(_state);
	for (const l of _listeners) l();
}

function subscribe(cb: () => void): () => void {
	_listeners.add(cb);
	return () => _listeners.delete(cb);
}

function logCompleted(entry: FocusLogEntry) {
	if (typeof window === "undefined") return;
	try {
		const raw = window.localStorage.getItem(LOG_LS_KEY);
		const existing: FocusLogEntry[] = raw ? JSON.parse(raw) : [];
		const next = [entry, ...existing].slice(0, 100);
		window.localStorage.setItem(LOG_LS_KEY, JSON.stringify(next));
	} catch {
		// fail open
	}
}

// ──────────────────────────────────────────────────────────────────────────
// Public helpers — exposed for the sidebar "start focus session" hook later.

export function startFocusSession(input?: {
	taskId?: string | null;
	taskTitle?: string | null;
	durationSec?: number;
}) {
	const now = Date.now();
	setState((prev) => ({
		...prev,
		status: "running",
		durationSec: input?.durationSec ?? prev.durationSec ?? FOCUS_DEFAULT_SEC,
		startedAt: now,
		priorElapsed: 0,
		taskId: input?.taskId ?? null,
		taskTitle: input?.taskTitle ?? null,
		openTray: true,
	}));
}

export function toggleFocusTray() {
	const current = _state;
	if (current.status === "idle") {
		setState((p) => ({ ...p, openTray: !p.openTray }));
		return;
	}
	setState((p) => ({ ...p, openTray: !p.openTray }));
}

export function getFocusSnapshot(): FocusState {
	return _state;
}

// ──────────────────────────────────────────────────────────────────────────

const DEFER_REASONS = [
	"Blocked",
	"Bored",
	"Interrupted",
	"Done early",
] as const;

function fmtClock(sec: number): string {
	const s = Math.max(0, Math.floor(sec));
	const m = Math.floor(s / 60);
	const r = s % 60;
	return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

function elapsedSec(s: FocusState, nowMs: number): number {
	if (s.status !== "running" || s.startedAt == null) return s.priorElapsed;
	return s.priorElapsed + Math.floor((nowMs - s.startedAt) / 1000);
}

function remainingSec(s: FocusState, nowMs: number): number {
	return Math.max(0, s.durationSec - elapsedSec(s, nowMs));
}

function useFocusState(): FocusState {
	return useSyncExternalStore(
		subscribe,
		() => _state,
		() => DEFAULT_STATE,
	);
}

function FocusTray({
	onClose,
	state,
}: {
	state: FocusState;
	onClose: () => void;
}) {
	const [now, setNow] = useState<number>(Date.now());

	useEffect(() => {
		if (state.status !== "running") return;
		const id = window.setInterval(() => setNow(Date.now()), 1000);
		return () => window.clearInterval(id);
	}, [state.status]);

	const remaining = remainingSec(state, now);
	const elapsed = elapsedSec(state, now);

	// Auto-transition to completed when the clock hits zero.
	useEffect(() => {
		if (state.status === "running" && remaining === 0) {
			logCompleted({
				completedAt: Date.now(),
				durationSec: state.durationSec,
				actualSec: elapsed,
				taskId: state.taskId,
				taskTitle: state.taskTitle,
				outcome: "completed",
			});
			setState((p) => ({
				...p,
				status: "completed",
				priorElapsed: 0,
				startedAt: null,
			}));
			toast.success("Focus session complete", {
				description: state.taskTitle
					? `25 minutes on "${state.taskTitle}"`
					: "25 minutes of focused work",
			});
		}
	}, [
		state.status,
		state.durationSec,
		state.taskId,
		state.taskTitle,
		remaining,
		elapsed,
	]);

	// Quick-search for tasks to associate with the session.
	const [search, setSearch] = useState("");
	const tasksQuery = useQuery({
		...trpc.tasks.get.queryOptions({
			search: search || undefined,
			pageSize: 8,
		} as any),
		enabled: state.status === "idle" && search.length > 0,
	});

	const handlePause = useCallback(() => {
		setState((p) => {
			if (p.status !== "running" || p.startedAt == null) return p;
			const addElapsed = Math.floor((Date.now() - p.startedAt) / 1000);
			return {
				...p,
				status: "paused",
				priorElapsed: p.priorElapsed + addElapsed,
				startedAt: null,
			};
		});
	}, []);

	const handleResume = useCallback(() => {
		setState((p) => ({ ...p, status: "running", startedAt: Date.now() }));
	}, []);

	const handleStop = useCallback(() => {
		const final = _state;
		const actual = elapsedSec(final, Date.now());
		logCompleted({
			completedAt: Date.now(),
			durationSec: final.durationSec,
			actualSec: actual,
			taskId: final.taskId,
			taskTitle: final.taskTitle,
			outcome: "stopped",
		});
		setState(() => ({
			...DEFAULT_STATE,
			openTray: true,
		}));
	}, []);

	const [deferOpen, setDeferOpen] = useState(false);
	const [customReason, setCustomReason] = useState("");

	const handleDefer = useCallback((reason: string) => {
		const final = _state;
		const actual = elapsedSec(final, Date.now());
		logCompleted({
			completedAt: Date.now(),
			durationSec: final.durationSec,
			actualSec: actual,
			taskId: final.taskId,
			taskTitle: final.taskTitle,
			outcome: "deferred",
			deferReason: reason,
		});
		setState(() => ({
			...DEFAULT_STATE,
			openTray: false,
		}));
		toast(`Session deferred · ${reason}`);
	}, []);

	const handleStartBreak = useCallback(() => {
		setState(() => ({
			...DEFAULT_STATE,
			status: "break",
			durationSec: BREAK_SEC,
			startedAt: Date.now(),
			openTray: true,
		}));
	}, []);

	const associateTask = (taskId: string, taskTitle: string) => {
		setState((p) => ({ ...p, taskId, taskTitle }));
		setSearch("");
	};

	return (
		<div
			className="w-[300px] rounded-lg border border-border bg-popover p-3 shadow-lg"
			role="dialog"
			aria-label="Focus session"
		>
			<header className="mb-2 flex items-center gap-2">
				<TargetIcon className="size-4 text-violet-400" />
				<span className="font-[510] text-[13px]">Focus</span>
				<span className="ml-auto text-[11px] text-muted-foreground capitalize">
					{state.status}
				</span>
				<button
					type="button"
					onClick={onClose}
					aria-label="Close focus tray"
					className="rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
				>
					<XIcon className="size-3.5" />
				</button>
			</header>

			<div className="flex items-baseline gap-2">
				<span
					className={cn(
						"font-mono font-[510] text-[28px] tabular-nums",
						state.status === "running" && "text-violet-400",
						state.status === "paused" && "text-muted-foreground",
						state.status === "break" && "text-emerald-500",
					)}
				>
					{fmtClock(remaining)}
				</span>
				<span className="text-[11px] text-muted-foreground">
					/ {fmtClock(state.durationSec)}
				</span>
			</div>

			{state.taskTitle && (
				<p className="mt-1 line-clamp-2 text-[12px] text-muted-foreground">
					on “{state.taskTitle}”
				</p>
			)}

			{state.status === "idle" && (
				<div className="mt-3 space-y-2">
					<Input
						value={search}
						onChange={(e) => setSearch(e.target.value)}
						placeholder="Search a task to focus on (optional)"
						className="h-7 text-[12px]"
					/>
					{search.length > 0 && (
						<ul className="max-h-32 space-y-0.5 overflow-y-auto rounded border border-border/50">
							{((tasksQuery.data as any)?.data ?? []).map((t: any) => (
								<li key={t.id}>
									<button
										type="button"
										className="w-full truncate px-2 py-1 text-left text-[12px] hover:bg-accent"
										onClick={() => associateTask(t.id, t.title)}
									>
										{t.title}
									</button>
								</li>
							))}
						</ul>
					)}
					<Button
						size="sm"
						className="h-7 w-full text-[12px]"
						onClick={() =>
							startFocusSession({
								taskId: state.taskId,
								taskTitle: state.taskTitle,
								durationSec: FOCUS_DEFAULT_SEC,
							})
						}
					>
						<PlayIcon className="mr-1 size-3" />
						Start 25-minute session
					</Button>
				</div>
			)}

			{state.status === "running" && (
				<div className="mt-3 flex flex-wrap gap-1.5">
					<Button
						size="sm"
						variant="outline"
						className="h-7 px-2 text-[12px]"
						onClick={handlePause}
					>
						<PauseIcon className="mr-1 size-3" />
						Pause
					</Button>
					<Button
						size="sm"
						variant="outline"
						className="h-7 px-2 text-[12px]"
						onClick={handleStop}
					>
						<SquareIcon className="mr-1 size-3" />
						Stop
					</Button>
					<Popover open={deferOpen} onOpenChange={setDeferOpen}>
						<PopoverTrigger asChild>
							<Button
								size="sm"
								variant="outline"
								className="h-7 px-2 text-[12px]"
							>
								Defer
							</Button>
						</PopoverTrigger>
						<PopoverContent
							side="top"
							align="end"
							className="w-[220px] p-2"
						>
							<p className="px-1 pb-1 text-[11px] text-muted-foreground">
								Why are you stopping?
							</p>
							<div className="flex flex-wrap gap-1">
								{DEFER_REASONS.map((r) => (
									<button
										key={r}
										type="button"
										onClick={() => {
											setDeferOpen(false);
											handleDefer(r);
										}}
										className="rounded border border-border bg-background px-1.5 py-0.5 text-[11px] hover:bg-accent"
									>
										{r}
									</button>
								))}
							</div>
							<form
								className="mt-2 flex items-center gap-1"
								onSubmit={(e) => {
									e.preventDefault();
									if (!customReason.trim()) return;
									setDeferOpen(false);
									handleDefer(customReason.trim());
									setCustomReason("");
								}}
							>
								<Input
									value={customReason}
									onChange={(e) => setCustomReason(e.target.value)}
									placeholder="Custom reason"
									className="h-6 text-[11px]"
								/>
								<Button
									type="submit"
									size="sm"
									variant="ghost"
									className="h-6 px-1.5 text-[11px]"
								>
									Save
								</Button>
							</form>
						</PopoverContent>
					</Popover>
				</div>
			)}

			{state.status === "paused" && (
				<div className="mt-3 flex flex-wrap gap-1.5">
					<Button
						size="sm"
						className="h-7 px-2 text-[12px]"
						onClick={handleResume}
					>
						<PlayIcon className="mr-1 size-3" />
						Resume
					</Button>
					<Button
						size="sm"
						variant="outline"
						className="h-7 px-2 text-[12px]"
						onClick={handleStop}
					>
						Stop
					</Button>
				</div>
			)}

			{state.status === "completed" && (
				<div className="mt-3 flex flex-wrap gap-1.5">
					<Button
						size="sm"
						className="h-7 px-2 text-[12px]"
						onClick={handleStartBreak}
					>
						<CoffeeIcon className="mr-1 size-3" />
						Start 5-min break
					</Button>
					<Button
						size="sm"
						variant="ghost"
						className="h-7 px-2 text-[12px]"
						onClick={() =>
							setState(() => ({ ...DEFAULT_STATE, openTray: true }))
						}
					>
						Done
					</Button>
				</div>
			)}

			{state.status === "break" && (
				<div className="mt-3 flex flex-wrap gap-1.5">
					<Button
						size="sm"
						variant="outline"
						className="h-7 px-2 text-[12px]"
						onClick={() => setState(() => DEFAULT_STATE)}
					>
						End break
					</Button>
				</div>
			)}
		</div>
	);
}

export function FocusSession() {
	const hydrated = useRef(false);
	const state = useFocusState();

	// Hydrate from localStorage on first mount so the widget survives a hard
	// refresh mid-session.
	useEffect(() => {
		if (hydrated.current) return;
		hydrated.current = true;
		const loaded = loadFromLocalStorage();
		setState(() => loaded);
	}, []);

	// Cmd+Shift+F toggles the tray
	useShortcut("focus.toggle", () => toggleFocusTray());

	const handleTrigger = useCallback(() => toggleFocusTray(), []);
	const handleClose = useCallback(
		() => setState((p) => ({ ...p, openTray: false })),
		[],
	);

	// Compact pill shown when running and tray is closed.
	const [now, setNow] = useState<number>(Date.now());
	useEffect(() => {
		if (state.status !== "running" && state.status !== "break") return;
		const id = window.setInterval(() => setNow(Date.now()), 1000);
		return () => window.clearInterval(id);
	}, [state.status]);

	const remaining = remainingSec(state, now);
	const isActive =
		state.status === "running" ||
		state.status === "paused" ||
		state.status === "break";

	return (
		<div className="pointer-events-none fixed right-4 bottom-4 z-40 flex flex-col items-end gap-2">
			{state.openTray && (
				<div className="pointer-events-auto">
					<FocusTray state={state} onClose={handleClose} />
				</div>
			)}
			<button
				type="button"
				onClick={handleTrigger}
				aria-label={
					isActive ? "Focus session in progress" : "Open focus session"
				}
				className={cn(
					"pointer-events-auto inline-flex h-9 items-center gap-1.5 rounded-full border border-border bg-background px-2.5 shadow-md transition-colors",
					isActive
						? "border-violet-500/60 text-violet-400 motion-safe:animate-pulse"
						: "text-muted-foreground hover:bg-accent",
				)}
			>
				<TargetIcon className="size-3.5" />
				<span className="font-mono text-[12px] tabular-nums">
					{isActive ? fmtClock(remaining) : "Focus"}
				</span>
			</button>
		</div>
	);
}
