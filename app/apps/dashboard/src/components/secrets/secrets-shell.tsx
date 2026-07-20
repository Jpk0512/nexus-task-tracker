"use client";

import { Button } from "@ui/components/ui/button";
import { Input } from "@ui/components/ui/input";
import { KeyRoundIcon, LockIcon, ShieldCheckIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { SoftIcon } from "@/components/ui/soft-icon";

type SecretRow = {
	id: string;
	key: string;
	masked: string;
	scope: string;
};

const STORE = "nexus.secrets.local";

/**
 * Secrets — Infisical-style vault UI (local-dev masked store).
 * Production bridges Infisical; never paste secrets into chat/agent context.
 */
export function SecretsShell() {
	const [rows, setRows] = useState<SecretRow[]>([]);
	const [keyName, setKeyName] = useState("");
	const [value, setValue] = useState("");

	useEffect(() => {
		try {
			const raw = localStorage.getItem(STORE);
			if (raw) setRows(JSON.parse(raw) as SecretRow[]);
		} catch {
			/* ignore */
		}
	}, []);

	const persist = (next: SecretRow[]) => {
		setRows(next);
		localStorage.setItem(STORE, JSON.stringify(next));
	};

	const add = () => {
		const k = keyName.trim();
		const v = value.trim();
		if (!k || !v) return;
		const masked =
			v.length <= 4 ? "••••" : `${v.slice(0, 2)}${"•".repeat(Math.min(12, v.length - 4))}${v.slice(-2)}`;
		persist([
			{
				id: crypto.randomUUID(),
				key: k,
				masked,
				scope: "local-dev",
			},
			...rows.filter((r) => r.key !== k),
		]);
		setKeyName("");
		setValue("");
		toast.success("Stored (masked). Value not shown again in UI.");
	};

	const remove = (id: string) => {
		persist(rows.filter((r) => r.id !== id));
		toast.message("Removed");
	};

	return (
		<div className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-4 py-8">
			<div className="flex items-start gap-3">
				<SoftIcon icon={ShieldCheckIcon} tone="green" size="lg" />
				<div>
					<h1 className="font-[510] text-[22px] tracking-[-0.02em]">Secrets</h1>
					<p className="mt-1 text-[13px] text-muted-foreground">
						Infisical-style vault. Values are masked after save. Never paste into
						chat — inject into MCP env only.
					</p>
				</div>
			</div>

			<div className="rounded-xl border border-border/60 bg-card/40 p-4 space-y-3">
				<div className="flex items-center gap-2 text-[12px] font-[510]">
					<LockIcon className="size-3.5" /> Add secret
				</div>
				<div className="grid gap-2 sm:grid-cols-2">
					<Input
						placeholder="KEY_NAME"
						value={keyName}
						onChange={(e) => setKeyName(e.target.value)}
						className="font-mono text-[12.5px]"
					/>
					<Input
						placeholder="value (hidden after save)"
						type="password"
						value={value}
						onChange={(e) => setValue(e.target.value)}
						className="font-mono text-[12.5px]"
					/>
				</div>
				<Button size="sm" onClick={add} disabled={!keyName.trim() || !value.trim()}>
					Save masked
				</Button>
			</div>

			<ul className="space-y-2">
				{rows.length === 0 ? (
					<li className="rounded-xl border border-dashed border-border/60 px-4 py-8 text-center text-[13px] text-muted-foreground">
						No secrets yet. Local-dev store only — Infisical bridge next.
					</li>
				) : (
					rows.map((r) => (
						<li
							key={r.id}
							className="flex items-center gap-3 rounded-xl border border-border/60 bg-card/30 px-3 py-2.5"
						>
							<SoftIcon icon={KeyRoundIcon} tone="gray" size="sm" />
							<div className="min-w-0 flex-1">
								<p className="truncate font-mono text-[12.5px] font-[510]">
									{r.key}
								</p>
								<p className="font-mono text-[11px] text-muted-foreground">
									{r.masked} · {r.scope}
								</p>
							</div>
							<Button size="sm" variant="ghost" onClick={() => remove(r.id)}>
								Remove
							</Button>
						</li>
					))
				)}
			</ul>
		</div>
	);
}
