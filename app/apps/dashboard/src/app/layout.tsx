import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import Providers from "@/components/providers";
import "../../index.css";

import { Toaster } from "@ui/components/ui/sonner";
import Head from "next/head";
import { NuqsAdapter } from "nuqs/adapters/next/app";
import { getSession } from "@/lib/get-session";

// Inter Variable carries the entire UI; cv01 + ss03 features are enabled
// globally in index.css. Loading axis 100–900 so weight 510 is reachable.
const inter = Inter({
	variable: "--font-inter",
	subsets: ["latin"],
	axes: ["opsz"],
	weight: "variable",
	display: "swap",
});

const mono = JetBrains_Mono({
	variable: "--font-mono-display",
	subsets: ["latin"],
	display: "swap",
});

export const metadata: Metadata = {
	title: "Mimrai - App",
	description: "Mimrai - Your AI Task Management Assistant",
};

export default async function RootLayout({
	children,
}: Readonly<{
	children: React.ReactNode;
}>) {
	const session = await getSession();

	return (
		<html lang="en" className="dark" suppressHydrationWarning>
			<Head>
				<meta name="apple-mobile-web-app-title" content="Mimrai" />
			</Head>
			<body
				className={`${inter.variable} ${mono.variable} flex min-h-screen flex-col bg-background antialiased`}
			>
				<NuqsAdapter>
					<Providers session={session}>{children}</Providers>
					{/*
					  Linear-style toast configuration: top-right, compact, no big
					  drop-shadow, slim border. Sonner defaults to bottom-right with
					  a heavy shadow which fights Mimrai's panel system and looks
					  un-Linear. `closeButton` shows a small X on hover (Linear parity).
					*/}
					<Toaster
						position="top-right"
						offset={16}
						gap={6}
						closeButton
						toastOptions={{
							classNames: {
								toast:
									"!shadow-sm !border-border !bg-popover !text-popover-foreground !rounded-md !px-3 !py-2 !text-xs",
								title: "text-xs!",
								description: "text-xs! !text-muted-foreground",
								actionButton: "!text-xs",
								cancelButton: "!text-xs",
								closeButton:
									"!bg-transparent !border-none !text-muted-foreground hover:!text-foreground",
							},
						}}
					/>
				</NuqsAdapter>
			</body>
		</html>
	);
}
