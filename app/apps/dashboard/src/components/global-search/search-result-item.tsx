import { ActionResultItem } from "./result-items/action-result-item";
import { ChatResultItem } from "./result-items/chat-result-item";
import { DocumentResultItem } from "./result-items/document-result-item";
import { KnowledgeResultItem } from "./result-items/knowledge-result-item";
import { LibraryResultItem } from "./result-items/library-result-item";
import { MilestoneResultItem } from "./result-items/milestone-result-item";
import { NavigationResultItem } from "./result-items/navigation-result-item";
import { ProjectResultItem } from "./result-items/project-result-item";
import { PromptResultItem } from "./result-items/prompt-result-item";
import { TaskResultItem } from "./result-items/task-result-item";
import { TodoResultItem } from "./result-items/todo-result-item";
import type { GlobalSearchItem } from "./types";

type SearchResultItemProps = {
	item: GlobalSearchItem;
};

export const SearchResultItem = ({ item }: SearchResultItemProps) => {
	const isAction = item.id === "action" || item.id.startsWith("action:");

	if (isAction) {
		return <ActionResultItem item={item} />;
	}

	switch (item.type) {
		case "task":
			return <TaskResultItem item={item} />;
		case "project":
			return <ProjectResultItem item={item} />;
		case "milestone":
			return <MilestoneResultItem item={item} />;
		case "document":
			return <DocumentResultItem item={item} />;
		case "chat":
			return <ChatResultItem item={item} />;
		case "todo":
			return <TodoResultItem item={item} />;
		case "knowledge":
			return <KnowledgeResultItem item={item} />;
		case "library":
			return <LibraryResultItem item={item} />;
		case "prompt":
			return <PromptResultItem item={item} />;
		case "navigation":
			return <NavigationResultItem item={item} />;
		default:
			return <TaskResultItem item={item} />;
	}
};
