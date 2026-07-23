import type { RouterOutputs } from "@nexus-app/trpc";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@nexus-app/ui/card";
import { IntegrationForm } from "@/components/integrations/components";
import { UninstallIntegrationCard } from "@/components/integrations/uninstall-card";
import { queryClient, trpc } from "@/utils/trpc";
import { LogsList } from "../logs-list";
import { RepositoriesList } from "./repositories-list";

export const revalidate = 0;

type GithubIntegrationInfo = RouterOutputs["integrations"]["getByType"];

// getByType 400s when GitHub isn't installed for the team (same failure mode
// that used to blank the Mattermost page) — degrade to a null "not installed"
// state instead of letting the throw reach the settings error boundary.
async function fetchGithubIntegration(): Promise<GithubIntegrationInfo | null> {
	try {
		return await queryClient.fetchQuery(
			trpc.integrations.getByType.queryOptions({
				type: "github",
			}),
		);
	} catch {
		return null;
	}
}

export default async function Page() {
	const integrationInfo = await fetchGithubIntegration();
	const integration = integrationInfo?.installedIntegration;

	if (!integration) {
		return (
			<div className="space-y-6">
				<Card>
					<CardHeader>
						<CardTitle>Settings</CardTitle>
						<CardDescription>
							Connect GitHub to sync repositories and enable pull request
							automation.
						</CardDescription>
					</CardHeader>
					<CardContent>
						<IntegrationForm type="github" />
					</CardContent>
				</Card>
			</div>
		);
	}

	const id = integration.id;

	return (
		<div className="space-y-6">
			<Card>
				<CardHeader>
					<CardTitle>Settings</CardTitle>
				</CardHeader>
				<CardContent>
					{integrationInfo?.isInstalledForUser ? (
						<p className="mb-4 text-muted-foreground text-sm">
							GitHub App is installed and configured.
						</p>
					) : (
						<IntegrationForm type={integration.type} />
					)}
				</CardContent>
			</Card>
			<RepositoriesList integrationId={id} />
			<Card>
				<CardHeader>
					<CardDescription>Integration logs</CardDescription>
				</CardHeader>
				<CardContent>
					<LogsList integrationId={id} />
				</CardContent>
			</Card>
			<UninstallIntegrationCard integrationType="github" />
		</div>
	);
}
