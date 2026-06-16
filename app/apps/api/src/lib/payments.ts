import { Stripe } from "stripe";

const LOCAL_DEV =
	process.env.NEXUS_LOCAL_DEV === "1" ||
	process.env.DISABLE_BILLING === "true";

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
