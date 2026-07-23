"use client";

import type { RouterOutputs } from "@nexus-app/trpc";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
	AlertDialogTrigger,
} from "@nexus-app/ui/alert-dialog";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, AlertDescription, AlertTitle } from "@ui/components/ui/alert";
import { Badge } from "@ui/components/ui/badge";
import { Button } from "@ui/components/ui/button";
import {
	Card,
	CardContent,
	CardHeader,
	CardTitle,
} from "@ui/components/ui/card";
import { Checkbox } from "@ui/components/ui/checkbox";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
	DialogTrigger,
} from "@ui/components/ui/dialog";
import {
	Form,
	FormControl,
	FormDescription,
	FormField,
	FormItem,
	FormLabel,
	FormMessage,
} from "@ui/components/ui/form";
import { Input } from "@ui/components/ui/input";
import { Label } from "@ui/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@ui/components/ui/radio-group";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@ui/components/ui/select";
import {
	AlertTriangle,
	Check,
	Copy,
	Key,
	Loader2,
	Pencil,
	Plus,
	Trash2,
} from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { useZodForm } from "@/hooks/use-zod-form";
import { authClient } from "@/lib/auth-client";
import { queryClient, trpc } from "@/utils/trpc";

type ApiKeyRow = RouterOutputs["apiKeys"]["list"][number];

/**
 * Better Auth's api-key plugin schema declares `metadata` as `type: "string"`
 * and JSON.stringifies it on write (its own transform, independent of the
 * column type); the `apikey.metadata` column is `jsonb`, so the driver
 * JSON-encodes that already-stringified value a second time. Reading it back
 * through a plain drizzle select (as apiKeysRouter.list does, bypassing
 * Better Auth's own `transform.output`) yields the metadata as a raw JSON
 * *string*, not a parsed object -- confirmed live via the tRPC response
 * (`"metadata":"{\"teamId\":...}"`, a string value) rather than an object.
 * Any consumer that assumes `metadata` is already an object (a `typeof
 * === "object"` guard, or spreading it with `...`) silently breaks: the
 * guard falls through to its "no metadata" default, and spreading a string
 * enumerates its characters into numeric keys instead of merging fields.
 * Parse defensively before touching the shape.
 */
function parseApiKeyMetadata(metadata: unknown): Record<string, unknown> {
	let value: unknown = metadata;
	if (typeof value === "string") {
		try {
			value = JSON.parse(value);
		} catch {
			return {};
		}
	}
	if (!value || typeof value !== "object") return {};
	return value as Record<string, unknown>;
}

/**
 * Mirrors the gateway leg's contract (app/apps/api/src/rest/routers/mcp.ts,
 * ApiKeyMetadata / resolveMcpServerScope): a key's metadata.mcpServers is
 * `"all" | string[]`; missing entirely defaults to `"all"` (same permissive
 * posture as native tools). Reimplemented here rather than imported since
 * apps/api and apps/dashboard are separate deployables.
 */
function resolveMcpServerScope(metadata: unknown): "all" | string[] {
	const value = parseApiKeyMetadata(metadata).mcpServers;
	if (value === undefined || value === "all") return "all";
	if (Array.isArray(value)) {
		return value.filter((id): id is string => typeof id === "string");
	}
	return "all";
}

const createApiKeySchema = z
	.object({
		name: z.string().min(1, "Name is required").max(100),
		expiresIn: z.enum(["never", "30d", "90d", "1y"]).default("never"),
		mcpScope: z.enum(["all", "custom"]).default("all"),
		mcpServerIds: z.array(z.string()).default([]),
	})
	.refine(
		(data) => data.mcpScope !== "custom" || data.mcpServerIds.length > 0,
		{
			message: "Select at least one MCP server",
			path: ["mcpServerIds"],
		},
	);

