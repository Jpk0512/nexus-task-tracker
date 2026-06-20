"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@ui/components/ui/button";
import { Input } from "@ui/components/ui/input";
import { Label } from "@ui/components/ui/label";
import { Skeleton } from "@ui/components/ui/skeleton";
import {
	AlertCircleIcon,
	BrainIcon,
	CheckIcon,
	RefreshCwIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { trpc } from "@/utils/trpc";

// Knowledge vault root-path settings form (GWT#8). Submits root_path via the
// knowledge.updateVault mutation and reflects the saved value by invalidating
// getVaults on success. One vault per team in the current model. Palette §5.

type VaultRow = {
	id: string;
	label: string;
	rootPath: string;
	isDefault: boolean;
	lastScannedAt: string | null;
	noteCount: number;
};

export function VaultSettingsForm() {
	const qc = useQueryClient();
	const vaultsQuery = useQuery(trpc.knowledge.getVaults.queryOptions());
	const vault = (vaultsQuery.data as VaultRow[] | undefined)?.[0] ?? null;
	const savedRootPath = vault?.rootPath ?? null;

	const [rootPath, setRootPath] = useState("");
	const [showSaved, setShowSaved] = useState(false);

	// Seed the input from the saved value once the vault loads, and re-seed
	// whenever the persisted rootPath changes (after a successful save).
	useEffect(() => {
		if (savedRootPath !== null) setRootPath(savedRootPath);
	}, [savedRootPath]);

	const updateMut = useMutation(
		trpc.knowledge.updateVault.mutationOptions({
			onSuccess: (saved) => {
				const next = (saved as { rootPath?: string } | undefined)?.rootPath;
				if (typeof next === "string") setRootPath(next);
				setShowSaved(true);
				qc.invalidateQueries({ queryKey: [["knowledge", "getVaults"]] });
			},
		}),
	);

	useEffect(() => {
		if (!showSaved) return;
		const t = setTimeout(() => setShowSaved(false), 2000);
		return () => clearTimeout(t);
	}, [showSaved]);

	if (vaultsQuery.isLoading) {
		return (
			<div className="space-y-4">
				<Skeleton className="h-9 w-full rounded-md" />
				<Skeleton className="mt-4 h-9 w-24 rounded-md" />
			</div>
		);
	}

	if (vaultsQuery.isError) {
		return (
			<div className="flex items-center gap-2 py-2 text-[12px] text-destructive">
				<AlertCircleIcon className="size-3.5" />
				<span>Could not load vault settings. Reload the page to retry.</span>
			</div>
		);
	}

	const dirty = !!vault && rootPath.trim() !== vault.rootPath;
	const error = updateMut.error;

	return (
		<form
			className="space-y-2"
			onSubmit={(e) => {
				e.preventDefault();
				if (!vault || !dirty) return;
				updateMut.mutate({ vaultId: vault.id, root_path: rootPath.trim() });
			}}
		>
			{!vault && (
				<div className="flex flex-col items-center gap-3 py-8 text-center">
					<BrainIcon className="size-8 text-muted-foreground/60" />
					<p className="text-[13px] text-muted-foreground">
						No vault configured. Enter a root path above to connect your
						Obsidian vault.
					</p>
				</div>
			)}
			<div className="space-y-1">
				<Label htmlFor="vault-root-path" className="font-[510] text-[13px]">
					Vault root path
				</Label>
				<Input
					id="vault-root-path"
					value={rootPath}
					onChange={(e) => setRootPath(e.target.value)}
					placeholder="/Users/you/vault"
					className="h-9 text-[13px]"
					disabled={!vault || updateMut.isPending}
				/>
				<p className="mt-1 text-[12px] text-muted-foreground">
					Absolute path on the server host. Changes take effect on next scan.
				</p>
			</div>
			<div className="flex items-center gap-2 pt-1">
				<Button
					type="submit"
					size="default"
					disabled={!vault || !dirty || updateMut.isPending}
				>
					{updateMut.isPending ? (
						<>
							<RefreshCwIcon className="size-3.5 animate-spin" /> Saving…
						</>
					) : (
						"Save"
					)}
				</Button>
				{showSaved && !updateMut.isPending && (
					<span className="ml-2 flex items-center gap-1 text-[12px] text-[var(--color-success)] transition-opacity duration-500">
						<CheckIcon className="size-3.5" /> Saved
					</span>
				)}
				{error && (
					<span className="ml-2 flex items-center gap-1 text-[12px] text-destructive">
						<AlertCircleIcon className="size-3.5" />
						{/NOT_FOUND/i.test(error.message)
							? "Vault not found — check your team settings"
							: error.message}
					</span>
				)}
			</div>
		</form>
	);
}
