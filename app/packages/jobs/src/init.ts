import { randomUUID } from "node:crypto";
import type { Database } from "@mimir/db/client";
import { createJobDb } from "@mimir/db/job-client";

// ---------------------------------------------------------------------------
// Minimal logger shim — replaces trigger.dev's logger
// ---------------------------------------------------------------------------

export const logger = {
	info: (...args: unknown[]) => console.info("[jobs]", ...args),
	warn: (...args: unknown[]) => console.warn("[jobs]", ...args),
	error: (...args: unknown[]) => console.error("[jobs]", ...args),
	log: (...args: unknown[]) => console.log("[jobs]", ...args),
	debug: (...args: unknown[]) => console.debug("[jobs]", ...args),
};

// ---------------------------------------------------------------------------
// Register enqueue into globalThis so @mimir/db can call it without
// a compile-time import (avoids the db → jobs circular dependency).
// Wired at module-eval time at the bottom of this file.

// ---------------------------------------------------------------------------
// Per-job-run DB context (replaces trigger.dev locals)
// ---------------------------------------------------------------------------

const _runDb = new Map<
	string,
	{ db: Database; disconnect: () => Promise<void> }
>();
let _currentRunId: string | null = null;

function withRunId<T>(runId: string, fn: () => Promise<T>): Promise<T> {
	const prev = _currentRunId;
	_currentRunId = runId;
	return fn().finally(() => {
		_currentRunId = prev;
		const entry = _runDb.get(runId);
		if (entry) {
			entry.disconnect().catch(() => {});
			_runDb.delete(runId);
		}
	});
}

export const getDb = (): Database => {
	if (!_currentRunId) throw new Error("getDb() called outside of a job run");
	const entry = _runDb.get(_currentRunId);
	if (!entry) throw new Error("Database not initialized for this job run");
	return entry.db;
};

// ---------------------------------------------------------------------------
// Job registry — maps id → run function
// ---------------------------------------------------------------------------

type RunFn<P> = (payload: P, ctx: JobContext) => Promise<unknown>;

interface JobDef<P = unknown> {
	id: string;
	run: RunFn<P>;
	onStart?: (opts: { payload: P }) => Promise<void>;
	onSuccess?: (opts: { payload: P }) => Promise<void>;
	onFailure?: (opts: { payload: P; error: unknown }) => Promise<void>;
}

const _registry = new Map<string, JobDef<unknown>>();

export interface JobContext {
	run: { id: string };
}

export interface JobHandle {
	id: string;
}

// ---------------------------------------------------------------------------
// Schedule registry — maps scheduled job id → cancel fn
// ---------------------------------------------------------------------------

const _schedules = new Map<string, () => void>();

// ---------------------------------------------------------------------------
// Deferred timer handles — for one-off delayed jobs (cancelRun)
// ---------------------------------------------------------------------------

const _timers = new Map<string, ReturnType<typeof setTimeout>>();

// ---------------------------------------------------------------------------
// defineJob — register a job definition
// ---------------------------------------------------------------------------

export function defineJob<P>(def: JobDef<P>): JobDef<P> & {
	trigger: (payload: P, opts?: TriggerOpts) => Promise<JobHandle>;
} {
	_registry.set(def.id, def as JobDef<unknown>);
	return {
		...def,
		trigger: (payload: P, opts?: TriggerOpts) =>
			enqueue(def.id, payload as Record<string, unknown>, opts),
	};
}

interface TriggerOpts {
	delay?: Date | number;
	idempotencyKey?: string;
	tags?: string[];
}

// ---------------------------------------------------------------------------
// enqueue — one-off job dispatch (immediate or delayed)
// ---------------------------------------------------------------------------

export async function enqueue(
	jobName: string,
	payload: Record<string, unknown>,
	opts?: { delayMs?: number; delay?: Date | number; idempotencyKey?: string },
): Promise<JobHandle> {
	const id = randomUUID();
	const delayMs = resolveDelayMs(opts);

	if (delayMs > 0) {
		const handle = setTimeout(() => {
			_timers.delete(id);
			void _execJob(jobName, payload, id);
		}, delayMs);
		_timers.set(id, handle);
	} else {
		void _execJob(jobName, payload, id);
	}

	return { id };
}

function resolveDelayMs(opts?: {
	delayMs?: number;
	delay?: Date | number;
}): number {
	if (!opts) return 0;
	if (opts.delayMs !== undefined) return Math.max(0, opts.delayMs);
	if (opts.delay instanceof Date) {
		return Math.max(0, opts.delay.getTime() - Date.now());
	}
	if (typeof opts.delay === "number") return Math.max(0, opts.delay);
	return 0;
}

async function _execJob(
	jobName: string,
	payload: Record<string, unknown>,
	runId: string,
): Promise<void> {
	const def = _registry.get(jobName);
	if (!def) {
		logger.warn(`[scheduler] Unknown job: ${jobName}`);
		return;
	}

	const dbObj = createJobDb();
	_runDb.set(runId, dbObj);
	const ctx: JobContext = { run: { id: runId } };

	try {
		await def.onStart?.({ payload });
		await withRunId(runId, () => def.run(payload, ctx));
		await def.onSuccess?.({ payload });
	} catch (error) {
		logger.error(`[scheduler] Job ${jobName} (${runId}) failed:`, error);
		await def.onFailure?.({ payload, error });
	}
}

// ---------------------------------------------------------------------------
// cancelRun — cancel a pending delayed job by id
// ---------------------------------------------------------------------------

export async function cancelJob(runId: string): Promise<void> {
	const handle = _timers.get(runId);
	if (handle) {
		clearTimeout(handle);
		_timers.delete(runId);
	}
}

// ---------------------------------------------------------------------------
// registerCron — register a recurring cron job
// Uses the 'croner' package already listed in jobs/package.json
// ---------------------------------------------------------------------------

export interface CronDescriptor {
	id: string;
	cron: string;
}

export function registerCron(
	id: string,
	cron: string,
	fn: () => Promise<void>,
): CronDescriptor {
	if (_schedules.has(id)) {
		_schedules.get(id)?.();
		_schedules.delete(id);
	}

	// Lazy require croner so the module is not imported until a cron is registered
	// eslint-disable-next-line @typescript-eslint/no-require-imports
	const { Cron } = require("croner") as typeof import("croner");
	const job = new Cron(cron, { catch: true, protect: true }, () => {
		void fn();
	});

	_schedules.set(id, () => job.stop());
	return { id, cron };
}

// ---------------------------------------------------------------------------
// Wire enqueue into globalThis so @mimir/db/queries/agent-triggers can call
// it without a compile-time import (breaks the db → jobs circular dep).
// ---------------------------------------------------------------------------
(globalThis as Record<string, unknown>).__jobsEnqueue = enqueue;
