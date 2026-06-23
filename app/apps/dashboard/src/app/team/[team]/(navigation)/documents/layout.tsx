import { BreadcrumbSetter } from "@/components/breadcrumbs";

export default function DocumentsLayout({
	children,
}: {
	children: React.ReactNode;
}) {
	return (
		<>
			<BreadcrumbSetter
				crumbs={[
					{
						label: "Documents",
						segments: ["documents"],
					},
				]}
			/>
			{children}
		</>
	);
}
