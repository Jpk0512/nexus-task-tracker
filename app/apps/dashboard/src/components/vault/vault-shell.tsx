"use client";
// 'use client': this is a fully interactive editor (mutation hooks, form
// state, clipboard, localStorage migration detection) — none of it has a
// server-rendered fallback, so the boundary is the whole shell, not a slice
// of it.

import type { RouterOutputs } from "@nexus-app/trpc";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, AlertDescription, AlertTitle } from "@ui/components/ui/alert";
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
import { Skeleton } from "@ui/components/ui/skeleton";
import { Textarea } from "@ui/components/ui/textarea";
import { cn } from "@ui/lib/utils";
import {
	AlertTriangleIcon,
	CheckCircle2Icon,
	CopyIcon,
	EyeIcon,
	EyeOffIcon,
	KeyRoundIcon,
	Loader2Icon,
	PencilIcon,
	PlusIcon,
	ServerIcon,
	ShieldAlertIcon,
	ShieldCheckIcon,
	Trash2Icon,
	XCircleIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { SoftIcon } from "@/components/ui/soft-icon";
import { runToastAction } from "@/lib/toast-action";
import { queryClient, trpc } from "@/utils/trpc";

/**
 * Vault — personal collection of secrets/API keys + MCP servers, backed by
 * the server-side `userSecrets` tRPC router (encrypted at rest via
 * TOKEN_ENCRYPTION_KEY). Replaces the earlier plaintext-localStorage version:
 * on mount this also offers a one-time migration of any legacy local data
 * into the new encrypted store (see `LEGACY_VAULT_KEY` / `LEGACY_SECRETS_KEY`
 * below) before the user ever touches the CRUD UI.
 */

type VaultKind = "secret" | "mcp";
type VaultSecret = RouterOutputs["userSecrets"]["list"][number];
type MigrateResult = RouterOutputs["userSecrets"]["migrate"];

// ─── Legacy localStorage shapes (pre-server-backed vault) ───────────────────

const LEGACY_VAULT_KEY = "nexus.vault.local";
const LEGACY_SECRETS_KEY = "nexus.secrets.local";

type LegacyVaultEntry = {
	id: string;
	kind: VaultKind;
	name: string;
	value: string;
	notes?: string;
	createdAt: string;
};

// The old `secrets-shell` local-dev store never persisted the raw value —
// only a masked display string — so these rows have nothing recoverable to
// send to `userSecrets.migrate`. They're surfaced in the migration prompt as
// "no recoverable value" and simply cleared, never fabricated into a fake
// secret value.
type LegacySecretRow = {
	id: string;
	key: string;
	masked: string;
	scope: string;
};

function readLegacyVaultEntries(): LegacyVaultEntry[] {
	try {
		const raw = localStorage.getItem(LEGACY_VAULT_KEY);
		if (!raw) return [];
		const parsed = JSON.parse(raw);
		return Array.isArray(parsed) ? (parsed as LegacyVaultEntry[]) : [];
	} catch {
		return [];
	}
}

function readLegacySecretRows(): LegacySecretRow[] {
	try {
		const raw = localStorage.getItem(LEGACY_SECRETS_KEY);
		if (!raw) return [];
		const parsed = JSON.parse(raw);
		return Array.isArray(parsed) ? (parsed as LegacySecretRow[]) : [];
	} catch {
		return [];
	}
}

type MigrationOutcome = {
	succeeded: string[];
	failed: MigrateResult["errors"];
	invalidSkipped: string[];
	placeholdersCleared: number;
};

// ─── Value masking / MCP JSON validation ────────────────────────────────────

function maskValue(v: string): string {
	if (!v) return "••••";
	if (v.length <= 4) return "••••";
	return `${v.slice(0, 2)}${"•".repeat(Math.min(12, v.length - 4))}${v.slice(-2)}`;
}

function prettyMcpValue(raw: string): string {
	try {
		return JSON.stringify(JSON.parse(raw), null, 2);
	} catch {
		// Pre-existing (migrated) entries may hold the old free-text
		// "command or URL" shape rather than JSON — show it verbatim
		// instead of pretending it parses.
		return raw;
	}
}

/**
 * Basic MCP-server config shape check: valid JSON object describing either
 * an http/sse transport (`url`, optional `headers`) or a stdio transport
 * (`command`, optional `args`/`env`). Intentionally loose — this blocks
 * obviously malformed input, not every possible misconfiguration.
 */
// Returns `null` when valid, or the inline error message to show. (Not a
// `{ ok, error }` discriminated union: this project's tsconfig runs with
// `strictNullChecks: false`, which breaks control-flow narrowing on the
// `ok: false` arm of such unions — a plain nullable string sidesteps that
// entirely and is what every call site below relies on.)
function validateMcpConfig(raw: string): string | null {
	let parsed: unknown;
	try {
		parsed = JSON.parse(raw);
	} catch {
		return "Must be valid JSON.";
	}
	if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
		return "Must be a JSON object describing the MCP server.";
	}
	const obj = parsed as Record<string, unknown>;
	const hasUrl = typeof obj.url === "string" && obj.url.trim().length > 0;
	const hasCommand =
		typeof obj.command === "string" && obj.command.trim().length > 0;
	if (!hasUrl && !hasCommand) {
		return 'Must include a "url" (http/sse transport) or a "command" (stdio transport).';
	}
	if (
		obj.headers !== undefined &&
		(typeof obj.headers !== "object" ||
			obj.headers === null ||
			Array.isArray(obj.headers))
	) {
		return '"headers" must be an object of string values.';
	}
	if (
		obj.args !== undefined &&
		!(Array.isArray(obj.args) && obj.args.every((a) => typeof a === "string"))
	) {
		return '"args" must be an array of strings.';
	}
	if (
		obj.env !== undefined &&
		(typeof obj.env !== "object" || obj.env === null || Array.isArray(obj.env))
	) {
		return '"env" must be an object of string values.';
	}
	return null;
}

