"use client";

import { Button } from "@ui/components/ui/button";
import { Textarea } from "@ui/components/ui/textarea";
import { BotIcon, SparklesIcon } from "lucide-react";
import { useState } from "react";
import { SoftIcon } from "@/components/ui/soft-icon";

/**
 * Ask vault / companion stub — LM Studio primary, Gemini fallback (labeled).
 * UI-only until host runtime wires streaming.
 */
export function CompanionStub({ compact }: { compact?: boolean }) {
	const [q, setQ] = useState("");
	const [answer, setAnswer] = useState<string | null>(null);
	const [provider, setProvider] = useState<"lmstudio" | "gemini">("lmstudio");

	const ask = () => {
		const query = q.trim();
		if (!query) return;
		const label = provider === "lmstudio" ? "LM Studio" : "Gemini";
		setAnswer(
			`[${label}] Stub response — host agent runtime not connected yet.\n\nYou asked: “${query}”\n\nWhen wired: vault-aware answer with citations under projects/{projectId}/ + task links. Primary route LM Studio; automatic Gemini fallback if local model is down.`,
		);
	};

	return (
		<div
			className={
				compact
					? "rounded-xl border border-border/60 bg-card/40 p-3"
					: "mx-auto max-w-2xl rounded-xl border border-border/60 bg-card/40 p-5"
			}
		>
			<div className="mb-3 flex items-center justify-between gap-2">
				<div className="flex items-center gap-2">
					<SoftIcon icon={BotIcon} tone="violet" size="sm" />
					<span className="font-[510] text-[13px]">Ask vault</span>
				</div>
				<div className="inline-flex rounded-md border border-border/60 p-0.5 text-[11px]">
					<button
						type="button"
						onClick={() => setProvider("lmstudio")}
						className={`rounded px-2 py-0.5 ${provider === "lmstudio" ? "bg-accent" : "text-muted-foreground"}`}
					>
						LM Studio
					</button>
					<button
						type="button"
						onClick={() => setProvider("gemini")}
						className={`rounded px-2 py-0.5 ${provider === "gemini" ? "bg-accent" : "text-muted-foreground"}`}
					>
						Gemini
					</button>
				</div>
			</div>
			<Textarea
				value={q}
				onChange={(e) => setQ(e.target.value)}
				placeholder="Ask about notes, tasks, or a project…"
				className="min-h-[72px] text-[13px]"
			/>
			<div className="mt-2 flex justify-end">
				<Button size="sm" className="gap-1.5" onClick={ask} disabled={!q.trim()}>
					<SparklesIcon className="size-3.5" />
					Ask ({provider === "lmstudio" ? "LM Studio" : "Gemini"})
				</Button>
			</div>
			{answer ? (
				<pre className="mt-3 whitespace-pre-wrap rounded-lg border border-border/50 bg-background/50 p-3 font-sans text-[12.5px] leading-relaxed text-muted-foreground">
					{answer}
				</pre>
			) : null}
		</div>
	);
}
