module.exports = {
	packagerConfig: {
		name: "nexus",
		asar: true,
		osxSign: {},
		appCategoryType: "public.app-category.developer-tools",
	},
	makers: [
		{
			name: "@electron-forge/maker-squirrel",
			config: {
				executableName: "nexus",
			},
		},
		{
			name: "@electron-forge/maker-zip",
			platforms: ["darwin"],
		},
		{
			name: "@electron-forge/maker-deb",
			config: {
				executableName: "nexus",
			},
		},
		{
			name: "@electron-forge/maker-rpm",
			config: {
				executableName: "nexus",
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
