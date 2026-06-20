import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@ui/components/ui/card";
import { VaultSettingsForm } from "@/components/knowledge/vault-settings-form";

export default function KnowledgeSettingsPage() {
	return (
		<div className="animate-blur-in space-y-6">
			<Card>
				<CardHeader>
					<CardTitle>Knowledge vault</CardTitle>
					<CardDescription>
						Path to the local Obsidian vault directory synced with this
						workspace.
					</CardDescription>
				</CardHeader>
				<CardContent>
					<VaultSettingsForm />
				</CardContent>
			</Card>
		</div>
	);
}
