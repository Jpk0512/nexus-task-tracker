import { parseAsBoolean, parseAsString, useQueryStates } from "nuqs";

export function useMilestoneParams() {
	const [params, setParams] = useQueryStates({
		milestoneId: parseAsString,
		createMilestone: parseAsBoolean,
		milestoneProjectId: parseAsString,
	});

	return {
		...params,
		setParams,
	};
}
