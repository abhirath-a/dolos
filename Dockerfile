FROM node:26-bookworm-slim AS frontend
WORKDIR /source
RUN npm install --global pnpm@12.9.0
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./frontend/
RUN pnpm --dir frontend install --frozen-lockfile
COPY frontend ./frontend
COPY dolos/migrations ./dolos/migrations
RUN pnpm --dir frontend build

FROM rust:1.99-bookworm AS backend
WORKDIR /source
RUN apt-get update \
    && apt-get install -y --no-install-recommends clang cmake pkg-config \
    && rm -rf /var/lib/apt/lists/*
COPY backend ./backend
COPY dolos/migrations ./dolos/migrations
COPY dolos/demo_assets ./dolos/demo_assets
RUN cargo build --locked --release --manifest-path backend/Cargo.toml

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates poppler-utils \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app/data \
    && chown 65532:65532 /app/data
WORKDIR /app
COPY --from=backend /source/backend/target/release/dolos-api /usr/local/bin/dolos-api
COPY --from=backend /source/dolos/demo_assets ./dolos/demo_assets
COPY --from=frontend /source/frontend/dist ./frontend/dist
ENV DOLOS_BIND=0.0.0.0:8000 \
    DOLOS_FRONTEND_DIR=/app/frontend/dist \
    DOLOS_DATA_DIR=/app/data \
    DOLOS_DATABASE_URL=file:/app/data/dolos.sqlite3
USER 65532:65532
EXPOSE 8000
ENTRYPOINT ["dolos-api"]
