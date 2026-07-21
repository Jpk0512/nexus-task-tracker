const path = require("node:path");

module.exports = {
	packagerConfig: {
		name: "Nexus",
		executableName: "Nexus",
		asar: true,
		appBundleId: "local.nexus.desktop",
		appCategoryType: "public.app-category.developer-tools",
		icon: path.resolve(__dirname, "assets", "icon"),
		// No osxSign here — signing needs Apple certs; unsigned local builds are fine.
	},
	rebuildConfig: {},
	makers: [
		{
			name: "@electron-forge/maker-zip",
			platforms: ["darwin"],
		},
		{
			name: "@electron-forge/maker-dmg",
			config: {
				name: "Nexus",
				format: "ULFO",
			},
		},
		{
			name: "@electron-forge/maker-squirrel",
			config: {
				name: "Nexus",
				executableName: "Nexus",
			},
		},
		{
			name: "@electron-forge/maker-deb",
			config: {
				options: {
					maintainer: "John Keeney",
					homepage: "https://github.com/Jpk0512/nexus-task-tracker",
				},
			},
		},
	],
	publishers: [
		{
			name: "@electron-forge/publisher-github",
			config: {
				repository: {
					owner: "Jpk0512",
					name: "nexus-task-tracker",
				},
				prerelease: true,
			},
		},
	],
};
