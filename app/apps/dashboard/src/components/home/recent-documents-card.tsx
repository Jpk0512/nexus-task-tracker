"use client";

import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNowStrict } from "date-fns";
import { FileTextIcon } from "lucide-react";
import { useUser } from "@/components/user-provider";
import { trpc } from "@/utils/trpc";
import { HomeCard, HomeCardEmpty, HomeCardRow } from "./home-card";

// Documents endpoint returns a tree-shaped list; flatten then take the most
// recently updated nodes. Server has no `sortBy`, so we sort client-side.
type TreeDoc = {
	id: string;
	name: string;
	icon?: string | null;
	updatedAt?: string | null;
	createdAt?: string | null;
	children?: TreeDoc[];
};

const flatten = (nodes: TreeDoc[]): TreeDoc[] => {
	const out: TreeDoc[] = [];
	const walk = (node: TreeDoc) => {
		out.push(node);
		if (node.children?.length) {
			for (const child of node.children) walk(child);
		}
	};
	for (const node of nodes) walk(node);
	return out;
};

export const RecentDocumentsCard = () => {
	const user = useUser();
	const { data, isLoading } = useQuery(
		trpc.documents.get.queryOptions(
			{ pageSize: 100 },
			{ staleTime: 5 * 60 * 1000 },
		),
	);

	const basePath = user?.basePath ?? "/team";
	const all = flatten((data?.data ?? []) as TreeDoc[]);
	const sorted = all.slice().sort((a, b) => {
		const aTime = a.updatedAt
			? new Date(a.updatedAt).getTime()
			: a.createdAt
				? new Date(a.createdAt).getTime()
				: 0;
		const bTime = b.updatedAt
			? new Date(b.updatedAt).getTime()
			: b.createdAt
				? new Date(b.createdAt).getTime()
				: 0;
		return bTime - aTime;
	});
	const top5 = sorted.slice(0, 5);

	return (
		<HomeCard
			title="Recent Documents"
			count={all.length}
			href={`${basePath}/documents`}
			isLoading={isLoading}
			isEmpty={top5.length === 0}
			emptyState={
				<HomeCardEmpty
					title="No documents yet"
					description="Capture decisions, specs, and notes alongside your projects."
					ctaHref={`${basePath}/documents/create`}
					ctaLabel="New document"
				/>
			}
		>
			<ul className="space-y-0.5">
				{top5.map((doc) => {
					const updated = doc.updatedAt ?? doc.createdAt;
					return (
						<li key={doc.id}>
							<HomeCardRow
								href={`${basePath}/documents/${doc.id}`}
								leading={
									doc.icon ? (
										<span className="text-[13px] leading-none">{doc.icon}</span>
									) : (
										<FileTextIcon className="size-3.5" />
									)
								}
								title={doc.name || "Untitled"}
								trailing={
									updated ? (
										<span>
											{formatDistanceToNowStrict(new Date(updated), {
												addSuffix: false,
											})}
										</span>
									) : null
								}
							/>
						</li>
					);
				})}
			</ul>
		</HomeCard>
	);
};
