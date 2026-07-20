import { Suspense } from "react";
import { HomeShell } from "@/components/home/home-shell";

type Props = {
	searchParams: Promise<{
		[key: string]: string | string[] | undefined;
	}>;
};

/**
 * Home — Dashboard OS shell (Do now, health strip, capture dump default, companion).
 */
export default async function Page({ searchParams: _searchParams }: Props) {
	return (
		<Suspense
			fallback={
				<div className="p-6 text-[13px] text-muted-foreground">Loading Home…</div>
			}
		>
			<HomeShell />
		</Suspense>
	);
}
