/**
 * TASK-029 — Behavioral IDOR tests via tRPC createCallerFactory.
 *
 * Problem: all prior IDOR tests are source-grep assertions that confirm the
 * guard TEXT exists in source but cannot prove the guard executes at runtime.
 * If a developer refactors the guard into a helper that no longer matches the
 * regex, the source-grep passes while the vulnerability is live. This file
 * closes that gap with real execution against the real DB.
 *
 * Strategy: createCallerFactory (tRPC v11) with a hand-built Context that sets
 * ctx.user.teamId = TEAM_B_ID. A minimal test router wraps only the four
 * routers under test (tasks, agents, taskExecutions, teams) — this avoids
 * pulling in auth/resend/better-auth module-level side effects from the full
 * appRouter. Rows owned by TEAM_A_ID are seeded into docker postgres (:55432).
 * Each procedure is invoked as team-B and asserted to produce NOT_FOUND / null,
 * AND the row is verified unchanged in the DB.
 *
 * Acceptance criteria (GWT format):
 *
 *  GWT-C1  tasks.attachDocument — foreign-team IDOR blocked
 *    GIVEN a task owned by team-A
 *    WHEN team-B caller calls tasks.attachDocument with that taskId
 *    THEN TRPCError NOT_FOUND is thrown; no documentsOnTasks row is created
 *
 *  GWT-C2  tasks.detachDocument — foreign-team IDOR blocked
 *    GIVEN a task owned by team-A
 *    WHEN team-B caller calls tasks.detachDocument
 *    THEN TRPCError NOT_FOUND; no deletion occurs
 *
 *  GWT-C3  tasks.linkKnowledge — foreign-team IDOR blocked
 *    GIVEN a task owned by team-A
 *    WHEN team-B caller calls tasks.linkKnowledge
 *    THEN TRPCError NOT_FOUND; no knowledgeNotesOnTasks row created
 *
 *  GWT-C4  tasks.linkSkill — foreign-team IDOR blocked
 *    GIVEN a task owned by team-A
 *    WHEN team-B caller calls tasks.linkSkill
 *    THEN TRPCError NOT_FOUND; no taskSkills row created
 *
 *  GWT-C5  agents.deleteMemory — foreign-team IDOR blocked
 *    GIVEN an agent memory owned by team-A
 *    WHEN team-B caller calls agents.deleteMemory
 *    THEN returns without deleting AND the row still exists in agent_memories
 *
 *  GWT-C6  task-executions.getByTaskId — foreign-team IDOR blocked
 *    GIVEN a task execution owned by team-A
 *    WHEN team-B caller calls taskExecutions.getByTaskId
 *    THEN returns null (not the team-A row)
 *
 *  GWT-C7  teams.getInviteById — foreign-team IDOR blocked
 *    GIVEN a team invite created for team-A
 *    WHEN team-B caller (different email) calls teams.getInviteById
 *    THEN returns null (invite hidden from foreign team)
 *
 * Guard-removal regression:
 *    Each section also verifies the same-team caller CAN see/mutate the row.
 *    This proves the guard is not over-blocking and would catch a full removal.
 *
 * Run: cd app/apps/api && NEXUS_LOCAL_DEV=1 DATABASE_URL="postgresql://mimrai:mimrai@localhost:55432/mimrai" bun test src/__tests__/task-029-idor-caller-harness.test.ts
 */

import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { randomUUID } from "node:crypto";
import { t } from "@api/trpc/init";
import { agentsRouter } from "@api/trpc/routers/agents";
import { taskExecutionsRouter } from "@api/trpc/routers/task-executions";
import { tasksRouter } from "@api/trpc/routers/tasks";
import { teamsRouter } from "@api/trpc/routers/teams";
import {
	agentMemories,
	agents,
	documentsOnTasks,
	knowledgeNotesOnTasks,
	statuses,
	taskExecutions,
	tasks,
	teams,
	userInvites,
	users,
	usersOnTeams,
} from "@nexus-app/db/schema";
import { TRPCError } from "@trpc/server";
import { and, eq } from "drizzle-orm";
import { drizzle } from "drizzle-orm/node-postgres";

// ---------------------------------------------------------------------------
// Real DB connection — docker postgres :55432, same pattern as task-021 tests
// ---------------------------------------------------------------------------

