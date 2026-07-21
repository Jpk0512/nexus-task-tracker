const { contextBridge } = require("electron");

/**
 * Tag the document so the web app can detect the desktop shell and apply
 * native-feeling polish (e.g. hiding scrollbars app-wide). contextIsolation
 * isolates the JS object graph, not the DOM, so this is safe.
 */
function tagDesktop() {
	try {
		document.documentElement.classList.add("nexus-desktop");
	} catch {
		/* ignore */
	}
}
tagDesktop();
if (document.readyState === "loading") {
	document.addEventListener("DOMContentLoaded", tagDesktop);
}

/**
 * Minimal bridge for the Nexus web app.
 * Keep this surface tiny — prefer ordinary web APIs in the dashboard.
 */
contextBridge.exposeInMainWorld("nexusDesktop", {
	isDesktop: true,
	platform: process.platform,
	version: "0.1.0",
});
