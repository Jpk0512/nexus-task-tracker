// Linear-style kind palette for the Library. Colors pulled from the prompts
// palette (DESIGN.md chart family) so chips visually match the rest of the
// product. The hex is the canonical dot color; the soft variant is used for
// the leading-icon square so the chip + icon read as one signal.

export type LibraryKind = "skill" | "agent" | "orchestration";

export const KIND_COLOR: Record<LibraryKind, string> = {
	skill: "#26b5ce", // teal-blue (Linear chart)
	orchestration: "#9b8afb", // violet
	agent: "#4cb782", // green
};

// Tailwind utility soup for the leading-icon square. Uses inline rgb()
// so we don't need to extend the tailwind config for one-off colors.
export const KIND_ICON_BG: Record<LibraryKind, string> = {
	skill: "bg-[rgba(38,181,206,0.12)] text-[#26b5ce]",
	orchestration: "bg-[rgba(155,138,251,0.14)] text-[#9b8afb]",
	agent: "bg-[rgba(76,183,130,0.14)] text-[#4cb782]",
};

export function kindColor(kind: string): string {
	return KIND_COLOR[kind as LibraryKind] ?? "#8a8f98";
}

export function kindIconBg(kind: string): string {
	return KIND_ICON_BG[kind as LibraryKind] ?? "bg-muted text-muted-foreground";
}
