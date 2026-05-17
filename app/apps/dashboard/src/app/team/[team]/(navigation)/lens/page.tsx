import { PersonalLens } from "@/components/lens/personal-lens";

/**
 * /team/[team]/lens — Things-style personal lens (codex delighter #2).
 *
 * Pure overlay on the user's existing task slice; no new schema, no new
 * endpoint. See `personal-lens.tsx` for the segment derivation rules.
 */
export default function LensPage() {
	return <PersonalLens />;
}
