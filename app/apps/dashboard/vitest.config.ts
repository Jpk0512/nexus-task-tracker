import path from "node:path";

export default {
	resolve: {
		alias: {
			"@mimir/jobs/init": path.resolve(
				__dirname,
				"../../packages/jobs/src/init.ts",
			),
			"@mimir/jobs": path.resolve(
				__dirname,
				"../../packages/jobs/src/index.ts",
			),
		},
	},
	test: {
		environment: "node",
	},
};
