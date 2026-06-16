import { Redis } from "@upstash/redis";

const LOCAL_DEV = process.env.NEXUS_LOCAL_DEV === "1";

// Stub subscriber that satisfies the @upstash/redis pub-sub shape: callers
// invoke `.on("subscribe" | "message" | "psubscribe", handler)` and
// `.subscribe(channel)` / `.unsubscribe()`. We just no-op everything.
function makeStubSubscriber(): any {
	const handlers = new Map<string, Array<(...args: any[]) => void>>();
	const self = {
		on: (event: string, handler: (...args: any[]) => void) => {
			const list = handlers.get(event) ?? [];
			list.push(handler);
			handlers.set(event, list);
			return self;
		},
		off: (event: string) => {
			handlers.delete(event);
			return self;
		},
		subscribe: async (..._channels: string[]) => self,
		psubscribe: async (..._patterns: string[]) => self,
		unsubscribe: async () => self,
		punsubscribe: async () => self,
		quit: async () => undefined,
	};
	return self;
}

// Map-backed Upstash-shaped fake. Implements only the methods this codebase
// touches; everything else returns null/no-op via a Proxy fallback. Guarded
// by NEXUS_LOCAL_DEV so upstream behavior is byte-identical when unset.
function makeLocalRedis(): any {
	const store = new Map<string, unknown>();
	const ttls = new Map<string, number>();

	const isExpired = (key: string): boolean => {
		const exp = ttls.get(key);
		if (exp == null) return false;
		if (Date.now() > exp) {
			store.delete(key);
			ttls.delete(key);
			return true;
		}
		return false;
	};

	const impl: Record<string, (...args: any[]) => any> = {
		get: async (key: string) => {
			if (isExpired(key)) return null;
			return store.get(key) ?? null;
		},
		set: async (
			key: string,
			value: unknown,
			opts?: { ex?: number; px?: number },
		) => {
			store.set(key, value);
			if (opts?.ex) ttls.set(key, Date.now() + opts.ex * 1000);
			else if (opts?.px) ttls.set(key, Date.now() + opts.px);
			else ttls.delete(key);
			return "OK";
		},
		del: async (...keys: string[]) => {
			let n = 0;
			for (const k of keys) {
				if (store.delete(k)) n++;
				ttls.delete(k);
			}
			return n;
		},
		exists: async (...keys: string[]) =>
			keys.filter((k) => !isExpired(k) && store.has(k)).length,
		incr: async (key: string) => {
			if (isExpired(key)) {
				/* purged */
			}
			const cur = Number(store.get(key) ?? 0) + 1;
			store.set(key, cur);
			return cur;
		},
		expire: async (key: string, seconds: number) => {
			if (!store.has(key)) return 0;
			ttls.set(key, Date.now() + seconds * 1000);
			return 1;
		},
		ttl: async (key: string) => {
			const exp = ttls.get(key);
			if (exp == null) return store.has(key) ? -1 : -2;
			return Math.max(0, Math.floor((exp - Date.now()) / 1000));
		},
		hget: async (key: string, field: string) => {
			if (isExpired(key)) return null;
			const h = store.get(key) as Record<string, unknown> | undefined;
			return h?.[field] ?? null;
		},
		hset: async (key: string, fields: Record<string, unknown>) => {
			const h = (store.get(key) as Record<string, unknown> | undefined) ?? {};
			Object.assign(h, fields);
			store.set(key, h);
			return Object.keys(fields).length;
		},
		hgetall: async (key: string) => {
			if (isExpired(key)) return {};
			return (store.get(key) as Record<string, unknown> | undefined) ?? {};
		},
		eval: async () => null,
		evalsha: async () => null,
		scriptLoad: async () => "stub-script",
		publish: async () => 0,
		// Pub/Sub stub: consumers do `subscriber.on("subscribe", …)` then
		// `subscriber.subscribe(channel)`. Return an EventEmitter-like object.
		subscribe: () => makeStubSubscriber(),
		psubscribe: () => makeStubSubscriber(),
		pipeline: () => ({
			exec: async () => [],
			get: function () {
				return this;
			},
			set: function () {
				return this;
			},
			del: function () {
				return this;
			},
		}),
		multi: () => ({ exec: async () => [] }),
	};

	return new Proxy(impl, {
		get: (target, prop) => {
			if (prop in target) return (target as any)[prop];
			// Default: any unknown method returns null.
			return async () => null;
		},
	});
}

export const redis = LOCAL_DEV
	? (makeLocalRedis() as unknown as Redis)
	: new Redis({
			url: process.env.UPSTASH_REDIS_REST_URL,
			token: process.env.UPSTASH_REDIS_REST_TOKEN,
		});
