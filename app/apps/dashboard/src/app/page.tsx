import { redirect } from "next/navigation";
import { getSession } from "@/lib/get-session";

export default async function Home() {
	const session = await getSession();
	// Local-dev: getSession() always returns the seed user (no auth flow).
	if (process.env.NEXUS_LOCAL_DEV === "1") {
		return redirect(`/team/${session?.user?.teamSlug ?? "local-dev"}`);
	}
	if (session?.user) return redirect("/team");

	return redirect("/sign-in");
}
