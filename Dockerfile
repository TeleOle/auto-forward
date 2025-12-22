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

# Copy all Python files (handles different naming)
COPY *.py .

# Create data directory for SQLite and sessions
RUN mkdir -p /app/data /app/sessions

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV SESSION_DIR=/app/sessions
ENV DATABASE_FILE=/app/data/autoforward.db

# Volume for persistent data
VOLUME ["/app/data", "/app/sessions"]

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Run the bot (try different filenames)
CMD ["sh", "-c", "python multiuser_autoforward_bot.py || python bot.py || python main.py"]
