"use client";

import { Button } from "@ui/components/ui/button";
import { Input } from "@ui/components/ui/input";
import { Textarea } from "@ui/components/ui/textarea";
import { cn } from "@ui/lib/utils";
import {
	CopyIcon,
	KeyRoundIcon,
	PlusIcon,
	ServerIcon,
	ShieldCheckIcon,
	Trash2Icon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { SoftIcon } from "@/components/ui/soft-icon";

/**
 * Vault — personal collection of secrets/API keys + MCP servers.
 * Reference list (local store). Not an "add to coding agent" UI.
 */

type VaultKind = "secret" | "mcp";

type VaultEntry = {
	id: string;
	kind: VaultKind;
	name: string;
	value: string; // secret value or mcp url/command
	notes?: string;
	createdAt: string;
};

const STORE = "nexus.vault.local";

type Filter = "all" | VaultKind;

function readStore(): VaultEntry[] {
	try {
		const raw = localStorage.getItem(STORE);
		if (!raw) return [];
		const parsed = JSON.parse(raw);
		return Array.isArray(parsed) ? (parsed as VaultEntry[]) : [];
	} catch {
		return [];
	}
}

function maskValue(v: string): string {
	if (!v) return "••••";
	if (v.length <= 4) return "••••";
	return `${v.slice(0, 2)}${"•".repeat(Math.min(12, v.length - 4))}${v.slice(-2)}`;
}

export function VaultShell() {
	const [entries, setEntries] = useState<VaultEntry[]>([]);
	const [filter, setFilter] = useState<Filter>("all");
	const [showForm, setShowForm] = useState(false);
	const [form, setForm] = useState<{
		kind: VaultKind;
		name: string;
		value: string;
		notes: string;
	}>({ kind: "secret", name: "", value: "", notes: "" });
	const [revealed, setRevealed] = useState<Record<string, boolean>>({});

	useEffect(() => setEntries(readStore()), []);

	const persist = (next: VaultEntry[]) => {
		setEntries(next);
		localStorage.setItem(STORE, JSON.stringify(next));
	};

	const add = () => {
		const name = form.name.trim();
		const value = form.value.trim();
		if (!name || !value) {
			toast.error("Name and value are required");
			return;
		}
		const entry: VaultEntry = {
			id: crypto.randomUUID(),
			kind: form.kind,
			name,
			value,
			notes: form.notes.trim() || undefined,
			createdAt: new Date().toISOString(),
		};
		persist([entry, ...entries]);
		setForm({ kind: form.kind, name: "", value: "", notes: "" });
		setShowForm(false);
		toast.success(`${form.kind === "secret" ? "Secret" : "MCP"} saved`);
	};

	const remove = (id: string) => {
		persist(entries.filter((e) => e.id !== id));
		toast.message("Removed");
	};

	const copy = async (e: VaultEntry) => {
		try {
			await navigator.clipboard.writeText(e.value);
			toast.success(`${e.kind === "secret" ? "Secret" : "MCP"} value copied`);
		} catch {
			toast.error("Copy failed");
		}
	};

	const counts = useMemo(
		() => ({
			all: entries.length,
			secret: entries.filter((e) => e.kind === "secret").length,
			mcp: entries.filter((e) => e.kind === "mcp").length,
		}),
		[entries],
	);

	const filtered = useMemo(
		() =>
			filter === "all" ? entries : entries.filter((e) => e.kind === filter),
		[entries, filter],
	);

	return (
		<div className="mx-auto flex h-full w-full max-w-2xl flex-col overflow-y-auto px-4 py-8">
			<div className="flex items-start gap-3">
				<SoftIcon icon={ShieldCheckIcon} tone="green" size="lg" />
				<div className="flex-1">
					<h1 className="font-[510] text-[22px] tracking-[-0.02em]">Vault</h1>
					<p className="mt-1 text-[13px] text-muted-foreground">
						Your collection of secrets, API keys, and MCP servers. Reference
						only — values masked after save. Never paste into chat.
					</p>
				</div>
				<Button
					size="sm"
					onClick={() => setShowForm((s) => !s)}
					className="gap-1.5"
				>
					<PlusIcon className="size-3.5" />
					Add
				</Button>
			</div>

			{/* Filter */}
			<div className="mt-5 inline-flex w-fit rounded-lg border border-border/60 bg-card/40 p-0.5">
				{(
					[
						["all", `All (${counts.all})`],
						["secret", `Secrets (${counts.secret})`],
						["mcp", `MCPs (${counts.mcp})`],
					] as const
				).map(([id, label]) => (
					<button
						key={id}
						type="button"
						onClick={() => setFilter(id)}
						className={cn(
							"rounded-md px-3 py-1.5 font-[510] text-[12.5px] transition-colors",
							filter === id
								? "bg-accent text-foreground"
								: "text-muted-foreground hover:text-foreground",
						)}
					>
						{label}
					</button>
				))}
			</div>

			{/* Add form */}
			{showForm ? (
				<div className="mt-4 space-y-3 rounded-xl border border-border/60 bg-card/40 p-4">
					<div className="inline-flex rounded-md border border-border/60 p-0.5 text-[12px]">
						<button
							type="button"
							onClick={() => setForm((f) => ({ ...f, kind: "secret" }))}
							className={cn(
								"flex items-center gap-1.5 rounded px-2.5 py-1",
								form.kind === "secret" ? "bg-accent" : "text-muted-foreground",
							)}
						>
							<KeyRoundIcon className="size-3.5" /> Secret / API key
						</button>
						<button
							type="button"
							onClick={() => setForm((f) => ({ ...f, kind: "mcp" }))}
							className={cn(
								"flex items-center gap-1.5 rounded px-2.5 py-1",
								form.kind === "mcp" ? "bg-accent" : "text-muted-foreground",
							)}
						>
							<ServerIcon className="size-3.5" /> MCP server
						</button>
					</div>
					<Input
						value={form.name}
						onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
						placeholder={
							form.kind === "secret"
								? "e.g. OPENAI_API_KEY"
								: "e.g. filesystem-mcp"
						}
						className="font-mono text-[12.5px]"
					/>
					<Input
						value={form.value}
						onChange={(e) => setForm((f) => ({ ...f, value: e.target.value }))}
						placeholder={
							form.kind === "secret"
								? "secret value (hidden after save)"
								: "URL or command (e.g. npx -y @modelcontextprotocol/server-filesystem .)"
						}
						type={form.kind === "secret" ? "password" : "text"}
						className="font-mono text-[12.5px]"
					/>
					<Textarea
						value={form.notes}
						onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
						placeholder="Notes (optional) — scope, project, where it's used…"
						className="min-h-[56px] text-[12.5px]"
					/>
					<div className="flex justify-end gap-2">
						<Button
							size="sm"
							variant="ghost"
							onClick={() => setShowForm(false)}
						>
							Cancel
						</Button>
						<Button size="sm" onClick={add}>
							Save
						</Button>
					</div>
				</div>
			) : null}

			{/* List */}
			<ul className="mt-4 space-y-2">
				{filtered.length === 0 ? (
					<li className="rounded-xl border border-border/60 border-dashed px-4 py-12 text-center text-[13px] text-muted-foreground">
						Nothing here yet. Add a secret or MCP to start your collection.
					</li>
				) : (
					filtered.map((e) => {
						const show = !!revealed[e.id];
						return (
							<li
								key={e.id}
								className="flex items-start gap-3 rounded-xl border border-border/60 bg-card/30 px-3 py-2.5"
							>
								<SoftIcon
									icon={e.kind === "secret" ? KeyRoundIcon : ServerIcon}
									tone={e.kind === "secret" ? "yellow" : "teal"}
									size="sm"
								/>
								<div className="min-w-0 flex-1">
									<div className="flex items-center gap-2">
										<p className="truncate font-[510] font-mono text-[12.5px]">
											{e.name}
										</p>
										<span className="rounded-full border border-border/60 px-1.5 py-0 text-[10px] text-muted-foreground uppercase tracking-wide">
											{e.kind}
										</span>
									</div>
									<p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
										{e.kind === "secret" && !show
											? maskValue(e.value)
											: e.value}
									</p>
									{e.notes ? (
										<p className="mt-1 text-[11px] text-muted-foreground">
											{e.notes}
										</p>
									) : null}
								</div>
								<div className="flex shrink-0 items-center gap-0.5">
									{e.kind === "secret" ? (
										<Button
											size="sm"
											variant="ghost"
											className="size-7 p-0"
											onClick={() =>
												setRevealed((r) => ({ ...r, [e.id]: !r[e.id] }))
											}
											title={show ? "Hide" : "Reveal"}
										>
											{show ? "🙈" : "👁"}
										</Button>
									) : null}
									<Button
										size="sm"
										variant="ghost"
										className="size-7 p-0"
										onClick={() => copy(e)}
										title="Copy value"
									>
										<CopyIcon className="size-3.5" />
									</Button>
									<Button
										size="sm"
										variant="ghost"
										className="size-7 p-0 text-muted-foreground hover:text-red-400"
										onClick={() => remove(e.id)}
										title="Remove"
									>
										<Trash2Icon className="size-3.5" />
									</Button>
								</div>
							</li>
						);
					})
				)}
			</ul>
		</div>
	);
}
