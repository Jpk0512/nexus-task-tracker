"use client";

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
					{/* Left: capture bar dominates */}
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
