import type { NextConfig } from "next";

const nextConfig: NextConfig = {
	devIndicators: false,
	poweredByHeader: false,
	reactStrictMode: true,
	typescript: {
		ignoreBuildErrors: true,
	},
	transpilePackages: [
		"@nexus-app/integration",
		"@nexus-app/api",
		"@nexus-app/ui",
	],
	images: {
		remotePatterns: [
			{
				protocol: "https",
				hostname: "**",
			},
			{
				protocol: "http",
				hostname: "(localhost|127.0.0.1)",
			},
		],
	},
};

export default nextConfig;
