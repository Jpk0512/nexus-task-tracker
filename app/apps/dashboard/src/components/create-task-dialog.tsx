"use client";

import { recurrenceEditorToCron } from "@mimir/utils/recurrence";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { Editor as EditorInstance } from "@tiptap/react";
import { Button } from "@ui/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogTitle,
} from "@ui/components/ui/dialog";
import { Form, FormControl, FormField, FormItem } from "@ui/components/ui/form";
import { Switch } from "@ui/components/ui/switch";
import { ChevronRightIcon, Loader2 } from "lucide-react";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import type z from "zod";
import { Editor } from "@/components/editor";
import { Assignee } from "@/components/forms/task-form/assignee";
import { taskFormSchema } from "@/components/forms/task-form/form-type";
import { Labels } from "@/components/forms/task-form/labels";
import { Priority } from "@/components/forms/task-form/priority";
import { ProjectSelect } from "@/components/forms/task-form/project-select";
import { Recurring } from "@/components/forms/task-form/recurring";
import { StatusSelect } from "@/components/forms/task-form/status-select";
import { ProjectIcon } from "@/components/project-icon";
import { useUser } from "@/components/user-provider";
import { updateTaskInCache } from "@/hooks/use-data-cache-helpers";
import { useTaskParams } from "@/hooks/use-task-params";
import { useZodForm } from "@/hooks/use-zod-form";
import { trpc } from "@/utils/trpc";

/**
 * Linear-style centered create-task modal.
 *
 * Reference: /Users/john.keeney/nexus-task-tracker/.claude/skills/designs/references/Screenshot 2026-05-16 at 11.55.28 PM.png
 *
 * Layout:
 *   - Header: breadcrumb (Project name or "Workspace") > "New Issue"
 *   - Body: title input (Linear 18px/510), description editor placeholder
 *   - Footer: 5 chip selectors (Status / Priority / Assignee / Project / Labels)
 *             + "Create more" toggle + lavender Create issue CTA
 */
