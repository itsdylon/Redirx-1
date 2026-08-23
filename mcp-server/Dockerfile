# Portable alternative to Render's native Node runtime (render.yaml uses the
# latter by default). Useful for running this gateway anywhere else Docker
# runs — the "one command" in the brief is about client connection, not
# deployment, but a container is still the most reproducible way to hand
# this to infra that isn't Render.
FROM node:20.20-slim AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY tsconfig.json ./
COPY src ./src
RUN npm run build

FROM node:20.20-slim
WORKDIR /app
ENV NODE_ENV=production
COPY package.json package-lock.json* ./
RUN npm install --omit=dev
COPY --from=build /app/dist ./dist

EXPOSE 8787
CMD ["node", "dist/index.js"]
