import type { Database } from "@mimir/db/client";
import { createJobDb } from "@mimir/db/job-client";
import { locals as realLocals, tasks as realTasks } from "@trigger.dev/sdk";

const LOCAL_DEV = process.env.MIMRAI_LOCAL_DEV === "1";

function recursiveTriggerStub(label: string): any {
	return new Proxy(() => {}, {
		get: (_t, prop) => {
			if (prop === "then") return undefined; // not a thenable
			return recursiveTriggerStub(`${label}.${String(prop)}`);
		},
		apply: (_t, _thisArg, args: any[]) => {
			if (label === "tasks.trigger") {
				const name = args?.[0];
				console.log(`[stub:trigger.dev] tasks.trigger ${name ?? ""}`);
				return Promise.resolve({ id: "stub-local-dev" });
			}
			// middleware/onWait/onResume etc. — accept callbacks but never invoke them
			return undefined;
		},
	});
}

// In LOCAL_DEV mode, locals storage is process-local and the trigger.dev runtime
// is not involved. We still need a working get/set/create for the helpers below.
function createLocalsStub() {
	const store = new Map<symbol, unknown>();
	return {
		create<T>(name: string) {
			return { __key: Symbol(name) } as unknown as { __key: symbol } & T;
		},
		get<T>(local: { __key: symbol }): T | undefined {
			return store.get(local.__key) as T | undefined;
		},
		set<T>(local: { __key: symbol }, value: T) {
			store.set(local.__key, value);
		},
	};
}

const tasks: typeof realTasks = LOCAL_DEV
	? (recursiveTriggerStub("tasks") as typeof realTasks)
	: realTasks;
const locals: typeof realLocals = LOCAL_DEV
	? (createLocalsStub() as unknown as typeof realLocals)
	: realLocals;

// Store the database instance
const DbLocal = locals.create<{
	db: Database;
	disconnect: () => Promise<void>;
}>("db");

// Helper function to get the database instance from locals
export const getDb = (): Database => {
	const dbObj = locals.get(DbLocal);
	if (!dbObj) throw new Error("Database not initialized in middleware");
	return dbObj.db;
};

// Helper function to get the disconnect function from locals
const getDisconnect = () => {
	const dbObj = locals.get(DbLocal);
	if (!dbObj) throw new Error("Database not initialized in middleware");
	return dbObj.disconnect();
};

// Middleware is run around every run
tasks.middleware("db", async ({ next }) => {
	// Create a fresh database instance for each job run
	// This ensures consistent connection pooling with optimized settings for Supabase
	const dbObj = createJobDb();
	locals.set(DbLocal, dbObj);

	await next();
});

// This lifecycle hook is called when a `wait` is hit
// In cloud this can result in the machine being suspended until later
tasks.onWait("db", async () => {
	// Close the connection pool to free database connections
	await getDisconnect();
});

// This lifecycle hook is called when a run is resumed after a `wait`
tasks.onResume("db", async () => {
	// Create a new database instance since the old pool was closed
	const db = createJobDb();
	locals.set(DbLocal, db);
});
