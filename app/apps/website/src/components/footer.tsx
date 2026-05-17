import Link from "next/link";
import type React from "react";
import { Logo } from "./logo";

export const Footer: React.FC = () => {
	return (
		<footer className="border-white/5 border-t bg-background py-12 text-sm">
			<div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-6 px-6 md:flex-row">
				<div className="flex items-center gap-2">
					<Logo className="size-8 rounded-sm bg-white" />
					<span className="font-semibold text-white tracking-wide">MIMRAI</span>
				</div>

				<div className="flex items-center gap-8 text-zinc-500">
					<Link href="/policy" className="transition-colors hover:text-white">
						Privacy
					</Link>
					<Link
						href="https://x.com/alain_0012"
						className="transition-colors hover:text-white"
						target="_blank"
					>
						X (Twitter)
					</Link>
					<Link href="#" className="transition-colors hover:text-white">
						Contact
					</Link>
				</div>

				<div className="text-zinc-600">
					&copy; {new Date().getFullYear()} Mimrai Inc.
				</div>
			</div>
		</footer>
	);
};
