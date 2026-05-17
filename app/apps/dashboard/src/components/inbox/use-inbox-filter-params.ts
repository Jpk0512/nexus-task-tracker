import { useQueryStates } from "nuqs";
import {
	parseAsArrayOf,
	parseAsString,
	parseAsStringLiteral,
} from "nuqs/server";

export const inboxTabs = ["all", "unread", "mentions", "subscribed"] as const;
export type InboxTab = (typeof inboxTabs)[number];

export const inboxFilterParams = {
	status: parseAsArrayOf(
		parseAsStringLiteral(["pending", "archived"]),
	).withDefault(["pending"]),
	tab: parseAsStringLiteral(inboxTabs).withDefault("all"),
	selectedInboxId: parseAsString,
};

export const useInboxFilterParams = () => {
	const [params, setParams] = useQueryStates(inboxFilterParams);

	return {
		params,
		setParams,
	};
};
