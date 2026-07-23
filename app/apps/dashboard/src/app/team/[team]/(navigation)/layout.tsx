import { SidebarProvider } from "@ui/components/ui/sidebar";
import { Suspense } from "react";
import { AppSidebar, AppSidebarWrapper } from "@/components/app-sidebar/main";
import { BreadcrumbsProvider } from "@/components/breadcrumbs";
import { DashboardDndProviderClient } from "@/components/dnd/dashboard-dnd-provider-client";
import Header from "@/components/header";
import { getSession } from "@/lib/get-session";

export default async function DashboardLayout({
	children,
}: {
	children: React.ReactNode;
}) {
	const session = await getSession();

	return (
		<Suspense>
			<a
				href="#main-content"
				className="sr-only z-50 rounded-md bg-foreground px-3 py-2 text-background focus:not-sr-only focus:absolute focus:top-3 focus:left-3"
			>
				Skip to content
			</a>
			<SidebarProvider
				defaultOpen={true}
				// Base primitive only sets `min-h-svh` (a floor, not a ceiling) --
				// at short window heights (Electron resize) that let page content
				// grow the whole document instead of scrolling internally. Pin an
				// exact viewport height here so descendants get a definite size to
				// flex/percentage against, and internal panes (not the window) own
				// the scroll.
				className="h-svh"
				style={
					{
						"--sidebar-width": "240px",
					} as React.CSSProperties
				}
			>
				<DashboardDndProviderClient>
					<AppSidebar />
					<BreadcrumbsProvider session={session}>
						<main id="main-content" className="flex flex-1 flex-col">
							<Header />
							<AppSidebarWrapper>{children}</AppSidebarWrapper>
						</main>
					</BreadcrumbsProvider>
				</DashboardDndProviderClient>
			</SidebarProvider>
		</Suspense>
	);
}
