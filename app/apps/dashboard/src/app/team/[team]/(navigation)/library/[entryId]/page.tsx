import { LibraryDetailView } from "@/components/library/detail-view";

type Props = {
	params: Promise<{ team: string; entryId: string }>;
};

export default async function LibraryEntryPage({ params }: Props) {
	const { entryId } = await params;
	return (
		<div className="animate-blur-in">
			<LibraryDetailView entryId={entryId} />
		</div>
	);
}
