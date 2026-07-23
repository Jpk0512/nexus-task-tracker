import type { Editor as EditorInstance } from "@tiptap/react";
import { FormControl, FormField, FormItem } from "@ui/components/ui/form";
import { useState } from "react";
import { useFormContext } from "react-hook-form";
import { Editor } from "@/components/editor";
import { AiDescriptionSuggestion } from "./ai-description-suggestion";

export const Description = ({
	editorRef,
}: {
	editorRef: React.Ref<EditorInstance>;
}) => {
	const form = useFormContext();
	const id = form.watch("id");
	const title = form.watch("title");
	const projectId = form.watch("projectId");
	// Tiptap's `useEditor` only reads `value` as the *initial* `content` (it's
	// mounted with a frozen `[]` deps array) — it never re-syncs later prop
	// changes. Bumping this key on Accept forces a clean remount so the
	// applied suggestion actually appears in the editor.
	const [editorGeneration, setEditorGeneration] = useState(0);

	return (
		<FormField
			control={form.control}
			name="description"
			render={({ field }) => (
				<FormItem>
					{id && (
						<AiDescriptionSuggestion
							title={title}
							description={field.value}
							projectId={projectId}
							onAccept={(suggestion) => {
								field.onChange(suggestion);
								setEditorGeneration((generation) => generation + 1);
							}}
						/>
					)}
					<FormControl>
						<Editor
							key={editorGeneration}
							className="editor-xl [&_.tiptap]:min-h-[100px]"
							placeholder="Add description..."
							taskId={id}
							value={field.value ?? ""}
							onChange={(value) => {
								field.onChange(value);
							}}
							autoFocus
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
