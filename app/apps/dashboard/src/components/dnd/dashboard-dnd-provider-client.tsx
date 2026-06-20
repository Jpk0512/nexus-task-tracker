// dynamic({ ssr: false }) is forbidden in Server Components — this client
// wrapper owns the dynamic import so the RSC layout can render it safely.
"use client";

import dynamic from "next/dynamic";
import type { ReactNode } from "react";

const DashboardDndProvider = dynamic(
	() =>
		import("@/components/dnd/dashboard-dnd-provider").then((m) => ({
			default: m.DashboardDndProvider,
		})),
	{ ssr: false },
);

export function DashboardDndProviderClient({
	children,
}: {
	children: ReactNode;
}) {
	return <DashboardDndProvider>{children}</DashboardDndProvider>;
}
