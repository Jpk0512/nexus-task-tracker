import { useMutation } from "@tanstack/react-query";
import { Badge } from "@ui/components/ui/badge";
import { Button } from "@ui/components/ui/button";
import { Input } from "@ui/components/ui/input";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@ui/components/ui/select";
import { cn } from "@ui/lib/utils";
import {
	CheckIcon,
	FolderIcon,
	Loader2Icon,
	PlusIcon,
	SparklesIcon,
	XIcon,
} from "lucide-react";
import { useState } from "react";
import { runToastAction } from "@/lib/toast-action";
import { trpc } from "@/utils/trpc";

const NO_PROJECT_VALUE = "__none__";

/** Editable tag chips (FEAT-015) — replaces the prior read-only tag list.
 *  Each chip carries its own remove button; new tags commit on Enter/comma/blur. */
export function EditableNoteTags({
	tags,
	onAdd,
	onRemove,
	disabled,
}: {
	tags: string[];
	onAdd: (tag: string) => void;
	onRemove: (tag: string) => void;
	disabled?: boolean;
}) {
	const [value, setValue] = useState("");

	const commit = () => {
		const next = value.trim().toLowerCase();
		setValue("");
		if (!next || tags.includes(next)) return;
		onAdd(next);
	};

	return (
		<div className="space-y-1.5">
			{tags.length > 0 ? (
				<div className="flex flex-wrap gap-1">
					{tags.map((tag) => (
						<span
							key={tag}
							className="inline-flex items-center gap-1 rounded-full border border-border/60 py-0.5 pr-1 pl-1.5 text-[10px] text-muted-foreground"
						>
							{tag}
							<button
								type="button"
								onClick={() => onRemove(tag)}
								aria-label={`Remove tag ${tag}`}
								disabled={disabled}
								className="rounded-full p-0.5 hover:bg-accent/60 hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
							>
								<XIcon className="size-2.5" />
							</button>
						</span>
					))}
				</div>
			) : null}
			<Input
				value={value}
				onChange={(event) => setValue(event.target.value)}
				onKeyDown={(event) => {
					if (event.key === "Enter" || event.key === ",") {
						event.preventDefault();
						commit();
					}
				}}
				onBlur={commit}
				placeholder="Add tag…"
				disabled={disabled}
				className="h-6.5 text-[11px]"
			/>
		</div>
	);
}

/** Project selector (FEAT-015) — value/options are project *names*, matching
 *  what `suggestTagsAndProject` validates against and what gets written to
 *  `frontmatter.project` (notes have no project foreign key; the vault is
 *  disk-backed, so the name string is the only association that round-trips
 *  through the frontmatter write path). */
export function NoteProjectSelect({
	value,
	options,
	onChange,
	disabled,
}: {
	value: string | null;
	options: string[];
	onChange: (project: string | null) => void;
	disabled?: boolean;
}) {
	return (
		<Select
			value={value ?? NO_PROJECT_VALUE}
			onValueChange={(next) =>
				onChange(next === NO_PROJECT_VALUE ? null : next)
			}
			disabled={disabled}
		>
			<SelectTrigger className="h-7 w-full text-[11px]">
				<FolderIcon className="mr-1 size-3 text-muted-foreground" />
				<SelectValue placeholder="No project" />
			</SelectTrigger>
			<SelectContent>
				<SelectItem value={NO_PROJECT_VALUE}>No project</SelectItem>
				{options.map((name) => (
					<SelectItem key={name} value={name}>
						{name}
					</SelectItem>
				))}
				{value && !options.includes(value) ? (
					<SelectItem value={value}>{value}</SelectItem>
				) : null}
			</SelectContent>
		</Select>
	);
}

type Suggestion = {
	tags: string[];
	project: string | null;
	confidence: number;
};

/**
 * "Suggest tags/project" action (FEAT-015) — mirrors the accept/discard
 * shape of `task-form/ai-description-suggestion.tsx`, but per-item instead
 * of whole-or-nothing: each tag and the project get their own Accept, since
 * a user may want three of five suggested tags without the project (or the
 * reverse). Confidence gating already happened server-side
 * (`suggestTagsAndProject`, threshold 0.55 on tags; project is separately
 * existence-gated against the team's real projects) — an empty `tags` array
 * here means "below threshold" and must not be re-derived client-side.
 * Never auto-applies: acceptance only fires the parent's `onAcceptTag` /
 * `onAcceptProject`, which write through the existing `knowledge.update`
 * frontmatter path — this component holds no write access of its own.
 */
