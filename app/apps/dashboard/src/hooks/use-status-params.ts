import { parseAsBoolean, parseAsString, useQueryStates } from "nuqs";

export function useStatusParams() {
	const [params, setParams] = useQueryStates({
		statusId: parseAsString,
		createStatus: parseAsBoolean,
	});

	return {
		...params,
		setParams,
	};
}
