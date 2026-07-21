import { BreadcrumbSetter } from "@/components/breadcrumbs";

export default function SiteDocsLayout({
	children,
}: {
	children: React.ReactNode;
}) {
	return (
		<>
			<BreadcrumbSetter
				crumbs={[
					{
						label: "Site Docs",
						segments: ["documents"],
					},
				]}
			/>
			{children}
		</>
	);
}
