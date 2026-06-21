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
	export const it: typeof test;
	export function beforeAll(fn: () => void | Promise<void>): void;
	export function afterAll(fn: () => void | Promise<void>): void;
	export function beforeEach(fn: () => void | Promise<void>): void;
	export function afterEach(fn: () => void | Promise<void>): void;

	export const vi: {
		fn: <T extends (...args: never[]) => unknown>(
			impl?: T,
		) => T & {
			mockReturnValue: (v: unknown) => unknown;
			mockImplementation: (fn: T) => unknown;
		};
		mock: (path: string, factory?: () => unknown) => void;
		clearAllMocks: () => void;
		resetAllMocks: () => void;
	};

	interface Matchers<R = void> {
		toHaveLength(length: number): R;
		toBe(expected: unknown): R;
		toEqual(expected: unknown): R;
		toMatch(pattern: string | RegExp): R;
		toHaveProperty(keyPath: string | string[], value?: unknown): R;
		toContain(item: unknown): R;
		toBeTruthy(): R;
		toBeFalsy(): R;
		toBeNull(): R;
		toBeDefined(): R;
		toBeGreaterThan(expected: number): R;
		toBeGreaterThanOrEqual(expected: number): R;
		toBeLessThan(expected: number): R;
		toBeLessThanOrEqual(expected: number): R;
		toThrow(expected?: string | RegExp | Error): R;
		toBeInTheDocument(): R;
		not: Matchers<R>;
	}

	export function expect<T>(value: T, message?: string): Matchers;
}
