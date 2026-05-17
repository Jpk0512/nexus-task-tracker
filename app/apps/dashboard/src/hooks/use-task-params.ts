import { parseAsBoolean, parseAsString, useQueryStates } from "nuqs";

export function useTaskParams() {
	const [params, setParams] = useQueryStates({
		taskId: parseAsString,
		taskStatusId: parseAsString,
		taskProjectId: parseAsString,
		taskMilestoneId: parseAsString,
		taskTitle: parseAsString,
		// Pre-fills the form's `recurring` field with a sensible daily cron when
		// the create dialog opens — used by the Recurring tab empty-state CTA.
		taskRecurring: parseAsBoolean,
		createTask: parseAsBoolean,
	});

	return {
		...params,
		setParams,
	};
}
