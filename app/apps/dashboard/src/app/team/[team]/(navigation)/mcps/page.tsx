import { redirect } from "next/navigation";

/** Legacy /mcps → /vault. */
export default async function McpsRedirectPage({
	params,
}: {
	params: Promise<{ team: string }>;
}) {
	const { team } = await params;
	redirect(`/team/${team}/vault`);
}
