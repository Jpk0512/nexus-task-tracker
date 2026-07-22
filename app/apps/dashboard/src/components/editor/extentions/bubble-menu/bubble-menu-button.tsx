"use client";

interface BubbleMenuButtonProps {
	action: () => void;
	isActive: boolean;
	children: React.ReactNode;
	className?: string;
	"aria-label"?: string;
}

export function BubbleMenuButton({
	action,
	isActive,
	children,
	className,
	"aria-label": ariaLabel,
}: BubbleMenuButtonProps) {
	return (
		<button
			type="button"
			onClick={action}
			aria-label={ariaLabel}
			className={`px-2.5 py-1.5 text-[11px] transition-colors ${className} ${
				isActive
					? "bg-white text-primary dark:bg-stone-900"
					: "bg-transparent hover:bg-muted"
			}`}
		>
			{children}
		</button>
	);
}
