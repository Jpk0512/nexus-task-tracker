import { Stripe } from "stripe";

const LOCAL_DEV =
	process.env.MIMRAI_LOCAL_DEV === "1" ||
	process.env.DISABLE_BILLING === "true";

// Stripe responses come in roughly four shapes that Nexus's callers reach for:
//   - list endpoints: { object: "list", data: [], has_more: false, url }
//   - single objects: { id, ... domain fields ... }
//   - checkout/portal sessions: { id, url, ... }
//   - webhook constructors: synchronous return
// Return an object that satisfies the union — extra fields are harmless to the
// callers that only read what they need.
const STRIPE_STUB_RESPONSE = Object.freeze({
	object: "list",
	data: [] as unknown[],
	has_more: false,
	url: "https://example.local/stub",
	id: "stub-stripe-id",
});

function recursiveStripeStub(label: string): any {
	return new Proxy(() => {}, {
		get: (_t, prop) => {
			if (prop === "then") return undefined; // not a thenable
			return recursiveStripeStub(`${label}.${String(prop)}`);
		},
		apply: () => {
			console.log(`[stub:stripe] ${label}`);
			return Promise.resolve({ ...STRIPE_STUB_RESPONSE });
		},
	});
}

export const stripeClient = LOCAL_DEV
	? (recursiveStripeStub("stripe") as Stripe)
	: new Stripe(process.env.STRIPE_SECRET_KEY!);
