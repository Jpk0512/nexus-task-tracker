"use client";
import { useQuery } from "@tanstack/react-query";
import { Alert, AlertDescription, AlertTitle } from "@ui/components/ui/alert";
import { InfoIcon } from "lucide-react";
import { trpc } from "@/utils/trpc";

export const SidebarSubscriptionStatus = () => {
	const { data } = useQuery(trpc.billing.subscription.queryOptions());

	if (!data) return null;
	if (!data.trialEnd) return null;

	return (
		<div>
			<Alert className="bg-transparent">
				{/*<InfoIcon className="stroke-yellow-600" />*/}
				<AlertTitle className="">You're on a free trial</AlertTitle>
				<AlertDescription className="text-xs">
					Your trial ends on{" "}
					{new Date(data.trialEnd * 1000).toLocaleDateString()}.
				</AlertDescription>
			</Alert>
		</div>
	);
};
