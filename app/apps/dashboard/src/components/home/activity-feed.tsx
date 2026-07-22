"use client";

import { ActivityTimeline } from "./activity-timeline";

/**
 * Activity feed — last 10 events, grouped by Today / Yesterday / Week / Earlier.
 *
 * Iter-10 redesign keeps the existing `ActivityTimeline` implementation (which
 * already uses the `activities` tRPC route) and re-exports it under the
 * designer-meta name. Renaming the underlying component would churn a stable
 * surface; the wrapper gives the config modal a stable identifier without
 * touching the implementation.
 */
export const ActivityFeed = ({
	enableBulkActions = false,
}: {
	enableBulkActions?: boolean;
} = {}) => <ActivityTimeline enableBulkActions={enableBulkActions} />;