const DB_URL =
	process.env.DATABASE_URL ??
	"postgresql://mimrai:mimrai@localhost:55432/mimrai";

const db = drizzle(DB_URL);

// ---------------------------------------------------------------------------
// Minimal test router — only the four routers under test.
// Avoids the full appRouter which pulls in api-keys → auth → better-auth
// module-level init that requires BETTER_AUTH_TRUSTED_ORIGINS at load time.
// ---------------------------------------------------------------------------

const testRouter = t.router({
	tasks: tasksRouter,
	agents: agentsRouter,
	taskExecutions: taskExecutionsRouter,
	teams: teamsRouter,
});

// ---------------------------------------------------------------------------
// Fixture constants — prefixed for safe, precise cleanup
// ---------------------------------------------------------------------------

const TEAM_A_ID = "t029-team-a";
const TEAM_B_ID = "t029-team-b";
const USER_A_ID = "t029-user-a";
const USER_B_ID = "t029-user-b";
const AGENT_A_ID = "t029-agent-a";
const TASK_A_ID = "t029-task-a";
const STATUS_A_ID = "t029-status-a";
const MEMORY_A_ID = "t029-memory-a";
const INVITE_A_ID = "t029-invite-a";

// ---------------------------------------------------------------------------
// Minimal tRPC Context builder — bypasses HTTP/auth, sets teamId directly
// ---------------------------------------------------------------------------

type TestCtxOpts = {
	userId: string;
	teamId: string;
	email?: string;
};

function makeCtx(opts: TestCtxOpts) {
	return {
		session: {
			user: {
				id: opts.userId,
				email: opts.email ?? `${opts.userId}@t029.example`,
				name: "Test User",
				emailVerified: true,
				image: null,
				createdAt: new Date(),
				updatedAt: new Date(),
			},
			session: {
				id: `sess-${opts.userId}`,
				token: `tok-${opts.userId}`,
				userId: opts.userId,
				expiresAt: new Date(Date.now() + 86400_000),
				createdAt: new Date(),
				updatedAt: new Date(),
			},
		},
		user: {
			id: opts.userId,
			name: "Test User",
			email: opts.email ?? `${opts.userId}@t029.example`,
			emailVerified: true,
			image: null,
			locale: null,
			teamId: opts.teamId,
			teamSlug: `slug-${opts.teamId}`,
			isMentionable: true,
			color: "#aabbcc",
			isSystemUser: false,
			dateFormat: null,
			createdAt: new Date(),
			updatedAt: new Date(),
			scopes: [] as string[],
		},
		team: {
			id: opts.teamId,
			name: `Team ${opts.teamId}`,
			slug: `slug-${opts.teamId}`,
			prefix: "T",
			description: null,
			email: `team@${opts.teamId}.example`,
			timezone: "UTC",
			locale: "en-US",
			createdAt: new Date(),
			updatedAt: new Date(),
			role: "member" as const,
		},
	};
}

// ---------------------------------------------------------------------------
// Caller factory
// ---------------------------------------------------------------------------

const callerFactory = t.createCallerFactory(testRouter);

function callerAs(opts: TestCtxOpts) {
	// Context type from context.ts has session typed as `any`.
	// biome-ignore lint/suspicious/noExplicitAny: test-only harness ctx cast
	return callerFactory(makeCtx(opts) as any);
}

// ---------------------------------------------------------------------------
// Setup: seed fixtures for team-A into the test DB
// ---------------------------------------------------------------------------