export const CreateTaskDialog = () => {
	const user = useUser();
	const params = useParams<{ projectId?: string }>();
	const editorRef = useRef<EditorInstance | null>(null);
	const titleInputRef = useRef<HTMLInputElement | null>(null);

	const {
		createTask,
		taskStatusId,
		taskProjectId,
		taskMilestoneId,
		taskTitle,
		taskRecurring,
		setParams,
	} = useTaskParams();

	const isOpen = Boolean(createTask);
	const [createMore, setCreateMore] = useState(false);

	// When the dialog is opened from the Recurring tab empty-state, prefill the
	// `recurring` field with a sensible daily cron so the user can hit Create
	// without touching the popover.
	const recurringDefault = taskRecurring
		? recurrenceEditorToCron({
				frequency: "daily",
				interval: 1,
				startDate: new Date().toISOString(),
			})
		: undefined;

	// Preset project: query param wins, then URL path /projects/[projectId].
	const presetProjectId = taskProjectId || params?.projectId || undefined;

	// Resolve default status (first "to_do" status for the resolved project).
	const { data: todoStatus } = useQuery(
		trpc.statuses.get.queryOptions(
			{
				type: ["to_do"],
				pageSize: 1,
				projectId: presetProjectId ?? null,
			} as any,
			{
				select: (data) => data.data[0] as { id: string } | undefined,
				refetchOnMount: false,
				refetchOnWindowFocus: false,
				enabled: isOpen,
			},
		),
	);

	// Resolve the preset project's display name for the breadcrumb.
	const { data: project } = useQuery(
		trpc.projects.getById.queryOptions({ id: presetProjectId! } as any, {
			enabled: isOpen && Boolean(presetProjectId),
			staleTime: 5 * 60 * 1000,
		}),
	);

	const form = useZodForm(taskFormSchema, {
		defaultValues: {
			title: taskTitle ?? "",
			description: "",
			priority: "low",
			labels: [],
			assigneeId: user?.id || null,
			statusId: taskStatusId || todoStatus?.id || "",
			projectId: presetProjectId,
			milestoneId: taskMilestoneId || undefined,
			recurring: recurringDefault ?? null,
		},
	});

	// Resync defaults when dialog opens with new context.
	useEffect(() => {
		if (!isOpen) return;
		form.reset({
			title: taskTitle ?? "",
			description: "",
			priority: "low",
			labels: [],
			assigneeId: user?.id || null,
			statusId: taskStatusId || todoStatus?.id || "",
			projectId: presetProjectId,
			milestoneId: taskMilestoneId || undefined,
			recurring: recurringDefault ?? null,
		});
		// We intentionally only resync when the dialog open transition fires.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [
		isOpen,
		todoStatus?.id,
		presetProjectId,
		taskStatusId,
		taskMilestoneId,
		taskTitle,
		recurringDefault,
	]);

	const { mutate: createTaskMutation, isPending } = useMutation(
		trpc.tasks.create.mutationOptions({
			onMutate: () => {
				toast.loading("Creating task...", { id: "create-task" });
			},
			onSuccess: (task) => {
				toast.success("Task created", { id: "create-task" });
				updateTaskInCache(task);
				if (createMore) {
					// Keep dialog open, reset everything except project/status presets.
					form.reset({
						title: "",
						description: "",
						priority: "low",
						labels: [],
						assigneeId: user?.id || null,
						statusId: form.getValues("statusId"),
						projectId: form.getValues("projectId"),
						milestoneId: form.getValues("milestoneId"),
					});
				} else {
					setParams(null);
				}
			},
			onError: (error) => {
				toast.error(error.message || "Failed to create task", {
					id: "create-task",
				});
			},
		}),
	);

	const parseMentions = (data: any): string[] => {
		const mentions: string[] = (data.content || []).flatMap(parseMentions);
		if (data.type === "mention") {
			mentions.push(data.attrs.id);
		}
		return mentions;
	};

	const onSubmit = async (data: z.infer<typeof taskFormSchema>) => {
		const mentions = parseMentions(editorRef.current?.getJSON() || {});
		createTaskMutation({
			...data,
			mentions,
		});
	};

	// Cmd+Enter submits from anywhere in the dialog.
	const handleKeyDown = (e: React.KeyboardEvent) => {
		if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
			e.preventDefault();
			form.handleSubmit(onSubmit)();
		}
	};

	const breadcrumbLabel = project?.name ?? "Workspace";

	return (
		<Dialog open={isOpen} onOpenChange={(o) => !o && setParams(null)}>
			<DialogContent
				className="top-[14%] max-h-[80vh] w-full max-w-2xl translate-y-0 gap-0 p-0 sm:max-w-2xl"
				onKeyDown={handleKeyDown}
				onOpenAutoFocus={(e) => {
					// Force focus to the title input; otherwise the description's
					// tiptap editor (mounted concurrently) steals it on open.
					e.preventDefault();
					// Two RAFs to wait past Radix's focus management AND the
					// tiptap editor's mount-time focus handling.
					requestAnimationFrame(() => {
						requestAnimationFrame(() => {
							titleInputRef.current?.focus();
						});
					});
				}}
			>
				<DialogTitle className="sr-only">New issue</DialogTitle>
				<DialogDescription className="sr-only">
					Create a new task. Use Cmd+Enter to submit.
				</DialogDescription>

				<Form {...form}>
					<form
						onSubmit={form.handleSubmit(onSubmit)}
						className="flex flex-col"
					>
						{/* Breadcrumb header */}
						<div className="flex items-center gap-1.5 border-border/60 border-b px-4 py-2.5 text-[12px] text-muted-foreground">
							<span className="inline-flex items-center gap-1.5 rounded-sm px-1.5 py-0.5 font-[510] text-foreground">
								{project ? (
									<ProjectIcon className="size-3.5" {...project} />
								) : null}
								{breadcrumbLabel}
							</span>
							<ChevronRightIcon className="size-3 opacity-60" />
							<span className="font-[510] text-foreground">New Issue</span>
						</div>

						{/* Body */}
						<div className="flex flex-col gap-2 px-4 py-3">
							<FormField
								control={form.control}
								name="title"
								render={({ field }) => (
									<FormItem>
										<FormControl>
											<input
												{...field}
												ref={(el) => {
													field.ref(el);
													titleInputRef.current = el;
												}}
												value={field.value ?? ""}
												placeholder="Issue title"
												className="w-full border-0 bg-transparent p-0 font-[510] text-[18px] text-foreground tracking-[-0.01em] outline-none placeholder:text-muted-foreground/80"
											/>
										</FormControl>
									</FormItem>
								)}
							/>

							<FormField
								control={form.control}
								name="description"
								render={({ field }) => (
									<FormItem>
										<FormControl>
											<Editor
												className="editor-sm min-h-[80px] text-[13px] [&_.tiptap]:min-h-[80px] [&_.tiptap_p.is-editor-empty:first-child::before]:text-muted-foreground/80"
												placeholder="Add description…"
												value={field.value ?? ""}
												onChange={(value) => field.onChange(value)}
												shouldInsertImage
												autoFocus={false}
												ref={editorRef}
											/>
										</FormControl>
									</FormItem>
								)}
							/>
						</div>

						{/* Footer chip row */}
						<div className="flex flex-wrap items-center gap-1.5 border-border/60 border-t px-3 py-2.5">
							<StatusSelect />
							<Priority />
							<Assignee />
							<ProjectSelect />
							<Labels />
							<Recurring />

							<div className="ml-auto flex items-center gap-3">
								<label className="flex items-center gap-2 text-[12px] text-muted-foreground">
									<Switch
										checked={createMore}
										onCheckedChange={setCreateMore}
									/>
									<span>Create more</span>
								</label>
								<Button
									type="submit"
									size="sm"
									disabled={isPending}
									className="text-[12px]"
								>
									{isPending ? (
										<Loader2 className="size-3 animate-spin" />
									) : null}
									Create issue
								</Button>
							</div>
						</div>
					</form>
				</Form>
			</DialogContent>
		</Dialog>
	);
};
