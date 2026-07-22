"use client";

import { Button } from "@ui/components/ui/button";
import { Textarea } from "@ui/components/ui/textarea";
import {
	CheckSquareIcon,
	FileAudioIcon,
	ListChecksIcon,
	SparklesIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import {
	EmptyState,
	EmptyStateDescription,
	EmptyStateIcon,
	EmptyStateTitle,
} from "@/components/empty-state";
import { SoftIcon } from "@/components/ui/soft-icon";
import { trpcClient } from "@/utils/trpc";

/**
 * Meetings — paste transcript → extract action-ish lines → promote to todos.
 * Full transcription REST + summary agent lands later.
 */
function extractActions(text: string): string[] {
	const lines = text
		.split(/\r?\n/)
		.map((l) => l.trim())
		.filter(Boolean);
	const out: string[] = [];
	for (const line of lines) {
		if (
			/^(action|todo|follow[- ]?up|owner|ai:|next)\b/i.test(line) ||
			/\b(will|should|need to|todo)\b/i.test(line)
		) {
			const cleaned = line.replace(/^[-*•\d.)\s]+/, "").trim();
			if (cleaned.length >= 6) out.push(cleaned);
		}
	}
	// Fallback: short imperative-ish sentences
	if (out.length === 0) {
		for (const line of lines) {
			if (line.length > 12 && line.length < 160 && /[.!?]$/.test(line)) {
				out.push(line);
			}
			if (out.length >= 8) break;
		}
	}
	return [...new Set(out)].slice(0, 20);
}

export function MeetingsShell() {
	const [transcript, setTranscript] = useState("");
	const [selected, setSelected] = useState<Record<number, boolean>>({});
	const actions = useMemo(() => extractActions(transcript), [transcript]);

	const toggle = (i: number) => setSelected((s) => ({ ...s, [i]: !s[i] }));

	const promote = async () => {
		const picks = actions.filter((_, i) => selected[i]);
		if (picks.length === 0) {
			toast.message("Select at least one action");
			return;
		}
		let ok = 0;
		for (const content of picks) {
			try {
				await trpcClient.todos.create.mutate({ content });
				ok++;
			} catch {
				/* continue */
			}
		}
		toast.success(`Promoted ${ok} action${ok === 1 ? "" : "s"} to Todos`);
	};

	return (
		<div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-8">
			<div className="flex items-start gap-3">
				<SoftIcon icon={FileAudioIcon} tone="orange" size="lg" />
				<div>
					<h1 className="font-[510] text-[22px] tracking-[-0.02em]">
						Meetings
					</h1>
					<p className="mt-1 text-[13px] text-muted-foreground">
						Paste a transcript. Pull open actions → Todos (safe promote). Agent
						summary + project notebook filing next.
					</p>
				</div>
			</div>

			<div className="space-y-2">
				<label className="font-[510] text-[12px]">Transcript</label>
				<Textarea
					value={transcript}
					onChange={(e) => {
						setTranscript(e.target.value);
						setSelected({});
					}}
					placeholder={
						"Paste meeting notes or transcript…\n\nAction: Ship Focus Needs-you tab\nFollow-up: John to file vault path decision"
					}
					className="min-h-[200px] font-mono text-[12.5px]"
				/>
			</div>

			<div className="rounded-xl border border-border/60 bg-card/40 p-4">
				<div className="mb-3 flex items-center justify-between gap-2">
					<div className="inline-flex items-center gap-2">
						<SoftIcon icon={ListChecksIcon} tone="blue" size="sm" />
						<span className="font-[510] text-[13px]">
							Open actions ({actions.length})
						</span>
					</div>
					<div className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
						<SparklesIcon className="size-3" /> Heuristic extract · AI later
					</div>
				</div>
				{actions.length === 0 ? (
					<EmptyState>
						<EmptyStateIcon>
							<FileAudioIcon className="size-full" />
						</EmptyStateIcon>
						<EmptyStateTitle>No actions yet</EmptyStateTitle>
						<EmptyStateDescription>
							Paste a transcript above — we'll pull out anything that reads like
							an action, todo, or follow-up.
						</EmptyStateDescription>
					</EmptyState>
				) : (
					<ul className="space-y-1.5">
						{actions.map((a, i) => (
							<li key={`${i}-${a.slice(0, 24)}`}>
								<label className="flex cursor-pointer items-start gap-2.5 rounded-lg px-2 py-2 hover:bg-accent/40">
									<input
										type="checkbox"
										checked={!!selected[i]}
										onChange={() => toggle(i)}
										className="mt-1"
									/>
									<span className="text-[13px] leading-snug">{a}</span>
								</label>
							</li>
						))}
					</ul>
				)}
				<div className="mt-3 flex justify-end">
					<Button
						size="sm"
						disabled={actions.length === 0}
						onClick={promote}
						className="gap-1.5"
					>
						<CheckSquareIcon className="size-3.5" />
						Promote selected to Todos
					</Button>
				</div>
			</div>
		</div>
	);
}
