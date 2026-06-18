import { FocusSessionLoader } from "@/components/focus/focus-session-loader";

export default function ProjectsLayout({
	children,
}: {
	children: React.ReactNode;
}) {
	return (
		<>
			{children}
			<FocusSessionLoader />
		</>
	);
}
