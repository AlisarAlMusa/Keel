# Keel — Mock SIS Portal (D-P5-001)
# Single build stage: install all deps (including workspace-local @keel/ui),
# build the React SPA, prune to production deps, then copy into a lean runtime.

# ── Stage 1: full build + prune ─────────────────────────────────────────────
FROM node:22-alpine AS builder

WORKDIR /app

# 1. Copy workspace manifests for layer-cache on npm install
COPY package.json package.json
COPY ui/package.json ui/package.json
COPY widget/package.json widget/package.json
COPY admin/package.json admin/package.json
COPY portal/package.json portal/package.json

# 2. Install ALL deps (workspace links @keel/ui → ../ui)
RUN npm install --workspaces --include-workspace-root

# 3. Copy full source
COPY ui/ ui/
COPY portal/ portal/

# 4. Build the portal React SPA
WORKDIR /app/portal
RUN npm run build

# 5. Prune workspace to production deps only.
#    This removes devDeps (including @keel/ui, react, vite, typescript) while
#    keeping express, pg, jsonwebtoken, cors, cookie-parser, node-fetch.
WORKDIR /app
RUN npm prune --omit=dev --workspaces --include-workspace-root

# ── Stage 2: lean runtime ────────────────────────────────────────────────────
FROM node:22-alpine AS runtime

WORKDIR /app

# Built SPA (served as static files by Express)
COPY --from=builder /app/portal/dist ./dist

# Express server
COPY --from=builder /app/portal/server ./server

# Pruned node_modules (production deps only — no @keel/ui, no react, no vite)
COPY --from=builder /app/node_modules ./node_modules

# Non-root user
RUN addgroup -S keel && adduser -S keel -G keel
USER keel

EXPOSE 3000

# node-fetch v3 is ESM; dynamic import() in server/index.cjs handles this
CMD ["node", "server/index.cjs"]
