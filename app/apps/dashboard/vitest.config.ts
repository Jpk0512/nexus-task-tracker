import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Vite 8 transforms with oxc (rolldown), not esbuild. The project tsconfig sets
// jsx:"preserve" (via @nexus-app/tsconfig/nextjs.json); under that setting Vite's
// import-analysis step runs es-module-lexer over raw, unconverted JSX and throws
// ("content contains invalid JS syntax … do not set jsx to preserve"). Setting
// oxc.jsx.runtime="automatic" overrides the inherited jsx:"preserve" so .tsx
// modules are converted to plain JS before the lexer sees them — this is what
// makes dashboard components actually render under the runner.
export default defineConfig({
	oxc: {
		jsx: {
			runtime: "automatic",
			importSource: "react",
		},
	},
	resolve: {
		alias: {
			"@": path.resolve(__dirname, "./src"),
			"@ui": path.resolve(__dirname, "../../packages/ui/src"),
			"@nexus-app/jobs/init": path.resolve(
				__dirname,
				"../../packages/jobs/src/init.ts",
			),
			"@nexus-app/jobs": path.resolve(
				__dirname,
				"../../packages/jobs/src/index.ts",
			),
		},
	},
	test: {
		environment: "jsdom",
		setupFiles: [path.resolve(__dirname, "./vitest.setup.ts")],
	},
});
