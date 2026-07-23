import { Button } from "@nexus-app/ui/button";
import { useMutation } from "@tanstack/react-query";
import { CheckIcon, Loader2Icon, SparklesIcon, XIcon } from "lucide-react";
import { useState } from "react";
import { useProjects } from "@/hooks/use-data";
import { runToastAction } from "@/lib/toast-action";
import { trpc } from "@/utils/trpc";

/**
 * "Generate from title" / "Enhance" affordance (FEAT-014).
 *
 * Edit-only by design: `tasks.suggestDescription` only makes sense once a
 * task exists (stable title, an id to key the request on) — a task being
 * drafted in create mode has no settled title/description pair to react to,
 * so the caller (`Description`) renders this only when `id` is set.
 *
 * Never auto-applies — the suggestion sits in a preview card until the user
 * explicitly Accepts (bubbled up via `onAccept`) or Discards (cleared
 * locally, description field left untouched).
 */
export const AiDescriptionSuggestion = ({
	title,
	description,
	projectId,
	onAccept,
}: {
	title?: string;
	description?: string | null;
	projectId?: string | null;
	onAccept: (suggestion: string) => void;
}) => {
	const [suggestion, setSuggestion] = useState<string | null>(null);
	const { data: projects } = useProjects();
	const projectName =
		projects?.data.find((project) => project.id === projectId)?.name ?? null;

	const { mutateAsync, isPending } = useMutation(
		trpc.tasks.suggestDescription.mutationOptions(),
	);

	const trimmedTitle = title?.trim() ?? "";
	const hasDescription = Boolean(description?.trim());

	const generate = () => {
		if (!trimmedTitle || isPending) return;
		runToastAction(
			async () => {
				const result = await mutateAsync({
					title: trimmedTitle,
					description,
					projectName,
				});
				// The procedure resolves `{ success: false }` instead of throwing
				// when the Gemini-lite env isn't configured (or generation fails
				// upstream) — surface that as the same toast-error path rather
				// than silently no-op-ing.
				if (!result.success || !result.suggestion) {
					throw new Error("No suggestion available right now");
				}
				return result;
			},
			{
				loading: hasDescription
					? "Enhancing description…"
					: "Generating description…",
				success: "Suggestion ready to review",
				error: "Couldn't generate a suggestion",
				retry: generate,
			},
		).then((result) => {
			if (result.ok) {
				setSuggestion(result.data.suggestion);
			}
		});
	};

	if (suggestion) {
		return (
			<div className="mb-2 rounded-md border border-dashed bg-muted/40 p-3 text-sm">
				<div className="mb-2 flex items-center gap-1.5 text-muted-foreground text-xs">
					<SparklesIcon className="size-3" />
					AI suggestion
				</div>
				<p className="whitespace-pre-wrap text-foreground/90">{suggestion}</p>
				<div className="mt-2 flex justify-end gap-2">
					<Button
						type="button"
						size="sm"
						variant="ghost"
						onClick={() => setSuggestion(null)}
					>
						<XIcon />
						Discard
					</Button>
					<Button
						type="button"
						size="sm"
						onClick={() => {
							onAccept(suggestion);
							setSuggestion(null);
						}}
					>
						<CheckIcon />
						Accept
					</Button>
				</div>
			</div>
		);
	}

	return (
		<Button
			type="button"
			size="sm"
			variant="ghost"
			className="text-muted-foreground hover:text-primary"
			onClick={generate}
			disabled={isPending || !trimmedTitle}
		>
			{isPending ? <Loader2Icon className="animate-spin" /> : <SparklesIcon />}
			{hasDescription ? "Enhance" : "Generate from title"}
		</Button>
	);
};
