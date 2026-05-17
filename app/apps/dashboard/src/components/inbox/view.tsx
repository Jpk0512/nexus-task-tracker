"use client";
import { InboxTabs } from "./inbox-tabs";
import { InboxList } from "./list";
import { InboxOverview } from "./overview";
import { InboxProvider } from "./use-inbox";

export const InboxView = () => {
	return (
		<InboxProvider>
			<div className="flex h-full overflow-hidden">
				<InboxTabs />
				<InboxList />
				<InboxOverview />
			</div>
		</InboxProvider>
	);
};
