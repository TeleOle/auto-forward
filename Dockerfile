# Dockerfile for Telegram Auto-Forward Bot
# Build: docker build -t telegram-autoforward-bot .
# Run: docker run -d --name autoforward-bot \
#      -e TELEGRAM_API_ID=xxx -e TELEGRAM_API_HASH=xxx -e TELEGRAM_BOT_TOKEN=xxx \
#      -v bot_data:/app/data telegram-autoforward-bot

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY multiuser_autoforward_bot.py .

# Create data directory for SQLite and sessions
RUN mkdir -p /app/data /app/sessions

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV SESSION_DIR=/app/sessions
ENV DATABASE_FILE=/app/data/autoforward.db

# Volume for persistent data
VOLUME ["/app/data", "/app/sessions"]

# Health check (optional)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Run the bot
CMD ["python", "multiuser_autoforward_bot.py"]

# ============================================
# Docker Compose (save as docker-compose.yml)
# ============================================
# version: '3.8'
# services:
#   bot:
#     build: .
#     container_name: telegram-autoforward-bot
#     restart: unless-stopped
#     environment:
#       - TELEGRAM_API_ID=${TELEGRAM_API_ID}
#       - TELEGRAM_API_HASH=${TELEGRAM_API_HASH}
#       - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
#     volumes:
#       - bot_data:/app/data
#       - bot_sessions:/app/sessions
# 
# volumes:
#   bot_data:
#   bot_sessions:
