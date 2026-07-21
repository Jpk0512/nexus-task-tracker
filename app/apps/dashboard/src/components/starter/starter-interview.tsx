"use client";

import { useChat } from "@ai-sdk/react";
import { Button } from "@ui/components/ui/button";
import { Textarea } from "@ui/components/ui/textarea";
import { cn } from "@ui/lib/utils";
import { DefaultChatTransport } from "ai";
import {
	ArrowLeftIcon,
	ArrowUpIcon,
	FileTextIcon,
	SparklesIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { SoftIcon } from "@/components/ui/soft-icon";

export interface StarterSeed {
	name: string;
	idea: string;
	drivers: string[];
}

interface StarterInterviewProps {
	seed: StarterSeed;
	onPrd: (prd: string) => void;
	onBack: () => void;
}

/**
 * Local, isolated chat for the Project Starter interview.
 *
 * Intentionally uses its OWN `useChat` instance pointed at
 * `/api/project-starter/chat` — it does NOT touch the global ChatProvider /
 * main app chat store. The seed (name/idea/drivers) is sent in the request
 * body every turn so the server-side interviewer has full context. When the
 * agent finalizes, it emits a `data-starter-prd` part which we lift up via
 * `onPrd`.
 */
export function StarterInterview({
	seed,
	onPrd,
	onBack,
}: StarterInterviewProps) {
	const [input, setInput] = useState("");

	const authenticatedFetch = useMemo(
		() => async (url: RequestInfo | URL, options?: RequestInit) =>
			fetch(url, {
				...options,
				headers: { ...options?.headers, "Content-Type": "application/json" },
				credentials: "include",
			}),
		[],
	);

	const transport = useMemo(
		() =>
			new DefaultChatTransport({
				api: `${process.env.NEXT_PUBLIC_SERVER_URL}/api/project-starter/chat`,
				fetch: authenticatedFetch,
				prepareSendMessagesRequest({ messages, id }) {
					return {
						body: {
							id,
							message: messages[messages.length - 1],
							seed,
							timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
						},
					};
				},
			}),
		// seed is captured once per wizard and is stable for this component's life
		[authenticatedFetch, seed],
	);

	const { messages, sendMessage, status, error } = useChat({
		id: `starter-${seed.name}`,
		transport,
	});

	const isStreaming = status === "streaming" || status === "submitted";

	// Kick off the interview with a single opener (guard against double-send).
	const startedRef = useRef(false);
	// Set when the user clicks "Generate PRD" — gates the text-PRD fallback.
	const finalizeRequestedRef = useRef(false);
	useEffect(() => {
		if (startedRef.current) return;
		if (status === "ready" && messages.length === 0) {
			startedRef.current = true;
			sendMessage({ text: `Let's begin the interview for "${seed.name}".` });
		}
	}, [status, messages.length, sendMessage, seed.name]);

	// Lift the finalized PRD up to the page. Preferred signal: the agent calls
	// the finalizeStarterPrd tool (the PRD is the tool's input). Fallback: some
	// OpenAI-compatible proxies drop tool calls, so if the agent instead wrote
	// the PRD as a long markdown doc after we asked it to finalize, lift that.
	useEffect(() => {
		for (const m of messages) {
			if (m.role !== "assistant") continue;
			for (const part of m.parts ?? []) {
				if (part.type === "tool-finalizeStarterPrd") {
					const prd = (part as { input?: { prd?: string } }).input?.prd;
					if (prd && prd.length > 50) {
						onPrd(prd);
						return;
					}
				}
			}
		}
		if (finalizeRequestedRef.current) {
			const last = [...messages].reverse().find((m) => m.role === "assistant");
			const text = (last?.parts ?? [])
				.filter((p) => p.type === "text")
				.map((p) => (p as { text?: string }).text ?? "")
				.join("")
				.trim();
			const headers = (text.match(/^#{1,3} /gm) ?? []).length;
			if (text.length > 500 && headers >= 3) {
				onPrd(text);
				return;
			}
		}
	}, [messages, onPrd]);

	// Auto-scroll to the latest message.
	const scrollRef = useRef<HTMLDivElement>(null);
	// biome-ignore lint/correctness/useExhaustiveDependencies: re-scroll whenever messages change (scrollHeight is read imperatively)
	useEffect(() => {
		scrollRef.current?.scrollTo({
			top: scrollRef.current.scrollHeight,
			behavior: "smooth",
		});
	}, [messages]);

	const submit = () => {
		const text = input.trim();
		if (!text || isStreaming) return;
		sendMessage({ text });
		setInput("");
	};

	const generatePrd = () => {
		if (isStreaming) return;
		finalizeRequestedRef.current = true;
		sendMessage({ text: "I'm done. Generate the full PRD now." });
	};

	return (
		<div className="flex h-[560px] flex-col rounded-lg border border-border/50 bg-background/40">
			{/* Header */}
			<div className="flex items-center justify-between gap-2 border-border/50 border-b px-4 py-2.5">
				<div className="flex items-center gap-2">
					<SoftIcon icon={SparklesIcon} tone="blue" size="sm" />
					<div>
						<p className="font-[510] text-[12.5px]">Guided interview</p>
						<p className="text-[11px] text-muted-foreground">
							One question at a time — grill the idea into a PRD.
						</p>
					</div>
				</div>
				<Button variant="ghost" size="sm" onClick={onBack}>
					<ArrowLeftIcon className="mr-1.5 size-3.5" />
					Seed
				</Button>
			</div>

			{/* Messages */}
			<div
				ref={scrollRef}
				className="flex-1 space-y-3 overflow-y-auto px-4 py-4"
			>
				{messages.length === 0 && status !== "ready" ? (
					<p className="py-8 text-center text-[12.5px] text-muted-foreground">
						Starting the interview…
					</p>
				) : null}
				{messages.map((m) => {
					const text = (m.parts ?? [])
						.filter((p) => p.type === "text")
						// biome-ignore lint/suspicious/noExplicitAny: text part
						.map((p) => (p as any).text as string)
						.join("");
					if (!text) return null;
					const isUser = m.role === "user";
					return (
						<div
							key={m.id}
							className={cn("flex", isUser ? "justify-end" : "justify-start")}
						>
							<div
								className={cn(
									"max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-[13px] leading-relaxed",
									isUser
										? "bg-primary text-primary-foreground"
										: "border border-border/60 bg-card/60 text-foreground",
								)}
							>
								{text}
							</div>
						</div>
					);
				})}
				{isStreaming ? (
					<div className="flex justify-start">
						<div className="rounded-lg border border-border/60 bg-card/60 px-3 py-2 text-[12.5px] text-muted-foreground">
							<span className="inline-flex gap-1">
								<span className="size-1.5 animate-pulse rounded-full bg-current" />
								<span className="size-1.5 animate-pulse rounded-full bg-current [animation-delay:120ms]" />
								<span className="size-1.5 animate-pulse rounded-full bg-current [animation-delay:240ms]" />
							</span>
						</div>
					</div>
				) : null}
				{error ? (
					<p className="text-[12px] text-destructive">
						Something went wrong. {error.message}
					</p>
				) : null}
			</div>

			{/* Composer */}
			<div className="space-y-2 border-border/50 border-t px-3 py-3">
				<Textarea
					value={input}
					onChange={(e) => setInput(e.target.value)}
					onKeyDown={(e) => {
						if (e.key === "Enter" && !e.shiftKey) {
							e.preventDefault();
							submit();
						}
					}}
					placeholder="Type your answer…"
					className="min-h-[44px] resize-none text-[13px]"
					rows={2}
				/>
				<div className="flex items-center justify-between gap-2">
					<Button
						variant="outline"
						size="sm"
						onClick={generatePrd}
						disabled={isStreaming || messages.length < 2}
					>
						<FileTextIcon className="mr-1.5 size-3.5" />
						Generate PRD
					</Button>
					<Button
						size="sm"
						onClick={submit}
						disabled={!input.trim() || isStreaming}
					>
						Send
						<ArrowUpIcon className="ml-1.5 size-3.5" />
					</Button>
				</div>
			</div>
		</div>
	);
}
