# ── Stage 1: Build React frontend ──
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python backend ──
FROM python:3.11-slim
WORKDIR /app

# Set timezone to IST (all datetime.now() calls must return Indian time)
ENV TZ=Asia/Kolkata
# Keep resident memory close to what Python actually uses: without this, glibc holds freed blocks
# in per-thread arenas and the container looks far larger than it is (and is billed that way).
ENV MALLOC_ARENA_MAX=2
# Re-fetchable caches (daily candles, scan snapshots) stay on the container disk, never a volume.
ENV CACHE_DIR=/app/.cache

# Install system deps for psycopg2 + timezone data
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc tzdata && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY . .

# Copy built frontend from stage 1
COPY --from=frontend-build /app/frontend/dist /app/frontend/dist

# Remove .env so Railway env vars take precedence
RUN rm -f .env

EXPOSE 8000

CMD ["sh", "-c", "echo '[CMD] Running alembic...'; alembic upgrade head 2>&1 || echo '[CMD] Migration warning — continuing...'; echo '[CMD] Starting server...'; python main.py server"]
