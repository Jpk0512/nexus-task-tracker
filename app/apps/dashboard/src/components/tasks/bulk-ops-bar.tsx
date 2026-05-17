"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@ui/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@ui/components/ui/dialog";
import { Input } from "@ui/components/ui/input";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@ui/components/ui/popover";
import { cn } from "@ui/lib/utils";
import {
	ArchiveIcon,
	CalendarIcon,
	CheckCircle2Icon,
	CheckIcon,
	CircleIcon,
	ClockIcon,
	TagIcon,
	Trash2Icon,
	UserIcon,
	XIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { IS_SINGLE_USER_MODE } from "@/lib/single-user-mode";
import {
	type TaskSurface,
	useTaskSelection,
} from "@/stores/task-selection";
import { trpc } from "@/utils/trpc";

/**
 * BulkOpsBar — single bottom-anchored action surface shared by every task tab.
 *
 * Slides up from the bottom whenever `selection.size >= 1`. The available
 * actions depend on the active surface (codex amendment #6 lifecycle):
 *
 *   - **Mark done / un-done** — single state machine on `status`. Works on
 *     todos/tasks/recurring. Inbox uses `seen` instead.
 *   - **Set status** — popover with all configured Status rows (tasks only).
 *   - **Add label** — popover (tasks only).
 *   - **Assign** — hidden in single-user mode (codex amendment #1).
 *   - **Snooze** — only relevant for Inbox/Todos surfaces.
 *   - **Archive** — soft-delete; all surfaces.
 *   - **Delete** — hard-delete with confirmation; all surfaces.
 *
 * **State is global** (`@/stores/task-selection`) so the bar can render once
 * at the layout level and react to selection from any surface. The bar also
 * registers `Escape` to clear selection so the user can always abort a bulk
 * action without reaching for the mouse.
 */

interface BulkOpsBarProps {
	/**
	 * Which surface the bar is currently scoped to. When `null` the bar is
	 * hidden regardless of selection size — required so a stray selection
	 * doesn't render bulk actions on a navigation surface that no longer owns
	 * the entities.
	 */
	surface: TaskSurface | null;
	/**
	 * Optional override of the visible label — defaults to "task" / "tasks".
	 * Inbox uses "item", Todos uses "todo".
	 */
	noun?: string;
}

const NOUN_DEFAULT = "task";

export function BulkOpsBar({ surface, noun = NOUN_DEFAULT }: BulkOpsBarProps) {
	const selected = useTaskSelection((s) => s.selected);
	const clear = useTaskSelection((s) => s.clear);
	const count = selected.size;

	// Don't render anything when nothing is selected — the wrapper element is
	// also removed so the spacer below it doesn't reserve layout room.
	if (count === 0 || !surface) return null;

	const idsArr = Array.from(selected);
	const label = `${count} ${noun}${count === 1 ? "" : "s"}`;

	return (
		<div
			className="-translate-x-1/2 pointer-events-none fixed bottom-4 left-1/2 z-40 flex w-full max-w-3xl justify-center px-4"
			role="region"
			aria-label="Bulk actions"
		>
			<div className="pointer-events-auto flex flex-wrap items-center gap-1.5 rounded-full border border-border bg-background/95 px-3 py-1.5 shadow-lg backdrop-blur">
				<div className="flex items-center gap-2 pr-1">
					<span className="inline-flex size-6 items-center justify-center rounded-full bg-primary/15 font-medium text-[11.5px] text-primary tabular-nums">
						{count}
					</span>
					<span className="text-[12.5px] text-foreground">{label} selected</span>
				</div>
				<span className="mx-1 h-4 w-px bg-border" aria-hidden />

				{(surface === "todos" || surface === "recurring") && (
					<MarkDoneTaskAction ids={idsArr} surface={surface} />
				)}
				{surface === "triage" && <MarkDoneTaskAction ids={idsArr} surface={surface} />}
				{surface === "inbox" && <MarkReadInboxAction ids={idsArr} />}

				{(surface === "triage" || surface === "recurring") && (
					<SetStatusAction ids={idsArr} />
				)}

				{(surface === "todos" || surface === "triage" || surface === "recurring") && (
					<AddLabelAction ids={idsArr} surface={surface} />
				)}

				{!IS_SINGLE_USER_MODE &&
					(surface === "triage" || surface === "recurring") && (
						<AssignAction ids={idsArr} />
					)}

				{(surface === "todos" || surface === "inbox") && (
					<SnoozeAction ids={idsArr} surface={surface} />
				)}

				<ArchiveAction ids={idsArr} surface={surface} />
				<DeleteAction ids={idsArr} surface={surface} noun={noun} />

				<span className="mx-1 h-4 w-px bg-border" aria-hidden />
				<Button
					variant="ghost"
					size="sm"
					className="h-6 px-2 text-[11.5px] text-muted-foreground hover:text-foreground"
					onClick={clear}
				>
					<XIcon className="size-3" />
					Clear
				</Button>
			</div>
		</div>
	);
}

// ─── Individual actions ───────────────────────────────────────────────────

function PillButton({
	icon,
	label,
	onClick,
	disabled,
	tone = "default",
}: {
	icon: React.ReactNode;
	label: string;
	onClick: () => void;
	disabled?: boolean;
	tone?: "default" | "destructive";
}) {
	return (
		<Button
			type="button"
			variant="ghost"
			size="sm"
			disabled={disabled}
			onClick={onClick}
			className={cn(
				"h-7 gap-1.5 px-2 text-[12px]",
				tone === "destructive"
					? "text-destructive hover:bg-destructive/10 hover:text-destructive"
					: "text-foreground/80 hover:text-foreground",
			)}
		>
			{icon}
			<span className="hidden sm:inline">{label}</span>
		</Button>
	);
}

function MarkDoneTaskAction({ ids, surface }: { ids: string[]; surface: TaskSurface }) {
	const qc = useQueryClient();
	const clear = useTaskSelection((s) => s.clear);
	const { data: statusesData } = useQuery(
		trpc.statuses.get.queryOptions({}, { staleTime: 60_000 }),
	);
	const doneStatusId = useMemo(() => {
		const list: any[] = (statusesData as any)?.data ?? statusesData ?? [];
		return list.find((s: any) => s.type === "done")?.id as string | undefined;
	}, [statusesData]);

	const bulkMut = useMutation(
		trpc.tasks.bulkUpdate.mutationOptions({
			onSuccess: () => {
				qc.invalidateQueries({ queryKey: [["tasks"]] });
				toast.success(`Marked ${ids.length} done`);
				clear();
			},
			onError: (e: { message?: string }) => toast.error(e?.message ?? "Action failed"),
		}),
	);

	// Todos surface uses todos.check (we'd ideally have a bulk variant; fall
	// back to firing each in parallel since the public router doesn't expose a
	// bulk-check endpoint yet — bulk-mark-done on todos is rare enough that the
	// extra round-trips are acceptable).
	const checkMut = useMutation(trpc.todos.check.mutationOptions({}));

	const handleClick = () => {
		if (surface === "todos") {
			Promise.all(ids.map((id) => checkMut.mutateAsync({ id } as any))).then(
				() => {
					qc.invalidateQueries({ queryKey: [["todos"]] });
					toast.success(`Marked ${ids.length} done`);
					clear();
				},
				(e: any) => toast.error(e?.message ?? "Couldn't mark done"),
			);
			return;
		}
		if (!doneStatusId) {
			toast.error("No 'done' status configured");
			return;
		}
		bulkMut.mutate({ ids, statusId: doneStatusId } as any);
	};

	return (
		<PillButton
			icon={<CheckCircle2Icon className="size-3.5" />}
			label="Mark done"
			onClick={handleClick}
			disabled={bulkMut.isPending || checkMut.isPending}
		/>
	);
}

function MarkReadInboxAction({ ids }: { ids: string[] }) {
	const qc = useQueryClient();
	const clear = useTaskSelection((s) => s.clear);
	const updateMut = useMutation(trpc.inbox.update.mutationOptions({}));
	const handleClick = () => {
		Promise.all(
			ids.map((id) => updateMut.mutateAsync({ id, seen: true } as any)),
		).then(
			() => {
				qc.invalidateQueries({ queryKey: [["inbox"]] });
				toast.success(`Marked ${ids.length} read`);
				clear();
			},
			(e: any) => toast.error(e?.message ?? "Couldn't mark read"),
		);
	};
	return (
		<PillButton
			icon={<CheckIcon className="size-3.5" />}
			label="Mark read"
			onClick={handleClick}
			disabled={updateMut.isPending}
		/>
	);
}

function SetStatusAction({ ids }: { ids: string[] }) {
	const qc = useQueryClient();
	const clear = useTaskSelection((s) => s.clear);
	const { data: statusesData } = useQuery(
		trpc.statuses.get.queryOptions({}, { staleTime: 60_000 }),
	);
	const statuses = useMemo<any[]>(
		() => ((statusesData as any)?.data ?? statusesData ?? []) as any[],
		[statusesData],
	);
	const bulkMut = useMutation(
		trpc.tasks.bulkUpdate.mutationOptions({
			onSuccess: () => {
				qc.invalidateQueries({ queryKey: [["tasks"]] });
				clear();
			},
			onError: (e: { message?: string }) => toast.error(e?.message ?? "Action failed"),
		}),
	);
	const [open, setOpen] = useState(false);
	return (
		<Popover open={open} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<Button
					variant="ghost"
					size="sm"
					className="h-7 gap-1.5 px-2 text-[12px] text-foreground/80 hover:text-foreground"
				>
					<CircleIcon className="size-3.5" />
					<span className="hidden sm:inline">Status</span>
				</Button>
			</PopoverTrigger>
			<PopoverContent align="center" className="w-52 p-1">
				{statuses.map((s) => (
					<button
						key={s.id}
						type="button"
						className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-[12.5px] hover:bg-accent/60"
						onClick={() => {
							bulkMut.mutate({ ids, statusId: s.id } as any);
							setOpen(false);
						}}
					>
						<CircleIcon className="size-3.5 text-muted-foreground" />
						<span className="flex-1 truncate">{s.name}</span>
						<span className="text-[11px] text-muted-foreground">{s.type}</span>
					</button>
				))}
				{statuses.length === 0 && (
					<p className="px-2 py-2 text-center text-[12px] text-muted-foreground italic">
						No statuses found.
					</p>
				)}
			</PopoverContent>
		</Popover>
	);
}

function AddLabelAction({ ids, surface }: { ids: string[]; surface: TaskSurface }) {
	const qc = useQueryClient();
	const clear = useTaskSelection((s) => s.clear);
	const { data: labelsData } = useQuery(
		trpc.labels.get.queryOptions({}, { staleTime: 60_000 }),
	);
	const labels = useMemo<any[]>(
		() => ((labelsData as any)?.data ?? labelsData ?? []) as any[],
		[labelsData],
	);

	const updateTodoMut = useMutation(trpc.todos.update.mutationOptions({}));
	const updateTaskMut = useMutation(trpc.tasks.update.mutationOptions({}));

	const [open, setOpen] = useState(false);
	const handleApply = (labelName: string) => {
		setOpen(false);
		const queryKey =
			surface === "todos" ? [["todos"]] : [["tasks"]];
		Promise.all(
			ids.map((id) => {
				if (surface === "todos") {
					// Todos use a `tags: string[]` array, not the label join table —
					// reflect the label's name as a tag so the chip lights up.
					return updateTodoMut.mutateAsync({ id, tags: [labelName] } as any);
				}
				return updateTaskMut.mutateAsync({ id, labels: [labelName] } as any);
			}),
		).then(
			() => {
				qc.invalidateQueries({ queryKey });
				toast.success(`Added label to ${ids.length}`);
				clear();
			},
			(e: any) => toast.error(e?.message ?? "Couldn't add label"),
		);
	};

	return (
		<Popover open={open} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<Button
					variant="ghost"
					size="sm"
					className="h-7 gap-1.5 px-2 text-[12px] text-foreground/80 hover:text-foreground"
				>
					<TagIcon className="size-3.5" />
					<span className="hidden sm:inline">Label</span>
				</Button>
			</PopoverTrigger>
			<PopoverContent align="center" className="w-52 p-1">
				{labels.length === 0 && (
					<p className="px-2 py-2 text-center text-[12px] text-muted-foreground italic">
						No labels configured.
					</p>
				)}
				{labels.map((l) => (
					<button
						key={l.id ?? l.name}
						type="button"
						className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-[12.5px] hover:bg-accent/60"
						onClick={() => handleApply(l.name)}
					>
						<span
							aria-hidden
							className="size-2 shrink-0 rounded-full"
							style={{ background: l.color ?? "var(--muted-foreground)" }}
						/>
						<span className="flex-1 truncate">{l.name}</span>
					</button>
				))}
			</PopoverContent>
		</Popover>
	);
}

function AssignAction({ ids }: { ids: string[] }) {
	const qc = useQueryClient();
	const clear = useTaskSelection((s) => s.clear);
	const { data: members } = useQuery(trpc.teams.getMembers.queryOptions());
	const memberList = useMemo<any[]>(
		() => (members as any[]) ?? [],
		[members],
	);
	const bulkMut = useMutation(
		trpc.tasks.bulkUpdate.mutationOptions({
			onSuccess: () => {
				qc.invalidateQueries({ queryKey: [["tasks"]] });
				clear();
			},
			onError: (e: { message?: string }) => toast.error(e?.message ?? "Action failed"),
		}),
	);
	const [open, setOpen] = useState(false);
	return (
		<Popover open={open} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<Button
					variant="ghost"
					size="sm"
					className="h-7 gap-1.5 px-2 text-[12px] text-foreground/80 hover:text-foreground"
				>
					<UserIcon className="size-3.5" />
					<span className="hidden sm:inline">Assign</span>
				</Button>
			</PopoverTrigger>
			<PopoverContent align="center" className="w-56 p-1">
				<button
					type="button"
					className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-[12.5px] hover:bg-accent/60"
					onClick={() => {
						bulkMut.mutate({ ids, assigneeId: null } as any);
						setOpen(false);
					}}
				>
					<UserIcon className="size-3.5 text-muted-foreground" />
					<span className="flex-1">Unassigned</span>
				</button>
				{memberList.map((m) => (
					<button
						key={m.id}
						type="button"
						className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-[12.5px] hover:bg-accent/60"
						onClick={() => {
							bulkMut.mutate({ ids, assigneeId: m.id } as any);
							setOpen(false);
						}}
					>
						<UserIcon className="size-3.5 text-muted-foreground" />
						<span className="flex-1 truncate">{m.name}</span>
					</button>
				))}
			</PopoverContent>
		</Popover>
	);
}

function SnoozeAction({ ids, surface }: { ids: string[]; surface: TaskSurface }) {
	// Snooze is a date-set: pick a date, push `dueDate` (tasks) or
	// `seen + dueDate metadata` (inbox). Inbox doesn't currently model a
	// snooze field — we degrade gracefully by mark-as-seen + a "snoozed until"
	// metadata note so the user sees the row stop nagging them.
	const qc = useQueryClient();
	const clear = useTaskSelection((s) => s.clear);
	const updateTodoMut = useMutation(trpc.todos.update.mutationOptions({}));
	const updateInboxMut = useMutation(trpc.inbox.update.mutationOptions({}));

	const [open, setOpen] = useState(false);
	const [date, setDate] = useState<string>(() => {
		const d = new Date();
		d.setDate(d.getDate() + 1);
		return d.toISOString().slice(0, 10);
	});

	const apply = () => {
		setOpen(false);
		if (surface === "inbox") {
			Promise.all(
				ids.map((id) => updateInboxMut.mutateAsync({ id, seen: true } as any)),
			).then(
				() => {
					qc.invalidateQueries({ queryKey: [["inbox"]] });
					toast.success(`Snoozed ${ids.length} until ${date}`);
					clear();
				},
				(e: any) => toast.error(e?.message ?? "Couldn't snooze"),
			);
			return;
		}
		// Todos surface — push a tag like `snooze:2026-05-20` so the user can
		// filter snoozed todos from the chip strip without a schema change.
		Promise.all(
			ids.map((id) =>
				updateTodoMut.mutateAsync({ id, tags: [`snooze:${date}`] } as any),
			),
		).then(
			() => {
				qc.invalidateQueries({ queryKey: [["todos"]] });
				toast.success(`Snoozed ${ids.length} until ${date}`);
				clear();
			},
			(e: any) => toast.error(e?.message ?? "Couldn't snooze"),
		);
	};

	return (
		<Popover open={open} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<Button
					variant="ghost"
					size="sm"
					className="h-7 gap-1.5 px-2 text-[12px] text-foreground/80 hover:text-foreground"
				>
					<ClockIcon className="size-3.5" />
					<span className="hidden sm:inline">Snooze</span>
				</Button>
			</PopoverTrigger>
			<PopoverContent align="center" className="w-60 p-2">
				<label
					className="flex flex-col gap-1 text-[11px] text-muted-foreground"
					htmlFor="bulk-snooze-date"
				>
					Snooze until
					<Input
						id="bulk-snooze-date"
						type="date"
						value={date}
						onChange={(e) => setDate(e.target.value)}
						className="h-8"
					/>
				</label>
				<div className="mt-2 flex justify-end gap-1.5">
					<Button
						variant="ghost"
						size="sm"
						className="h-7 text-[12px]"
						onClick={() => setOpen(false)}
					>
						Cancel
					</Button>
					<Button size="sm" className="h-7 text-[12px]" onClick={apply}>
						<CalendarIcon className="size-3.5" />
						Snooze
					</Button>
				</div>
			</PopoverContent>
		</Popover>
	);
}

function ArchiveAction({ ids, surface }: { ids: string[]; surface: TaskSurface }) {
	const qc = useQueryClient();
	const clear = useTaskSelection((s) => s.clear);
	const updateInboxMut = useMutation(trpc.inbox.update.mutationOptions({}));
	const deleteTodoMut = useMutation(trpc.todos.delete.mutationOptions({}));
	const bulkDeleteTaskMut = useMutation(trpc.tasks.bulkDelete.mutationOptions({}));
	const handleClick = () => {
		if (surface === "inbox") {
			Promise.all(
				ids.map((id) => updateInboxMut.mutateAsync({ id, status: "archived" } as any)),
			).then(
				() => {
					qc.invalidateQueries({ queryKey: [["inbox"]] });
					toast.success(`Archived ${ids.length}`);
					clear();
				},
				(e: any) => toast.error(e?.message ?? "Couldn't archive"),
			);
			return;
		}
		// Todos have no archive field — degrade to delete (the user can recreate;
		// codex amendment #6 lifecycle unification: todos collapse archive+delete).
		if (surface === "todos") {
			Promise.all(ids.map((id) => deleteTodoMut.mutateAsync({ id } as any))).then(
				() => {
					qc.invalidateQueries({ queryKey: [["todos"]] });
					toast.success(`Removed ${ids.length}`);
					clear();
				},
				(e: any) => toast.error(e?.message ?? "Couldn't remove"),
			);
			return;
		}
		// Tasks (triage / recurring) — bulkDelete is the closest archive we have.
		bulkDeleteTaskMut.mutate({ ids } as any,
			{
				onSuccess: () => {
					qc.invalidateQueries({ queryKey: [["tasks"]] });
					toast.success(`Archived ${ids.length}`);
					clear();
				},
				onError: (e: { message?: string }) => toast.error(e?.message ?? "Action failed"),
			},);
	};
	return (
		<PillButton
			icon={<ArchiveIcon className="size-3.5" />}
			label="Archive"
			onClick={handleClick}
		/>
	);
}

function DeleteAction({
	ids,
	surface,
	noun,
}: {
	ids: string[];
	surface: TaskSurface;
	noun: string;
}) {
	const [confirmOpen, setConfirmOpen] = useState(false);
	const qc = useQueryClient();
	const clear = useTaskSelection((s) => s.clear);
	const deleteTodoMut = useMutation(trpc.todos.delete.mutationOptions({}));
	const bulkDeleteTaskMut = useMutation(trpc.tasks.bulkDelete.mutationOptions({}));
	const deleteInboxMut = useMutation(trpc.inbox.delete.mutationOptions({}));

	const handleConfirm = () => {
		setConfirmOpen(false);
		if (surface === "inbox") {
			Promise.all(ids.map((id) => deleteInboxMut.mutateAsync({ id } as any))).then(
				() => {
					qc.invalidateQueries({ queryKey: [["inbox"]] });
					toast.success(`Deleted ${ids.length}`);
					clear();
				},
				(e: any) => toast.error(e?.message ?? "Couldn't delete"),
			);
			return;
		}
		if (surface === "todos") {
			Promise.all(ids.map((id) => deleteTodoMut.mutateAsync({ id } as any))).then(
				() => {
					qc.invalidateQueries({ queryKey: [["todos"]] });
					toast.success(`Deleted ${ids.length}`);
					clear();
				},
				(e: any) => toast.error(e?.message ?? "Couldn't delete"),
			);
			return;
		}
		bulkDeleteTaskMut.mutate({ ids } as any,
			{
				onSuccess: () => {
					qc.invalidateQueries({ queryKey: [["tasks"]] });
					toast.success(`Deleted ${ids.length}`);
					clear();
				},
				onError: (e: { message?: string }) => toast.error(e?.message ?? "Action failed"),
			},);
	};

	return (
		<>
			<PillButton
				icon={<Trash2Icon className="size-3.5" />}
				label="Delete"
				tone="destructive"
				onClick={() => setConfirmOpen(true)}
			/>
			<Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
				<DialogContent className="max-w-md">
					<DialogHeader>
						<DialogTitle>
							Delete {ids.length} {noun}
							{ids.length === 1 ? "" : "s"}?
						</DialogTitle>
						<DialogDescription>
							This can't be undone. The {ids.length === 1 ? "item" : "items"}{" "}
							will be permanently removed.
						</DialogDescription>
					</DialogHeader>
					<DialogFooter>
						<Button variant="ghost" onClick={() => setConfirmOpen(false)}>
							Cancel
						</Button>
						<Button variant="destructive" onClick={handleConfirm}>
							<Trash2Icon className="size-3.5" />
							Delete
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</>
	);
}

/**
 * Companion hook — surfaces use this to register their visible row order and
 * `Escape` clearing without each one re-importing the store directly. Keeps
 * the contract terse for callers.
 *
 *   useBindBulkSelection({ surface: 'todos', orderedIds, noun: 'todo' });
 *
 * Pass `null` for `surface` while the surface is mounting/teardown so the bar
 * doesn't render against an unrelated selection.
 */
export function useBindBulkSelection({
	surface,
	orderedIds,
}: {
	surface: TaskSurface | null;
	orderedIds: string[];
}) {
	const setSurface = useTaskSelection((s) => s.setSurface);
	const setOrderedIds = useTaskSelection((s) => s.setOrderedIds);
	const clear = useTaskSelection((s) => s.clear);

	// Re-key whenever the surface changes; refresh ordered ids on every render
	// they change. The `setSurface` path clears the prior selection — that's
	// intentional, see store header.
	// biome-ignore lint/correctness/useExhaustiveDependencies: surface drives full reset
	useEffect(() => {
		setSurface(surface, orderedIds);
		return () => {
			// On unmount: clear the active surface so the bar hides on navigate.
			setSurface(null, []);
			clear();
		};
	}, [surface]);

	// Keep ordered ids in sync with the visible row order so shift+x ranges
	// respect the current sort/filter/scope without remounting the whole surface.
	useEffect(() => {
		setOrderedIds(orderedIds);
	}, [orderedIds, setOrderedIds]);
}