export function ApiKeysList() {
	const [isCreateOpen, setIsCreateOpen] = useState(false);
	const [newApiKey, setNewApiKey] = useState<string | null>(null);
	const [copiedField, setCopiedField] = useState<string | null>(null);
	const [editingKey, setEditingKey] = useState<ApiKeyRow | null>(null);
	const [editScope, setEditScope] = useState<"all" | "custom">("all");
	const [editServerIds, setEditServerIds] = useState<string[]>([]);

	const { data: session } = authClient.useSession();

	// Fetch user's API keys
	const { data: apiKeys, isLoading } = useQuery(
		trpc.apiKeys.list.queryOptions({}),
	);

	// Fetch the team's configured MCP servers, for the exposure selector +
	// resolving subset ids to names on each key row.
	const { data: mcpServers } = useQuery(
		trpc.mcpServers.list.queryOptions({ activeOnly: true }),
	);

	const serverNameById = useMemo(
		() => new Map((mcpServers ?? []).map((server) => [server.id, server.name])),
		[mcpServers],
	);

	// Create new API key mutation. mcpServers metadata isn't part of Better
	// Auth's createApiKey input, so it's set via a follow-up apiKey.update
	// call once the key exists (see handleCreate).
	const { mutateAsync: createApiKey, isPending: isCreating } = useMutation(
		trpc.apiKeys.create.mutationOptions(),
	);

	// Delete API key mutation
	const { mutate: deleteApiKey, isPending: isDeleting } = useMutation(
		trpc.apiKeys.delete.mutationOptions({
			onSuccess: () => {
				queryClient.invalidateQueries(trpc.apiKeys.list.queryOptions({}));
				toast.success("API key deleted");
			},
			onError: () => {
				toast.error("Failed to delete API key");
			},
		}),
	);

	// Update an existing key's MCP server exposure via Better Auth's
	// apiKey.update endpoint (metadata is a full replace server-side, so
	// callers must merge with the key's existing metadata themselves).
	// authClient calls resolve to { data, error } instead of throwing, so the
	// error branch must be checked explicitly -- otherwise a failed request
	// (e.g. an invalid/expired session) surfaces as a false "updated" toast.
	const { mutate: updateMcpAccess, isPending: isUpdatingAccess } = useMutation({
		mutationFn: async (vars: {
			keyId: string;
			metadata: Record<string, unknown>;
		}) => {
			const { data, error } = await authClient.apiKey.update({
				keyId: vars.keyId,
				metadata: vars.metadata,
			});
			if (error) {
				throw new Error(error.message ?? "Failed to update MCP server access");
			}
			return data;
		},
		onSuccess: () => {
			queryClient.invalidateQueries(trpc.apiKeys.list.queryOptions({}));
			toast.success("MCP server access updated");
			setEditingKey(null);
		},
		onError: () => {
			toast.error("Failed to update MCP server access");
		},
	});

	const form = useZodForm(createApiKeySchema, {
		defaultValues: {
			name: "",
			expiresIn: "never",
			mcpScope: "all",
			mcpServerIds: [],
		},
	});

	const handleCreate = async (data: z.infer<typeof createApiKeySchema>) => {
		// Calculate expiration in seconds
		let expiresIn: number | undefined;
		if (data.expiresIn !== "never") {
			const durations: Record<string, number> = {
				"30d": 30 * 24 * 60 * 60, // 30 days in seconds
				"90d": 90 * 24 * 60 * 60, // 90 days in seconds
				"1y": 365 * 24 * 60 * 60, // 1 year in seconds
			};
			expiresIn = durations[data.expiresIn];
		}

		let result: { id: string; key: string; name: string };
		try {
			result = await createApiKey({ name: data.name, expiresIn });
		} catch {
			toast.error("Failed to create API key");
			return;
		}

		if (result?.key) {
			setNewApiKey(result.key);
		}

		try {
			const { error } = await authClient.apiKey.update({
				keyId: result.id,
				metadata: {
					teamId: (session?.user as { teamId?: string } | undefined)?.teamId,
					mcpServers: data.mcpScope === "all" ? "all" : data.mcpServerIds,
				},
			});
			if (error) {
				throw new Error(error.message ?? "Failed to set MCP server access");
			}
			toast.success("API key created");
		} catch {
			toast.error("API key created, but MCP server access could not be set");
		}

		queryClient.invalidateQueries(trpc.apiKeys.list.queryOptions({}));
	};

	const openEditAccess = (key: ApiKeyRow) => {
		const scope = resolveMcpServerScope(key.metadata);
		setEditScope(scope === "all" ? "all" : "custom");
		setEditServerIds(scope === "all" ? [] : scope);
		setEditingKey(key);
	};

	const handleCopy = async (text: string, field: string) => {
		await navigator.clipboard.writeText(text);
		setCopiedField(field);
		setTimeout(() => setCopiedField(null), 2000);
		toast.success("Copied to clipboard");
	};

	const handleCloseSecret = () => {
		setNewApiKey(null);
		setIsCreateOpen(false);
		form.reset();
	};

	const formatDate = (date: Date | string | null) => {
		if (!date) return "Never";
		return new Date(date).toLocaleDateString("en-US", {
			year: "numeric",
			month: "short",
			day: "numeric",
		});
	};

	if (isLoading) {
		return (
			<div className="flex items-center justify-center p-8">
				<Loader2 className="size-6 animate-spin text-muted-foreground" />
			</div>
		);
	}

	return (
		<div className="space-y-6">
			<div className="flex items-center justify-between">
				<div>
					<h2 className="font-semibold text-lg">API Keys</h2>
					<p className="text-muted-foreground text-sm">
						Manage API keys for MCP integrations and external applications
					</p>
				</div>
				<Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen}>
					<DialogTrigger asChild>
						<Button>
							<Plus className="mr-2 size-4" />
							Create API Key
						</Button>
					</DialogTrigger>
					<DialogContent className="max-w-max!">
						{newApiKey ? (
							<>
								<DialogHeader>
									<DialogTitle className="flex items-center gap-2">
										<Check className="size-5 text-green-500" />
										API Key Created Successfully
									</DialogTitle>
									<DialogDescription>
										Save your API key now. You won't be able to see it again.
									</DialogDescription>
								</DialogHeader>
								<div className="space-y-4 py-4">
									<Alert variant="destructive">
										<AlertTriangle />
										<AlertTitle>
											Make sure to copy your API key now. You won't be able to
											see it again!
										</AlertTitle>
										<AlertDescription>
											Store it securely, as it provides access to your Nexus
											account.
										</AlertDescription>
									</Alert>
									<div className="space-y-2">
										<span className="font-medium text-sm">API Key</span>
										<div className="flex items-center gap-2">
											<code className="flex-1 break-all rounded-md border bg-muted p-3 font-mono text-sm">
												{newApiKey}
											</code>
											<Button
												variant="outline"
												size="icon"
												onClick={() => handleCopy(newApiKey, "key")}
											>
												{copiedField === "key" ? (
													<Check className="size-4" />
												) : (
													<Copy className="size-4" />
												)}
											</Button>
										</div>
									</div>
									<div className="space-y-2">
										<span className="font-medium text-sm">
											VS Code MCP Configuration
										</span>
										<pre className="overflow-x-auto rounded-md border bg-muted p-3 font-mono text-xs">
											{JSON.stringify(
												{
													servers: {
														nexus: {
															url: `${process.env.NEXT_PUBLIC_SERVER_URL}/mcp`,
															type: "http",
															headers: {
																"x-api-key": newApiKey,
															},
														},
													},
												},
												null,
												2,
											)}
										</pre>
										<Button
											variant="outline"
											size="sm"
											className="w-full"
											onClick={() =>
												handleCopy(
													JSON.stringify(
														{
															servers: {
																nexus: {
																	url: `${process.env.NEXT_PUBLIC_SERVER_URL}/mcp`,
																	type: "http",
																	headers: {
																		"x-api-key": newApiKey,
																	},
																},
															},
														},
														null,
														2,
													),
													"config",
												)
											}
										>
											{copiedField === "config" ? (
												<>
													<Check className="mr-2 size-4" />
													Copied!
												</>
											) : (
												<>
													<Copy className="mr-2 size-4" />
													Copy MCP Config
												</>
											)}
										</Button>
									</div>
								</div>
								<DialogFooter>
									<Button onClick={handleCloseSecret}>Done</Button>
								</DialogFooter>
							</>
						) : (
							<>
								<DialogHeader>
									<DialogTitle>Create API Key</DialogTitle>
									<DialogDescription>
										Create a new API key to connect MCP clients like VS Code,
										Cursor, or other AI assistants to your Nexus account.
									</DialogDescription>
								</DialogHeader>
								<Form {...form}>
									<form
										onSubmit={form.handleSubmit(handleCreate)}
										className="space-y-4"
									>
										<FormField
											control={form.control}
											name="name"
											render={({ field }) => (
												<FormItem>
													<FormLabel>Name</FormLabel>
													<FormControl>
														<Input placeholder="VS Code MCP" {...field} />
													</FormControl>
													<FormDescription>
														A friendly name to identify this API key
													</FormDescription>
													<FormMessage />
												</FormItem>
											)}
										/>
										<FormField
											control={form.control}
											name="expiresIn"
											render={({ field }) => (
												<FormItem>
													<FormLabel>Expiration</FormLabel>
													<Select
														onValueChange={field.onChange}
														defaultValue={field.value}
													>
														<FormControl>
															<SelectTrigger>
																<SelectValue placeholder="Select expiration" />
															</SelectTrigger>
														</FormControl>
														<SelectContent>
															<SelectItem value="never">
																Never expires
															</SelectItem>
															<SelectItem value="30d">30 days</SelectItem>
															<SelectItem value="90d">90 days</SelectItem>
															<SelectItem value="1y">1 year</SelectItem>
														</SelectContent>
													</Select>
													<FormDescription>
														When the API key should expire
													</FormDescription>
													<FormMessage />
												</FormItem>
											)}
										/>
										<FormField
											control={form.control}
											name="mcpScope"
											render={({ field }) => (
												<FormItem className="space-y-3">
													<FormLabel>MCP Server Access</FormLabel>
													<FormControl>
														<RadioGroup
															value={field.value}
															onValueChange={field.onChange}
															className="gap-2"
														>
															<div className="flex items-center gap-2">
																<RadioGroupItem
																	value="all"
																	id="mcp-scope-all"
																/>
																<Label
																	htmlFor="mcp-scope-all"
																	className="font-normal"
																>
																	All MCP servers (default)
																</Label>
															</div>
															<div className="flex items-center gap-2">
																<RadioGroupItem
																	value="custom"
																	id="mcp-scope-custom"
																/>
																<Label
																	htmlFor="mcp-scope-custom"
																	className="font-normal"
																>
																	Specific servers
																</Label>
															</div>
														</RadioGroup>
													</FormControl>
													<FormDescription>
														Which of the team's configured MCP servers this key
														can proxy tools from.
													</FormDescription>
													<FormMessage />
												</FormItem>
											)}
										/>
										{form.watch("mcpScope") === "custom" && (
											<FormField
												control={form.control}
												name="mcpServerIds"
												render={({ field }) => (
													<FormItem>
														<div className="space-y-2 rounded-md border p-3">
															{!mcpServers || mcpServers.length === 0 ? (
																<p className="text-muted-foreground text-xs">
																	No MCP servers configured for this team yet.
																</p>
															) : (
																mcpServers.map((server) => (
																	<div
																		key={server.id}
																		className="flex items-center gap-2"
																	>
																		<Checkbox
																			id={`mcp-server-${server.id}`}
																			checked={
																				field.value?.includes(server.id) ??
																				false
																			}
																			onCheckedChange={(checked) => {
																				const current = field.value ?? [];
																				field.onChange(
																					checked
																						? [...current, server.id]
																						: current.filter(
																								(id: string) =>
																									id !== server.id,
																							),
																				);
																			}}
																		/>
																		<Label
																			htmlFor={`mcp-server-${server.id}`}
																			className="font-normal text-sm"
																		>
																			{server.name}
																		</Label>
																	</div>
																))
															)}
														</div>
														<FormMessage />
													</FormItem>
												)}
											/>
										)}
										<DialogFooter>
											<Button
												type="button"
												variant="outline"
												onClick={() => setIsCreateOpen(false)}
											>
												Cancel
											</Button>
											<Button type="submit" disabled={isCreating}>
												{isCreating && (
													<Loader2 className="mr-2 size-4 animate-spin" />
												)}
												Create
											</Button>
										</DialogFooter>
									</form>
								</Form>
							</>
						)}
					</DialogContent>
				</Dialog>
			</div>

			{!apiKeys || apiKeys.length === 0 ? (
				<Card>
					<CardContent className="flex flex-col items-center justify-center py-12">
						<Key className="mb-4 size-12 text-muted-foreground/50" />
						<h3 className="mb-2 font-medium">No API Keys</h3>
						<p className="mb-4 max-w-sm text-center text-muted-foreground text-sm">
							Create an API key to connect MCP clients like VS Code or Cursor to
							your Nexus tasks.
						</p>
						<Button onClick={() => setIsCreateOpen(true)}>
							<Plus className="mr-2 size-4" />
							Create Your First API Key
						</Button>
					</CardContent>
				</Card>
			) : (
				<div className="space-y-4">
					{apiKeys.map((apiKey) => (
						<Card key={apiKey.id}>
							<CardHeader className="pb-3">
								<div className="flex items-start justify-between">
									<div>
										<CardTitle className="flex items-center gap-2 text-base">
											{apiKey.name || "Unnamed Key"}
											{!apiKey.enabled && (
												<Badge variant="secondary">Disabled</Badge>
											)}
											{apiKey.expiresAt &&
												new Date(apiKey.expiresAt) < new Date() && (
													<Badge variant="destructive">Expired</Badge>
												)}
										</CardTitle>
									</div>
									<AlertDialog>
										<AlertDialogTrigger asChild>
											<Button
												variant="ghost"
												size="icon"
												disabled={isDeleting}
												aria-label={`Delete ${apiKey.name || "Unnamed Key"}`}
											>
												<Trash2 className="size-4 text-muted-foreground" />
											</Button>
										</AlertDialogTrigger>
										<AlertDialogContent>
											<AlertDialogHeader>
												<AlertDialogTitle>Delete API Key?</AlertDialogTitle>
												<AlertDialogDescription>
													This will permanently delete the API key and revoke
													all access. Any applications using this key will stop
													working immediately.
												</AlertDialogDescription>
											</AlertDialogHeader>
											<AlertDialogFooter>
												<AlertDialogCancel>Cancel</AlertDialogCancel>
												<AlertDialogAction
													onClick={() => deleteApiKey({ id: apiKey.id })}
													className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
												>
													Delete
												</AlertDialogAction>
											</AlertDialogFooter>
										</AlertDialogContent>
									</AlertDialog>
								</div>
							</CardHeader>
							<CardContent className="space-y-3">
								<div className="flex gap-6 text-sm">
									<div>
										<span className="font-medium text-muted-foreground text-xs">
											Key Prefix
										</span>
										<div className="mt-1">
											<code className="rounded border bg-muted px-2 py-1 font-mono text-xs">
												{apiKey.start || apiKey.prefix || "mimir_"}***
											</code>
										</div>
									</div>
									<div>
										<span className="font-medium text-muted-foreground text-xs">
											Created
										</span>
										<p className="mt-1 text-sm">
											{formatDate(apiKey.createdAt)}
										</p>
									</div>
									<div>
										<span className="font-medium text-muted-foreground text-xs">
											Expires
										</span>
										<p className="mt-1 text-sm">
											{formatDate(apiKey.expiresAt)}
										</p>
									</div>
								</div>
								{apiKey.permissions && (
									<div>
										<span className="font-medium text-muted-foreground text-xs">
											Permissions
										</span>
										<div className="mt-1 flex flex-wrap gap-1">
											{Object.entries(apiKey.permissions).map(
												([resource, actions]) =>
													(actions as string[]).map((action) => (
														<Badge
															key={`${resource}-${action}`}
															variant="outline"
															className="text-xs"
														>
															{resource}:{action}
														</Badge>
													)),
											)}
										</div>
									</div>
								)}
								<div>
									<div className="flex items-center justify-between">
										<span className="font-medium text-muted-foreground text-xs">
											MCP Server Access
										</span>
										<Button
											variant="ghost"
											size="icon"
											className="size-6"
											aria-label={`Edit MCP server access for ${apiKey.name || "Unnamed Key"}`}
											onClick={() => openEditAccess(apiKey)}
										>
											<Pencil className="size-3" />
										</Button>
									</div>
									<div className="mt-1 flex flex-wrap items-center gap-1">
										{(() => {
											const scope = resolveMcpServerScope(apiKey.metadata);
											if (scope === "all") {
												return (
													<Badge variant="outline" className="text-xs">
														All servers
													</Badge>
												);
											}
											if (scope.length === 0) {
												return (
													<span className="text-muted-foreground text-xs">
														No servers
													</span>
												);
											}
											return scope.map((id) => (
												<Badge key={id} variant="outline" className="text-xs">
													{serverNameById.get(id) ?? id}
												</Badge>
											));
										})()}
									</div>
								</div>
							</CardContent>
						</Card>
					))}
				</div>
			)}

			<Card className="bg-muted/50">
				<CardHeader>
					<CardTitle className="text-sm">MCP Integration Guide</CardTitle>
				</CardHeader>
				<CardContent className="space-y-3 text-sm">
					<p className="text-muted-foreground">
						To connect an MCP client to your Nexus account:
					</p>
					<ol className="list-inside list-decimal space-y-2 text-muted-foreground">
						<li>Create an API key above</li>
						<li>
							Add the MCP server configuration to your client (VS Code, Cursor,
							etc.)
						</li>
						<li>
							Set the MCP endpoint to:{" "}
							<code className="rounded bg-background px-1.5 py-0.5 text-xs">
								{process.env.NEXT_PUBLIC_SERVER_URL}/mcp
							</code>
						</li>
						<li>
							Add the{" "}
							<code className="rounded bg-background px-1.5 py-0.5 text-xs">
								x-api-key
							</code>{" "}
							header with your API key
						</li>
					</ol>
					<div className="mt-4 rounded-md border bg-background p-3">
						<p className="mb-2 font-medium text-xs">
							Example VS Code .vscode/mcp.json:
						</p>
						<pre className="overflow-x-auto font-mono text-xs">
							{JSON.stringify(
								{
									servers: {
										nexus: {
											url: `${process.env.NEXT_PUBLIC_SERVER_URL}/mcp`,
											type: "http",
											headers: {
												"x-api-key": "YOUR_API_KEY",
											},
										},
									},
								},
								null,
								2,
							)}
						</pre>
					</div>
				</CardContent>
			</Card>

			<Dialog
				open={Boolean(editingKey)}
				onOpenChange={(open) => !open && setEditingKey(null)}
			>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Edit MCP Server Access</DialogTitle>
						<DialogDescription>
							Choose which MCP servers "{editingKey?.name || "this key"}" can
							proxy tools from.
						</DialogDescription>
					</DialogHeader>
					<div className="space-y-4 py-2">
						<RadioGroup
							value={editScope}
							onValueChange={(value) => setEditScope(value as "all" | "custom")}
							className="gap-2"
						>
							<div className="flex items-center gap-2">
								<RadioGroupItem value="all" id="edit-mcp-scope-all" />
								<Label htmlFor="edit-mcp-scope-all" className="font-normal">
									All MCP servers
								</Label>
							</div>
							<div className="flex items-center gap-2">
								<RadioGroupItem value="custom" id="edit-mcp-scope-custom" />
								<Label htmlFor="edit-mcp-scope-custom" className="font-normal">
									Specific servers
								</Label>
							</div>
						</RadioGroup>
						{editScope === "custom" && (
							<div className="space-y-2 rounded-md border p-3">
								{!mcpServers || mcpServers.length === 0 ? (
									<p className="text-muted-foreground text-xs">
										No MCP servers configured for this team yet.
									</p>
								) : (
									mcpServers.map((server) => (
										<div key={server.id} className="flex items-center gap-2">
											<Checkbox
												id={`edit-mcp-server-${server.id}`}
												checked={editServerIds.includes(server.id)}
												onCheckedChange={(checked) => {
													setEditServerIds((prev) =>
														checked
															? [...prev, server.id]
															: prev.filter((id) => id !== server.id),
													);
												}}
											/>
											<Label
												htmlFor={`edit-mcp-server-${server.id}`}
												className="font-normal text-sm"
											>
												{server.name}
											</Label>
										</div>
									))
								)}
							</div>
						)}
					</div>
					<DialogFooter>
						<Button
							type="button"
							variant="outline"
							onClick={() => setEditingKey(null)}
						>
							Cancel
						</Button>
						<Button
							type="button"
							disabled={
								isUpdatingAccess ||
								(editScope === "custom" && editServerIds.length === 0)
							}
							onClick={() => {
								if (!editingKey) return;
								const existingMetadata = parseApiKeyMetadata(
									editingKey.metadata,
								);
								updateMcpAccess({
									keyId: editingKey.id,
									metadata: {
										...existingMetadata,
										mcpServers: editScope === "all" ? "all" : editServerIds,
									},
								});
							}}
						>
							{isUpdatingAccess && (
								<Loader2 className="mr-2 size-4 animate-spin" />
							)}
							Save
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</div>
	);
}
