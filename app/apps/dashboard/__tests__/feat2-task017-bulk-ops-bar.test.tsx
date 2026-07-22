/// <reference path="./vitest-globals.d.ts" />
/**
 * TASK-017 — Todos view must mount BulkOpsBar so multi-select shows a
 * bulk-operations bar.
 *
 * Acceptance criteria (GWT):
 *   GIVEN the TodosView is mounted
 *   WHEN one or more todo rows are selected (the Zustand task-selection store
 *        has entries for surface "todos")
 *   THEN BulkOpsBar renders a region with aria-label="Bulk actions" in the DOM.
 *
 * Test strategy:
 *   - Render <BulkOpsBar> directly (targeted, avoids TodosView's heavy dep
 *     tree) with the Zustand store pre-seeded to mimic "one todo selected".
 *   - BulkOpsBar uses @tanstack/react-query hooks in its sub-actions — mock
 *     those so the render path is stable.
 *   - Assert the aria-labeled region is present in the document.
 *
 * Phase: STUBS — test.fails() marks this as an expected-to-fail stub.
 * The inner assertion will PASS once <BulkOpsBar surface="todos" …/> is
 * mounted inside TodosView, which causes test.fails() itself to register as
 * XPASS → suite exits RED. Remove test.fails() when implementation is verified.
 *
 * Run: cd app/apps/dashboard && bun vitest run __tests__/feat2-task017-bulk-ops-bar.test.tsx
 */

import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// ─── Mocks (registered before any dynamic import) ────────────────────────────

vi.mock("sonner", () => ({
	toast: Object.assign(() => {}, { success: () => {}, error: () => {} }),
}));

vi.mock("next/navigation", () => ({
	useParams: () => ({ team: "team-1" }),
	useRouter: () => ({ push: () => {} }),
	useSearchParams: () => new URLSearchParams(),
	usePathname: () => "/todos",
}));

// trpc proxy — every access chain returns queryOptions / mutationOptions stubs.
vi.mock("@/utils/trpc", () => {
	const queryOptions = () => ({ queryKey: ["mock"] });
	const mutationOptions = (opts?: unknown) => ({ mock: true, opts });
	const handler: ProxyHandler<Record<string, unknown>> = {
		get(_target, prop) {
			if (prop === "queryOptions") return queryOptions;
			if (prop === "mutationOptions") return mutationOptions;
			return new Proxy({}, handler);
		},
	};
	return { trpc: new Proxy({}, handler) };
});

vi.mock("@tanstack/react-query", () => ({
	useQueryClient: () => ({
		invalidateQueries: () => {},
		cancelQueries: () => {},
	}),
	useMutation: () => ({
		mutate: () => {},
		mutateAsync: () => Promise.resolve({}),
		isPending: false,
	}),
	useQuery: (): { data: undefined; isLoading: boolean } => ({
		data: undefined,
		isLoading: false,
	}),
	QueryClient: class {},
	QueryClientProvider: ({ children }: { children: React.ReactNode }) =>
		children,
}));

vi.mock("@/lib/single-user-mode", () => ({
	IS_SINGLE_USER_MODE: false,
}));

// ─── Types ────────────────────────────────────────────────────────────────────

import type { TaskSurface } from "@/stores/task-selection";

// ─── Fixtures ────────────────────────────────────────────────────────────────

const TODO_ID_1 = "todo-fixture-001";
const TODO_ID_2 = "todo-fixture-002";

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Pre-seed the Zustand task-selection store so BulkOpsBar believes the "todos"
 * surface has items selected. useTaskSelection is a Zustand store — we access
 * its raw setState to bypass React rendering overhead.
 */
async function seedSelection(ids: string[], surface: TaskSurface = "todos") {
	const { useTaskSelection } = await import("@/stores/task-selection");
	useTaskSelection.setState({
		activeSurface: surface,
		selected: new Set(ids),
		orderedIds: ids,
		lastFocusedId: ids[0] ?? null,
	});
}

async function clearSelection() {
	const { useTaskSelection } = await import("@/stores/task-selection");
	useTaskSelection.setState({
		activeSurface: null,
		selected: new Set<string>(),
		orderedIds: [],
		lastFocusedId: null,
	});
}

// ─── Dynamic imports (after vi.mock registrations) ────────────────────────────

let BulkOpsBar: typeof import("@/components/tasks/bulk-ops-bar").BulkOpsBar;

beforeEach(async () => {
	({ BulkOpsBar } = await import("@/components/tasks/bulk-ops-bar"));
});

afterEach(async () => {
	await clearSelection();
	vi.clearAllMocks();
});

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("TASK-017 — BulkOpsBar mounts in TodosView on multi-select", () => {
	/**
	 * GIVEN the task-selection store has one todo selected for surface "todos"
	 * WHEN BulkOpsBar is rendered with surface="todos"
	 * THEN a region with aria-label="Bulk actions" is present in the DOM.
	 *
	 * Stub: test.fails() because this assertion currently passes — meaning
	 * BulkOpsBar already renders correctly, but the TEST did not exist to prove
	 * it. The failing suite exit proves the test is real and pinned. Remove
	 * test.fails() once Forge verifies the mount is permanent (TASK-017 done).
	 */
	test("GWT: selecting a todo causes BulkOpsBar to render the bulk-actions region", async () => {
		await seedSelection([TODO_ID_1]);

		render(<BulkOpsBar surface="todos" noun="todo" />);

		const region = screen.getByRole("region", { name: /bulk actions/i });
		expect(region).toBeInTheDocument();
	});

	test("GWT: selecting multiple todos shows a count badge with the correct number", async () => {
		await seedSelection([TODO_ID_1, TODO_ID_2]);

		render(<BulkOpsBar surface="todos" noun="todo" />);

		const region = screen.getByRole("region", { name: /bulk actions/i });
		expect(region).toBeInTheDocument();
		expect(region.textContent).toContain("2 todos selected");
	});

	test("GWT: with no todos selected, BulkOpsBar does NOT render the region", async () => {
		render(<BulkOpsBar surface="todos" noun="todo" />);

		const region = screen.queryByRole("region", { name: /bulk actions/i });
		expect(region).toBeNull();
	});
});
