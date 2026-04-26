FROM node:22-alpine AS build

WORKDIR /app/web

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web ./
RUN npm run build

FROM node:22-alpine AS runtime

WORKDIR /app/web
ENV NODE_ENV=production

COPY web/package.json web/package-lock.json ./
RUN npm ci --omit=dev

COPY --from=build /app/web/dist ./dist
COPY --from=build /app/web/dist-server ./dist-server
COPY web/server ./server

EXPOSE 3000

CMD ["node", "dist-server/index.js"]
