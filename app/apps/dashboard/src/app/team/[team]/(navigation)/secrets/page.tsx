import { redirect } from "next/navigation";

/** Legacy /secrets → /vault. */
export default async function SecretsRedirectPage({
	params,
}: {
	params: Promise<{ team: string }>;
}) {
	const { team } = await params;
	redirect(`/team/${team}/vault`);
}
