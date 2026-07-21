"use client";

import { cn } from "@ui/lib/utils";
import type { LucideIcon } from "lucide-react";
import type { CSSProperties } from "react";

/**
 * Soft tinted icon tile — OpenShip-style identity mark for **content surfaces
 * only** (Home tiles, Skills cards, empty states, project overview).
 * Do NOT use in the app sidebar (DEC: plain stroke icons only).
 */
export type SoftIconTone =
	| "blue"
	| "green"
	| "violet"
	| "orange"
	| "teal"
	| "pink"
	| "red"
	| "yellow"
	| "gray";

const TONE: Record<
	SoftIconTone,
	{ color: string; bg: string; ring: string }
> = {
	blue: {
		/* Skills teal — product accent */
		color: "#26b5ce",
		bg: "rgba(38,181,206,0.16)",
		ring: "rgba(38,181,206,0.28)",
	},
	green: {
		color: "#4cb782",
		bg: "rgba(76,183,130,0.14)",
		ring: "rgba(76,183,130,0.22)",
	},
	violet: {
		/* Aligned to skills teal (no lavender product accents). */
		color: "#26b5ce",
		bg: "rgba(38,181,206,0.14)",
		ring: "rgba(38,181,206,0.25)",
	},
	orange: {
		color: "#f2a65a",
		bg: "rgba(242,166,90,0.14)",
		ring: "rgba(242,166,90,0.25)",
	},
	teal: {
		color: "#26b5ce",
		bg: "rgba(38,181,206,0.14)",
		ring: "rgba(38,181,206,0.22)",
	},
	pink: {
		color: "#e879a9",
		bg: "rgba(232,121,169,0.14)",
		ring: "rgba(232,121,169,0.22)",
	},
	red: {
		color: "#eb5757",
		bg: "rgba(235,87,87,0.14)",
		ring: "rgba(235,87,87,0.25)",
	},
	yellow: {
		color: "#e6c35c",
		bg: "rgba(230,195,92,0.14)",
		ring: "rgba(230,195,92,0.25)",
	},
	gray: {
		color: "#8a8f98",
		bg: "rgba(138,143,152,0.12)",
		ring: "rgba(138,143,152,0.2)",
	},
};

const SIZE = {
	sm: "size-7 rounded-lg [&_svg]:size-3.5",
	md: "size-9 rounded-[10px] [&_svg]:size-4",
	lg: "size-11 rounded-xl [&_svg]:size-5",
} as const;

export function SoftIcon({
	icon: Icon,
	tone = "blue",
	size = "md",
	className,
}: {
	icon: LucideIcon;
	tone?: SoftIconTone;
	size?: keyof typeof SIZE;
	className?: string;
}) {
	const t = TONE[tone];
	const style = {
		color: t.color,
		backgroundColor: t.bg,
		boxShadow: `inset 0 0 0 1px ${t.ring}`,
	} as CSSProperties;

	return (
		<span
			className={cn(
				"inline-flex shrink-0 items-center justify-center",
				SIZE[size],
				className,
			)}
			style={style}
			aria-hidden
		>
			<Icon className="stroke-[1.75]" />
		</span>
	);
}
