/**
 * Single-user-mode contract (iter-10, codex amendment #1).
 *
 * mimrai runs in two postures: hosted multi-user (production) and local-only
 * single-user (the default developer + dogfood experience). In single-user
 * mode we hide multi-user affordances — assignee pickers, member lists,
 * "notify users" toggles, share targeting — because there is exactly one
 * actor (the local-dev user) and surfacing those controls is noise + IA
 * drift.
 *
 * We piggy-back on the SAME env var the auth bypass already uses
 * (`MIMRAI_LOCAL_DEV` on the server, `NEXT_PUBLIC_MIMRAI_LOCAL_DEV` mirror
 * for the client bundle) so there is one source of truth. Introducing a
 * separate flag would create the IA drift codex flagged. The mirror is
 * required because `process.env.MIMRAI_LOCAL_DEV` is undefined in the
 * browser bundle unless prefixed with `NEXT_PUBLIC_`.
 *
 * Usage:
 *   import { IS_SINGLE_USER_MODE } from "@/lib/single-user-mode";
 *   {!IS_SINGLE_USER_MODE && <AssignToPicker ... />}
 *
 * This is a build-time constant on the client (Next.js inlines
 * `process.env.NEXT_PUBLIC_*`) so the gated code is dead-stripped from
 * the production bundle in hosted mode.
 */
export const IS_SINGLE_USER_MODE: boolean =
	process.env.NEXT_PUBLIC_MIMRAI_LOCAL_DEV === "1" ||
	process.env.MIMRAI_LOCAL_DEV === "1";
