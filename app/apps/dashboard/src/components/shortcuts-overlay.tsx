"use client";

import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@ui/components/ui/dialog";
import { Kbd, KbdGroup } from "@ui/components/ui/kbd";

/**
 * Keyboard-shortcuts cheatsheet (Linear parity).
 *
 * Mounted globally via `GlobalShortcuts`. Pressing `?` (Shift+/) anywhere
 * outside an editable target opens this centered dialog listing every
 * dashboard hotkey. The user can dismiss with Esc.
 */

type Shortcut = {
	keys: string[];
	label: string;
};

type Section = {
	title: string;
	items: Shortcut[];
};

const SECTIONS: Section[] = [
	{
		title: "Create",
		items: [
			{ keys: ["c"], label: "New task" },
			{ keys: ["N"], label: "Capture todo" },
		],
	},
	{
		title: "Navigation",
		items: [
			{ keys: ["j"], label: "Next row" },
			{ keys: ["k"], label: "Previous row" },
			{ keys: ["Enter"], label: "Open focused row" },
			{ keys: ["⌘", "P"], label: "Search anything" },
			{ keys: ["⌘", "J"], label: "Quick switch project" },
		],
	},
	{
		title: "General",
		items: [
			{ keys: ["?"], label: "Show this overlay" },
			{ keys: ["Esc"], label: "Close panel or dialog" },
		],
	},
];

export const ShortcutsOverlay = ({
	open,
	onOpenChange,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
}) => {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-w-md gap-0 p-0">
				<DialogHeader className="border-border border-b px-5 py-3.5">
					<DialogTitle className="font-[510] text-[14px] tracking-[-0.012em]">
						Keyboard shortcuts
					</DialogTitle>
					<DialogDescription>
						The fastest way to move around Nexus.
					</DialogDescription>
				</DialogHeader>
				<div className="flex flex-col gap-4 px-5 py-4">
					{SECTIONS.map((section) => (
						<section key={section.title}>
							<h3 className="mb-2 font-[510] text-[10px] text-muted-foreground uppercase tracking-[0.06em]">
								{section.title}
							</h3>
							<ul className="flex flex-col">
								{section.items.map((item) => (
									<li
										key={item.label}
										className="flex items-center justify-between py-1.5 text-[12.5px] text-foreground"
									>
										<span>{item.label}</span>
										<KbdGroup>
											{item.keys.map((k) => (
												<Kbd key={k}>{k}</Kbd>
											))}
										</KbdGroup>
									</li>
								))}
							</ul>
						</section>
					))}
				</div>
				<div className="border-border border-t bg-muted/20 px-5 py-2.5 text-[11px] text-muted-foreground">
					Press <Kbd>?</Kbd> at any time to reopen this list.
				</div>
			</DialogContent>
		</Dialog>
	);
};
