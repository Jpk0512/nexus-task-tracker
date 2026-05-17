import { PromptEditView } from "@/components/prompts/edit-view";

type Props = {
	params: Promise<{ productSlug: string; promptSlug: string; team: string }>;
};

export default async function PromptEditPage({ params }: Props) {
	const { productSlug, promptSlug, team } = await params;
	return (
		<div className="h-full animate-blur-in">
			<PromptEditView
				productSlug={productSlug}
				promptSlug={promptSlug}
				team={team}
			/>
		</div>
	);
}
