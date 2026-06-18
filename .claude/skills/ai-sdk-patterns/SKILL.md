---
name: ai-sdk-patterns
description: "INTERNAL — invoke by explicit name only via `Skill ai-sdk-patterns`. Do NOT auto-load. Vercel AI SDK 4 streaming, tool use, model config, Azure routing, streamText/generateText patterns."
---

# AI SDK 4 Patterns (canonical for `app/lib/ai/**`, `app/api/**`)

## Import pattern

```ts
import { streamText, generateText, tool } from 'ai'
import { createAnthropic } from '@ai-sdk/anthropic'

const anthropic = createAnthropic({
  baseURL: process.env.AI_API_BASE_URL,
  apiKey: process.env.ANTHROPIC_API_KEY,
})
const model = anthropic('claude-sonnet-4-6')
```

Never import from `@anthropic-ai/sdk` directly. Always use `@ai-sdk/anthropic`.

## Streaming responses

```ts
const result = await streamText({ model, messages, tools })
return result.toDataStreamResponse()
```

For route handlers, return `result.toDataStreamResponse()` directly — don't wrap in `NextResponse`.

## Tool definitions

```ts
const myTool = tool({
  description: '...',
  parameters: z.object({ query: z.string() }),
  execute: async ({ query }) => { /* return serializable value */ },
})
```

Tools must be registered in `app/api/mcp/route.ts` if they're MCP-exposed.

## generateText (non-streaming)

Use for structured extraction or one-shot prompts where streaming adds no UX value:

```ts
const { text } = await generateText({ model, prompt })
```

## Error handling

- AI SDK throws `AISDKError` subtypes. Catch `AISDKError` at the route handler boundary.
- Don't catch and swallow — surface the error as a typed response.

## Token budget

- Don't set `maxTokens` in individual calls unless the use case requires a hard cap.
- Forge token budget is tripled (post-PR #13) — trust the default unless you have a reason.

## Forbidden

- Direct `@anthropic-ai/sdk` usage in `app/`.
- Hardcoded model strings other than `claude-sonnet-4-6` without a decision record.
- Streaming in Server Components (use route handlers for streaming).
