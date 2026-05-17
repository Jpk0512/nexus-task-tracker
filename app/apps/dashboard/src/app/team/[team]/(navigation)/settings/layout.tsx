import { SettingsSidebar } from "./settings-sidebar";

export default async function Layout({
	children,
}: {
	children: React.ReactNode;
}) {
	return (
		<div className="mx-auto grid w-full max-w-5xl grid-cols-[220px_minmax(0,1fr)] gap-8 px-6 pt-4">
			<SettingsSidebar />
			<div className="min-w-0">{children}</div>
		</div>
	);
}
