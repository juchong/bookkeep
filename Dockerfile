# Multi-stage build for single-container deployment
FROM node:22-slim AS frontend-builder

WORKDIR /app/frontend

# Build metadata (forwarded to Vite)
ARG APP_VERSION=dev
ENV VITE_APP_VERSION=$APP_VERSION

# Copy only dependency files first for better layer caching
COPY package*.json ./

# Install dependencies (this layer will be cached unless package.json changes)
# Use BuildKit cache mount for npm cache (faster subsequent builds)
RUN --mount=type=cache,target=/root/.npm \
    npm ci --legacy-peer-deps || npm install --legacy-peer-deps

# Copy config files needed for build
COPY vite.config.ts tsconfig*.json ./
COPY tailwind.config.ts postcss.config.js ./
COPY index.html ./

# Copy source files (these change frequently, so copy after dependencies)
COPY src ./src
COPY public ./public

# Build frontend in production mode
ENV NODE_ENV=production
RUN npm run build

# Backend stage
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Copy backend dependency files
COPY backend/pyproject.toml backend/requirements.txt ./
RUN uv pip install --system --no-cache -r requirements.txt

# Copy backend code (including Alembic migrations)
COPY backend ./backend

# Copy built frontend from builder stage
COPY --from=frontend-builder /app/frontend/dist ./frontend_dist

# Create data directory for SQLite
RUN groupadd --gid 10001 bookkeep \
    && useradd --uid 10001 --gid bookkeep --create-home --shell /usr/sbin/nologin bookkeep \
    && mkdir -p /app/data \
    && chown bookkeep:bookkeep /app/data \
    && chmod -R a+rX /app/backend /app/frontend_dist

# Copy and make entrypoint script executable
COPY backend/entrypoint.sh /app/entrypoint.sh
RUN chmod 755 /app/entrypoint.sh

# Set PYTHONPATH to include backend directory so imports work correctly
ENV PYTHONPATH="/app/backend"

# Expose port
EXPOSE 8000

# The application only needs write access to /app/data (SQLite deployments),
# /tmp, and explicitly mounted download directories.
USER bookkeep:bookkeep

# Use entrypoint script to run migrations then start the app
ENTRYPOINT ["/app/entrypoint.sh"]
