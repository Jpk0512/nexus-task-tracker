import { Resend } from "resend";

const LOCAL_DEV = process.env.NEXUS_LOCAL_DEV === "1";

function recursiveResendStub(label: string): any {
	return new Proxy(() => {}, {
		get: (_t, prop) => {
			if (prop === "then") return undefined; // not a thenable
			return recursiveResendStub(`${label}.${String(prop)}`);
		},
		apply: (_t, _thisArg, args: any[]) => {
			const first = args?.[0];
			const subject =
				first && typeof first === "object" && "subject" in first
					? (first as { subject?: string }).subject
					: undefined;
			if (label.endsWith("emails.send")) {
				console.log("[stub:resend] emails.send", subject ?? "");
			} else {
				console.log(`[stub:${label}]`);
			}
			return Promise.resolve({ data: { id: "stub-local-dev" }, error: null });
		},
	});
}

export const resend = LOCAL_DEV
	? (recursiveResendStub("resend") as Resend)
	: new Resend(process.env.RESEND_API_KEY!);
