const {
	app,
	BrowserWindow,
	Menu,
	shell,
	dialog,
	nativeTheme,
} = require("electron");
const path = require("node:path");
const http = require("node:http");

// Handle Windows squirrel install/uninstall shortcuts.
if (require("electron-squirrel-startup")) {
	app.quit();
}

const DASHBOARD_URL =
	process.env.NEXUS_DESKTOP_URL || "http://localhost:5179";
const START_PATH = process.env.NEXUS_DESKTOP_PATH || "/team/local-dev";

/** @type {BrowserWindow | null} */
let mainWindow = null;

function offlineHtml(url) {
	return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Nexus — waiting for dashboard</title>
  <style>
    :root { color-scheme: dark; }
    body {
      margin: 0; min-height: 100vh; display: grid; place-items: center;
      font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
      background: #1c1c1b; color: #e8e6e3;
    }
    .card {
      max-width: 420px; padding: 28px 28px 24px; border-radius: 14px;
      border: 1px solid rgba(255,255,255,.08); background: #1f1f1e;
      box-shadow: 0 20px 50px rgba(0,0,0,.35);
    }
    h1 { margin: 0 0 8px; font-size: 18px; letter-spacing: -0.02em; }
    p { margin: 0 0 14px; font-size: 13px; line-height: 1.5; color: #8a8780; }
    code {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 11.5px; color: #26b5ce;
    }
    button {
      appearance: none; border: 0; border-radius: 8px; padding: 9px 14px;
      background: #26b5ce; color: #0b1c20; font-weight: 600; font-size: 13px;
      cursor: pointer;
    }
    button:hover { filter: brightness(1.05); }
  </style>
</head>
<body>
  <div class="card">
    <h1>Nexus dashboard isn’t running</h1>
    <p>Start the local stack, then retry. Expected URL:</p>
    <p><code>${url}</code></p>
    <p>Typical: Docker compose for API + dashboard on port 5179.</p>
    <button id="retry">Retry</button>
  </div>
  <script>
    document.getElementById('retry').onclick = () => location.reload();
    setTimeout(() => location.reload(), 4000);
  </script>
</body>
</html>`;
}

function probeDashboard(url) {
	return new Promise((resolve) => {
		try {
			const req = http.get(url, { timeout: 2000 }, (res) => {
				res.resume();
				resolve(res.statusCode !== undefined && res.statusCode < 500);
			});
			req.on("error", () => resolve(false));
			req.on("timeout", () => {
				req.destroy();
				resolve(false);
			});
		} catch {
			resolve(false);
		}
	});
}

function createWindow() {
	const iconPath = path.join(__dirname, "..", "assets", "icon.png");

	mainWindow = new BrowserWindow({
		width: 1440,
		height: 900,
		minWidth: 1024,
		minHeight: 680,
		title: "Nexus",
		backgroundColor: nativeTheme.shouldUseDarkColors ? "#1c1c1b" : "#f6f7f9",
		show: false,
		icon: iconPath,
		webPreferences: {
			preload: path.join(__dirname, "preload.js"),
			contextIsolation: true,
			nodeIntegration: false,
			sandbox: true,
			spellcheck: true,
		},
	});

	const target = new URL(START_PATH, DASHBOARD_URL).toString();

	mainWindow.once("ready-to-show", () => {
		mainWindow?.show();
	});

	mainWindow.webContents.setWindowOpenHandler(({ url }) => {
		shell.openExternal(url);
		return { action: "deny" };
	});

	mainWindow.webContents.on("will-navigate", (event, url) => {
		try {
			const dest = new URL(url);
			const allowed = new URL(DASHBOARD_URL);
			if (dest.origin !== allowed.origin) {
				event.preventDefault();
				shell.openExternal(url);
			}
		} catch {
			event.preventDefault();
		}
	});

	(async () => {
		const ok = await probeDashboard(DASHBOARD_URL);
		if (!mainWindow) return;
		if (ok) {
			await mainWindow.loadURL(target);
		} else {
			await mainWindow.loadURL(
				`data:text/html;charset=utf-8,${encodeURIComponent(offlineHtml(DASHBOARD_URL))}`,
			);
		}
	})();

	mainWindow.on("closed", () => {
		mainWindow = null;
	});
}

function buildMenu() {
	const isMac = process.platform === "darwin";
	/** @type {Electron.MenuItemConstructorOptions[]} */
	const template = [
		...(isMac
			? [
					{
						label: app.name,
						submenu: [
							{ role: "about" },
							{ type: "separator" },
							{ role: "services" },
							{ type: "separator" },
							{ role: "hide" },
							{ role: "hideOthers" },
							{ role: "unhide" },
							{ type: "separator" },
							{ role: "quit" },
						],
					},
				]
			: []),
		{
			label: "File",
			submenu: [
				{
					label: "Reload Dashboard",
					accelerator: "CmdOrCtrl+R",
					click: () => {
						const win = BrowserWindow.getFocusedWindow() || mainWindow;
						win?.loadURL(new URL(START_PATH, DASHBOARD_URL).toString());
					},
				},
				{
					label: "Open in Browser",
					click: () => shell.openExternal(DASHBOARD_URL),
				},
				{ type: "separator" },
				isMac ? { role: "close" } : { role: "quit" },
			],
		},
		{ role: "editMenu" },
		{ role: "viewMenu" },
		{
			label: "Go",
			submenu: [
				{
					label: "Home",
					accelerator: "CmdOrCtrl+1",
					click: () =>
						mainWindow?.loadURL(
							new URL("/team/local-dev", DASHBOARD_URL).toString(),
						),
				},
				{
					label: "Site Docs",
					accelerator: "CmdOrCtrl+2",
					click: () =>
						mainWindow?.loadURL(
							new URL("/team/local-dev/documents", DASHBOARD_URL).toString(),
						),
				},
				{
					label: "Agent Config",
					accelerator: "CmdOrCtrl+3",
					click: () =>
						mainWindow?.loadURL(
							new URL("/team/local-dev/agent-config", DASHBOARD_URL).toString(),
						),
				},
				{
					label: "Chat",
					accelerator: "CmdOrCtrl+4",
					click: () =>
						mainWindow?.loadURL(
							new URL("/team/local-dev/chat", DASHBOARD_URL).toString(),
						),
				},
			],
		},
		{
			role: "windowMenu",
		},
		{
			role: "help",
			submenu: [
				{
					label: "Nexus Dashboard URL",
					click: () => {
						dialog.showMessageBox({
							type: "info",
							title: "Nexus",
							message: "Desktop shell target",
							detail: DASHBOARD_URL,
						});
					},
				},
			],
		},
	];

	Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

app.setName("Nexus");

app.whenReady().then(() => {
	buildMenu();
	createWindow();

	app.on("activate", () => {
		if (BrowserWindow.getAllWindows().length === 0) {
			createWindow();
		}
	});
});

app.on("window-all-closed", () => {
	if (process.platform !== "darwin") {
		app.quit();
	}
});
