import type { MetadataRoute } from "next";

export default function sitemap(): MetadataRoute.Sitemap {
	const baseUrl = process.env.NEXT_PUBLIC_SITE_URL || "https://mimrai.com";
	const currentDate = new Date();

	return [
		{
			url: baseUrl,
			lastModified: currentDate,
			changeFrequency: "weekly",
			priority: 1,
		},
		{
			url: `${baseUrl}/terms`,
			lastModified: currentDate,
			changeFrequency: "monthly",
			priority: 0.5,
		},
		{
			url: `${baseUrl}/policy`,
			lastModified: currentDate,
			changeFrequency: "monthly",
			priority: 0.5,
		},
	];
}
