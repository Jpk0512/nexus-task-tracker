import type { Editor as EditorInstance } from "@tiptap/react";
import { FormControl, FormField, FormItem } from "@ui/components/ui/form";
import { useFormContext } from "react-hook-form";
import { Editor } from "@/components/editor";

export const Description = ({
	editorRef,
	variant = "compact",
}: {
	editorRef?: React.Ref<EditorInstance>;
	/** "compact" (default) = tight summary field; "page" = block-stacked page editor (Notion / Linear). */
	variant?: "compact" | "page";
}) => {
	const form = useFormContext();
	const isPage = variant === "page";

	return (
		<FormField
			control={form.control}
			name="description"
			render={({ field }) => (
				<FormItem>
					<FormControl>
						<Editor
							className={
								isPage
									? "editor-xl [&_.tiptap]:min-h-[240px]"
									: "text-muted-foreground [&_.tiptap]:min-h-[40px]"
							}
							placeholder={
								// Tiptap's Placeholder extension renders this via
								// `content: attr(data-placeholder)` in CSS. Apostrophes /
								// quotes in this string get HTML-escaped and previously
								// rendered as `&#x27;` glyphs around the "/" — which made
								// the placeholder look like a stray "/" sitting on an
								// otherwise-empty line. Keep this ASCII-only.
								isPage
									? "Type  /  for blocks, or just start writing…"
									: "Add a short summary…"
							}
							value={field.value ?? ""}
							onChange={(value) => {
								field.onChange(value);
							}}
							autoFocus={!isPage}
							shouldInsertImage={true}
							onUpload={async (url) => {
								const currentValue = form.getValues("attachments") ?? [];
								form.setValue("attachments", [...currentValue, url], {
									shouldDirty: true,
									shouldValidate: true,
								});
							}}
							ref={editorRef}
						/>
					</FormControl>
				</FormItem>
			)}
		/>
	);
};
