import { FrownIcon } from "lucide-react";

export const EmptyState = ({ children }: { children: React.ReactNode }) => {
	return (
		<div className="flex h-full flex-col items-center justify-center gap-2 text-center">
			{children}
		</div>
	);
};

export const EmptyStateIcon = ({
	children,
}: {
	children?: React.ReactNode;
}) => {
	return (
		<div className="mb-2 size-10 text-muted-foreground">
			{children ? children : <FrownIcon className="size-full" />}
		</div>
	);
};

export const EmptyStateTitle = ({
	children,
}: {
	children: React.ReactNode;
}) => {
	return (
		<h3 className="flex items-center gap-2 font-[510] text-[15px] text-foreground tracking-[-0.012em]">
			{children}
		</h3>
	);
};

export const EmptyStateDescription = ({
	children,
}: {
	children: React.ReactNode;
}) => {
	return (
		<p className="max-w-md text-balance text-[12px] text-muted-foreground">
			{children}
		</p>
	);
};

export const EmptyStateAction = ({
	children,
}: {
	children: React.ReactNode;
}) => {
	return <div className="mt-4">{children}</div>;
};