export function NoteTagProjectSuggestions({
	content,
	currentTags,
	currentProject,
	onAcceptTag,
	onAcceptProject,
	disabled,
}: {
	content: string;
	currentTags: string[];
	currentProject: string | null;
	onAcceptTag: (tag: string) => void;
	onAcceptProject: (project: string) => void;
	disabled?: boolean;
}) {
	const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
	const [acceptedTags, setAcceptedTags] = useState<Set<string>>(new Set());
	const [projectAccepted, setProjectAccepted] = useState(false);

	const { mutateAsync, isPending } = useMutation(
		trpc.knowledge.suggestTagsAndProject.mutationOptions(),
	);

	const trimmed = content.trim();

	const generate = () => {
		if (!trimmed || isPending) return;
		runToastAction(
			async () => {
				const result = await mutateAsync({ content: trimmed });
				if (!result.success) {
					throw new Error("No suggestion available right now");
				}
				return result;
			},
			{
				loading: "Suggesting tags & project…",
				success: "Suggestion ready to review",
				error: "Couldn't generate a suggestion",
				retry: generate,
			},
		).then((result) => {
			if (result.ok) {
				setSuggestion(result.data);
				setAcceptedTags(new Set());
				setProjectAccepted(false);
			}
		});
	};

	if (!suggestion) {
		return (
			<Button
				type="button"
				size="sm"
				variant="ghost"
				className="h-7 w-full justify-start px-2 text-[11px] text-muted-foreground hover:text-primary"
				onClick={generate}
				disabled={disabled || isPending || !trimmed}
			>
				{isPending ? (
					<Loader2Icon className="size-3.5 animate-spin" />
				) : (
					<SparklesIcon className="size-3.5" />
				)}
				Suggest tags/project
			</Button>
		);
	}

	const newTags = suggestion.tags.filter((tag) => !currentTags.includes(tag));
	const suggestsProject =
		suggestion.project !== null && suggestion.project !== currentProject;

	return (
		<div className="rounded-md border border-dashed bg-muted/40 p-2.5 text-[11px]">
			<div className="mb-1.5 flex items-center justify-between">
				<span className="flex items-center gap-1.5 text-muted-foreground uppercase tracking-wider">
					<SparklesIcon className="size-3" />
					AI suggestion
					<Badge
						variant="outline"
						className="h-[16px] px-1 font-normal text-[9.5px]"
					>
						{Math.round(suggestion.confidence * 100)}%
					</Badge>
				</span>
				<button
					type="button"
					onClick={() => setSuggestion(null)}
					className="text-muted-foreground hover:text-foreground"
					aria-label="Dismiss suggestion"
				>
					<XIcon className="size-3" />
				</button>
			</div>

			{newTags.length === 0 && !suggestsProject ? (
				<p className="text-muted-foreground">Nothing new to suggest.</p>
			) : (
				<div className="space-y-2">
					{newTags.length > 0 ? (
						<div className="flex flex-wrap gap-1">
							{newTags.map((tag) => {
								const accepted = acceptedTags.has(tag);
								return (
									<button
										key={tag}
										type="button"
										disabled={accepted || disabled}
										onClick={() => {
											onAcceptTag(tag);
											setAcceptedTags((prev) => new Set(prev).add(tag));
										}}
										className={cn(
											"inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 transition-colors disabled:pointer-events-none",
											accepted
												? "border-primary/40 bg-primary/10 text-primary"
												: "border-border/60 text-muted-foreground hover:border-primary/40 hover:text-primary",
										)}
									>
										{accepted ? (
											<CheckIcon className="size-2.5" />
										) : (
											<PlusIcon className="size-2.5" />
										)}
										{tag}
									</button>
								);
							})}
						</div>
					) : null}
					{suggestsProject && suggestion.project ? (
						<div className="flex items-center justify-between gap-2">
							<span className="truncate text-foreground/90">
								Project:{" "}
								<span className="font-[510]">{suggestion.project}</span>
							</span>
							<Button
								type="button"
								size="sm"
								variant={projectAccepted ? "secondary" : "default"}
								className="h-6 px-2 text-[10.5px]"
								disabled={projectAccepted || disabled}
								onClick={() => {
									onAcceptProject(suggestion.project as string);
									setProjectAccepted(true);
								}}
							>
								{projectAccepted ? <CheckIcon className="size-2.5" /> : null}
								{projectAccepted ? "Applied" : "Accept"}
							</Button>
						</div>
					) : null}
				</div>
			)}
		</div>
	);
}
