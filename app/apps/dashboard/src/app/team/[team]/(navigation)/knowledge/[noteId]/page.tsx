import { KnowledgeFocusView } from "@/components/knowledge/knowledge-focus-view";

type Props = {
	params: Promise<{
		team: string;
		noteId: string;
	}>;
};

export default async function KnowledgeFocusPage({ params }: Props) {
	const { noteId } = await params;
	return (
		<div className="h-full">
			<KnowledgeFocusView noteId={noteId} />
		</div>
	);
}
