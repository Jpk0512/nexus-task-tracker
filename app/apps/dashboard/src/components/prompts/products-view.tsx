"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@ui/components/ui/badge";
import { Button } from "@ui/components/ui/button";
import { Input } from "@ui/components/ui/input";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@ui/components/ui/select";
import { formatDistanceToNowStrict } from "date-fns";
import {
	MessageSquareTextIcon,
	PlusIcon,
	SearchIcon,
	XIcon,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { trpc } from "@/utils/trpc";

type Product = {
	id: string;
	name: string;
	slug: string;
	description: string | null;
	icon: string | null;
	color: string | null;
	promptCount: number;
	updatedAt: string;
};

// Keep this list short — it's the filter dropdown the audit referenced
// ("All products / kbuddy / Claude / GPT"). We derive the actual filter set
// from the products that exist on the team so it always reflects reality.
function deriveFilterOptions(products: Product[]) {
	const seen = new Set<string>();
	const opts: { value: string; label: string }[] = [];
	for (const p of products) {
		const key = p.slug;
		if (seen.has(key)) continue;
		seen.add(key);
		opts.push({ value: key, label: p.name });
	}
	return opts;
}

export function PromptProductsView() {
	const { team } = useParams<{ team: string }>();
	const router = useRouter();
	const qc = useQueryClient();
	const productsQuery = useQuery(
		trpc.prompts.getProducts.queryOptions(undefined),
	);
	const [showNew, setShowNew] = useState(false);
	const [name, setName] = useState("");
	const [description, setDescription] = useState("");
	const [icon, setIcon] = useState("🤖");
	const [search, setSearch] = useState("");
	const [productFilter, setProductFilter] = useState<string>("all");
	// productId currently being captured-into via the per-card "+" affordance.
	// Null when no card has its inline composer open.
	const [inlineForProductId, setInlineForProductId] = useState<string | null>(
		null,
	);
	const [inlinePromptName, setInlinePromptName] = useState("");

	const createMut = useMutation(
		trpc.prompts.createProduct.mutationOptions({
			onSuccess: (p) => {
				toast.success(`Added ${(p as { name: string }).name}`);
				setShowNew(false);
				setName("");
				setDescription("");
				setIcon("🤖");
				qc.invalidateQueries({ queryKey: [["prompts", "getProducts"]] });
			},
			onError: (e) => toast.error(e.message),
		}),
	);
	// Per-card inline prompt creation. After insert we navigate to the new
	// prompt's edit page so the user lands directly in the content editor.
	const createPromptMut = useMutation(
		trpc.prompts.createPrompt.mutationOptions({
			onSuccess: (created, vars) => {
				const slug = (created as { slug: string }).slug;
				const product = products.find((p) => p.id === vars.productId);
				toast.success("Created prompt");
				setInlineForProductId(null);
				setInlinePromptName("");
				qc.invalidateQueries({ queryKey: [["prompts", "getProducts"]] });
				qc.invalidateQueries({ queryKey: [["prompts", "getPrompts"]] });
				if (product) {
					router.push(`/team/${team}/prompts/${product.slug}/${slug}`);
				}
			},
			onError: (e) => toast.error(e.message),
		}),
	);
	const products = (productsQuery.data ?? []) as Product[];

	const filterOptions = useMemo(
		() => deriveFilterOptions(products),
		[products],
	);

	const filtered = useMemo(() => {
		const q = search.trim().toLowerCase();
		return products.filter((p) => {
			if (productFilter !== "all" && p.slug !== productFilter) return false;
			if (!q) return true;
			return (
				p.name.toLowerCase().includes(q) ||
				(p.description ?? "").toLowerCase().includes(q)
			);
		});
	}, [products, search, productFilter]);

	const totalPrompts = filtered.reduce((n, p) => n + p.promptCount, 0);

	return (
		<div className="flex h-full flex-col">
			<header className="border-border border-b px-6 py-3">
				<div className="flex items-baseline justify-between gap-4">
					<div>
						<h1 className="font-[510] text-[15px] text-foreground tracking-[-0.012em]">
							Prompt Library
						</h1>
						<p className="mt-0.5 text-[12px] text-muted-foreground">
							AI products (kbuddy, Claude, GPT…) and the prompts you've saved
							for each.
						</p>
					</div>
					<Button
						variant="outline"
						size="sm"
						onClick={() => setShowNew((s) => !s)}
					>
						<PlusIcon className="size-3.5" />{" "}
						{showNew ? "Cancel" : "New product"}
					</Button>
				</div>
				{showNew && (
					<form
						onSubmit={(e) => {
							e.preventDefault();
							if (name.trim())
								createMut.mutate({
									name: name.trim(),
									description: description || undefined,
									icon,
								});
						}}
						className="mt-3 grid grid-cols-[max-content_2fr_3fr_max-content] items-end gap-2"
					>
						<div>
							<label
								htmlFor="prompt-product-icon"
								className="mb-1 block text-muted-foreground text-xs"
							>
								Icon
							</label>
							<Input
								id="prompt-product-icon"
								value={icon}
								onChange={(e) => setIcon(e.target.value)}
								className="h-8 w-12 text-center"
								maxLength={4}
							/>
						</div>
						<div>
							<label
								htmlFor="prompt-product-name"
								className="mb-1 block text-muted-foreground text-xs"
							>
								Name
							</label>
							<Input
								id="prompt-product-name"
								autoFocus
								value={name}
								onChange={(e) => setName(e.target.value)}
								placeholder="e.g. kbuddy, Claude, ChatGPT"
								className="h-8"
							/>
						</div>
						<div>
							<label
								htmlFor="prompt-product-description"
								className="mb-1 block text-muted-foreground text-xs"
							>
								Description
							</label>
							<Input
								id="prompt-product-description"
								value={description}
								onChange={(e) => setDescription(e.target.value)}
								placeholder="optional"
								className="h-8"
							/>
						</div>
						<Button
							type="submit"
							size="sm"
							disabled={!name.trim() || createMut.isPending}
						>
							Add
						</Button>
					</form>
				)}

				{/* Linear-style filter bar */}
				<div className="mt-3 flex flex-wrap items-center gap-2">
					<div className="relative">
						<SearchIcon className="absolute top-2.5 left-2.5 size-3.5 text-muted-foreground" />
						<Input
							value={search}
							onChange={(e) => setSearch(e.target.value)}
							placeholder="Search products…"
							className="h-9 w-64 pl-8"
						/>
					</div>
					<Select value={productFilter} onValueChange={setProductFilter}>
						<SelectTrigger className="h-9 w-44">
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="all">All products</SelectItem>
							{filterOptions.map((o) => (
								<SelectItem key={o.value} value={o.value}>
									{o.label}
								</SelectItem>
							))}
						</SelectContent>
					</Select>
					<div className="ml-auto flex items-center gap-2 text-muted-foreground text-xs">
						{filtered.map((p) => (
							<Badge key={p.id} variant="outline" className="font-normal">
								{p.name}: {p.promptCount}
							</Badge>
						))}
						<span>
							· {totalPrompts} prompt{totalPrompts === 1 ? "" : "s"}
						</span>
					</div>
				</div>
			</header>
			<div className="grow overflow-y-auto px-6 py-4">
				{filtered.length === 0 && !productsQuery.isLoading && (
					<div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
						<MessageSquareTextIcon className="size-10 text-muted-foreground" />
						<p className="text-muted-foreground">
							{products.length === 0
								? "No AI products yet. Add one above."
								: "No products match these filters."}
						</p>
					</div>
				)}
				<div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
					{filtered.map((p) => {
						const inlineOpen = inlineForProductId === p.id;
						return (
							<div
								key={p.id}
								className="group relative rounded-lg border border-border bg-card/40 p-3 transition hover:border-primary/40 hover:bg-accent/30"
							>
								{/*
								 * Tiny "+" affordance in the top-right of each card. Stops
								 * propagation so we don't navigate to the product page when
								 * the user means to add a new prompt under it.
								 *
								 * Accessibility (iter8): the button is reachable via Tab
								 * (no negative tabindex). It reveals on hover OR keyboard
								 * focus, and renders a visible :focus-visible ring so
								 * keyboard users can see where they are.
								 */}
								<button
									type="button"
									aria-label={`New prompt under ${p.name}`}
									title={`New prompt under ${p.name}`}
									onClick={(e) => {
										e.preventDefault();
										e.stopPropagation();
										setInlineForProductId(p.id);
										setInlinePromptName("");
									}}
									className="absolute top-2 right-2 z-10 inline-flex size-6 items-center justify-center rounded-md text-muted-foreground opacity-0 transition hover:bg-accent hover:text-foreground focus-visible:bg-accent focus-visible:text-foreground focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/60 group-hover:opacity-100"
								>
									<PlusIcon className="size-3.5" />
								</button>

								{/* Card link target — only active when the inline composer is closed. */}
								{!inlineOpen && (
									<Link
										href={`/team/${team}/prompts/${p.slug}`}
										className="absolute inset-0 rounded-lg"
										aria-label={`Open ${p.name}`}
									/>
								)}

								<div className="pointer-events-none relative z-[1] flex items-center gap-2">
									<span className="text-xl">{p.icon ?? "🤖"}</span>
									<h3 className="font-[510] text-[14px] tracking-[-0.006em]">
										{p.name}
									</h3>
									<Badge
										variant="outline"
										className="mr-7 ml-auto font-normal text-[11px]"
									>
										{p.promptCount}
									</Badge>
								</div>
								{p.description && (
									<p className="pointer-events-none relative z-[1] mt-1.5 line-clamp-2 text-[12px] text-muted-foreground">
										{p.description}
									</p>
								)}
								{/*
								 * Usage strip: last-updated + total uses (proxied by
								 * promptCount, since real run telemetry isn't wired yet).
								 * Format mirrors Linear's metadata strips: bullet-separated,
								 * single-line, muted.
								 */}
								<div className="pointer-events-none relative z-[1] mt-2 flex items-center gap-2 text-[11px] text-muted-foreground">
									<span>
										updated{" "}
										{formatDistanceToNowStrict(new Date(p.updatedAt), {
											addSuffix: true,
										})}
									</span>
									<span>·</span>
									<span>
										{p.promptCount} prompt
										{p.promptCount === 1 ? "" : "s"}
									</span>
								</div>

								{inlineOpen && (
									<form
										onSubmit={(e) => {
											e.preventDefault();
											const trimmed = inlinePromptName.trim();
											if (!trimmed) return;
											createPromptMut.mutate({
												productId: p.id,
												name: trimmed,
											});
										}}
										className="relative z-10 mt-3 flex items-center gap-2 rounded-md border border-border/70 bg-background px-2 py-1.5"
									>
										<PlusIcon className="size-3.5 text-muted-foreground" />
										<Input
											autoFocus
											value={inlinePromptName}
											onChange={(e) => setInlinePromptName(e.target.value)}
											onKeyDown={(e) => {
												if (e.key === "Escape") {
													e.preventDefault();
													setInlineForProductId(null);
													setInlinePromptName("");
												}
											}}
											placeholder="Prompt name, e.g. onboarding-v2"
											className="h-7 grow border-0 bg-transparent px-0 text-[13px] shadow-none focus-visible:ring-0"
										/>
										<button
											type="button"
											aria-label="Cancel"
											onClick={() => {
												setInlineForProductId(null);
												setInlinePromptName("");
											}}
											className="text-muted-foreground hover:text-foreground"
										>
											<XIcon className="size-3.5" />
										</button>
										<Button
											type="submit"
											size="sm"
											disabled={
												!inlinePromptName.trim() || createPromptMut.isPending
											}
										>
											Create
										</Button>
									</form>
								)}
							</div>
						);
					})}
				</div>
			</div>
		</div>
	);
}
