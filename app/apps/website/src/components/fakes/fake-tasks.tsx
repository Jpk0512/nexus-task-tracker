import { randomColor } from "@mimir/utils/random";
import { motion } from "motion/react";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { DEFAULT_CONSTRAINT } from "./constraints";

export const FakeTask = ({
	className,
	constraintsRef,
	title,
	...props
}: {
	className?: string;
	constraintsRef: React.RefObject<HTMLDivElement | null>;
	sequence?: number;
	assigneeColor?: string;
	assigneeInitials?: string;
	labels?: ("Bug" | "Feature")[];
	title: string;
}) => {
	const [loaded, setLoaded] = useState(false);

	const {
		sequence = Math.floor(Math.random() * 100),
		assigneeColor = randomColor(),
		assigneeInitials = ["A", "B", "C", "D", "E", "F"][
			Math.floor(Math.random() * 6)
		],
		labels,
	} = props;

	useEffect(() => {
		setLoaded(true);
	}, []);

	if (!loaded) return null;

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
				"pointer-events-auto flex min-h-14 w-82 flex-col rounded-none border bg-card p-3",
				className,
			)}
		>
			<div className="flex h-full grow-1 flex-col justify-between gap-2">
				<div className="flex items-center justify-between gap-2">
					<span className="font-medium text-sm">
						<span className="mr-2 text-muted-foreground">{sequence}</span>
						{title}
					</span>
					<div>
						<button
							type="button"
							aria-haspopup="dialog"
							aria-expanded="false"
							aria-controls="radix-_r_5g_"
							data-state="closed"
							data-slot="popover-trigger"
						>
							<div className="flex items-center">
								<div className="flex flex-row-reverse items-center">
									<div className="">
										<span
											data-slot="tooltip-trigger"
											className="relative flex size-6 shrink-0 overflow-hidden rounded-full"
											data-state="closed"
										>
											<span
												data-slot="avatar-fallback"
												className="flex size-full items-center justify-center rounded-full bg-primary text-primary-foreground"
												style={{
													backgroundColor: assigneeColor,
													color: "white",
												}}
											>
												{assigneeInitials}
											</span>
										</span>
									</div>
								</div>
							</div>
						</button>
					</div>
				</div>
				<div className="mt-2 flex flex-wrap items-center gap-2">
					<div className="flex flex-wrap gap-1">
						{labels?.map((label) => {
							switch (label) {
								case "Bug":
									return (
										<span
											key={label}
											className="flex w-fit shrink-0 items-center justify-start gap-1 overflow-hidden whitespace-nowrap rounded-xs border border-transparent bg-secondary px-2 py-0.5 text-start font-medium text-secondary-foreground text-xs transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&>svg]:pointer-events-none [&>svg]:size-3"
										>
											<div
												className="size-3 rounded-full"
												style={{
													backgroundColor: "rgb(82, 49, 48)",
													color: "white",
												}}
											/>
											Bug
										</span>
									);
								case "Feature":
									return (
										<span
											key={label}
											className="flex w-fit shrink-0 items-center justify-start gap-1 overflow-hidden whitespace-nowrap rounded-xs border border-transparent bg-secondary px-2 py-0.5 text-start font-medium text-secondary-foreground text-xs transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&>svg]:pointer-events-none [&>svg]:size-3"
										>
											<div
												className="size-3 rounded-full"
												style={{
													backgroundColor: "rgb(30, 64, 175)",
													color: "white",
												}}
											/>
											Feature
										</span>
									);
								default:
									return null;
							}
						})}
					</div>
				</div>
			</div>
		</motion.div>
	);
};
