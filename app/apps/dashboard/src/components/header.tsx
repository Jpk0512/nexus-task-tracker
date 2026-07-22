"use client";

import { SidebarTrigger } from "@ui/components/ui/sidebar";
import { Breadcrumbs } from "./breadcrumbs";
import { CaptureBar } from "./capture-bar";
import { DumpModalProvider } from "./dump/dump-modal";
import { NavSearch } from "./nav-search";
import { NavUser } from "./nav-user";

export default function Header() {
	return (
		<DumpModalProvider>
			<header className="sticky top-0 z-5 h-12 border-border border-b bg-background px-4">
				<div className="flex h-full items-center justify-between gap-3">
					{/* Left: capture bar dominates. Below the sidebar's own `md`
					 *  breakpoint the collapsible sidebar renders as an off-canvas
					 *  Sheet that fully unmounts while closed — its own trigger
					 *  lives inside that (now-gone) sidebar, so a narrowed window
					 *  had no way back in. This mirrors that trigger here, outside
					 *  the sidebar tree, visible only at the same narrow width
					 *  (FEAT-009 item 5 — responsive-window polish; see return
					 *  notes for the explicit no-bottom-nav scope decision). */}
					<SidebarTrigger className="shrink-0 md:hidden" />
					<CaptureBar className="min-w-0 flex-1 sm:max-w-xl" />
					{/* Right: context + search + profile */}
					<div className="flex shrink-0 items-center gap-2">
						<div className="hidden min-w-0 max-w-[220px] items-center md:flex">
							<Breadcrumbs />
						</div>
						<NavSearch />
						<NavUser />
					</div>
				</div>
			</header>
		</DumpModalProvider>
	);
}
