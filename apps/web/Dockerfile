# syntax=docker/dockerfile:1
FROM node:22-alpine AS deps
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
RUN npm ci

# --- development: next dev with hot reload, source bind-mounted by compose ---
FROM deps AS dev
COPY . .
CMD ["npm", "run", "dev"]

# --- build: rewrites (/api → FastAPI) are resolved at build time -------------
FROM deps AS build
ARG API_INTERNAL_URL=http://api:8000
ARG MAX_UPLOAD_MB=15
ENV API_INTERNAL_URL=$API_INTERNAL_URL MAX_UPLOAD_MB=$MAX_UPLOAD_MB
COPY . .
RUN npm run build

# --- production: standalone server, non-root, no dev dependencies ------------
FROM node:22-alpine AS production
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -S cermat && adduser -S -G cermat cermat
COPY --from=build --chown=cermat:cermat /app/.next/standalone ./
COPY --from=build --chown=cermat:cermat /app/.next/static ./.next/static
USER cermat
EXPOSE 3000
CMD ["node", "server.js"]
