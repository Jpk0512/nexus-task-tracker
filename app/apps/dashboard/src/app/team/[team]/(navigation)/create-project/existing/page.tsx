"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@ui/components/ui/button";
import { Input } from "@ui/components/ui/input";
import { Label } from "@ui/components/ui/label";
import { Textarea } from "@ui/components/ui/textarea";
import { HardDriveIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { SoftIcon } from "@/components/ui/soft-icon";
import { useUser } from "@/components/user-provider";
import { queryClient, trpc } from "@/utils/trpc";

export default function CreateExistingProjectPage() {
	const user = useUser();
	const router = useRouter();
	const base = user.basePath;

	const [rootInput, setRootInput] = useState("");
	const [docsPath, setDocsPath] = useState("");
	const [name, setName] = useState("");
	const [description, setDescription] = useState("");
	const [prefix, setPrefix] = useState("");
	const [probePath, setProbePath] = useState("");

	const probe = useQuery({
		...trpc.siteDocs.probePath.queryOptions({ path: probePath }),
		enabled: probePath.length > 1,
	});

	const candidates = probe.data?.ok ? probe.data.candidates : [];

	const create = useMutation(
		trpc.projects.create.mutationOptions({
			onSuccess: async (project) => {
				toast.success(`Linked "${project.name}"`);
				await queryClient.invalidateQueries({ queryKey: [["siteDocs"]] });
				await queryClient.invalidateQueries({ queryKey: [["projects"]] });
				router.push(`${base}/documents`);
			},
			onError: (err) => toast.error(err.message),
		}),
	);

	const ensureMaps = useMutation(
		trpc.siteDocs.ensureDefaultMaps.mutationOptions(),
	);

	const canSubmit = useMemo(
		() => name.trim().length > 0 && docsPath.trim().length > 0,
		[name, docsPath],
	);

	useEffect(() => {
		if (probe.data?.ok && !docsPath && candidates[0]) {
			setDocsPath(candidates[0]!);
		}
	}, [probe.data, candidates, docsPath]);

	const onProbe = () => {
		const path = rootInput.trim();
		if (!path) return;
		setProbePath(path);
		const baseName = path.split("/").filter(Boolean).pop() ?? "";
		if (!name && baseName) setName(baseName.replace(/[-_]/g, " "));
		if (!prefix && baseName) {
			const letters = baseName
				.replace(/[^a-zA-Z]/g, "")
				.slice(0, 3)
				.toUpperCase();
			setPrefix(letters || "SITE");
		}
	};

	const onCreate = async () => {
		if (!canSubmit) return;
		const root = probe.data?.resolved ?? rootInput.trim();
		const project = await create.mutateAsync({
			name: name.trim(),
			description: description.trim() || null,
			prefix: prefix.trim() || null,
			rootPath: root,
			docsPath: docsPath.trim(),
			visibility: "team",
		});
		if (project?.id) {
			ensureMaps.mutate({ projectId: project.id });
		}
	};

	return (
		<div className="mx-auto flex max-w-xl flex-col gap-6 px-6 py-10">
			<div className="flex items-start gap-3">
				<SoftIcon icon={HardDriveIcon} tone="orange" size="lg" />
				<div>
					<h1 className="font-[510] text-[20px] tracking-[-0.02em]">
						Link existing site
					</h1>
					<p className="mt-1 text-[13px] text-muted-foreground">
						Mount the site under <code className="text-[11px]">/host/sites/</code>
						, then paste the host path or container path.
					</p>
				</div>
			</div>

			<div className="space-y-4 rounded-xl border border-border/60 bg-card/40 p-5">
				<div className="space-y-1.5">
					<Label htmlFor="root">Site folder on disk</Label>
					<div className="flex gap-2">
						<Input
							id="root"
							value={rootInput}
							onChange={(e) => setRootInput(e.target.value)}
							placeholder="/Users/you/my-site or /host/sites/my-site"
							className="font-mono text-[12px]"
						/>
						<Button type="button" variant="secondary" onClick={onProbe}>
							Probe
						</Button>
					</div>
					{probePath && probe.data && !probe.data.ok ? (
						<p className="text-[12px] text-destructive">{probe.data.error}</p>
					) : null}
					{probe.data?.ok ? (
						<p className="font-mono text-[11px] text-muted-foreground">
							Resolved: {probe.data.resolved}
						</p>
					) : null}
				</div>

				{candidates.length > 0 ? (
					<div className="space-y-1.5">
						<Label>Site Docs folder</Label>
						<div className="flex flex-col gap-1">
							{candidates.map((c) => (
								<button
									key={c}
									type="button"
									onClick={() => setDocsPath(c)}
									className={`rounded-md border px-3 py-2 text-left font-mono text-[11.5px] ${
										docsPath === c
											? "border-primary/50 bg-primary/10"
											: "border-border hover:bg-accent/40"
									}`}
								>
									{c}
								</button>
							))}
						</div>
					</div>
				) : (
					<div className="space-y-1.5">
						<Label htmlFor="docs">Site Docs path</Label>
						<Input
							id="docs"
							value={docsPath}
							onChange={(e) => setDocsPath(e.target.value)}
							placeholder="/host/sites/my-site/docs"
							className="font-mono text-[12px]"
						/>
					</div>
				)}

				<div className="space-y-1.5">
					<Label htmlFor="name">Project name</Label>
					<Input
						id="name"
						value={name}
						onChange={(e) => setName(e.target.value)}
						placeholder="My Site"
					/>
				</div>

				<div className="grid grid-cols-3 gap-3">
					<div className="col-span-1 space-y-1.5">
						<Label htmlFor="prefix">Prefix</Label>
						<Input
							id="prefix"
							value={prefix}
							onChange={(e) => setPrefix(e.target.value.toUpperCase())}
							maxLength={6}
							className="font-mono uppercase"
						/>
					</div>
					<div className="col-span-2 space-y-1.5">
						<Label htmlFor="desc">Description</Label>
						<Textarea
							id="desc"
							value={description}
							onChange={(e) => setDescription(e.target.value)}
							rows={2}
							placeholder="Optional"
						/>
					</div>
				</div>

				<div className="flex items-center justify-between gap-2 pt-2">
					<Button asChild variant="ghost">
						<Link href={`${base}/create-project`}>Back</Link>
					</Button>
					<Button onClick={onCreate} disabled={!canSubmit || create.isPending}>
						{create.isPending ? "Linking..." : "Link site"}
					</Button>
				</div>
			</div>
		</div>
	);
}
