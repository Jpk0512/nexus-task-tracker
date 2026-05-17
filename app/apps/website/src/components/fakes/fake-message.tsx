import { motion } from "motion/react";
import { cn } from "@/lib/utils";
import { DEFAULT_CONSTRAINT } from "./constraints";

export const FakeMessage = ({
	className,
	variant,
	body,
}: {
	className?: string;
	variant?: "user" | "assistant";
	body: string;
}) => {
	return (
		<motion.div
			drag={false}
			dragConstraints={{
				top: -DEFAULT_CONSTRAINT,
				left: -DEFAULT_CONSTRAINT,
				right: DEFAULT_CONSTRAINT,
				bottom: DEFAULT_CONSTRAINT,
			}}
			dragTransition={{ bounceStiffness: 500, bounceDamping: 15 }}
			dragElastic={0.2}
			whileDrag={{ cursor: "grabbing", scale: 1.05 }}
			className={cn(
				"pointer-events-auto max-w-sm border bg-card p-4 text-sm",
				className,
				{
					"text-right": variant === "user",
					"border-dashed text-left": variant === "assistant",
				},
			)}
		>
			{body}
		</motion.div>
	);
};
