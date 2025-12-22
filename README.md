# 🤖 Telegram Auto-Forward Bot

A powerful multi-user Telegram bot for automatically forwarding messages between channels and groups.

## ✨ Features

- **Multi-User Support** - Each user manages their own phone accounts and rules
- **Multiple Sources/Destinations** - Forward from many channels to many destinations
- **Forward & Copy Modes** - Keep original sender or copy as new message
- **18 Media Filters** - Skip photos, videos, documents, stickers, etc.
- **7 Caption Cleaners** - Remove hashtags, mentions, links, emojis, phones, emails, or entire caption
- **9 Content Modifiers** - Rename files, block/whitelist words, replace text, add header/footer, link buttons, delay, history
- **Album Support** - Properly forwards grouped media (photo albums)
- **Rule Management** - Create, edit, toggle, and delete rules easily

## 🚀 Quick Start

### Prerequisites

1. **Bot Token** from [@BotFather](https://t.me/BotFather)
2. **API ID & Hash** from [my.telegram.org](https://my.telegram.org)

### Local Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/telegram-autoforward-bot.git
cd telegram-autoforward-bot

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your credentials

# Run the bot
python multiuser_autoforward_bot.py
```

### Docker Deployment

```bash
# Build and run
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

### Deploy to Render

1. Fork this repository
2. Connect to [Render](https://render.com)
3. Create new "Background Worker"
4. Set environment variables:
   - `BOT_TOKEN`
   - `API_ID`
   - `API_HASH`
5. Deploy!

### Deploy to Heroku

```bash
heroku create your-bot-name
heroku config:set BOT_TOKEN=xxx API_ID=xxx API_HASH=xxx
git push heroku main
heroku ps:scale worker=1
```

## 📖 Usage

1. Start the bot: `/start`
2. Connect your Telegram account: **🔗 Connect Account**
3. Add forwarding rules: **➕ Add Rule**
4. Configure filters and modifiers
5. Done! Messages will auto-forward

### Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Main menu |
| `/help` | Help information |
| `/rules` | View your rules |
| `/accounts` | Manage accounts |

## 🔧 Configuration Options

### Media Filters (Skip these types)
- 📷 Photos, 🎥 Videos, 📄 Documents
- 🎵 Audio, 🎤 Voice, 🎨 Stickers
- 🎞️ GIFs, ⭕ Video Notes, 📊 Polls
- 📚 Albums, 🔗 Links, 🔘 Buttons
- ↩️ Forwards, 💬 Replies

### Caption Cleaners (Remove from caption)
- ❌ Entire Caption
- #️⃣ Hashtags, @ Mentions
- 🔗 Links, 😀 Emojis
- 📞 Phone Numbers, 📧 Emails

### Content Modifiers
- 📝 Rename Files - Pattern-based renaming
- 🚫 Block Words - Skip messages with words
- ✅ Whitelist - Only forward if contains words
- 🔄 Replace Words - Text replacement
- 📌 Header / 📎 Footer - Add text
- 🔘 Link Buttons - Add inline buttons
- ⏱️ Delay - Delayed forwarding
- 📜 History - Forward past messages

## 📁 Project Structure

```
├── multiuser_autoforward_bot.py  # Main bot code
├── requirements.txt              # Python dependencies
├── Dockerfile                    # Docker image
├── docker-compose.yml            # Docker Compose config
├── render.yaml                   # Render deployment
├── Procfile                      # Heroku deployment
├── .env.example                  # Environment template
└── .gitignore                    # Git ignore rules
```

## ⚠️ Important Notes

- **Session Security**: Never share `.session` files - they contain auth tokens
- **Rate Limits**: Telegram has rate limits; don't forward too frequently
- **Storage**: Use persistent storage for database and sessions in production

## 📄 License

MIT License - feel free to use and modify!

## 🤝 Contributing

Contributions welcome! Please open an issue or PR.

---

Made with ❤️ for the Telegram community
