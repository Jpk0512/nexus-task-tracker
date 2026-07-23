"use client";
import type { RouterOutputs } from "@nexus-app/trpc";

type IntegrationName = "mattermost" | "github" | "whatsapp" | "slack" | "smtp";

import { useQuery } from "@tanstack/react-query";
import { Alert, AlertDescription } from "@ui/components/ui/alert";
import { trpc } from "@/utils/trpc";
import { InstallIntegrationGithubForm } from "./github/install";

export type IntegrationType = RouterOutputs["integrations"]["getByType"];

export interface IntegrationConfigFormProps {
	type: IntegrationName;
	integration: IntegrationType;
}

export const integrationInstallForms: Partial<
	Record<IntegrationName, React.ComponentType<IntegrationConfigFormProps>>
> = {
	github: InstallIntegrationGithubForm,
};

export const integrationLinkUserForms: Partial<
	Record<IntegrationName, React.ComponentType<IntegrationConfigFormProps>>
> = {
	github: InstallIntegrationGithubForm,
};

export const integrationConfigForms: Partial<
	Record<IntegrationName, React.ComponentType<IntegrationConfigFormProps>>
> = {
	github: InstallIntegrationGithubForm,
};

export const IntegrationForm = ({ type }: { type: IntegrationName }) => {
	const { data: integration, isLoading } = useQuery(
		trpc.integrations.getByType.queryOptions({
			type,
		}),
	);

	if (isLoading) {
		return <p className="text-muted-foreground">Loading...</p>;
	}

	if (!integration) {
		return (
			<Alert>
				<AlertDescription>Integration {type} not found.</AlertDescription>
			</Alert>
		);
	}

	const FormComponent = !integration?.installedIntegration
		? integrationInstallForms[type]
		: !integration?.installedUserIntegration
			? integrationLinkUserForms[type]
			: integrationConfigForms[type];

	if (!FormComponent) {
		return (
			<Alert>
				<AlertDescription>
					No configuration available for this integration.
				</AlertDescription>
			</Alert>
		);
	}

	return <FormComponent type={type} integration={integration} />;
};
