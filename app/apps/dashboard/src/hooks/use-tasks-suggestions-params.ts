import { parseAsBoolean, useQueryStates } from "nuqs";

export function useTaskSuggestionsParams() {
	const [params, setParams] = useQueryStates({
		showTaskSuggestions: parseAsBoolean,
	});

	return {
		...params,
		setParams,
	};
}
