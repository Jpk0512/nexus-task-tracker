import { InboxView } from "@/components/inbox/view";

type Props = {
	params: Promise<{
		id: string;
	}>;
};

export default async function Page({ params }: Props) {
	const { id } = await params;
	void id;
	return <InboxView />;
}
