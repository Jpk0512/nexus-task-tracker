import type { Metadata } from "next";
import { Geist, Geist_Mono, Noto_Sans_Runic } from "next/font/google";
import Providers from "@/components/providers";
import "../../index.css";
import { Provider as OpenPanelProvider } from "@mimir/events/client";
import { Footer } from "@/components/footer";
import { Navbar } from "@/components/navbar";

const geistSans = Geist({
	variable: "--font-geist-sans",
	subsets: ["latin"],
});

const geistMono = Geist_Mono({
	variable: "--font-geist-mono",
	subsets: ["latin"],
});

const notoSansRunic = Noto_Sans_Runic({
	variable: "--font-noto-sans-runic",
	subsets: ["latin"],
	weight: ["400"],
});

export const metadata: Metadata = {
	metadataBase: new URL(
		process.env.NEXT_PUBLIC_SITE_URL || "https://mimrai.com",
	),
	title: {
		default: "Mimrai - Minimalist Task Management Tool",
		template: "%s | Mimrai",
	},
	description:
		"Open source minimalist task management tool to track your tasks and projects with ease. Built with AI-powered features.",
	keywords: [
		"task management",
		"project management",
		"productivity",
		"open source",
		"AI assistant",
		"minimalist",
	],
	authors: [{ name: "Mimrai" }],
	creator: "Mimrai",
	publisher: "Mimrai",
	robots: {
		index: true,
		follow: true,
		googleBot: {
			index: true,
			follow: true,
			"max-video-preview": -1,
			"max-image-preview": "large",
			"max-snippet": -1,
		},
	},
	openGraph: {
		type: "website",
		locale: "en_US",
		url: "/",
		title: "Mimrai - Minimalist Task Management Tool",
		description:
			"Open source minimalist task management tool to track your tasks and projects with ease.",
		siteName: "Mimrai",
	},
	twitter: {
		card: "summary_large_image",
		title: "Mimrai - Minimalist Task Management Tool",
		description:
			"Open source minimalist task management tool to track your tasks and projects with ease.",
		creator: "@mimrai",
	},
	alternates: {
		canonical: "/",
	},
};

export default function RootLayout({
	children,
}: Readonly<{
	children: React.ReactNode;
}>) {
	return (
		<html lang="en" suppressHydrationWarning>
			<body
				className={`${geistSans.variable} ${geistMono.variable} ${notoSansRunic.variable} flex min-h-screen flex-col antialiased`}
			>
				<OpenPanelProvider />
				<Providers>
					<Navbar />
					<main>{children}</main>
					<Footer />
				</Providers>
			</body>
		</html>
	);
}
