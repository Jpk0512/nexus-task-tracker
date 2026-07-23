import { TRPCError } from "@trpc/server";

// node-postgres surfaces Postgres errors as plain Error objects decorated
// with SQLSTATE metadata (`code`, `constraint`, `column`, `detail`) rather
// than a typed class. Narrow on `code` being present — that's the one field
// every DatabaseError carries.
type PgError = Error & {
	code?: string;
	constraint?: string;
	column?: string;
	detail?: string;
};

const isPgError = (error: unknown): error is PgError =>
	error instanceof Error && typeof (error as PgError).code === "string";

/**
 * drizzle-orm's node-postgres driver wraps every query failure in a
 * `DrizzleQueryError` and preserves the real `pg` DatabaseError (the one
 * carrying `code`/`constraint`) on `.cause` — the SQLSTATE metadata is never
 * on the outer error itself. Unwrap one level before giving up.
 */
const unwrapPgError = (error: unknown): PgError | undefined => {
	if (isPgError(error)) return error;
	if (error instanceof Error && isPgError(error.cause)) return error.cause;
	return undefined;
};

/**
 * Maps a thrown project-create error into a specific, user-readable
 * TRPCError. Never returns a silent/generic failure: a known Postgres
 * constraint gets a targeted message, anything else still carries the
 * underlying error text so a create failure is always diagnosable from
 * the client toast rather than surfacing as an opaque 500.
 */
export function toProjectCreateError(error: unknown): TRPCError {
	if (error instanceof TRPCError) return error;

	const pgError = unwrapPgError(error);
	if (pgError) {
		switch (pgError.code) {
			// unique_violation
			case "23505": {
				if (pgError.constraint === "unique_project_name_per_team") {
					return new TRPCError({
						code: "CONFLICT",
						message:
							"A project with that name already exists on this team. Choose a different name.",
					});
				}
				return new TRPCError({
					code: "CONFLICT",
					message: `Project could not be created: a value already in use (constraint: ${
						pgError.constraint ?? "unknown"
					}).`,
				});
			}
			// foreign_key_violation
			case "23503": {
				if (pgError.constraint === "projects_lead_id_fkey") {
					return new TRPCError({
						code: "BAD_REQUEST",
						message:
							"The selected lead is not a valid user — pick a current team member.",
					});
				}
				if (pgError.constraint === "projects_team_id_fkey") {
					return new TRPCError({
						code: "PRECONDITION_FAILED",
						message:
							"Your team record could not be found. If this is a local-dev environment, run packages/db/src/seed-local-dev.ts and sign in again.",
					});
				}
				return new TRPCError({
					code: "BAD_REQUEST",
					message: `Project could not be created: a referenced record was not found (constraint: ${
						pgError.constraint ?? "unknown"
					}).`,
				});
			}
			// not_null_violation
			case "23502": {
				return new TRPCError({
					code: "BAD_REQUEST",
					message: `Project could not be created: required field "${
						pgError.column ?? "unknown"
					}" was missing.`,
				});
			}
			default:
				break;
		}
	}

	return new TRPCError({
		code: "INTERNAL_SERVER_ERROR",
		message: `Project could not be created: ${
			error instanceof Error ? error.message : "unknown error"
		}`,
	});
}
