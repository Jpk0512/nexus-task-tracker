/**
 * Minimal ambient declarations for vitest symbols used in this test suite.
 * vitest is provided at runtime by `rtk vitest` (npx cache) and is not
 * installed in the project's node_modules; this file makes tsc happy.
 */

declare module "vitest" {
	export function describe(name: string, fn: () => void): void;
	export function test(name: string, fn: () => void | Promise<void>): void;
	export namespace test {
		export function fails(name: string, fn: () => void | Promise<void>): void;
	}
	export function expect<T>(
		value: T,
		message?: string,
	): {
		toHaveLength(length: number): void;
		toBe(expected: T): void;
		toEqual(expected: unknown): void;
	};
}
