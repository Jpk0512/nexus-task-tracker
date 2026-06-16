import { embed } from "ai";
import { stripHtml } from "string-strip-html";
import { EMBEDDING_MODEL, EMBEDDING_OPTIONS } from "../constants";

const LOCAL_DEV = process.env.NEXUS_LOCAL_DEV === "1";

export const generateTaskEmbedding = async ({
	title,
	description,
}: {
	title: string;
	description?: string | null;
}) => {
	const cleanTitle = stripHtml(title).result;
	const cleanDescription = description
		? stripHtml(description).result
		: undefined;
	const value = [cleanTitle, cleanDescription].filter(Boolean).join("\n");

	if (LOCAL_DEV) {
		console.log("[stub:ai] embed task");
		return {
			embedding: Array(768).fill(0) as number[],
			model: EMBEDDING_MODEL,
		};
	}

	const embedding = await embed({
		model: EMBEDDING_MODEL,
		value,
		providerOptions: EMBEDDING_OPTIONS,
	});

	return {
		embedding: embedding.embedding,
		model: EMBEDDING_MODEL,
	};
};
