# Multi-stage build: compile the Vite app, then serve the static bundle from nginx (Spec 05 §2,
# D22). A build failure here never blocks the core `docker compose up` (the service lives under
# the `web` profile).
FROM node:24-slim AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json vite.config.ts index.html ./
COPY src/ src/
RUN npm run build

FROM nginx:alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
