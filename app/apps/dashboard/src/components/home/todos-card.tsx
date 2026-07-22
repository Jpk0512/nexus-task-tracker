"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Checkbox } from "@ui/components/ui/checkbox";
import { Input } from "@ui/components/ui/input";
import { PlusIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { useUser } from "@/components/user-provider";
import { trpc } from "@/utils/trpc";
import { HomeCard, HomeCardEmpty } from "./home-card";

type Todo = {
	id: string;
	content: string;
	projectId: string | null;
	projectName: string | null;
	projectPrefix: string | null;
	checked: boolean;
	tags: string[];
};

/** Home todos card — quick check-off + add. Unchecked only. */
export function TodosCard() {
	const user = useUser();
	const qc = useQueryClient();
	const [draft, setDraft] = useState("");

	const { data: todosData, isLoading: loading } = useQuery(
		trpc.todos.get.queryOptions({ includeChecked: false }),
	);
	const todos = (todosData ?? []) as Todo[];

	const createMut = useMutation(
		trpc.todos.create.mutationOptions({
			onSuccess: () => qc.invalidateQueries({ queryKey: [["todos", "get"]] }),
			onError: (e: { message?: string }) => toast.error(e.message ?? "Failed"),
		}),
	);
	const checkMut = useMutation(
		trpc.todos.check.mutationOptions({
			onSuccess: () => qc.invalidateQueries({ queryKey: [["todos", "get"]] }),
		}),
	);

	const add = () => {
		const content = draft.trim();
		if (!content) return;
		createMut.mutate({ content });
		setDraft("");
	};

	const todosHref = `${user.basePath}/todos`;

	return (
		<HomeCard
			title="To-do"
			count={todos.length}
			href={todosHref}
			isLoading={loading}
			isEmpty={!loading && todos.length === 0}
			emptyState={
				<HomeCardEmpty
					title="No open todos"
					description="Quick captures live here. Check them off as you go."
					ctaLabel="Open To-do"
					ctaHref={todosHref}
				/>
			}
		>
			<div className="space-y-0.5 p-1">
				<ul className="divide-y divide-border/40">
					{todos.slice(0, 6).map((t) => (
						<li key={t.id}>
							<div className="flex items-center gap-2 px-1 py-1.5">
								<Checkbox
									checked={t.checked}
									onCheckedChange={() => checkMut.mutate({ id: t.id })}
									className="size-3.5"
								/>
								<Link
									href={todosHref}
									className="min-w-0 flex-1 truncate text-[13px]"
								>
									{t.content}
								</Link>
								{t.projectName ? (
									<span className="shrink-0 text-[10px] text-muted-foreground">
										{t.projectPrefix ? `${t.projectPrefix} · ` : ""}
										{t.projectName}
									</span>
								) : null}
							</div>
						</li>
					))}
				</ul>
				<div className="mt-1 flex items-center gap-2 px-1 py-1.5">
					<PlusIcon className="size-3.5 shrink-0 text-muted-foreground" />
					<Input
						value={draft}
						onChange={(e) => setDraft(e.target.value)}
						onKeyDown={(e) => {
							if (e.key === "Enter") {
								e.preventDefault();
								add();
							}
						}}
						placeholder="Add a todo…"
						className="h-7 border-0 bg-transparent px-0 text-[13px] shadow-none focus-visible:ring-0"
					/>
				</div>
			</div>
		</HomeCard>
	);
}
