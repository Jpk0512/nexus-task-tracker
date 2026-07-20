import { PersonalLens } from "@/components/lens/personal-lens";

/**
 * /focus — Dashboard OS Attention surface.
 * Reuses Lens segment math (Today / Upcoming / Anytime / Someday / Logbook).
 * Needs-you segment lands in a follow-up phase.
 */
export default function FocusPage() {
	return <PersonalLens />;
}