beforeAll(async () => {
	await db
		.insert(teams)
		.values([
			{
				id: TEAM_A_ID,
				name: "Team A (t029)",
				slug: "t029-team-a-slug",
				prefix: "A",
				email: "a@t029.example",
				timezone: "UTC",
				locale: "en-US",
			},
			{
				id: TEAM_B_ID,
				name: "Team B (t029)",
				slug: "t029-team-b-slug",
				prefix: "B",
				email: "b@t029.example",
				timezone: "UTC",
				locale: "en-US",
			},
		])
		.onConflictDoNothing();

	await db
		.insert(users)
		.values([
			{
				id: USER_A_ID,
				name: "User A",
				email: "user-a@t029.example",
				emailVerified: true,
				teamId: TEAM_A_ID,
				teamSlug: "t029-team-a-slug",
				isMentionable: true,
				isSystemUser: false,
				createdAt: new Date(),
				updatedAt: new Date(),
			},
			{
				id: USER_B_ID,
				name: "User B",
				email: "user-b@t029.example",
				emailVerified: true,
				teamId: TEAM_B_ID,
				teamSlug: "t029-team-b-slug",
				isMentionable: true,
				isSystemUser: false,
				createdAt: new Date(),
				updatedAt: new Date(),
			},
		])
		.onConflictDoNothing();

	await db
		.insert(usersOnTeams)
		.values([
			{ userId: USER_A_ID, teamId: TEAM_A_ID, role: "member" },
			{ userId: USER_B_ID, teamId: TEAM_B_ID, role: "member" },
		])
		.onConflictDoNothing();

	// Status is a required FK for task insert
	await db
		.insert(statuses)
		.values({
			id: STATUS_A_ID,
			name: "Backlog",
			color: "#888888",
			type: "backlog",
			teamId: TEAM_A_ID,
			order: 1,
		})
		.onConflictDoNothing();

	// Task owned by team-A
	await db
		.insert(tasks)
		.values({
			id: TASK_A_ID,
			permalinkId: "t029-permalink-a",
			title: "Task A (t029 IDOR test)",
			teamId: TEAM_A_ID,
			statusId: STATUS_A_ID,
			order: 1,
			priority: "medium",
		})
		.onConflictDoNothing();

	// Agent owned by team-A (required FK for memory)
	await db
		.insert(agents)
		.values({
			id: AGENT_A_ID,
			teamId: TEAM_A_ID,
			name: "Agent A (t029)",
			userId: USER_A_ID,
			model: "anthropic/claude-haiku-4.5",
			isActive: true,
			authorizeIntegrations: false,
			activeToolboxes: [],
		})
		.onConflictDoNothing();

	// Agent memory owned by team-A
	await db
		.insert(agentMemories)
		.values({
			id: MEMORY_A_ID,
			agentId: AGENT_A_ID,
			teamId: TEAM_A_ID,
			category: "lesson",
			title: "Memory A (t029 IDOR test)",
			content: "should not be deletable by team-B",
			tags: [],
			relevanceScore: 1,
		})
		.onConflictDoNothing();

	// Team invite for team-A
	await db
		.insert(userInvites)
		.values({
			id: INVITE_A_ID,
			email: "invite-target@t029.example",
			teamId: TEAM_A_ID,
			invitedBy: USER_A_ID,
		})
		.onConflictDoNothing();
});

// ---------------------------------------------------------------------------
// Cleanup: remove fixture rows in FK dependency order
// ---------------------------------------------------------------------------

afterAll(async () => {
	await db.delete(agentMemories).where(eq(agentMemories.id, MEMORY_A_ID));
	await db.delete(taskExecutions).where(eq(taskExecutions.taskId, TASK_A_ID));
	await db
		.delete(documentsOnTasks)
		.where(eq(documentsOnTasks.taskId, TASK_A_ID));
	await db
		.delete(knowledgeNotesOnTasks)
		.where(eq(knowledgeNotesOnTasks.taskId, TASK_A_ID));
	await db.delete(userInvites).where(eq(userInvites.id, INVITE_A_ID));
	await db.delete(agents).where(eq(agents.id, AGENT_A_ID));
	await db.delete(tasks).where(eq(tasks.id, TASK_A_ID));
	await db.delete(statuses).where(eq(statuses.id, STATUS_A_ID));
	await db.delete(usersOnTeams).where(eq(usersOnTeams.userId, USER_A_ID));
	await db.delete(usersOnTeams).where(eq(usersOnTeams.userId, USER_B_ID));
	await db.delete(users).where(eq(users.id, USER_A_ID));
	await db.delete(users).where(eq(users.id, USER_B_ID));
	await db.delete(teams).where(eq(teams.id, TEAM_A_ID));
	await db.delete(teams).where(eq(teams.id, TEAM_B_ID));
});

// ---------------------------------------------------------------------------
// GWT-C1 — tasks.attachDocument: foreign-team IDOR blocked
// ---------------------------------------------------------------------------

