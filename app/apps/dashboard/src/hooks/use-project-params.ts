import { parseAsBoolean, parseAsString, useQueryStates } from "nuqs";

export function useProjectParams() {
	const [params, setParams] = useQueryStates({
		projectId: parseAsString,
		createProject: parseAsBoolean,
		// Cross-entity "convert to project" quick action (FEAT-006 item 5) —
		// seeds the create-project dialog's name/description without a round
		// trip through a dedicated draft store.
		projectSeedName: parseAsString,
		projectSeedDescription: parseAsString,
	});

	return {
		...params,
		setParams,
	};
}
