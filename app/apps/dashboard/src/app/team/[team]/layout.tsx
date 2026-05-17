import { Provider as OpenPanelProvider } from "@mimir/events/client";
import { redirect } from "next/navigation";
import { Suspense } from "react";
import { CommandTray } from "@/components/command-tray";
import { PanelProvider } from "@/components/panels/panel-context";
import { PanelStack } from "@/components/panels/panel-stack";
import { GlobalSheets } from "@/components/sheets/global-sheets";
import { UserProvider } from "@/components/user-provider";
import { getSession } from "@/lib/get-session";
import { trpcClient } from "@/utils/trpc";

type Props = {
	children: React.ReactNode;
	params: Promise<{
		team: string;
	}>;
};

export default async function Layout({ children, params }: Props) {
	const { team } = await params;
	const session = await getSession();

	// Local-dev: skip the sign-in redirect; the api injects the seed user.
	const LOCAL_DEV = process.env.MIMRAI_LOCAL_DEV === "1";

	if (!LOCAL_DEV && !session?.user?.teamSlug) {
		return redirect("/sign-in");
	}

	// switch to the team in the URL (skipped in local-dev — single seed team)
	if (!LOCAL_DEV) {
		try {
			if (session?.user?.teamSlug !== team) {
				await trpcClient.users.switchTeam.mutate({ slug: team });
			}
		} catch (_error) {
			if (!session?.user?.teamSlug) {
				return redirect("/team");
			}
			return redirect(`/team/${session.user.teamSlug}/onboarding`);
		}
	}

	const user = await trpcClient.users.getCurrent.query();

	return (
		<Suspense>
			<UserProvider user={user}>
				<PanelProvider>
					<GlobalSheets />
					{children}
					<CommandTray />
					<OpenPanelProvider profileId={user.id} />
					<PanelStack />
				</PanelProvider>
			</UserProvider>
		</Suspense>
	);
}
