import { useMutation } from "@tanstack/react-query";
import { cn } from "@ui/lib/utils";
import { FolderIcon, XIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useDebounceValue } from "usehooks-ts";
import { trpc } from "@/utils/trpc";

/**
 * FEAT-016 — smart project routing for captures with NO explicit `@project`
 * token. Wires `projects.suggestBySimilarity` as a DEBOUNCED, NON-BLOCKING
 * background call: the capture bar's own submit path never awaits this, so a
 * slow or failed AI call can never stall or break a capture — it just means
 * no chip renders. This file carries no "use client" directive of its own;
 * it's a leaf import of `capture-bar.tsx` (already a client component), same
 * precedent as `components/knowledge/note-tag-suggestions.tsx`.
 */

const MIN_CHARS = 8;
const DEBOUNCE_MS = 500;
const MIN_SCORE = 0.35;

export type SuggestedProject = {
	id: string;
	name: string;
	prefix: string | null;
	score: number;
};

/**
 * Debounces `text`, fires `suggestBySimilarity` in the background once it
 * settles, and exposes the latest suggestion. `enabled` should be false
 * whenever the capture already has an explicit `@project` token (or a
 * `@task:` mention) — the AI suggestion only exists to fill the *missing*
 * case, never to second-guess an explicit one.
 *
 * Race-safety: each fire gets a generation stamp; a response only applies if
 * its generation is still the latest by the time it resolves, so a slow
 * response for stale text can never clobber a newer one.
 */
export function useSuggestedProjectBySimilarity(
	text: string,
	enabled: boolean,
): {
	suggestion: SuggestedProject | null;
	isPending: boolean;
	dismiss: () => void;
} {
	const [debouncedText] = useDebounceValue(text, DEBOUNCE_MS);
	const [suggestion, setSuggestion] = useState<SuggestedProject | null>(null);
	const [dismissed, setDismissed] = useState(false);
	const generationRef = useRef(0);
	const lastFiredRef = useRef<string | null>(null);

	const { mutate, isPending } = useMutation(
		trpc.projects.suggestBySimilarity.mutationOptions(),
	);

	useEffect(() => {
		const trimmed = debouncedText.trim();
		if (!enabled || trimmed.length < MIN_CHARS) {
			lastFiredRef.current = null;
			setSuggestion(null);
			return;
		}
		if (lastFiredRef.current === trimmed) return;
		lastFiredRef.current = trimmed;
		const generation = ++generationRef.current;
		mutate(
			{ text: trimmed },
			{
				onSuccess: (result) => {
					if (generation !== generationRef.current) return;
					const top = result.suggestions[0];
					setDismissed(false);
					setSuggestion(
						top && top.score >= MIN_SCORE
							? {
									id: top.id,
									name: top.name,
									prefix: top.prefix,
									score: top.score,
								}
							: null,
					);
				},
				onError: () => {
					if (generation !== generationRef.current) return;
					setSuggestion(null);
				},
			},
		);
	}, [debouncedText, enabled, mutate]);

	return {
		suggestion: dismissed ? null : suggestion,
		isPending,
		dismiss: () => setDismissed(true),
	};
}

export function SuggestedProjectChip({
	suggestion,
	onAccept,
	onDismiss,
}: {
	suggestion: SuggestedProject;
	onAccept: (suggestion: SuggestedProject) => void;
	onDismiss: () => void;
}) {
	return (
		<div
			className={cn(
				"absolute inset-x-0 top-full z-20 mt-1 flex items-center gap-1.5",
				"rounded-md border border-cyan-500/30 border-dashed bg-popover px-2 py-1 text-[11px] shadow-sm",
			)}
		>
			<button
				type="button"
				onClick={() => onAccept(suggestion)}
				className="inline-flex min-w-0 flex-1 items-center gap-1 truncate text-cyan-500 hover:text-cyan-400"
			>
				<FolderIcon className="size-3 shrink-0" />
				<span className="truncate">
					Suggested project:{" "}
					<span className="font-[510]">{suggestion.name}</span>
				</span>
			</button>
			<button
				type="button"
				onClick={onDismiss}
				aria-label="Dismiss suggested project"
				className="shrink-0 text-muted-foreground hover:text-foreground"
			>
				<XIcon className="size-3" />
			</button>
		</div>
	);
}
