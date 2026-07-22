"use client";

import { Button } from "@ui/components/ui/button";
import { AlertTriangleIcon, RefreshCwIcon } from "lucide-react";
import { useEffect } from "react";

/**
 * Route-segment error boundary for the dashboard navigation group.
 *
 * A single failing query/widget (e.g. a tRPC error) no longer blanks the
 * whole page — the user sees a calm recovery card and can retry without a
 * full reload. Logged to the console for debugging.
 */
export default function RouteError({
	error,
	reset,
}: {
	error: Error & { digest?: string };
	reset: () => void;
}) {
	useEffect(() => {
		console.error("[dashboard] route error:", error);
	}, [error]);

	return (
		<div className="flex flex-1 flex-col items-center justify-center gap-4 p-10 text-center">
			<div className="flex size-12 items-center justify-center rounded-full bg-destructive/10 text-destructive">
				<AlertTriangleIcon className="size-6" />
			</div>
			<div>
				<h2 className="font-[510] text-[16px]">Something went wrong</h2>
				<p className="mt-1 max-w-sm text-[13px] text-muted-foreground">
					That section hit an error. The rest of the app is still working — try
					again, or reload if it keeps happening.
				</p>
			</div>
			<div className="flex gap-2">
				<Button onClick={reset} size="sm">
					<RefreshCwIcon className="mr-1.5 size-3.5" />
					Try again
				</Button>
				<Button
					variant="outline"
					size="sm"
					onClick={() => window.location.reload()}
				>
					Reload page
				</Button>
			</div>
		</div>
	);
}
