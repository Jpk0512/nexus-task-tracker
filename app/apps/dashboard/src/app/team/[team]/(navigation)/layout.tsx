import { SidebarProvider } from "@ui/components/ui/sidebar";
import dynamic from "next/dynamic";
import { Suspense } from "react";
import { AppSidebar, AppSidebarWrapper } from "@/components/app-sidebar/main";
import { BreadcrumbsProvider } from "@/components/breadcrumbs";
import Header from "@/components/header";
import { getSession } from "@/lib/get-session";

const DashboardDndProvider = dynamic(
	() =>
		import("@/components/dnd/dashboard-dnd-provider").then((m) => ({
			default: m.DashboardDndProvider,
		})),
	{ ssr: false },
);

export default async function DashboardLayout({
	children,
}: {
	children: React.ReactNode;
}) {
	const session = await getSession();

	return (
		<Suspense>
			<SidebarProvider
				defaultOpen={true}
				style={
					{
						"--sidebar-width": "240px",
					} as React.CSSProperties
				}
			>
				<DashboardDndProvider>
					<AppSidebar />
					<BreadcrumbsProvider session={session}>
						<main className="flex flex-1 flex-col">
							<Header />
							<AppSidebarWrapper>{children}</AppSidebarWrapper>
						</main>
					</BreadcrumbsProvider>
				</DashboardDndProvider>
			</SidebarProvider>
		</Suspense>
	);
}
