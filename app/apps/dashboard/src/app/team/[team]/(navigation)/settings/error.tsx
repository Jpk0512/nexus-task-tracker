"use client";

import { Button } from "@ui/components/ui/button";
import { AlertTriangleIcon, RefreshCwIcon } from "lucide-react";
import { useEffect } from "react";

/**
 * Route-segment error boundary for /settings and everything nested under it
 * (integrations, notifications, api-keys, etc).
 *
 * FEAT-020: the outer (navigation) group already has an error.tsx, but a
 * server-side throw from a settings sub-page (e.g. a tRPC 400 from
 * integrations.getByType on load) can blank the settings panel before that
 * boundary re-renders. This boundary sits directly around the settings
 * route segment — closer to the failure — while settings/layout.tsx (the
 * sidebar) stays mounted outside it, so navigation is never lost even if a
 * single settings page errors.
 */
export default function SettingsRouteError({
	error,
	reset,
}: {
	error: Error & { digest?: string };
	reset: () => void;
}) {
	useEffect(() => {
		console.error("[dashboard] settings route error:", error);
	}, [error]);

	return (
		<div className="flex flex-1 flex-col items-center justify-center gap-4 p-10 text-center">
			<div className="flex size-12 items-center justify-center rounded-full bg-destructive/10 text-destructive">
				<AlertTriangleIcon className="size-6" />
			</div>
			<div>
				<h2 className="font-[510] text-[16px]">
					This settings page hit an error
				</h2>
				<p className="mt-1 max-w-sm text-[13px] text-muted-foreground">
					The rest of settings is still working — try again, or reload if it
					keeps happening.
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
