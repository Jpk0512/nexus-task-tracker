"use client";

import { Button } from "@ui/components/ui/button";
import { cn } from "@ui/lib/utils";
import {
	LightbulbIcon,
	MessageSquarePlusIcon,
	SearchIcon,
	XIcon,
} from "lucide-react";
import { useEffect, useState } from "react";

const DISMISS_KEY = "nexus.coach.home.dismissed.v1";

const TIPS = [
	{
		icon: MessageSquarePlusIcon,
		title: "Capture anything",
		body: "Press ⌘J or use the top bar to dump a thought — it lands in Notes.",
	},
	{
		icon: LightbulbIcon,
		title: "Press c to create",
		body: "Create a task from anywhere. On a project page it pre-fills the project.",
	},
	{
		icon: SearchIcon,
		title: "⌘K to jump",
		body: "Open the command palette to find anything or run an action fast.",
	},
];

/**
 * Dismissible first-run coach strip for Home. Shows once (until dismissed,
 * persisted to localStorage) to orient new users to the three core moves:
 * capture, create, command palette.
 */
export function FirstRunCoach() {
	const [show, setShow] = useState(false);

	useEffect(() => {
		try {
			setShow(!localStorage.getItem(DISMISS_KEY));
		} catch {
			setShow(true);
		}
	}, []);

	const dismiss = () => {
		try {
			localStorage.setItem(DISMISS_KEY, "1");
		} catch {
			/* ignore */
		}
		setShow(false);
	};

	if (!show) return null;

	return (
		<div
			className={cn(
				"relative overflow-hidden rounded-xl border border-border/60 bg-gradient-to-br from-primary/5 to-card/40 p-4",
			)}
		>
			<button
				type="button"
				aria-label="Dismiss tips"
				onClick={dismiss}
				className="absolute top-2 right-2 inline-flex size-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
			>
				<XIcon className="size-3.5" />
			</button>
			<div className="flex flex-wrap gap-4">
				{TIPS.map((t) => (
					<div
						key={t.title}
						className="flex min-w-[200px] flex-1 items-start gap-2.5"
					>
						<div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
							<t.icon className="size-3.5" />
						</div>
						<div>
							<p className="font-[510] text-[12.5px] text-foreground">
								{t.title}
							</p>
							<p className="mt-0.5 text-[11.5px] text-muted-foreground leading-relaxed">
								{t.body}
							</p>
						</div>
					</div>
				))}
			</div>
			<div className="mt-3 flex justify-end">
				<Button variant="ghost" size="sm" onClick={dismiss}>
					Got it
				</Button>
			</div>
		</div>
	);
}
