export function getEmailFrom() {
	return "Nexus <nexus@localhost>";
}

export function getAppUrl() {
	if (process.env.VERCEL_ENV === "preview") {
		return `https://${process.env.VERCEL_URL}`;
	}

	return "http://localhost:3000";
}

export function getEmailUrl() {
	return "http://localhost:3000";
}

export function getWebsiteUrl() {
	if (process.env.VERCEL_ENV === "preview") {
		return `https://${process.env.VERCEL_URL}`;
	}

	return "http://localhost:3001";
}

export function getCdnUrl() {
	return "http://localhost:3000";
}

export function getApiUrl() {
	if (process.env.NEXT_PUBLIC_SERVER_URL) {
		return process.env.NEXT_PUBLIC_SERVER_URL;
	}

	return "http://localhost:3003";
}
