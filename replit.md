# Telegram Auto-Forward Bot

## Overview
A multi-user Telegram bot for automatically forwarding messages between channels and groups. Users can connect their Telegram accounts and set up forwarding rules with filters and content modifiers.

## Project Structure
- `bot.py` - Main bot application with all handlers and logic
- `keep_alive.py` - Flask web server for health checks (port 5000)
- `requirements.txt` - Python dependencies

## Setup Requirements

### Required Secrets
The bot requires three secrets to be configured:
1. **TELEGRAM_API_ID** - API ID from https://my.telegram.org
2. **TELEGRAM_API_HASH** - API Hash from https://my.telegram.org  
3. **TELEGRAM_BOT_TOKEN** - Bot token from @BotFather on Telegram

### Optional Environment Variables
- `ADMIN_USER_ID` - Admin user ID for special permissions
- `SESSION_DIR` - Directory for session files (default: user_sessions)
- `DATABASE_FILE` - SQLite database path (default: autoforward.db)

## Running the Bot
The bot runs via the "Telegram Bot" workflow which:
1. Starts a Flask health check server on port 5000
2. Runs the Telegram bot polling loop

## Features
- Multi-user support with per-user phone account management
- Forward from multiple sources to multiple destinations
- Forward & Copy modes (keep original sender or copy as new message)
- 18 media type filters (skip photos, videos, documents, etc.)
- 7 caption cleaners (remove hashtags, mentions, links, etc.)
- Content modifiers (rename files, word replacement, add headers/footers)
- Album/grouped media support

## Database
Uses SQLite for storing:
- User information
- Connected Telegram accounts
- Forwarding rules with filters and settings

## Recent Changes
- December 2024: Imported to Replit and configured for Replit environment
