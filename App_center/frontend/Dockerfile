FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

# Build-time env vars cho Vite (phải khai báo ARG trước khi dùng)
ARG VITE_WS_URL=ws://localhost:8000
ARG VITE_API_URL=http://localhost:8000
ENV VITE_WS_URL=$VITE_WS_URL
ENV VITE_API_URL=$VITE_API_URL

# Build production bundle
RUN npm run build

# ── Serve với nginx ───────────────────────────────────────────
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html

# Config nginx: serve SPA + proxy /events /health /ws sang backend
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
