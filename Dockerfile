FROM node:22-bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends libc++1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN corepack enable
WORKDIR /site
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
EXPOSE 3000
CMD ["pnpm","exec","vinext","dev","--hostname","0.0.0.0"]
