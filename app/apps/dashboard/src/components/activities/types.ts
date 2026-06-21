import type { RouterOutputs } from "@nexus-app/trpc";

export type Activity = RouterOutputs["activities"]["get"]["data"][number];
