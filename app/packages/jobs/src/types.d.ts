declare module "xlsx/dist/cpexcel.full.mjs" {
	export const version: string;
	export const cptable: Record<
		number,
		{ d: string; enc: Record<string, number> }
	>;
}
