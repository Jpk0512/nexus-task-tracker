import { memo } from "react";
import type { EnrichedTask } from "@/hooks/use-data";
import { PropertiesComponents } from "./task-properties-components";

export type Task = EnrichedTask;
export const propertiesList = Object.keys(
	PropertiesComponents,
) as Array<PropertyKey>;

export type PropertyKey = keyof typeof PropertiesComponents;

/**
 * Renders a single task property.
 * No context subscription - visibility check happens at parent level for performance.
 */
export const TaskProperty = memo(function TaskProperty({
	property,
	task,
}: {
	property: PropertyKey;
	task: Task;
}) {
	const Component = PropertiesComponents[property];
	return <Component task={task} />;
});

/**
 * Renders all task properties for a given task.
 * visibleProperties should be memoized at the parent level.
 */
export const TaskProperties = memo(function TaskProperties({
	task,
	visibleProperties,
}: {
	task: Task;
	visibleProperties: PropertyKey[];
}) {
	return (
		<>
			{visibleProperties.map((property) => (
				<TaskProperty key={property} property={property} task={task} />
			))}
		</>
	);
});