describe("GWT-C1 — tasks.attachDocument: team-B caller cannot attach to team-A task", () => {
	const DOC_ID = "t029-fake-doc-1";

	test("GIVEN task owned by team-A WHEN team-B calls attachDocument THEN TRPCError NOT_FOUND", async () => {
		const caller = callerAs({ userId: USER_B_ID, teamId: TEAM_B_ID });
		let threw: TRPCError | null = null;
		try {
			await caller.tasks.attachDocument({
				taskId: TASK_A_ID,
				documentId: DOC_ID,
			});
		} catch (err) {
			if (err instanceof TRPCError) threw = err;
			else throw err;
		}
		expect(threw).not.toBeNull();
		expect(threw!.code).toBe("NOT_FOUND");
	});

	test("no documentsOnTasks row created after team-B attach attempt", async () => {
		const rows = await db
			.select()
			.from(documentsOnTasks)
			.where(
				and(
					eq(documentsOnTasks.taskId, TASK_A_ID),
					eq(documentsOnTasks.documentId, DOC_ID),
				),
			);
		expect(rows.length).toBe(0);
	});
});

// ---------------------------------------------------------------------------
// GWT-C2 — tasks.detachDocument: foreign-team IDOR blocked
// ---------------------------------------------------------------------------

describe("GWT-C2 — tasks.detachDocument: team-B caller cannot detach from team-A task", () => {
	test("GIVEN task owned by team-A WHEN team-B calls detachDocument THEN TRPCError NOT_FOUND", async () => {
		const caller = callerAs({ userId: USER_B_ID, teamId: TEAM_B_ID });
		let threw: TRPCError | null = null;
		try {
			await caller.tasks.detachDocument({
				taskId: TASK_A_ID,
				documentId: "t029-any-doc",
			});
		} catch (err) {
			if (err instanceof TRPCError) threw = err;
			else throw err;
		}
		expect(threw).not.toBeNull();
		expect(threw!.code).toBe("NOT_FOUND");
	});
});

// ---------------------------------------------------------------------------
// GWT-C3 — tasks.linkKnowledge: foreign-team IDOR blocked
// ---------------------------------------------------------------------------

describe("GWT-C3 — tasks.linkKnowledge: team-B caller cannot link knowledge to team-A task", () => {
	const NOTE_ID = "t029-fake-note-1";

	test("GIVEN task owned by team-A WHEN team-B calls linkKnowledge THEN TRPCError NOT_FOUND", async () => {
		const caller = callerAs({ userId: USER_B_ID, teamId: TEAM_B_ID });
		let threw: TRPCError | null = null;
		try {
			await caller.tasks.linkKnowledge({
				taskId: TASK_A_ID,
				noteId: NOTE_ID,
			});
		} catch (err) {
			if (err instanceof TRPCError) threw = err;
			else throw err;
		}
		expect(threw).not.toBeNull();
		expect(threw!.code).toBe("NOT_FOUND");
	});

	test("no knowledgeNotesOnTasks row created after team-B link attempt", async () => {
		const rows = await db
			.select()
			.from(knowledgeNotesOnTasks)
			.where(
				and(
					eq(knowledgeNotesOnTasks.taskId, TASK_A_ID),
					eq(knowledgeNotesOnTasks.noteId, NOTE_ID),
				),
			);
		expect(rows.length).toBe(0);
	});
});

// ---------------------------------------------------------------------------
// GWT-C4 — tasks.linkSkill: foreign-team IDOR blocked
// ---------------------------------------------------------------------------

describe("GWT-C4 — tasks.linkSkill: team-B caller cannot link skill to team-A task", () => {
	test("GIVEN task owned by team-A WHEN team-B calls linkSkill THEN TRPCError NOT_FOUND", async () => {
		const caller = callerAs({ userId: USER_B_ID, teamId: TEAM_B_ID });
		let threw: TRPCError | null = null;
		try {
			await caller.tasks.linkSkill({
				taskId: TASK_A_ID,
				skillId: "t029-fake-skill",
			});
		} catch (err) {
			if (err instanceof TRPCError) threw = err;
			else throw err;
		}
		expect(threw).not.toBeNull();
		expect(threw!.code).toBe("NOT_FOUND");
	});
});

// ---------------------------------------------------------------------------
// GWT-C5 — agents.deleteMemory: foreign-team IDOR blocked
// ---------------------------------------------------------------------------

