export type DateRangeValue = [string, string];

export interface SelectOptionItem {
	label: string;
	value: string;
	icon?: React.ReactNode;
}

export interface DateOptionItem {
	label: string;
	value: string; // ISO string
}

export interface DateRangeOptionItem {
	label: string;
	value: DateRangeValue;
}

interface BaseFilterOption {
	label: string;
	icon: React.ReactNode;
	filterKey: string;
}

export interface SelectFilterOption extends BaseFilterOption {
	type: "select";
	multiple: boolean;
	// Accept any react-query-compatible options object; consumers narrow via select
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	queryOptions: Record<string, any> & { queryKey: unknown[] };
}

export interface DateFilterOption extends BaseFilterOption {
	type: "date";
	options: DateOptionItem[];
}

export interface DateRangeFilterOption extends BaseFilterOption {
	type: "date-range";
	options: DateRangeOptionItem[];
}

export type FilterOption =
	| SelectFilterOption
	| DateFilterOption
	| DateRangeFilterOption;

export type FilterOptions = Record<string, FilterOption>;