function isEncryptionKeyMissingError(error: unknown): boolean {
	return (
		error instanceof Error && error.message.includes("TOKEN_ENCRYPTION_KEY")
	);
}

// ─── Setup callout (TOKEN_ENCRYPTION_KEY missing) ───────────────────────────

function EncryptionSetupCallout() {
	return (
		<Alert variant="destructive" className="mt-4">
			<ShieldAlertIcon />
			<AlertTitle>Secret storage isn't configured yet</AlertTitle>
			<AlertDescription>
				<p>
					The server needs{" "}
					<code className="font-mono">TOKEN_ENCRYPTION_KEY</code> set to a
					64-character hex string (32 random bytes) before secrets can be
					created or read. Generate one:
				</p>
				<pre className="mt-1 w-full overflow-x-auto rounded-md bg-muted px-2 py-1.5 font-mono text-[11px]">
					openssl rand -hex 32
				</pre>
				<p>
					Set the output as{" "}
					<code className="font-mono">TOKEN_ENCRYPTION_KEY</code> in the API's
					environment, then restart it.
				</p>
			</AlertDescription>
		</Alert>
	);
}

// ─── Main shell ──────────────────────────────────────────────────────────────

type FormState = {
	kind: VaultKind;
	name: string;
	value: string;
	notes: string;
};

const emptyForm: FormState = { kind: "secret", name: "", value: "", notes: "" };

type Filter = "all" | VaultKind;

