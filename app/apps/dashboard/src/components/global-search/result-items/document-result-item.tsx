import { useQuery } from "@tanstack/react-query";
import { FileTextIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { Response } from "@/components/chat/response";
import { trpc } from "@/utils/trpc";
import { useGlobalSearch } from "../global-search-context";
import type { ResultItemProps } from "../types";
import { BaseResultItem } from "./base-result-item";

export const DocumentResultItem = ({ item }: ResultItemProps) => {
	const router = useRouter();
	const { onOpenChange, basePath } = useGlobalSearch();

	const handleSelect = () => {
		router.push(`${basePath}/documents/${item.id}`);
		onOpenChange(false);
	};

	// parent_id carries projectId — show "in project" subtitle hint when set.
	const projectId = item.parentId || undefined;
	const { data: project } = useQuery({
		...trpc.projects.getById.queryOptions({ id: projectId ?? "" }),
		enabled: !!projectId,
	});

	return (
		<BaseResultItem
			onSelect={handleSelect}
			icon={FileTextIcon}
			preview={<DocumentResultPreview item={item} />}
			title={item.title}
			item={item}
		>
			<div className="flex min-w-0 flex-1 items-baseline gap-2">
				<span className="truncate">{item.title}</span>
				{project?.name && (
					<span className="truncate text-muted-foreground text-xs">
						in {project.name}
					</span>
				)}
			</div>
		</BaseResultItem>
	);
};

const DocumentResultPreview = ({ item }: ResultItemProps) => {
	const { data: doc } = useQuery(
		trpc.documents.getById.queryOptions({ id: item.id }),
	);

	return (
		<div>
			<h2 className="mb-2 font-medium text-xl">{item.title}</h2>
			<div className="text-sm">
				<Response>{doc?.content || "No content available."}</Response>
			</div>
		</div>
	);
};
