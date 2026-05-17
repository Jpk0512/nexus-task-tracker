"use client";

import {
	closestCenter,
	DndContext,
	type DragEndEvent,
	KeyboardSensor,
	PointerSensor,
	useSensor,
	useSensors,
} from "@dnd-kit/core";
import {
	SortableContext,
	sortableKeyboardCoordinates,
	useSortable,
	verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Button } from "@ui/components/ui/button";
import { Checkbox } from "@ui/components/ui/checkbox";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@ui/components/ui/dialog";
import { cn } from "@ui/lib/utils";
import { GripVerticalIcon, RotateCcwIcon } from "lucide-react";
import {
	DEFAULT_HOME_CONFIG,
	HOME_CARD_DESCRIPTIONS,
	HOME_CARD_LABELS,
	type HomeCardId,
	type HomeConfig,
} from "./home-config";

/**
 * Dashboard configurator modal — toggle visibility + drag-reorder Home cards.
 *
 * Caller owns the config state; this component is presentational. On
 * change it dispatches the new HomeConfig synchronously so the parent can
 * persist to localStorage and re-render the home shell without a round
 * trip.
 *
 * Keyboard support comes free with @dnd-kit's sortable keyboard sensor:
 * Tab to focus a row, Space to "pick up", arrow keys to move, Space to
 * drop. Matches the existing zen-mode queue UX.
 */
export const DashboardConfigModal = ({
	open,
	onOpenChange,
	config,
	onChange,
	onReset,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	config: HomeConfig;
	onChange: (next: HomeConfig) => void;
	onReset: () => void;
}) => {
	const sensors = useSensors(
		useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
		useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
	);

	const handleDragEnd = ({ active, over }: DragEndEvent) => {
		if (!over || active.id === over.id) return;
		const oldIndex = config.cards.findIndex((c) => c.id === active.id);
		const newIndex = config.cards.findIndex((c) => c.id === over.id);
		if (oldIndex === -1 || newIndex === -1) return;
		const next = [...config.cards];
		const [moved] = next.splice(oldIndex, 1);
		next.splice(newIndex, 0, moved);
		onChange({ cards: next });
	};

	const toggle = (id: HomeCardId, enabled: boolean) => {
		onChange({
			cards: config.cards.map((c) =>
				c.id === id ? { ...c, enabled } : c,
			),
		});
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-w-md p-0">
				<DialogHeader className="border-border border-b px-4 py-3">
					<DialogTitle className="font-[510] text-[14px] tracking-[-0.005em]">
						Customize your home
					</DialogTitle>
					<DialogDescription className="text-[12px] text-muted-foreground">
						Toggle visibility and drag to reorder. Layout saves to this
						browser; share via the URL param.
					</DialogDescription>
				</DialogHeader>
				<div className="max-h-[60vh] overflow-y-auto px-2 py-2">
					<DndContext
						sensors={sensors}
						collisionDetection={closestCenter}
						onDragEnd={handleDragEnd}
					>
						<SortableContext
							items={config.cards.map((c) => c.id)}
							strategy={verticalListSortingStrategy}
						>
							<ul className="space-y-1">
								{config.cards.map((card) => (
									<ConfigRow
										key={card.id}
										card={card}
										onToggle={(v) => toggle(card.id, v)}
									/>
								))}
							</ul>
						</SortableContext>
					</DndContext>
				</div>
				<footer className="flex items-center justify-between gap-2 border-border border-t px-4 py-3">
					<Button
						variant="ghost"
						size="sm"
						onClick={() => {
							onReset();
							onChange(DEFAULT_HOME_CONFIG);
						}}
						className="gap-1.5 text-[12px]"
					>
						<RotateCcwIcon className="size-3.5" />
						Reset to defaults
					</Button>
					<Button size="sm" onClick={() => onOpenChange(false)}>
						Done
					</Button>
				</footer>
			</DialogContent>
		</Dialog>
	);
};

function ConfigRow({
	card,
	onToggle,
}: {
	card: { id: HomeCardId; enabled: boolean };
	onToggle: (next: boolean) => void;
}) {
	const {
		attributes,
		listeners,
		setNodeRef,
		transform,
		transition,
		isDragging,
	} = useSortable({ id: card.id });

	const style = {
		transform: CSS.Transform.toString(transform),
		transition,
	};

	return (
		<li
			ref={setNodeRef}
			style={style}
			className={cn(
				"flex items-center gap-2 rounded-md border border-transparent bg-background px-2 py-2 transition-colors",
				"hover:border-border",
				isDragging && "border-border bg-accent/40 shadow",
			)}
		>
			<button
				type="button"
				className="cursor-grab text-muted-foreground transition-colors hover:text-foreground active:cursor-grabbing"
				aria-label="Drag to reorder"
				{...attributes}
				{...listeners}
			>
				<GripVerticalIcon className="size-4" />
			</button>
			<Checkbox
				id={`home-card-${card.id}`}
				checked={card.enabled}
				onCheckedChange={(v) => onToggle(v === true)}
			/>
			<label
				htmlFor={`home-card-${card.id}`}
				className="flex min-w-0 flex-1 cursor-pointer flex-col"
			>
				<span className="font-[510] text-[13px] text-foreground">
					{HOME_CARD_LABELS[card.id]}
				</span>
				<span className="text-[11px] text-muted-foreground">
					{HOME_CARD_DESCRIPTIONS[card.id]}
				</span>
			</label>
		</li>
	);
}
