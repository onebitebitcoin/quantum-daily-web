# Stage 1: 빌드
FROM node:20-alpine AS builder

WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# Stage 2: 정적 서빙 + /api 리버스프록시
FROM nginx:alpine

# dist 를 html 루트가 아니라 그 아래 ai/ 로 넣는다. vite 가 base:'/ai/' 로 뽑으므로
# index.html 은 /ai/assets/... 를 참조하는데, dist 를 루트에 풀면 파일은 assets/ 에
# 있고 nginx 는 root+URI 로 ai/assets/ 를 찾아 404 가 난다(2026-08-29 프로덕션 실측).
# URL 경로와 파일 경로를 같게 두면 location 마다 alias 를 붙일 필요가 없다.
COPY --from=builder /app/dist /usr/share/nginx/html/ai
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
