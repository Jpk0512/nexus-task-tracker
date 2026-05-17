"use client";

import dynamic from "next/dynamic";

const FocusSession = dynamic(
	() =>
		import("./focus-session").then((m) => ({
			default: m.FocusSession,
		})),
	{ ssr: false },
);

export function FocusSessionLoader() {
	return <FocusSession />;
}
