import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default {
	// esbuild.jsx:"automatic" converts to oxc jsx runtime:"automatic" via
	// convertEsbuildConfigToOxcConfig, overriding tsconfig jsx:"preserve".
	// Required so .tsx component files imported by tests are parseable by
	// Vite's import analysis plugin (es-module-lexer fails on JSX with preserve).
	esbuild: {
		jsx: "automatic",
		jsxImportSource: "react",
	},
	resolve: {
		alias: {
			"@": path.resolve(__dirname, "./src"),
			"@ui": path.resolve(__dirname, "../../packages/ui/src"),
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
