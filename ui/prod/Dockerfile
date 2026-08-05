# AssetAtlas Production UI — Multi-stage build
# Stage 1: Build React app with Vite
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json vite.config.ts index.html ./
COPY src/ ./src/
# Vite copies publicDir (default: public/) into dist/ at build time.
# Without this, the logo, favicons, preview chart icons, and data-source
# logos end up missing from the production bundle.
COPY public/ ./public/
RUN npm run build

# Stage 2: Production server
FROM node:20-alpine
LABEL org.opencontainers.image.source="https://github.com/dotdna-io/assetatlas" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY server.js ./
COPY --from=build /app/dist ./dist
COPY public/ ./public/

# Create non-root user
RUN addgroup -g 1001 -S assetatlas && \
    adduser -S assetatlas -u 1001 -G assetatlas && \
    chown -R assetatlas:assetatlas /app

USER assetatlas

EXPOSE 3000

ENV NODE_ENV=production

CMD ["node", "server.js"]
