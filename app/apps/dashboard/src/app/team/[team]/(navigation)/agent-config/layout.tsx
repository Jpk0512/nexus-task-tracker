import { BreadcrumbSetter } from "@/components/breadcrumbs";

export default function AgentConfigLayout({
	children,
}: {
	children: React.ReactNode;
}) {
	return (
		<>
			<BreadcrumbSetter
				crumbs={[
					{
						label: "Agent Config",
						segments: ["agent-config"],
					},
				]}
			/>
			{children}
		</>
	);
}
