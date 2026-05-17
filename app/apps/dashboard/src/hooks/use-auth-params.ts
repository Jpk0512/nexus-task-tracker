"use client";
import { parseAsString, useQueryStates } from "nuqs";
import { useEffect } from "react";

export const useAuthParams = () => {
	const [params, setParams] = useQueryStates({
		callbackUrl: parseAsString,
		email: parseAsString,
		token: parseAsString,
	});

	useEffect(() => {
		if (typeof window !== "undefined" && params.callbackUrl) {
			localStorage.setItem("callbackUrl", params.callbackUrl);
		}
	}, [params.callbackUrl]);

	return {
		...params,
		setParams,
	};
};
