# syntax=docker/dockerfile:1
# Supervisory console: static React build served by nginx (in production: push /usr/share/nginx/html to a managed CDN
# bucket instead and route /api and /ws to the gateway).
FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY infra/docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
# 127.0.0.1, not localhost: BusyBox wget resolves "localhost" to ::1 first, and nginx here only binds IPv4 - that mismatch
# made this healthcheck report unhealthy even while nginx served real traffic fine.
HEALTHCHECK --interval=15s --timeout=3s CMD wget -qO- http://127.0.0.1/healthz || exit 1