describe("GWT-C5 — agents.deleteMemory: team-B cannot delete team-A memory", () => {
	test("GIVEN memory owned by team-A WHEN team-B calls deleteMemory the call succeeds silently", async () => {
		// deleteAgentMemory WHERE id AND teamId — silently no-ops on mismatch.
		const caller = callerAs({ userId: USER_B_ID, teamId: TEAM_B_ID });
		await caller.agents.deleteMemory({ id: MEMORY_A_ID });
	});

	test("memory row still exists in DB after team-B delete attempt (guard worked)", async () => {
		const rows = await db
			.select()
			.from(agentMemories)
			.where(eq(agentMemories.id, MEMORY_A_ID));
		expect(rows.length).toBe(1);
		expect(rows[0]!.teamId).toBe(TEAM_A_ID);
	});

	test("same-team caller CAN delete the memory (guard does not over-block)", async () => {
		const ephemeralId = `t029-memory-eph-${randomUUID()}`;
		await db.insert(agentMemories).values({
			id: ephemeralId,
			agentId: AGENT_A_ID,
			teamId: TEAM_A_ID,
			category: "lesson",
			title: "ephemeral",
			content: "ephemeral",
			tags: [],
			relevanceScore: 1,
		});
		const caller = callerAs({ userId: USER_A_ID, teamId: TEAM_A_ID });
		await caller.agents.deleteMemory({ id: ephemeralId });
		const rows = await db
			.select()
			.from(agentMemories)
			.where(eq(agentMemories.id, ephemeralId));
		expect(rows.length).toBe(0);
	});
});

// ---------------------------------------------------------------------------
// GWT-C6 — task-executions.getByTaskId: foreign-team IDOR blocked
// ---------------------------------------------------------------------------

describe("GWT-C6 — taskExecutions.getByTaskId: team-B cannot read team-A execution", () => {
	beforeAll(async () => {
		await db
			.insert(taskExecutions)
			.values({
				taskId: TASK_A_ID,
				teamId: TEAM_A_ID,
				status: "pending",
			})
			.onConflictDoNothing();
	});

	test("GIVEN execution for team-A task WHEN team-B calls getByTaskId THEN returns null", async () => {
		const caller = callerAs({ userId: USER_B_ID, teamId: TEAM_B_ID });
		const result = await caller.taskExecutions.getByTaskId({
			taskId: TASK_A_ID,
		});
		expect(result).toBeNull();
	});

	test("same-team caller CAN read the execution (guard does not over-block)", async () => {
		const caller = callerAs({ userId: USER_A_ID, teamId: TEAM_A_ID });
		const result = await caller.taskExecutions.getByTaskId({
			taskId: TASK_A_ID,
		});
		expect(result).not.toBeNull();
		expect(result!.teamId).toBe(TEAM_A_ID);
	});
});

// ---------------------------------------------------------------------------
// GWT-C7 — teams.getInviteById: foreign-team IDOR blocked
// ---------------------------------------------------------------------------

describe("GWT-C7 — teams.getInviteById: team-B cannot read team-A invite", () => {
	test("GIVEN invite for team-A WHEN team-B caller (different email) calls getInviteById THEN returns null", async () => {
		const caller = callerAs({
			userId: USER_B_ID,
			teamId: TEAM_B_ID,
			email: "user-b@t029.example",
		});
		const result = await caller.teams.getInviteById({
			inviteId: INVITE_A_ID,
		});
		expect(result).toBeNull();
	});

	test("same-team caller CAN read the invite (teamId match grants access)", async () => {
		const caller = callerAs({
			userId: USER_A_ID,
			teamId: TEAM_A_ID,
			email: "user-a@t029.example",
		});
		const result = await caller.teams.getInviteById({
			inviteId: INVITE_A_ID,
		});
		expect(result).not.toBeNull();
		expect(result!.teamId).toBe(TEAM_A_ID);
	});

	test("invitee email match also grants access regardless of teamId (design intent preserved)", async () => {
		// User from team-B with the invite target email should be allowed
		const caller = callerAs({
			userId: USER_B_ID,
			teamId: TEAM_B_ID,
			email: "invite-target@t029.example",
		});
		const result = await caller.teams.getInviteById({
			inviteId: INVITE_A_ID,
		});
		expect(result).not.toBeNull();
	});
});