export function VaultShell() {
	const [filter, setFilter] = useState<Filter>("all");
	const [showForm, setShowForm] = useState(false);
	const [editingId, setEditingId] = useState<string | null>(null);
	const [form, setForm] = useState<FormState>(emptyForm);
	const [mcpError, setMcpError] = useState<string | null>(null);
	const [revealed, setRevealed] = useState<Record<string, boolean>>({});
	const [deleteTarget, setDeleteTarget] = useState<VaultSecret | null>(null);
	const [mutationSetupBlocked, setMutationSetupBlocked] = useState(false);

	const [legacyVault, setLegacyVault] = useState<LegacyVaultEntry[]>([]);
	const [legacySecrets, setLegacySecrets] = useState<LegacySecretRow[]>([]);
	const [migrationOpen, setMigrationOpen] = useState(false);
	const [migrationOutcome, setMigrationOutcome] =
		useState<MigrationOutcome | null>(null);

	useEffect(() => {
		const vaultEntries = readLegacyVaultEntries();
		const secretRows = readLegacySecretRows();
		setLegacyVault(vaultEntries);
		setLegacySecrets(secretRows);
		if (vaultEntries.length > 0 || secretRows.length > 0) {
			setMigrationOpen(true);
		}
	}, []);

	const listQuery = useQuery(trpc.userSecrets.list.queryOptions({}));
	const createMutation = useMutation(trpc.userSecrets.create.mutationOptions());
	const updateMutation = useMutation(trpc.userSecrets.update.mutationOptions());
	const deleteMutation = useMutation(trpc.userSecrets.delete.mutationOptions());
	const migrateMutation = useMutation(
		trpc.userSecrets.migrate.mutationOptions(),
	);

	const listBlocked =
		listQuery.isError && isEncryptionKeyMissingError(listQuery.error);
	const showSetupCallout = listBlocked || mutationSetupBlocked;
	const secrets = listQuery.data ?? [];

	const invalidateList = () =>
		queryClient.invalidateQueries(trpc.userSecrets.list.queryOptions({}));

	// ─── Legacy migration ──────────────────────────────────────────────────

	const migratableEntries = useMemo(
		() =>
			legacyVault
				.map((e) => ({
					kind: e.kind,
					name: (e.name ?? "").trim(),
					value: e.value ?? "",
					notes: e.notes,
				}))
				.filter((e) => e.name.length > 0 && e.value.length > 0),
		[legacyVault],
	);

	const invalidVaultEntries = useMemo(
		() => legacyVault.filter((e) => !(e.name ?? "").trim() || !(e.value ?? "")),
		[legacyVault],
	);

	const handleMigrate = () => {
		const payload = migratableEntries;
		runToastAction(() => migrateMutation.mutateAsync({ entries: payload }), {
			id: "vault-migrate",
			loading: "Migrating legacy vault entries…",
			success: (result) =>
				result.errors.length > 0
					? `${result.migrated} migrated, ${result.errors.length} failed`
					: `${result.migrated} entr${result.migrated === 1 ? "y" : "ies"} migrated`,
			error: (err) => {
				// Side effect lives here, not in the `.then()` narrowing below —
				// this project's tsconfig runs with `strictNullChecks: false`,
				// which breaks discriminated-union narrowing on the `ok: false`
				// arm of `ToastActionResult` (confirmed: accessing `.error` after
				// negating/else-narrowing `res.ok` fails to type-check, while the
				// `.data` access on the `ok: true` arm narrows fine). The raw
				// `err` here is unnarrowed and always safe to inspect.
				if (isEncryptionKeyMissingError(err)) setMutationSetupBlocked(true);
				return isEncryptionKeyMissingError(err)
					? "Server secret storage isn't configured yet — nothing was cleared from your browser."
					: "Migration failed — nothing was cleared from your browser.";
			},
		}).then((res) => {
			if (!res.ok) return;

			const result = res.data;
			const failedNames = new Set(result.errors.map((e) => e.name));
			const succeeded = payload
				.map((p) => p.name)
				.filter((name) => !failedNames.has(name));

			// Data-loss guard: only clear entries that actually succeeded —
			// anything the server reported as failed stays in localStorage.
			if (failedNames.size === 0) {
				localStorage.removeItem(LEGACY_VAULT_KEY);
				setLegacyVault([]);
			} else {
				const remaining = legacyVault.filter((e) =>
					failedNames.has((e.name ?? "").trim()),
				);
				localStorage.setItem(LEGACY_VAULT_KEY, JSON.stringify(remaining));
				setLegacyVault(remaining);
			}

			const placeholdersCleared = legacySecrets.length;
			if (placeholdersCleared > 0) {
				localStorage.removeItem(LEGACY_SECRETS_KEY);
				setLegacySecrets([]);
			}

			setMigrationOutcome({
				succeeded,
				failed: result.errors,
				invalidSkipped: invalidVaultEntries.map((e) => e.name || "(unnamed)"),
				placeholdersCleared,
			});

			invalidateList();
		});
	};

	// ─── Form (create + in-place edit) ────────────────────────────────────

	const openCreateForm = () => {
		setEditingId(null);
		setForm(emptyForm);
		setMcpError(null);
		setShowForm(true);
	};

	const openEditForm = (entry: VaultSecret) => {
		setEditingId(entry.id);
		setForm({
			kind: entry.kind,
			name: entry.name,
			value: "",
			notes: entry.notes ?? "",
		});
		setMcpError(null);
		setShowForm(true);
	};

	const resetForm = () => {
		setForm(emptyForm);
		setShowForm(false);
		setEditingId(null);
		setMcpError(null);
	};

	const onValueChange = (next: string) => {
		setForm((f) => ({ ...f, value: next }));
		if (form.kind !== "mcp") {
			setMcpError(null);
			return;
		}
		if (!next.trim()) {
			setMcpError(null);
			return;
		}
		setMcpError(validateMcpConfig(next));
	};

	const isSaving = createMutation.isPending || updateMutation.isPending;

	const handleSubmit = () => {
		const name = form.name.trim();
		const value = form.value.trim();
		const notes = form.notes.trim();

		if (!name) {
			toast.error("Name is required");
			return;
		}
		if (!editingId && !value) {
			toast.error("Value is required");
			return;
		}
		if (form.kind === "mcp" && value) {
			const mcpValidationError = validateMcpConfig(value);
			if (mcpValidationError) {
				setMcpError(mcpValidationError);
				return;
			}
		}
		setMcpError(null);

		if (editingId) {
			runToastAction(
				() =>
					updateMutation.mutateAsync({
						id: editingId,
						name,
						// Blank = "keep the current value" — never overwrite a secret
						// with an empty string just because the field was left blank.
						value: value ? value : undefined,
						notes,
					}),
				{
					id: `vault-update-${editingId}`,
					loading: "Saving changes…",
					success: `${name} updated`,
					error: (err) => {
						if (isEncryptionKeyMissingError(err)) setMutationSetupBlocked(true);
						return isEncryptionKeyMissingError(err)
							? "Server secret storage isn't configured yet."
							: "Failed to update secret";
					},
				},
			).then((res) => {
				if (!res.ok) return;
				resetForm();
				invalidateList();
			});
			return;
		}

		runToastAction(
			() =>
				createMutation.mutateAsync({
					kind: form.kind,
					name,
					value,
					notes: notes || undefined,
				}),
			{
				id: "vault-create",
				loading: `Saving ${form.kind === "secret" ? "secret" : "MCP server"}…`,
				success: `${form.kind === "secret" ? "Secret" : "MCP"} saved`,
				error: (err) => {
					if (isEncryptionKeyMissingError(err)) setMutationSetupBlocked(true);
					return isEncryptionKeyMissingError(err)
						? "Server secret storage isn't configured yet."
						: "Failed to save";
				},
			},
		).then((res) => {
			if (!res.ok) return;
			resetForm();
			invalidateList();
		});
	};

	// ─── Delete ────────────────────────────────────────────────────────────

	const confirmDelete = () => {
		const target = deleteTarget;
		if (!target) return;
		setDeleteTarget(null);
		runToastAction(() => deleteMutation.mutateAsync({ id: target.id }), {
			id: `vault-delete-${target.id}`,
			loading: `Deleting ${target.name}…`,
			success: `${target.name} deleted`,
			error: (err) => {
				if (isEncryptionKeyMissingError(err)) setMutationSetupBlocked(true);
				return isEncryptionKeyMissingError(err)
					? "Server secret storage isn't configured yet."
					: "Failed to delete";
			},
		}).then((res) => {
			if (res.ok) invalidateList();
		});
	};

	// ─── Copy (clipboard only — not a server mutation) ────────────────────

	const copy = async (e: VaultSecret) => {
		try {
			await navigator.clipboard.writeText(e.value);
			toast.success(
				`${e.kind === "secret" ? "Secret" : "MCP config"} value copied`,
			);
		} catch {
			toast.error("Copy failed");
		}
	};

	// ─── Derived list state ────────────────────────────────────────────────

	const counts = useMemo(
		() => ({
			all: secrets.length,
			secret: secrets.filter((e) => e.kind === "secret").length,
			mcp: secrets.filter((e) => e.kind === "mcp").length,
		}),
		[secrets],
	);

	const filtered = useMemo(
		() =>
			filter === "all" ? secrets : secrets.filter((e) => e.kind === filter),
		[secrets, filter],
	);

	const editingEntry = editingId
		? secrets.find((e) => e.id === editingId)
		: undefined;
	const legacyPending = legacyVault.length > 0 || legacySecrets.length > 0;

	return (
		<div className="mx-auto flex h-full w-full max-w-2xl flex-col overflow-y-auto px-4 py-8">
			<div className="flex items-start gap-3">
				<SoftIcon icon={ShieldCheckIcon} tone="green" size="lg" />
				<div className="flex-1">
					<h1 className="font-[510] text-[22px] tracking-[-0.02em]">Vault</h1>
					<p className="mt-1 text-[13px] text-muted-foreground">
						Your collection of secrets, API keys, and MCP servers — encrypted
						server-side. Values masked by default. Never paste into chat.
					</p>
				</div>
				{!listBlocked ? (
					<Button
						size="sm"
						onClick={() =>
							showForm && !editingId ? setShowForm(false) : openCreateForm()
						}
						className="gap-1.5"
					>
						<PlusIcon className="size-3.5" />
						Add
					</Button>
				) : null}
			</div>

			{showSetupCallout ? <EncryptionSetupCallout /> : null}

			{!migrationOpen && legacyPending ? (
				<button
					type="button"
					onClick={() => {
						setMigrationOutcome(null);
						setMigrationOpen(true);
					}}
					className="mt-4 flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-600 hover:bg-amber-500/15"
				>
					<AlertTriangleIcon className="size-3.5 shrink-0" />
					{legacyVault.length + legacySecrets.length} legacy entr
					{legacyVault.length + legacySecrets.length === 1 ? "y" : "ies"} still
					stored in this browser — migrate to encrypted storage
				</button>
			) : null}

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

			{/* Add / edit form */}
			{showForm ? (
				<div className="mt-4 space-y-3 rounded-xl border border-border/60 bg-card/40 p-4">
					{editingId ? (
						<div className="inline-flex items-center gap-1.5 rounded-md border border-border/60 px-2.5 py-1 text-[12px] text-muted-foreground">
							{form.kind === "secret" ? (
								<KeyRoundIcon className="size-3.5" />
							) : (
								<ServerIcon className="size-3.5" />
							)}
							Editing{" "}
							{form.kind === "secret" ? "secret / API key" : "MCP server"}
						</div>
					) : (
						<div className="inline-flex rounded-md border border-border/60 p-0.5 text-[12px]">
							<button
								type="button"
								onClick={() => setForm((f) => ({ ...f, kind: "secret" }))}
								className={cn(
									"flex items-center gap-1.5 rounded px-2.5 py-1",
									form.kind === "secret"
										? "bg-accent"
										: "text-muted-foreground",
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
					)}

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

					{editingId ? (
						<div className="space-y-1.5">
							<div className="flex items-center justify-between gap-2 rounded-md border border-border/60 bg-background/40 px-2.5 py-1.5">
								<span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground">
									{revealed[editingId]
										? editingEntry?.value
										: maskValue(editingEntry?.value ?? "")}
								</span>
								<Button
									size="sm"
									variant="ghost"
									className="size-6 shrink-0 p-0"
									onClick={() =>
										setRevealed((r) => ({ ...r, [editingId]: !r[editingId] }))
									}
									title={
										revealed[editingId]
											? "Hide current value"
											: "Reveal current value"
									}
								>
									{revealed[editingId] ? (
										<EyeOffIcon className="size-3.5" />
									) : (
										<EyeIcon className="size-3.5" />
									)}
								</Button>
							</div>
							{form.kind === "mcp" ? (
								<Textarea
									value={form.value}
									onChange={(e) => onValueChange(e.target.value)}
									placeholder="New MCP server JSON (leave blank to keep current)"
									className="min-h-[88px] font-mono text-[12px]"
								/>
							) : (
								<Input
									value={form.value}
									onChange={(e) => onValueChange(e.target.value)}
									placeholder="New value (leave blank to keep current)"
									type="password"
									className="font-mono text-[12.5px]"
								/>
							)}
						</div>
					) : form.kind === "mcp" ? (
						<Textarea
							value={form.value}
							onChange={(e) => onValueChange(e.target.value)}
							placeholder='{"url": "https://...", "headers": {...}} or {"command": "npx", "args": [...]}'
							className="min-h-[88px] font-mono text-[12px]"
						/>
					) : (
						<Input
							value={form.value}
							onChange={(e) => onValueChange(e.target.value)}
							placeholder="secret value (hidden after save)"
							type="password"
							className="font-mono text-[12.5px]"
						/>
					)}

					{mcpError ? (
						<p className="text-[11.5px] text-red-400">{mcpError}</p>
					) : null}

					<Textarea
						value={form.notes}
						onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
						placeholder="Notes (optional) — scope, project, where it's used…"
						className="min-h-[56px] text-[12.5px]"
					/>
					<div className="flex justify-end gap-2">
						<Button size="sm" variant="ghost" onClick={resetForm}>
							Cancel
						</Button>
						<Button
							size="sm"
							onClick={handleSubmit}
							disabled={isSaving || (form.kind === "mcp" && !!mcpError)}
						>
							{isSaving ? (
								<Loader2Icon className="size-3.5 animate-spin" />
							) : null}
							{editingId ? "Save changes" : "Save"}
						</Button>
					</div>
				</div>
			) : null}

			{/* List */}
			{showSetupCallout ? null : listQuery.isLoading ? (
				<div className="mt-4 space-y-2" aria-hidden>
					{Array.from({ length: 3 }).map((_, i) => (
						<Skeleton
							key={`vault-skel-${i}`}
							className="h-[52px] w-full rounded-xl"
						/>
					))}
				</div>
			) : (
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
										{show ? (
											e.kind === "mcp" ? (
												<pre className="mt-0.5 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-md bg-background/60 px-2 py-1.5 font-mono text-[11px] text-foreground/90">
													{prettyMcpValue(e.value)}
												</pre>
											) : (
												<p className="mt-0.5 break-all font-mono text-[11px] text-foreground/90">
													{e.value}
												</p>
											)
										) : (
											<p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
												{maskValue(e.value)}
											</p>
										)}
										{e.notes ? (
											<p className="mt-1 text-[11px] text-muted-foreground">
												{e.notes}
											</p>
										) : null}
									</div>
									<div className="flex shrink-0 items-center gap-0.5">
										<Button
											size="sm"
											variant="ghost"
											className="size-7 p-0"
											onClick={() =>
												setRevealed((r) => ({ ...r, [e.id]: !r[e.id] }))
											}
											title={show ? "Hide" : "Reveal"}
										>
											{show ? (
												<EyeOffIcon className="size-3.5" />
											) : (
												<EyeIcon className="size-3.5" />
											)}
										</Button>
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
											className="size-7 p-0"
											onClick={() => openEditForm(e)}
											title="Edit"
										>
											<PencilIcon className="size-3.5" />
										</Button>
										<Button
											size="sm"
											variant="ghost"
											className="size-7 p-0 text-muted-foreground hover:text-red-400"
											onClick={() => setDeleteTarget(e)}
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
			)}

			{/* Delete confirmation */}
			<Dialog
				open={!!deleteTarget}
				onOpenChange={(open) => {
					if (!open) setDeleteTarget(null);
				}}
			>
				<DialogContent className="max-w-md">
					<DialogHeader>
						<DialogTitle>Delete "{deleteTarget?.name}"?</DialogTitle>
						<DialogDescription>
							This can't be undone. The{" "}
							{deleteTarget?.kind === "mcp" ? "MCP server" : "secret"} will be
							permanently removed from the server.
						</DialogDescription>
					</DialogHeader>
					<DialogFooter>
						<Button variant="ghost" onClick={() => setDeleteTarget(null)}>
							Cancel
						</Button>
						<Button variant="destructive" onClick={confirmDelete}>
							<Trash2Icon className="size-3.5" />
							Delete
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			{/* Legacy localStorage migration */}
			<Dialog
				open={migrationOpen}
				onOpenChange={(open) => {
					if (!open) {
						setMigrationOpen(false);
						setMigrationOutcome(null);
					}
				}}
			>
				<DialogContent className="max-w-lg">
					<DialogHeader>
						<DialogTitle>
							Move legacy vault data to encrypted storage
						</DialogTitle>
						<DialogDescription>
							{migrationOutcome
								? "Here's what happened:"
								: "We found secrets stored in this browser's local storage. Move them to the server, where they're encrypted at rest, instead of sitting in plaintext on this device."}
						</DialogDescription>
					</DialogHeader>

					<div className="max-h-[50vh] space-y-3 overflow-y-auto px-4 py-3 text-[12.5px]">
						{!migrationOutcome ? (
							<>
								{migratableEntries.length > 0 ? (
									<div>
										<p className="mb-1 font-[510] text-muted-foreground">
											{migratableEntries.length} entr
											{migratableEntries.length === 1 ? "y" : "ies"} to migrate
										</p>
										<ul className="space-y-1">
											{migratableEntries.map((e) => (
												<li
													key={e.name}
													className="flex items-center gap-2 rounded-md border border-border/60 px-2 py-1"
												>
													<SoftIcon
														icon={
															e.kind === "secret" ? KeyRoundIcon : ServerIcon
														}
														tone={e.kind === "secret" ? "yellow" : "teal"}
														size="sm"
													/>
													<span className="truncate font-mono">{e.name}</span>
												</li>
											))}
										</ul>
									</div>
								) : null}
								{invalidVaultEntries.length > 0 ? (
									<p className="text-muted-foreground">
										{invalidVaultEntries.length} invalid entr
										{invalidVaultEntries.length === 1 ? "y" : "ies"} (missing
										name or value) will be skipped.
									</p>
								) : null}
								{legacySecrets.length > 0 ? (
									<p className="text-muted-foreground">
										{legacySecrets.length} legacy secret placeholder
										{legacySecrets.length === 1 ? "" : "s"} from an older
										local-only store — these never held a recoverable value
										(only a masked display string), so they'll simply be
										cleared.
									</p>
								) : null}
							</>
						) : (
							<>
								{migrationOutcome.succeeded.length > 0 ? (
									<div>
										<p className="mb-1 flex items-center gap-1.5 font-[510] text-emerald-500">
											<CheckCircle2Icon className="size-3.5" /> Migrated (
											{migrationOutcome.succeeded.length})
										</p>
										<ul className="space-y-0.5 pl-5 text-muted-foreground">
											{migrationOutcome.succeeded.map((name) => (
												<li key={name} className="font-mono">
													{name}
												</li>
											))}
										</ul>
									</div>
								) : null}
								{migrationOutcome.failed.length > 0 ? (
									<div>
										<p className="mb-1 flex items-center gap-1.5 font-[510] text-red-400">
											<XCircleIcon className="size-3.5" /> Failed (
											{migrationOutcome.failed.length}) — kept in your browser
										</p>
										<ul className="space-y-0.5 pl-5 text-muted-foreground">
											{migrationOutcome.failed.map((f) => (
												<li key={f.name}>
													<span className="font-mono">{f.name}</span> —{" "}
													{f.message}
												</li>
											))}
										</ul>
									</div>
								) : null}
								{migrationOutcome.invalidSkipped.length > 0 ? (
									<p className="text-muted-foreground">
										Skipped (missing name/value):{" "}
										{migrationOutcome.invalidSkipped.join(", ")}
									</p>
								) : null}
								{migrationOutcome.placeholdersCleared > 0 ? (
									<p className="text-muted-foreground">
										{migrationOutcome.placeholdersCleared} legacy placeholder
										{migrationOutcome.placeholdersCleared === 1 ? "" : "s"}{" "}
										cleared (no value existed to migrate).
									</p>
								) : null}
							</>
						)}
					</div>

					<DialogFooter>
						{!migrationOutcome ? (
							<>
								<Button variant="ghost" onClick={() => setMigrationOpen(false)}>
									Not now
								</Button>
								<Button
									onClick={handleMigrate}
									disabled={migrateMutation.isPending}
								>
									{migrateMutation.isPending ? (
										<Loader2Icon className="size-3.5 animate-spin" />
									) : null}
									{migratableEntries.length > 0
										? "Migrate to encrypted storage"
										: "Clear legacy data"}
								</Button>
							</>
						) : (
							<Button
								onClick={() => {
									setMigrationOpen(false);
									setMigrationOutcome(null);
								}}
							>
								Done
							</Button>
						)}
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</div>
	);
}
