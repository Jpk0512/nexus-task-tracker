# Production image for Nexus dashboard. Mirrors apps/api/Dockerfile: oven/bun 1.3.5 pinned, turbo prune, plain bun install, next build, next start. Build AND runtime both use the locked next@16.1.6.
FROM oven/bun:1.3.5 AS base

FROM base AS turbo-cli
RUN bun add -g turbo

FROM turbo-cli AS builder
WORKDIR /app
COPY . .
RUN turbo prune @nexus-app/dashboard --docker

FROM base AS installer
WORKDIR /app
COPY --from=builder /app/out/json/ .
RUN bun install
COPY --from=builder /app/out/full/ .
# Bun workspace install hoists bins only for the root workspace; symlink the locked
# next package + its binary (hash-agnostic 16.1.6 glob) so BOTH the build stage and
# the runtime resolve next@16.1.6 (never a bun x npm-fetched newer version).
RUN NEXT_PKG="$(ls -d /app/node_modules/.bun/next@16.1.6*/node_modules/next | head -1)" \
    && ln -sf "$NEXT_PKG" /app/node_modules/next \
    && ln -sf /app/node_modules/next/dist/bin/next /app/node_modules/.bin/next

FROM installer AS build
WORKDIR /app/apps/dashboard
ENV NODE_ENV=production
RUN bun run build

FROM base AS runner
WORKDIR /app
COPY --from=installer /app/node_modules ./node_modules
COPY --from=builder /app/out/full/ .
COPY --from=build /app/apps/dashboard/.next ./apps/dashboard/.next
COPY --from=build /app/apps/dashboard/public ./apps/dashboard/public
WORKDIR /app/apps/dashboard
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
EXPOSE 3000
CMD ["bun", "run", "start"]
