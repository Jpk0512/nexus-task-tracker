import { PromptListView } from "@/components/prompts/list-view";

type Props = {
	params: Promise<{ productSlug: string; team: string }>;
};

export default async function PromptProductPage({ params }: Props) {
	const { productSlug, team } = await params;
	return (
		<div className="animate-blur-in">
			<PromptListView productSlug={productSlug} team={team} />
		</div>
	);
}
