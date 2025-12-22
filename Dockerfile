# Dockerfile for Telegram Auto-Forward Bot
# Build: docker build -t telegram-autoforward-bot .
# Run: docker run -d --name autoforward-bot \
#      -e BOT_TOKEN=xxx -e API_ID=xxx -e API_HASH=xxx \
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
ENV DATA_DIR=/app/data
ENV SESSION_DIR=/app/sessions

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
#       - BOT_TOKEN=${BOT_TOKEN}
#       - API_ID=${API_ID}
#       - API_HASH=${API_HASH}
#     volumes:
#       - bot_data:/app/data
#       - bot_sessions:/app/sessions
# 
# volumes:
#   bot_data:
#   bot_sessions:
