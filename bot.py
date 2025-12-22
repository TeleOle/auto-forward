"""
Multi-User Telegram Auto-Forward Bot
=====================================
Features:
- Multiple sources per rule (comma-separated)
- Multiple destinations per rule (comma-separated)
- Per-user session management
- SQLite database storage
"""

import asyncio
import logging
import os
import sqlite3
import json
import re
from datetime import datetime
from typing import Dict, Set, Optional, List
from asyncio import Lock
from contextlib import contextmanager

def escape_markdown(text: str) -> str:
    """Escape special Markdown characters to prevent parsing errors."""
    if not text:
        return ""
    # Escape special characters: _ * [ ] ( ) ~ ` > # + - = | { } . !
    escape_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')
    return text

def safe_text(text: str) -> str:
    """Make text safe for Markdown by escaping special characters."""
    if not text:
        return ""
    # Only escape the most problematic characters for Telegram Markdown
    return text.replace('_', '\\_').replace('*', '\\*').replace('`', '\\`').replace('[', '\\[')

# Default filters configuration (all OFF = keep everything)
DEFAULT_FILTERS = {
    # Media type filters (ignore/skip these)
    'document': False,      # Ignore document messages (PDF, DOCX, etc.)
    'video': False,         # Ignore video messages
    'audio': False,         # Ignore audio messages
    'sticker': False,       # Ignore sticker messages
    'text': False,          # Ignore text-only messages
    'photo': False,         # Ignore photo messages
    'photo_only': False,    # Ignore photos WITHOUT caption
    'photo_with_text': False,  # Ignore photos WITH caption
    'album': False,         # Ignore album/grouped media
    'poll': False,          # Ignore poll messages
    'voice': False,         # Ignore voice messages
    'video_note': False,    # Ignore round video notes
    'gif': False,           # Ignore animated GIFs
    'emoji': False,         # Ignore animated emoji
    'forward': False,       # Ignore forwarded messages
    'reply': False,         # Ignore reply messages
    'link': False,          # Ignore messages with links
    'button': False,        # Ignore messages with buttons
    
    # Cleaner options (remove from caption)
    'clean_caption': False,   # Remove entire caption (hide caption)
    'clean_hashtag': False,   # Remove #hashtags from caption
    'clean_mention': False,   # Remove @mentions from caption
    'clean_link': False,      # Remove links from caption
    'clean_emoji': False,     # Remove emojis from caption
    'clean_phone': False,     # Remove phone numbers from caption
    'clean_email': False,     # Remove email addresses from caption
}

# Default modify content configuration
DEFAULT_MODIFY = {
    # Filename rename
    'rename_enabled': False,
    'rename_pattern': '{original}',  # Patterns: {original}, {date}, {time}, {random}, {counter}
    
    # Block/Whitelist words
    'block_words_enabled': False,
    'block_words': [],  # List of words to block (message skipped if contains)
    'whitelist_enabled': False,
    'whitelist_words': [],  # List of words required (message skipped if NOT contains)
    
    # Word replacement
    'replace_enabled': False,
    'replace_pairs': [],  # List of {'from': 'old', 'to': 'new', 'regex': False}
    
    # Caption editing
    'header_enabled': False,
    'header_text': '',  # Text to add at the beginning
    'footer_enabled': False,
    'footer_text': '',  # Text to add at the end
    
    # Link buttons
    'buttons_enabled': False,
    'buttons': [],  # List of [{'text': 'Button', 'url': 'https://...'}] per row
    
    # Delay
    'delay_enabled': False,
    'delay_seconds': 0,  # Delay in seconds before forwarding
    
    # History
    'history_enabled': False,
    'history_count': 0,  # Number of past messages to forward when rule created
}

# Optional dependency handling
TELETHON_AVAILABLE = True
try:
    from telethon import TelegramClient, events, errors
    from telethon.tl.types import User, Channel, Chat, PeerChannel
    from telethon.tl import types
except ImportError:
    TELETHON_AVAILABLE = False
    class _PlaceholderErrors:
        class SessionPasswordNeededError(Exception): pass
        class PhoneCodeInvalidError(Exception): pass
        class PhoneCodeExpiredError(Exception): pass
        class FloodWaitError(Exception):
            def __init__(self, seconds=0): self.seconds = seconds
        class PhoneNumberBannedError(Exception): pass
        class ChannelPrivateError(Exception): pass
        class ChatWriteForbiddenError(Exception): pass
    errors = _PlaceholderErrors
    TelegramClient = None
    events = None
    PeerChannel = None
    types = None

TELEGRAM_AVAILABLE = True
try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import (
        Application, ApplicationBuilder, ContextTypes, CommandHandler,
        CallbackQueryHandler, MessageHandler, filters
    )
    from telegram.error import BadRequest
except ImportError:
    TELEGRAM_AVAILABLE = False
    Update = None
    ContextTypes = None
    BadRequest = Exception

# ==================== CONFIGURATION ====================
API_ID = int(os.getenv('TELEGRAM_API_ID', '0'))
API_HASH = os.getenv('TELEGRAM_API_HASH', '0')
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '0')

SESSION_DIR = "user_sessions"
DATABASE_FILE = "autoforward.db"

os.makedirs(SESSION_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)

# ==================== HELPER FUNCTIONS ====================
def parse_multi_ids(text: str) -> List[str]:
    """Parse comma/space separated IDs into a list."""
    # Replace common separators with comma
    text = text.replace('\n', ',').replace(';', ',').replace(' ', ',')
    # Split and clean
    parts = [p.strip() for p in text.split(',')]
    # Filter empty and duplicates while preserving order
    seen = set()
    result = []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            result.append(p)
    return result

def format_id_list(ids: List[str], max_show: int = 3) -> str:
    """Format ID list for display."""
    if len(ids) <= max_show:
        return ', '.join(f'`{i}`' for i in ids)
    return ', '.join(f'`{i}`' for i in ids[:max_show]) + f' +{len(ids)-max_show} more'

# ==================== DATABASE MANAGER ====================
class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = Lock()
        self._init_db()
    
    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS connected_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    phone TEXT NOT NULL,
                    display_name TEXT,
                    connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active INTEGER DEFAULT 1,
                    UNIQUE(user_id, phone)
                )
            ''')
            
            # Updated schema: sources and destinations are comma-separated lists
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS forward_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    phone TEXT NOT NULL,
                    source TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    is_enabled INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    forward_count INTEGER DEFAULT 0
                )
            ''')
            
            # Migration columns
            for col in ['source_id', 'dest_id', 'sources', 'destinations', 'forward_mode', 'filters', 'modify']:
                try:
                    cursor.execute(f'ALTER TABLE forward_rules ADD COLUMN {col} TEXT')
                    log.info(f"Added {col} column")
                except sqlite3.OperationalError:
                    pass
            
            conn.commit()
    
    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    async def ensure_user(self, user_id: int, username: str = None, first_name: str = None):
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('INSERT OR REPLACE INTO users (user_id, username, first_name) VALUES (?, ?, ?)',
                             (user_id, username, first_name))
                conn.commit()
    
    async def add_connected_account(self, user_id: int, phone: str, display_name: str = None):
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('INSERT OR REPLACE INTO connected_accounts (user_id, phone, display_name, is_active) VALUES (?, ?, ?, 1)',
                             (user_id, phone, display_name))
                conn.commit()
    
    async def get_user_accounts(self, user_id: int) -> List[dict]:
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT phone, display_name, connected_at FROM connected_accounts WHERE user_id = ? AND is_active = 1', (user_id,))
                return [dict(row) for row in cursor.fetchall()]
    
    async def remove_account(self, user_id: int, phone: str):
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('UPDATE connected_accounts SET is_active = 0 WHERE user_id = ? AND phone = ?', (user_id, phone))
                cursor.execute('UPDATE forward_rules SET is_enabled = 0 WHERE user_id = ? AND phone = ?', (user_id, phone))
                conn.commit()
    
    async def add_forward_rule(self, user_id: int, phone: str, sources: List[str], destinations: List[str], forward_mode: str = "forward", filters: dict = None, modify: dict = None) -> int:
        """Add rule with multiple sources and destinations."""
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Store as comma-separated for backward compatibility
                source_str = ','.join(sources)
                dest_str = ','.join(destinations)
                filters_str = json.dumps(filters) if filters else None
                modify_str = json.dumps(modify) if modify else None
                cursor.execute('''
                    INSERT INTO forward_rules (user_id, phone, source, destination, sources, destinations, forward_mode, filters, modify)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (user_id, phone, source_str, dest_str, source_str, dest_str, forward_mode, filters_str, modify_str))
                conn.commit()
                return cursor.lastrowid
    
    async def get_user_rules(self, user_id: int) -> List[dict]:
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM forward_rules WHERE user_id = ? ORDER BY id DESC', (user_id,))
                rows = cursor.fetchall()
                result = []
                for row in rows:
                    d = dict(row)
                    # Parse sources and destinations
                    d['sources'] = d.get('sources') or d.get('source', '')
                    d['destinations'] = d.get('destinations') or d.get('destination', '')
                    d['source_list'] = [s.strip() for s in d['sources'].split(',') if s.strip()]
                    d['dest_list'] = [s.strip() for s in d['destinations'].split(',') if s.strip()]
                    d['forward_mode'] = d.get('forward_mode') or 'forward'
                    # Parse filters
                    filters_str = d.get('filters')
                    if filters_str:
                        try:
                            d['filters'] = json.loads(filters_str)
                        except:
                            d['filters'] = DEFAULT_FILTERS.copy()
                    else:
                        d['filters'] = DEFAULT_FILTERS.copy()
                    # Parse modify settings
                    modify_str = d.get('modify')
                    if modify_str:
                        try:
                            d['modify'] = json.loads(modify_str)
                        except:
                            d['modify'] = DEFAULT_MODIFY.copy()
                    else:
                        d['modify'] = DEFAULT_MODIFY.copy()
                    result.append(d)
                return result
    
    async def get_rules_by_phone(self, phone: str) -> List[dict]:
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM forward_rules WHERE phone = ? AND is_enabled = 1', (phone,))
                rows = cursor.fetchall()
                result = []
                for row in rows:
                    d = dict(row)
                    d['sources'] = d.get('sources') or d.get('source', '')
                    d['destinations'] = d.get('destinations') or d.get('destination', '')
                    d['source_list'] = [s.strip() for s in d['sources'].split(',') if s.strip()]
                    d['dest_list'] = [s.strip() for s in d['destinations'].split(',') if s.strip()]
                    d['forward_mode'] = d.get('forward_mode') or 'forward'
                    # Parse filters
                    filters_str = d.get('filters')
                    if filters_str:
                        try:
                            d['filters'] = json.loads(filters_str)
                        except:
                            d['filters'] = DEFAULT_FILTERS.copy()
                    else:
                        d['filters'] = DEFAULT_FILTERS.copy()
                    # Parse modify settings
                    modify_str = d.get('modify')
                    if modify_str:
                        try:
                            d['modify'] = json.loads(modify_str)
                        except:
                            d['modify'] = DEFAULT_MODIFY.copy()
                    else:
                        d['modify'] = DEFAULT_MODIFY.copy()
                    result.append(d)
                return result
    
    async def delete_rule(self, user_id: int, rule_id: int) -> bool:
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM forward_rules WHERE id = ? AND user_id = ?', (rule_id, user_id))
                conn.commit()
                return cursor.rowcount > 0
    
    async def toggle_rule(self, user_id: int, rule_id: int) -> Optional[bool]:
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('UPDATE forward_rules SET is_enabled = 1 - is_enabled WHERE id = ? AND user_id = ?', (rule_id, user_id))
                conn.commit()
                if cursor.rowcount > 0:
                    cursor.execute('SELECT is_enabled FROM forward_rules WHERE id = ?', (rule_id,))
                    row = cursor.fetchone()
                    return bool(row['is_enabled']) if row else None
                return None
    
    async def update_rule_mode(self, user_id: int, rule_id: int, mode: str) -> bool:
        """Update rule forward mode."""
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('UPDATE forward_rules SET forward_mode = ? WHERE id = ? AND user_id = ?', 
                             (mode, rule_id, user_id))
                conn.commit()
                return cursor.rowcount > 0
    
    async def update_rule_sources(self, user_id: int, rule_id: int, sources: List[str]) -> bool:
        """Update rule sources."""
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                source_str = ','.join(sources)
                cursor.execute('UPDATE forward_rules SET source = ?, sources = ? WHERE id = ? AND user_id = ?', 
                             (source_str, source_str, rule_id, user_id))
                conn.commit()
                return cursor.rowcount > 0
    
    async def update_rule_destinations(self, user_id: int, rule_id: int, destinations: List[str]) -> bool:
        """Update rule destinations."""
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                dest_str = ','.join(destinations)
                cursor.execute('UPDATE forward_rules SET destination = ?, destinations = ? WHERE id = ? AND user_id = ?', 
                             (dest_str, dest_str, rule_id, user_id))
                conn.commit()
                return cursor.rowcount > 0
    
    async def update_rule_filters(self, user_id: int, rule_id: int, filters: dict) -> bool:
        """Update rule filters."""
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                filters_str = json.dumps(filters)
                cursor.execute('UPDATE forward_rules SET filters = ? WHERE id = ? AND user_id = ?', 
                             (filters_str, rule_id, user_id))
                conn.commit()
                return cursor.rowcount > 0
    
    async def update_rule_modify(self, user_id: int, rule_id: int, modify: dict) -> bool:
        """Update rule modify settings."""
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                modify_str = json.dumps(modify)
                cursor.execute('UPDATE forward_rules SET modify = ? WHERE id = ? AND user_id = ?', 
                             (modify_str, rule_id, user_id))
                conn.commit()
                return cursor.rowcount > 0
    
    async def increment_forward_count(self, rule_id: int):
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('UPDATE forward_rules SET forward_count = forward_count + 1 WHERE id = ?', (rule_id,))
                conn.commit()
    
    async def get_all_active_phones(self) -> List[str]:
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT DISTINCT phone FROM connected_accounts WHERE is_active = 1')
                return [row['phone'] for row in cursor.fetchall()]
    
    async def get_phone_user_id(self, phone: str) -> Optional[int]:
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT user_id FROM connected_accounts WHERE phone = ? AND is_active = 1', (phone,))
                row = cursor.fetchone()
                return row['user_id'] if row else None

# ==================== SESSION MANAGER ====================
class UserSessionManager:
    def __init__(self, db: DatabaseManager):
        self.db = db
        self.clients: Dict[str, TelegramClient] = {}
        self.handlers_attached: Set[str] = set()
        self._locks: Dict[str, Lock] = {}
        # Album cache: {phone: {grouped_id: {'messages': [], 'timer': task, 'rule': rule}}}
        self.album_cache: Dict[str, Dict[int, dict]] = {}
    
    def _get_session_path(self, user_id: int, phone: str) -> str:
        safe_phone = phone.replace("+", "").replace(" ", "")
        return os.path.join(SESSION_DIR, f"user_{user_id}_{safe_phone}")
    
    async def get_or_create_client(self, user_id: int, phone: str) -> TelegramClient:
        if not TELETHON_AVAILABLE:
            raise RuntimeError("Telethon not installed")
        
        if phone in self.clients:
            client = self.clients[phone]
            if not client.is_connected():
                await client.connect()
            return client
        
        session_path = self._get_session_path(user_id, phone)
        client = TelegramClient(session_path, API_ID, API_HASH)
        await client.connect()
        self.clients[phone] = client
        return client
    
    async def disconnect_client(self, phone: str):
        if phone in self.clients:
            try:
                client = self.clients[phone]
                if client.is_connected():
                    await client.disconnect()
            except Exception as e:
                log.error(f"Error disconnecting {phone}: {e}")
            finally:
                self.clients.pop(phone, None)
                self.handlers_attached.discard(phone)
    
    async def resolve_entity(self, phone: str, identifier: str) -> tuple:
        """Returns (success, entity, error_msg)"""
        client = self.clients.get(phone)
        if not client:
            return False, None, "Client not connected"
        
        try:
            if identifier.startswith('@'):
                entity = await client.get_entity(identifier)
                return True, entity, None
            
            try:
                num_id = int(identifier)
            except ValueError:
                return False, None, "Invalid ID format"
            
            # Try direct resolution
            try:
                entity = await client.get_entity(num_id)
                return True, entity, None
            except Exception:
                pass
            
            # For -100 format channel IDs
            if str(num_id).startswith('-100'):
                real_id = int(str(num_id)[4:])
                try:
                    entity = await client.get_entity(PeerChannel(real_id))
                    return True, entity, None
                except Exception:
                    pass
            
            # Search through dialogs
            async for dialog in client.iter_dialogs():
                dialog_id = dialog.id
                if dialog_id == num_id or abs(dialog_id) == abs(num_id):
                    return True, dialog.entity, None
                if str(num_id).startswith('-100'):
                    real_id = int(str(num_id)[4:])
                    if dialog_id == real_id or abs(dialog_id) == real_id:
                        return True, dialog.entity, None
            
            return False, None, "Entity not found. Make sure account is a member."
            
        except Exception as e:
            return False, None, str(e)
    
    async def load_existing_sessions(self):
        if not TELETHON_AVAILABLE:
            return
        
        phones = await self.db.get_all_active_phones()
        log.info(f"Loading {len(phones)} sessions...")
        
        for phone in phones:
            try:
                user_id = await self.db.get_phone_user_id(phone)
                if not user_id:
                    continue
                
                session_path = self._get_session_path(user_id, phone)
                if not os.path.exists(session_path + '.session'):
                    continue
                
                client = TelegramClient(session_path, API_ID, API_HASH)
                await client.connect()
                
                if await client.is_user_authorized():
                    self.clients[phone] = client
                    await self.attach_forward_handler(phone)
                    log.info(f"✅ Loaded: {phone}")
                else:
                    await client.disconnect()
            except Exception as e:
                log.error(f"Failed loading {phone}: {e}")
    
    async def attach_forward_handler(self, phone: str):
        if phone in self.handlers_attached:
            return
        
        client = self.clients.get(phone)
        if not client:
            return
        
        db_ref = self.db
        phone_ref = phone
        entity_cache = {}
        
        async def resolve_dest(dest_str: str):
            """Resolve destination with caching."""
            if dest_str in entity_cache:
                return entity_cache[dest_str]
            
            try:
                if dest_str.startswith('@'):
                    entity = await client.get_entity(dest_str)
                    entity_cache[dest_str] = entity
                    return entity
                
                dest_id = int(dest_str)
                
                try:
                    entity = await client.get_entity(dest_id)
                    entity_cache[dest_str] = entity
                    return entity
                except Exception:
                    pass
                
                if str(dest_id).startswith('-100'):
                    real_id = int(str(dest_id)[4:])
                    try:
                        entity = await client.get_entity(PeerChannel(real_id))
                        entity_cache[dest_str] = entity
                        return entity
                    except Exception:
                        pass
                
                async for dialog in client.iter_dialogs():
                    did = dialog.id
                    if did == dest_id or abs(did) == abs(dest_id):
                        entity_cache[dest_str] = dialog.entity
                        return dialog.entity
                    if str(dest_id).startswith('-100'):
                        real_id = int(str(dest_id)[4:])
                        if did == real_id or abs(did) == real_id:
                            entity_cache[dest_str] = dialog.entity
                            return dialog.entity
                
                return None
            except Exception as e:
                log.error(f"Failed to resolve {dest_str}: {e}")
                return None
        
        def check_source_match(chat_id: int, chat_username: str, source: str) -> bool:
            """Check if chat matches a source."""
            # Username match
            if source.startswith('@'):
                src_username = source[1:].lower()
                return chat_username and chat_username.lower() == src_username
            
            # Numeric ID match
            try:
                src_num = int(source)
                if chat_id == src_num or abs(chat_id) == abs(src_num):
                    return True
                # Handle -100 prefix
                if str(src_num).startswith('-100'):
                    src_real = int(str(src_num)[4:])
                    if str(chat_id).startswith('-100'):
                        chat_real = int(str(chat_id)[4:])
                        if chat_real == src_real:
                            return True
                    if abs(chat_id) == src_real:
                        return True
                if str(chat_id).startswith('-100'):
                    chat_real = int(str(chat_id)[4:])
                    if chat_real == abs(src_num):
                        return True
            except ValueError:
                pass
            
            return False
        
        # Album handling
        album_cache = {}  # {grouped_id: {'messages': [], 'dest_list': [], 'rule': {}, 'timer': None}}
        album_lock = asyncio.Lock()
        
        async def send_album_group(grouped_id: int):
            """Send collected album messages as a group."""
            async with album_lock:
                if grouped_id not in album_cache:
                    return
                
                album_data = album_cache.pop(grouped_id)
                messages = album_data['messages']
                dest_list = album_data['dest_list']
                rule = album_data['rule']
                caption_text = album_data.get('caption_text', '')
                forward_mode = rule.get('forward_mode', 'forward')
                
                if not messages:
                    return
                
                log.info(f"📚 [{phone_ref}] Sending album with {len(messages)} items")
                
                for dest in dest_list:
                    try:
                        dest_entity = await resolve_dest(dest)
                        if dest_entity is None:
                            log.error(f"❌ [{phone_ref}] Could not resolve: {dest}")
                            continue
                        
                        if forward_mode == "copy":
                            # COPY MODE: Download and re-upload as album
                            import tempfile
                            import os as temp_os
                            
                            # Download all media files
                            files = []
                            for msg in messages:
                                temp_file = await client.download_media(msg, file=tempfile.gettempdir())
                                if temp_file:
                                    files.append(temp_file)
                            
                            if files:
                                try:
                                    # Send as album (first file gets caption)
                                    await client.send_file(
                                        dest_entity,
                                        files,
                                        caption=caption_text if caption_text else None
                                    )
                                    log.info(f"📚 [{phone_ref}] ALBUM ({len(files)} files) -> {dest}")
                                finally:
                                    # Clean up temp files
                                    for f in files:
                                        try:
                                            if f and temp_os.path.exists(f):
                                                temp_os.remove(f)
                                        except Exception:
                                            pass
                        else:
                            # FORWARD MODE: Forward all messages together
                            await client.forward_messages(entity=dest_entity, messages=messages)
                            log.info(f"✅ [{phone_ref}] ALBUM forwarded ({len(messages)} items) -> {dest}")
                        
                    except Exception as e:
                        log.error(f"❌ [{phone_ref}] Album send failed: {e}")
        
        @client.on(events.NewMessage(incoming=True))
        async def forward_handler(event):
            try:
                chat_id = event.chat_id
                chat = await event.get_chat()
                chat_username = getattr(chat, 'username', None)
                
                rules = await db_ref.get_rules_by_phone(phone_ref)
                if not rules:
                    return
                
                for rule in rules:
                    source_list = rule.get('source_list', [])
                    dest_list = rule.get('dest_list', [])
                    rule_id = rule['id']
                    forward_mode = rule.get('forward_mode', 'forward')
                    filters = rule.get('filters', {})
                    
                    # Check if message matches ANY source
                    is_match = False
                    matched_source = None
                    
                    for src in source_list:
                        if check_source_match(chat_id, chat_username, src):
                            is_match = True
                            matched_source = src
                            break
                    
                    if not is_match:
                        continue
                    
                    # ========== FILTER CHECK ==========
                    msg = event.message
                    
                    # Determine message type for filtering
                    msg_type = None
                    has_caption = bool(msg.message or msg.text)
                    
                    if msg.photo:
                        msg_type = 'photo'
                        # Check photo_only (photo WITHOUT text)
                        if filters.get('photo_only', False) and not has_caption:
                            log.info(f"[{phone_ref}] SKIPPED: photo_only (no caption)")
                            continue
                        # Check photo_with_text (photo WITH text)
                        if filters.get('photo_with_text', False) and has_caption:
                            log.info(f"[{phone_ref}] SKIPPED: photo_with_text (has caption)")
                            continue
                    elif msg.video_note:
                        msg_type = 'video_note'
                    elif msg.video:
                        msg_type = 'video'
                    elif msg.voice:
                        msg_type = 'voice'
                    elif msg.audio:
                        msg_type = 'audio'
                    elif msg.sticker:
                        msg_type = 'sticker'
                    elif msg.gif:
                        msg_type = 'gif'
                    elif msg.document:
                        msg_type = 'document'
                    elif msg.poll:
                        msg_type = 'poll'
                    elif msg.text or msg.message:
                        msg_type = 'text'
                    
                    # Check POLL filter
                    if msg.poll and filters.get('poll', False):
                        log.info(f"[{phone_ref}] SKIPPED: poll")
                        continue
                    
                    # Check ALBUM filter (grouped media) - if filter ON, skip albums
                    if msg.grouped_id and filters.get('album', False):
                        log.info(f"[{phone_ref}] SKIPPED: album (grouped_id={msg.grouped_id})")
                        continue
                    
                    # Handle ALBUM (grouped media) - collect and send as group
                    if msg.grouped_id and not filters.get('album', False):
                        grouped_id = msg.grouped_id
                        
                        async with album_lock:
                            if grouped_id not in album_cache:
                                # First message of album - start collecting
                                album_cache[grouped_id] = {
                                    'messages': [msg],
                                    'dest_list': dest_list,
                                    'rule': rule,
                                    'caption_text': msg.message or msg.text or "",
                                }
                                # Schedule sending after 1 second (to collect all album items)
                                asyncio.create_task(asyncio.sleep(1.5))
                                asyncio.get_event_loop().call_later(
                                    1.5, 
                                    lambda gid=grouped_id: asyncio.create_task(send_album_group(gid))
                                )
                                log.info(f"📚 [{phone_ref}] Album started: {grouped_id}")
                            else:
                                # Additional message in album
                                album_cache[grouped_id]['messages'].append(msg)
                                # Update caption if this message has one
                                if msg.message or msg.text:
                                    album_cache[grouped_id]['caption_text'] = msg.message or msg.text
                                log.info(f"📚 [{phone_ref}] Album item added: {grouped_id} (total: {len(album_cache[grouped_id]['messages'])})")
                        
                        # Skip normal processing - album will be sent by timer
                        continue
                    
                    # Check FORWARD filter (forwarded messages)
                    if msg.forward and filters.get('forward', False):
                        log.info(f"[{phone_ref}] SKIPPED: forwarded message")
                        continue
                    
                    # Check REPLY filter
                    if msg.reply_to and filters.get('reply', False):
                        log.info(f"[{phone_ref}] SKIPPED: reply message")
                        continue
                    
                    # Check LINK filter (messages containing links)
                    if filters.get('link', False):
                        text_to_check = msg.message or msg.text or ""
                        has_links = bool(re.search(r'https?://|www\.|t\.me/|tg://', text_to_check))
                        # Also check for MessageEntityUrl or TextUrl in entities
                        if msg.entities:
                            for ent in msg.entities:
                                if hasattr(ent, '__class__') and ent.__class__.__name__ in ['MessageEntityUrl', 'MessageEntityTextUrl']:
                                    has_links = True
                                    break
                        if has_links:
                            log.info(f"[{phone_ref}] SKIPPED: contains links")
                            continue
                    
                    # Check BUTTON filter (inline keyboards)
                    if msg.reply_markup and filters.get('button', False):
                        log.info(f"[{phone_ref}] SKIPPED: has buttons")
                        continue
                    
                    # Check animated EMOJI filter
                    if filters.get('emoji', False):
                        # Check for custom emoji entities
                        if msg.entities:
                            has_custom_emoji = False
                            for ent in msg.entities:
                                if hasattr(ent, '__class__') and ent.__class__.__name__ == 'MessageEntityCustomEmoji':
                                    has_custom_emoji = True
                                    break
                            if has_custom_emoji:
                                log.info(f"[{phone_ref}] SKIPPED: has custom/animated emoji")
                                continue
                    
                    # Check if this message type should be SKIPPED
                    # filter value True = SKIP this type (checked button)
                    # filter value False = KEEP this type (unchecked button)
                    skip_filter = filters.get(msg_type, False) if msg_type else False
                    if skip_filter:
                        log.info(f"[{phone_ref}] SKIPPED: {msg_type} (filter ON)")
                        continue
                    
                    # ========== CAPTION CONTENT CLEANING ==========
                    # Get original text/caption
                    original_text = msg.message or msg.text or ""
                    filtered_text = original_text
                    removed_items = []
                    
                    # 0. CAPTION REMOVE - Remove entire caption
                    if filters.get('clean_caption', False):
                        if original_text:
                            filtered_text = ""
                            removed_items.append('entire caption')
                    
                    # 1. HASHTAG CLEANER - Remove all #hashtags
                    if filters.get('clean_hashtag', False) and filtered_text:
                        before = filtered_text
                        # Match #word patterns (supports unicode letters)
                        filtered_text = re.sub(r'#\w+', '', filtered_text)
                        if before != filtered_text:
                            removed_items.append('#hashtags')
                    
                    # 2. MENTION CLEANER - Remove all @mentions
                    if filters.get('clean_mention', False):
                        before = filtered_text
                        # Match @username patterns
                        filtered_text = re.sub(r'@\w+', '', filtered_text)
                        if before != filtered_text:
                            removed_items.append('@mentions')
                    
                    # 3. LINK CLEANER - Remove URLs from text
                    if filters.get('clean_link', False):
                        before = filtered_text
                        # Remove various URL formats
                        filtered_text = re.sub(r'https?://\S+', '', filtered_text)
                        filtered_text = re.sub(r'http?://\S+', '', filtered_text)
                        filtered_text = re.sub(r'www\.\S+', '', filtered_text)
                        filtered_text = re.sub(r't\.me/\S+', '', filtered_text)
                        filtered_text = re.sub(r'tg://\S+', '', filtered_text)
                        if before != filtered_text:
                            removed_items.append('links')
                    
                    # 4. EMOJI CLEANER - Remove all emojis from text
                    if filters.get('clean_emoji', False):
                        before = filtered_text
                        # Comprehensive emoji regex including variation selectors
                        emoji_pattern = re.compile(
                            "["
                            "\U0001F600-\U0001F64F"  # emoticons 😀-🙏
                            "\U0001F300-\U0001F5FF"  # symbols & pictographs 🌀-🗿
                            "\U0001F680-\U0001F6FF"  # transport 🚀-🛿
                            "\U0001F700-\U0001F77F"  # alchemical
                            "\U0001F780-\U0001F7FF"  # geometric
                            "\U0001F800-\U0001F8FF"  # arrows
                            "\U0001F900-\U0001F9FF"  # supplemental 🤀-🧿
                            "\U0001FA00-\U0001FA6F"  # chess
                            "\U0001FA70-\U0001FAFF"  # extended-a 🩰-🫿
                            "\U00002702-\U000027B0"  # dingbats ✂-➰
                            "\U0001F1E0-\U0001F1FF"  # flags 🇦-🇿
                            "\U00002600-\U000026FF"  # misc ☀-⛿
                            "\U00002700-\U000027BF"  # dingbats ✀-➿
                            "\U0001F000-\U0001F02F"  # mahjong
                            "\U0001F0A0-\U0001F0FF"  # cards
                            "\U0000FE00-\U0000FE0F"  # variation selectors
                            "\U0000FE0E-\U0000FE0F"  # text/emoji variation
                            "\U0000200D"             # zero width joiner
                            "\U00002640-\U00002642"  # gender ♀♂
                            "\U000023E9-\U000023F3"  # media ⏩-⏳
                            "\U000023F8-\U000023FA"  # media ⏸-⏺
                            "\U00002B50"             # star ⭐
                            "\U00002B55"             # circle ⭕
                            "\U00002934-\U00002935"  # arrows
                            "\U00002B05-\U00002B07"  # arrows
                            "\U00002B1B-\U00002B1C"  # squares
                            "\U00003030"             # wavy dash
                            "\U0000303D"             # part mark
                            "\U00003297"             # circled
                            "\U00003299"             # circled
                            "\U000024C2-\U0001F251"  # enclosed
                            "\U00002500-\U00002BEF"  # various
                            "\U0000231A-\U0000231B"  # watch ⌚⌛
                            "\U000025AA-\U000025AB"  # squares
                            "\U000025B6"             # play ▶
                            "\U000025C0"             # reverse ◀
                            "\U000025FB-\U000025FE"  # squares
                            "\U00002764"             # heart ❤
                            "\U00002763"             # heart exclamation ❣
                            "\U00002665"             # heart suit ♥
                            "\U0001F493-\U0001F49F"  # hearts 💓-💟
                            "\U00002714"             # check ✔
                            "\U00002716"             # x ✖
                            "\U0000270A-\U0000270D"  # hands ✊-✍
                            "\U000023CF"             # eject ⏏
                            "\U000023ED-\U000023EF"  # media ⏭-⏯
                            "\U000023F1-\U000023F2"  # timer ⏱⏲
                            "\U0000200B-\U0000200F"  # zero width chars
                            "\U00002028-\U0000202F"  # separators
                            "\U0000205F-\U0000206F"  # format chars
                            "]+",
                            flags=re.UNICODE
                        )
                        filtered_text = emoji_pattern.sub('', filtered_text)
                        if before != filtered_text:
                            removed_items.append('emojis')
                    
                    # 5. PHONE CLEANER - Remove phone numbers
                    if filters.get('clean_phone', False):
                        before = filtered_text
                        # Match various phone formats
                        filtered_text = re.sub(r'\+?\d{1,4}[-.\s]?\(?\d{1,3}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,4}[-.\s]?\d{1,9}', '', filtered_text)
                        filtered_text = re.sub(r'\+?\d{10,15}', '', filtered_text)  # Simple long numbers
                        if before != filtered_text:
                            removed_items.append('phones')
                    
                    # 6. EMAIL CLEANER - Remove email addresses
                    if filters.get('clean_email', False):
                        before = filtered_text
                        filtered_text = re.sub(r'[\w.+-]+@[\w-]+\.[\w.-]+', '', filtered_text)
                        if before != filtered_text:
                            removed_items.append('emails')
                    
                    # Clean up the filtered text
                    filtered_text = re.sub(r'[ \t]+', ' ', filtered_text)  # multiple spaces
                    filtered_text = re.sub(r'^ +| +$', '', filtered_text, flags=re.MULTILINE)  # line trim
                    filtered_text = re.sub(r'\n{3,}', '\n\n', filtered_text)  # multiple newlines
                    filtered_text = filtered_text.strip()
                    
                    # Store for copy mode
                    caption_text = filtered_text
                    caption_entities = None  # Entities invalid after text modification
                    
                    # Link preview removal
                    remove_link_preview = filters.get('clean_link', False)
                    
                    log.info(f"[{phone_ref}] MATCH rule {rule_id}: {msg_type}, mode={forward_mode}")
                    if removed_items:
                        log.info(f"[{phone_ref}] Cleaned: {', '.join(removed_items)}")
                        log.info(f"[{phone_ref}] Before: {original_text[:100]}")
                        log.info(f"[{phone_ref}] After: {filtered_text[:100]}")
                    
                    # Forward/Copy to ALL destinations
                    success_count = 0
                    for dest in dest_list:
                        try:
                            dest_entity = await resolve_dest(dest)
                            if dest_entity is None:
                                log.error(f"❌ [{phone_ref}] Could not resolve: {dest}")
                                continue
                            
                            if forward_mode == "copy":
                                # COPY MODE: Download & re-upload with filtered caption
                                
                                try:
                                    # Use filtered text (hashtags, mentions, links, emojis removed if filter ON)
                                    # caption_text and caption_entities are set above in filter section
                                    
                                    # Check if message has web preview (link preview card)
                                    has_web_preview = msg.web_preview is not None and not remove_link_preview
                                    
                                    if msg.media:
                                        # Check if media is ONLY a web preview (no photo/video/doc)
                                        is_only_web_preview = (
                                            msg.web_preview is not None and 
                                            not msg.photo and 
                                            not msg.video and 
                                            not msg.document and 
                                            not msg.audio and 
                                            not msg.voice and 
                                            not msg.sticker and 
                                            not msg.gif and
                                            not msg.video_note
                                        )
                                        
                                        if is_only_web_preview:
                                            # TEXT with hidden link + preview card
                                            await client.send_message(
                                                dest_entity,
                                                caption_text,
                                                formatting_entities=caption_entities if caption_entities else None,
                                                link_preview=not remove_link_preview  # Remove preview if link filter ON
                                            )
                                            success_count += 1
                                            log.info(f"🔗 [{phone_ref}] TEXT+PREVIEW -> {dest}")
                                        else:
                                            # HAS REAL MEDIA - Download and re-send with filtered caption
                                            import tempfile
                                            import os as temp_os
                                            
                                            # Download to temp file
                                            temp_file = await client.download_media(msg, file=tempfile.gettempdir())
                                            
                                            if temp_file is None:
                                                log.error(f"❌ [{phone_ref}] Download failed")
                                                if caption_text:
                                                    await client.send_message(
                                                        dest_entity, 
                                                        caption_text,
                                                        formatting_entities=caption_entities if caption_entities else None,
                                                        link_preview=has_web_preview
                                                    )
                                                continue
                                            
                                            try:
                                                # Common send parameters for filtered caption
                                                caption_kwargs = {}
                                                if caption_text:
                                                    caption_kwargs['caption'] = caption_text
                                                    if caption_entities:
                                                        caption_kwargs['formatting_entities'] = caption_entities
                                                
                                                # PHOTO with caption
                                                if msg.photo:
                                                    await client.send_file(
                                                        dest_entity,
                                                        temp_file,
                                                        force_document=False,
                                                        **caption_kwargs
                                                    )
                                                    log.info(f"📷 [{phone_ref}] PHOTO -> {dest}")
                                                
                                                # VIDEO NOTE (round video - no caption)
                                                elif msg.video_note:
                                                    await client.send_file(
                                                        dest_entity,
                                                        temp_file,
                                                        video_note=True
                                                    )
                                                    log.info(f"⭕ [{phone_ref}] VIDEO_NOTE -> {dest}")
                                                
                                                # VOICE MESSAGE
                                                elif msg.voice:
                                                    # Get voice duration
                                                    voice_attrs = []
                                                    if msg.document and types:
                                                        for attr in getattr(msg.document, 'attributes', []):
                                                            if hasattr(attr, 'duration'):
                                                                voice_attrs.append(types.DocumentAttributeAudio(
                                                                    duration=attr.duration,
                                                                    voice=True
                                                                ))
                                                                break
                                                    
                                                    await client.send_file(
                                                        dest_entity,
                                                        temp_file,
                                                        voice_note=True,
                                                        attributes=voice_attrs if voice_attrs else None
                                                    )
                                                    # Voice doesn't support caption, send separately
                                                    if original_text:
                                                        await client.send_message(
                                                            dest_entity, 
                                                            original_text,
                                                            formatting_entities=original_entities,
                                                            link_preview=has_web_preview
                                                        )
                                                    log.info(f"🎤 [{phone_ref}] VOICE -> {dest}")
                                                
                                                # VIDEO with caption
                                                elif msg.video:
                                                    # Get video attributes
                                                    video_attrs = []
                                                    if msg.document and types:
                                                        for attr in getattr(msg.document, 'attributes', []):
                                                            if hasattr(attr, 'duration') and hasattr(attr, 'w'):
                                                                video_attrs.append(types.DocumentAttributeVideo(
                                                                    duration=getattr(attr, 'duration', 0),
                                                                    w=getattr(attr, 'w', 1280),
                                                                    h=getattr(attr, 'h', 720),
                                                                    supports_streaming=True
                                                                ))
                                                                break
                                                    
                                                    await client.send_file(
                                                        dest_entity,
                                                        temp_file,
                                                        supports_streaming=True,
                                                        force_document=False,
                                                        attributes=video_attrs if video_attrs else None,
                                                        **caption_kwargs
                                                    )
                                                    log.info(f"🎥 [{phone_ref}] VIDEO -> {dest}")
                                                
                                                # GIF / Animation with caption
                                                elif msg.gif:
                                                    await client.send_file(
                                                        dest_entity,
                                                        temp_file,
                                                        force_document=False,
                                                        **caption_kwargs
                                                    )
                                                    log.info(f"🎞️ [{phone_ref}] GIF -> {dest}")
                                                
                                                # STICKER (no caption)
                                                elif msg.sticker:
                                                    await client.send_file(
                                                        dest_entity,
                                                        temp_file,
                                                        force_document=False
                                                    )
                                                    log.info(f"🎨 [{phone_ref}] STICKER -> {dest}")
                                                
                                                # AUDIO (music) with caption
                                                elif msg.audio:
                                                    # Get audio attributes (title, performer, duration)
                                                    audio_attrs = []
                                                    if msg.document and types:
                                                        duration = 0
                                                        title = None
                                                        performer = None
                                                        filename = None
                                                        for attr in getattr(msg.document, 'attributes', []):
                                                            if hasattr(attr, 'duration'):
                                                                duration = getattr(attr, 'duration', 0)
                                                            if hasattr(attr, 'title'):
                                                                title = getattr(attr, 'title', None)
                                                            if hasattr(attr, 'performer'):
                                                                performer = getattr(attr, 'performer', None)
                                                            if hasattr(attr, 'file_name'):
                                                                filename = getattr(attr, 'file_name', None)
                                                        
                                                        if duration or title or performer:
                                                            audio_attrs.append(types.DocumentAttributeAudio(
                                                                duration=duration,
                                                                title=title,
                                                                performer=performer,
                                                                voice=False
                                                            ))
                                                        if filename:
                                                            audio_attrs.append(types.DocumentAttributeFilename(filename))
                                                    
                                                    await client.send_file(
                                                        dest_entity,
                                                        temp_file,
                                                        force_document=False,
                                                        attributes=audio_attrs if audio_attrs else None,
                                                        **caption_kwargs
                                                    )
                                                    log.info(f"🎵 [{phone_ref}] AUDIO -> {dest}")
                                                
                                                # DOCUMENT (file) with caption
                                                elif msg.document:
                                                    # Get original filename
                                                    doc_attrs = []
                                                    if msg.document and types:
                                                        for attr in getattr(msg.document, 'attributes', []):
                                                            if hasattr(attr, 'file_name') and attr.file_name:
                                                                doc_attrs.append(types.DocumentAttributeFilename(attr.file_name))
                                                                break
                                                    
                                                    await client.send_file(
                                                        dest_entity,
                                                        temp_file,
                                                        force_document=True,
                                                        attributes=doc_attrs if doc_attrs else None,
                                                        **caption_kwargs
                                                    )
                                                    log.info(f"📄 [{phone_ref}] DOCUMENT -> {dest}")
                                                
                                                # OTHER MEDIA with caption
                                                else:
                                                    await client.send_file(
                                                        dest_entity,
                                                        temp_file,
                                                        **caption_kwargs
                                                    )
                                                    log.info(f"📎 [{phone_ref}] MEDIA -> {dest}")
                                                
                                                success_count += 1
                                                
                                            finally:
                                                # Clean up temp file
                                                try:
                                                    if temp_file and temp_os.path.exists(temp_file):
                                                        temp_os.remove(temp_file)
                                                except Exception:
                                                    pass
                                    
                                    else:
                                        # TEXT ONLY MESSAGE (no media, may have link preview)
                                        if caption_text:
                                            await client.send_message(
                                                dest_entity,
                                                caption_text,
                                                formatting_entities=caption_entities if caption_entities else None,
                                                link_preview=not remove_link_preview  # Remove preview if link filter ON
                                            )
                                            success_count += 1
                                            log.info(f"💬 [{phone_ref}] TEXT -> {dest}")
                                
                                except Exception as copy_err:
                                    log.error(f"❌ [{phone_ref}] Copy failed: {copy_err}")
                                    import traceback
                                    traceback.print_exc()
                                
                            else:
                                # FORWARD MODE: Keep original sender + forward header
                                await client.forward_messages(entity=dest_entity, messages=event.message)
                                success_count += 1
                                log.info(f"✅ [{phone_ref}] Forwarded -> {dest}")
                            
                        except Exception as e:
                            log.error(f"❌ [{phone_ref}] {forward_mode} to {dest} failed: {e}")
                    
                    if success_count > 0:
                        await db_ref.increment_forward_count(rule_id)
                        
            except Exception as e:
                log.exception(f"Handler error: {e}")
        
        self.handlers_attached.add(phone)
        log.info(f"✅ Handler attached for {phone}")
    
    async def cleanup(self):
        for phone in list(self.clients.keys()):
            await self.disconnect_client(phone)

# ==================== CONNECT STATE ====================
class ConnectState:
    IDLE = "idle"
    WAITING_PHONE = "waiting_phone"
    WAITING_CODE = "waiting_code"
    WAITING_PASSWORD = "waiting_password"
    ADD_RULE_SOURCE = "add_rule_source"
    ADD_RULE_DEST = "add_rule_dest"
    ADD_RULE_MODE = "add_rule_mode"  # Step 3: Forward mode
    ADD_RULE_FILTERS = "add_rule_filters"  # Step 4: Media filters
    ADD_RULE_CLEANER = "add_rule_cleaner"  # Step 5: Caption cleaner
    ADD_RULE_MODIFY = "add_rule_modify"  # Step 6: Modify content
    # Sub-states for modify content input
    MODIFY_RENAME = "modify_rename"
    MODIFY_BLOCK_WORDS = "modify_block_words"
    MODIFY_WHITELIST = "modify_whitelist"
    MODIFY_REPLACE = "modify_replace"
    MODIFY_HEADER = "modify_header"
    MODIFY_FOOTER = "modify_footer"
    MODIFY_BUTTONS = "modify_buttons"
    MODIFY_DELAY = "modify_delay"
    MODIFY_HISTORY = "modify_history"
    # Edit rule states
    EDIT_RULE_SOURCE = "edit_rule_source"
    EDIT_RULE_DEST = "edit_rule_dest"
    
    def __init__(self):
        self.step = self.IDLE
        self.phone: Optional[str] = None
        self.phone_code_hash: Optional[str] = None
        self.sources: List[str] = []
        self.destinations: List[str] = []
        self.forward_mode: str = "forward"  # "forward" or "copy"
        self.filters: Dict[str, bool] = DEFAULT_FILTERS.copy()
        self.modify: Dict = DEFAULT_MODIFY.copy()  # Step 6 settings
        self.edit_rule_id: Optional[int] = None  # For editing existing rule

# ==================== GLOBALS ====================
db: DatabaseManager = None
session_manager: UserSessionManager = None
connect_states: Dict[int, ConnectState] = {}
connect_locks: Dict[int, Lock] = {}

async def get_connect_lock(user_id: int) -> Lock:
    if user_id not in connect_locks:
        connect_locks[user_id] = Lock()
    return connect_locks[user_id]

# ==================== KEYBOARDS ====================
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Connect Account", callback_data="connect")],
        [InlineKeyboardButton("📋 My Rules", callback_data="rules")],
        [InlineKeyboardButton("📱 My Accounts", callback_data="accounts")],
        [InlineKeyboardButton("➕ Add Rule", callback_data="add_rule")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
    ])

def back_kb(callback: str = "main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=callback)]])

def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])

# ==================== HANDLERS ====================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await db.ensure_user(user.id, user.username, user.first_name)
    
    text = (
        f"👋 Welcome, {user.first_name}!\n\n"
        "🤖 *Auto-Forward Bot*\n\n"
        "• Connect your Telegram accounts\n"
        "• Create forwarding rules\n"
        "• Support multiple sources & destinations\n"
        "• Messages forward automatically 24/7\n\n"
        "Choose an option:"
    )
    
    if not TELETHON_AVAILABLE:
        text += "\n\n⚠️ _Telethon not installed_"
    
    await update.message.reply_text(text, reply_markup=main_menu_kb())

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    accounts = await db.get_user_accounts(user.id)
    
    if not accounts:
        await update.message.reply_text("📊 No accounts connected.")
        return
    
    text = "📊 *Status:*\n\n"
    for acc in accounts:
        phone = acc['phone']
        client = session_manager.clients.get(phone)
        status = "🟢" if client and client.is_connected() else "🔴"
        rules = await db.get_user_rules(user.id)
        count = len([r for r in rules if r['phone'] == phone and r['is_enabled']])
        text += f"{status} {phone} ({count} rules)\n"
    
    await update.message.reply_text(text)

async def cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    rules = await db.get_user_rules(user.id)
    
    if not rules:
        await update.message.reply_text("📋 No rules configured.")
        return
    
    text = "📋 *Your Rules:*\n\n"
    for i, r in enumerate(rules, 1):
        status = "✅" if r['is_enabled'] else "⏸️"
        src_count = len(r.get('source_list', []))
        dst_count = len(r.get('dest_list', []))
        text += f"{i}. {status} {src_count} sources → {dst_count} destinations\n"
        text += f"   📱 {r['phone']} | 📨 {r['forward_count']}\n\n"
    
    await update.message.reply_text(text)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
ℹ️ *Help*

*Commands:*
/start - Menu
/status - Account status  
/rules - Your rules
/help - This help

*How to use:*
1. Connect your Telegram account
2. Add forwarding rules
3. Done! Messages forward automatically

*Multiple Sources/Destinations:*
You can add multiple IDs separated by commas:

Sources: `-1001234567890, -1009876543210, @channel1`
Destinations: `@dest1, -1001111111111, 123456789`

*Format:*
• `@channelname` - public channel
• `-1001234567890` - channel ID

*Get Channel ID:*
Forward message to @userinfobot
    """
    await update.message.reply_text(text)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data
    
    log.info(f"Callback from {user.id}: {data}")
    
    try:
        await query.answer()
    except Exception:
        pass
    
    await db.ensure_user(user.id, user.username, user.first_name)
    
    try:
        if data == "main":
            await show_main_menu(query)
        elif data == "cancel":
            connect_states.pop(user.id, None)
            await query.edit_message_text("❌ Cancelled.", reply_markup=main_menu_kb())
        elif data == "connect":
            await start_connect_flow(query, user)
        elif data == "accounts":
            await show_accounts(query, user)
        elif data == "rules":
            await show_rules(query, user)
        elif data == "add_rule":
            await start_add_rule(query, user)
        elif data == "help":
            await show_help(query)
        elif data.startswith("acc_"):
            await handle_account_callback(query, user, data)
        elif data.startswith("rule_"):
            await handle_rule_callback(query, user, data)
        elif data.startswith("selphone_"):
            phone = data.replace("selphone_", "")
            await start_add_rule_for_phone(query, user, phone)
        elif data.startswith("mode_"):
            await handle_mode_selection(query, user, data)
        elif data.startswith("filter_"):
            await handle_filter_toggle(query, user, data)
        elif data == "filters_done":
            await handle_filters_done(query, user)
        elif data == "filters_all_on":
            await handle_filters_all(query, user, True)
        elif data == "filters_all_off":
            await handle_filters_all(query, user, False)
        elif data == "filters_back":
            await handle_filters_back(query, user)
        elif data == "goto_cleaner":
            await handle_goto_cleaner(query, user)
        elif data == "cleaner_done":
            await handle_cleaner_done(query, user)
        elif data == "cleaner_all_on":
            await handle_cleaner_all(query, user, True)
        elif data == "cleaner_all_off":
            await handle_cleaner_all(query, user, False)
        elif data == "cleaner_back":
            await handle_cleaner_back(query, user)
        # Step 6: Modify content callbacks
        elif data == "goto_modify":
            await handle_goto_modify(query, user)
        elif data == "modify_done":
            await handle_modify_done(query, user)
        elif data == "modify_back":
            await handle_modify_back(query, user)
        elif data == "modify_back_to_main":
            await handle_modify_back_to_main(query, user)
        elif data == "modify_rename":
            await handle_modify_rename(query, user)
        elif data == "modify_block":
            await handle_modify_block(query, user)
        elif data == "modify_whitelist":
            await handle_modify_whitelist(query, user)
        elif data == "modify_replace":
            await handle_modify_replace(query, user)
        elif data == "modify_header":
            await handle_modify_header(query, user)
        elif data == "modify_footer":
            await handle_modify_footer(query, user)
        elif data == "modify_buttons":
            await handle_modify_buttons(query, user)
        elif data == "modify_delay":
            await handle_modify_delay(query, user)
        elif data == "modify_history":
            await handle_modify_history(query, user)
        elif data.startswith("toggle_"):
            await handle_modify_toggle(query, user, data)
        elif data.startswith("delay_"):
            await handle_delay_set(query, user, data)
        elif data.startswith("history_"):
            await handle_history_set(query, user, data)
        elif data.startswith("clear_"):
            await handle_clear_option(query, user, data)
        elif data == "noop":
            # Do nothing - just a label button
            await query.answer()
        else:
            log.warning(f"Unknown callback: {data}")
            
    except BadRequest as e:
        log.error(f"BadRequest: {e}")
    except Exception as e:
        log.exception(f"Callback error: {e}")
        try:
            await query.edit_message_text(f"❌ Error: {e}", reply_markup=main_menu_kb())
        except Exception:
            pass

async def handle_mode_selection(query, user, data: str):
    """Handle forward mode selection (Step 3) -> Go to Step 4 (Filters)."""
    # Format: mode_forward or mode_copy
    mode = data.replace("mode_", "")
    
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state or state.step != ConnectState.ADD_RULE_MODE:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        state.forward_mode = mode
        state.step = ConnectState.ADD_RULE_FILTERS
        
        # Show filters selection (Step 4)
        await show_filters_keyboard(query, user, state)

def build_filters_keyboard(filters: dict) -> InlineKeyboardMarkup:
    """Build inline keyboard for filter toggles (Step 4)."""
    
    # All ignore filters with their descriptions
    ignore_filters = [
        ('document', '📄', 'Document'),
        ('video', '🎥', 'Video'),
        ('audio', '🎵', 'Audio'),
        ('sticker', '🎨', 'Sticker'),
        ('text', '💬', 'Text'),
        ('photo', '📷', 'Photo'),
        ('photo_only', '🖼️', 'Photo Only'),
        ('photo_with_text', '📝', 'Photo+Text'),
        ('album', '📚', 'Album'),
        ('poll', '📊', 'Poll'),
        ('voice', '🎤', 'Voice'),
        ('video_note', '⭕', 'Video Note'),
        ('gif', '🎞️', 'GIF'),
        ('emoji', '😀', 'Emoji'),
        ('forward', '↩️', 'Forwards'),
        ('reply', '💬', 'Reply'),
        ('link', '🔗', 'Link'),
        ('button', '🔘', 'Button'),
    ]
    
    buttons = []
    
    # Header
    buttons.append([InlineKeyboardButton("━━━ 🚫 IGNORE FILTERS ━━━", callback_data="noop")])
    
    # Filter buttons (3 per row)
    row = []
    for key, icon, label in ignore_filters:
        is_on = filters.get(key, False)
        status = "✅" if is_on else "⬜"
        btn_text = f"{status} {icon}"
        row.append(InlineKeyboardButton(btn_text, callback_data=f"filter_{key}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    
    # Legend
    buttons.append([InlineKeyboardButton("✅ = Ignore | ⬜ = Keep", callback_data="noop")])
    
    # Control buttons
    buttons.append([
        InlineKeyboardButton("✅ All ON", callback_data="filters_all_on"),
        InlineKeyboardButton("⬜ All OFF", callback_data="filters_all_off")
    ])
    
    # Navigation
    buttons.append([
        InlineKeyboardButton("✂️ Cleaner", callback_data="goto_cleaner"),
        InlineKeyboardButton("✅ Done", callback_data="filters_done")
    ])
    buttons.append([
        InlineKeyboardButton("🔙 Back", callback_data="filters_back"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel")
    ])
    
    return InlineKeyboardMarkup(buttons)


def build_cleaner_keyboard(filters: dict) -> InlineKeyboardMarkup:
    """Build inline keyboard for caption cleaner options (Step 5)."""
    
    buttons = []
    
    # Header
    buttons.append([InlineKeyboardButton("━━━ ✂️ CAPTION CLEANER ━━━", callback_data="noop")])
    
    # Remove entire caption option (prominent at top)
    caption_status = "✅" if filters.get('clean_caption', False) else "⬜"
    buttons.append([InlineKeyboardButton(f"{caption_status} ❌ Remove Entire Caption", callback_data="filter_clean_caption")])
    
    buttons.append([InlineKeyboardButton("─ Or remove specific items: ─", callback_data="noop")])
    
    cleaner_options = [
        ('clean_hashtag', '#️⃣', 'Hashtags'),
        ('clean_mention', '@', 'Mentions'),
        ('clean_link', '🔗', 'Links'),
        ('clean_emoji', '😀', 'Emojis'),
        ('clean_phone', '📞', 'Phones'),
        ('clean_email', '📧', 'Emails'),
    ]
    
    # Cleaner buttons (2 per row)
    row = []
    for key, icon, label in cleaner_options:
        is_on = filters.get(key, False)
        status = "✅" if is_on else "⬜"
        btn_text = f"{status} {icon} {label}"
        row.append(InlineKeyboardButton(btn_text, callback_data=f"filter_{key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    
    # Legend
    buttons.append([InlineKeyboardButton("✅ = Remove | ⬜ = Keep", callback_data="noop")])
    
    # Control buttons
    buttons.append([
        InlineKeyboardButton("✅ All ON", callback_data="cleaner_all_on"),
        InlineKeyboardButton("⬜ All OFF", callback_data="cleaner_all_off")
    ])
    
    # Navigation - Go to Step 6 (Modify)
    buttons.append([
        InlineKeyboardButton("🔙 Back", callback_data="cleaner_back"),
        InlineKeyboardButton("✏️ Modify", callback_data="goto_modify")
    ])
    buttons.append([
        InlineKeyboardButton("✅ Done", callback_data="cleaner_done"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel")
    ])
    
    return InlineKeyboardMarkup(buttons)


def build_modify_keyboard(modify: dict) -> InlineKeyboardMarkup:
    """Build inline keyboard for modify content options (Step 6)."""
    buttons = []
    
    # Header
    buttons.append([InlineKeyboardButton("━━━ ✏️ MODIFY CONTENT ━━━", callback_data="noop")])
    
    # Rename
    rename_status = "✅" if modify.get('rename_enabled') else "⬜"
    rename_pattern = modify.get('rename_pattern', '{original}')
    buttons.append([InlineKeyboardButton(f"{rename_status} 📝 Rename Files: {rename_pattern[:15]}", callback_data="modify_rename")])
    
    # Block Words
    block_status = "✅" if modify.get('block_words_enabled') else "⬜"
    block_count = len(modify.get('block_words', []))
    buttons.append([InlineKeyboardButton(f"{block_status} 🚫 Block Words ({block_count})", callback_data="modify_block")])
    
    # Whitelist
    whitelist_status = "✅" if modify.get('whitelist_enabled') else "⬜"
    whitelist_count = len(modify.get('whitelist_words', []))
    buttons.append([InlineKeyboardButton(f"{whitelist_status} ✅ Whitelist ({whitelist_count})", callback_data="modify_whitelist")])
    
    # Replace Words
    replace_status = "✅" if modify.get('replace_enabled') else "⬜"
    replace_count = len(modify.get('replace_pairs', []))
    buttons.append([InlineKeyboardButton(f"{replace_status} 🔄 Replace Words ({replace_count})", callback_data="modify_replace")])
    
    # Header/Footer
    header_status = "✅" if modify.get('header_enabled') else "⬜"
    footer_status = "✅" if modify.get('footer_enabled') else "⬜"
    buttons.append([
        InlineKeyboardButton(f"{header_status} 📌 Header", callback_data="modify_header"),
        InlineKeyboardButton(f"{footer_status} 📎 Footer", callback_data="modify_footer")
    ])
    
    # Link Buttons
    buttons_status = "✅" if modify.get('buttons_enabled') else "⬜"
    buttons_count = len(modify.get('buttons', []))
    buttons.append([InlineKeyboardButton(f"{buttons_status} 🔘 Link Buttons ({buttons_count})", callback_data="modify_buttons")])
    
    # Delay
    delay_status = "✅" if modify.get('delay_enabled') else "⬜"
    delay_sec = modify.get('delay_seconds', 0)
    buttons.append([InlineKeyboardButton(f"{delay_status} ⏱️ Delay ({delay_sec}s)", callback_data="modify_delay")])
    
    # History
    history_status = "✅" if modify.get('history_enabled') else "⬜"
    history_count = modify.get('history_count', 0)
    buttons.append([InlineKeyboardButton(f"{history_status} 📜 History ({history_count} msgs)", callback_data="modify_history")])
    
    # Navigation
    buttons.append([
        InlineKeyboardButton("🔙 Back", callback_data="modify_back"),
        InlineKeyboardButton("✅ Done", callback_data="modify_done")
    ])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    
    return InlineKeyboardMarkup(buttons)


async def show_modify_keyboard(query, user, state):
    """Show the modify content keyboard (Step 6)."""
    mode_text = "📤 Forward" if state.forward_mode == "forward" else "📋 Copy"
    
    # Count active modifications
    active_mods = sum([
        state.modify.get('rename_enabled', False),
        state.modify.get('block_words_enabled', False),
        state.modify.get('whitelist_enabled', False),
        state.modify.get('replace_enabled', False),
        state.modify.get('header_enabled', False),
        state.modify.get('footer_enabled', False),
        state.modify.get('buttons_enabled', False),
        state.modify.get('delay_enabled', False),
        state.modify.get('history_enabled', False),
    ])
    
    text = (
        f"*Step 6: Modify Content*\n\n"
        f"📱 Phone: `{state.phone}`\n"
        f"⚙️ Mode: {mode_text}\n"
        f"✏️ Modifications: {active_mods} active\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Configure content modifications:\n\n"
        f"Tap an option to configure:"
    )
    
    await query.edit_message_text(
        text,
        reply_markup=build_modify_keyboard(state.modify),
        parse_mode='Markdown'
    )


async def show_filters_keyboard(query, user, state):
    """Show the filters selection keyboard (Step 4)."""
    mode_text = "📤 Forward" if state.forward_mode == "forward" else "📋 Copy"
    
    # Count active filters
    ignore_keys = ['document', 'video', 'audio', 'sticker', 'text', 'photo', 
                   'photo_only', 'photo_with_text', 'album', 'poll', 'voice', 
                   'video_note', 'gif', 'emoji', 'forward', 'reply', 'link', 'button']
    active_ignores = sum(1 for k in ignore_keys if state.filters.get(k, False))
    
    text = (
        f"*Step 4: Ignore Filters*\n\n"
        f"📱 Phone: `{state.phone}`\n"
        f"📥 Sources: {len(state.sources)}\n"
        f"📤 Destinations: {len(state.destinations)}\n"
        f"⚙️ Mode: {mode_text}\n"
        f"🚫 Ignoring: {active_ignores} types\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Select message types to *IGNORE*:\n\n"
        f"✅ = Will be IGNORED (not forwarded)\n"
        f"⬜ = Will be FORWARDED\n\n"
        f"Tap to toggle:"
    )
    
    await query.edit_message_text(
        text,
        reply_markup=build_filters_keyboard(state.filters),
        parse_mode='Markdown'
    )


async def show_cleaner_keyboard(query, user, state):
    """Show the caption cleaner keyboard (Step 5)."""
    mode_text = "📤 Forward" if state.forward_mode == "forward" else "📋 Copy"
    
    # Count active cleaners
    cleaner_keys = ['clean_hashtag', 'clean_mention', 'clean_link', 
                    'clean_emoji', 'clean_phone', 'clean_email']
    active_cleaners = sum(1 for k in cleaner_keys if state.filters.get(k, False))
    
    text = (
        f"*Step 5: Caption Cleaner*\n\n"
        f"📱 Phone: `{state.phone}`\n"
        f"⚙️ Mode: {mode_text}\n"
        f"✂️ Cleaning: {active_cleaners} types\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Select what to *REMOVE* from captions:\n\n"
        f"✅ = Will be REMOVED from text\n"
        f"⬜ = Will be KEPT in text\n\n"
        f"Tap to toggle:"
    )
    
    await query.edit_message_text(
        text,
        reply_markup=build_cleaner_keyboard(state.filters)
    )

async def handle_filter_toggle(query, user, data: str):
    """Toggle a single filter on/off."""
    # Format: filter_text, filter_photo, filter_clean_hashtag, etc.
    filter_key = data.replace("filter_", "")
    
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state or state.step not in [ConnectState.ADD_RULE_FILTERS, ConnectState.ADD_RULE_CLEANER]:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        # Toggle the filter
        current = state.filters.get(filter_key, False)
        state.filters[filter_key] = not current
        
        # Update appropriate keyboard
        if state.step == ConnectState.ADD_RULE_CLEANER:
            await show_cleaner_keyboard(query, user, state)
        else:
            await show_filters_keyboard(query, user, state)

async def handle_filters_all(query, user, turn_on: bool):
    """Turn all ignore filters on or off."""
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state or state.step != ConnectState.ADD_RULE_FILTERS:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        # Only set ignore filters (not cleaner filters)
        ignore_keys = ['document', 'video', 'audio', 'sticker', 'text', 'photo', 
                       'photo_only', 'photo_with_text', 'album', 'poll', 'voice', 
                       'video_note', 'gif', 'emoji', 'forward', 'reply', 'link', 'button']
        for key in ignore_keys:
            state.filters[key] = turn_on
        
        # Update keyboard
        await show_filters_keyboard(query, user, state)

async def handle_cleaner_all(query, user, turn_on: bool):
    """Turn all cleaner filters on or off."""
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state or state.step != ConnectState.ADD_RULE_CLEANER:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        # Only set cleaner filters (excluding clean_caption which should be toggled separately)
        cleaner_keys = ['clean_hashtag', 'clean_mention', 'clean_link', 
                        'clean_emoji', 'clean_phone', 'clean_email']
        for key in cleaner_keys:
            state.filters[key] = turn_on
        
        # Update keyboard
        await show_cleaner_keyboard(query, user, state)

async def handle_goto_cleaner(query, user):
    """Go to cleaner step (Step 5)."""
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state or state.step != ConnectState.ADD_RULE_FILTERS:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        state.step = ConnectState.ADD_RULE_CLEANER
        await show_cleaner_keyboard(query, user, state)

async def handle_cleaner_back(query, user):
    """Go back from cleaner to filters (Step 4)."""
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state or state.step != ConnectState.ADD_RULE_CLEANER:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        state.step = ConnectState.ADD_RULE_FILTERS
        await show_filters_keyboard(query, user, state)

# ==================== STEP 6: MODIFY CONTENT HANDLERS ====================

async def handle_goto_modify(query, user):
    """Go to modify content step (Step 6)."""
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state or state.step not in [ConnectState.ADD_RULE_CLEANER, ConnectState.ADD_RULE_FILTERS]:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        state.step = ConnectState.ADD_RULE_MODIFY
        await show_modify_keyboard(query, user, state)

async def handle_modify_back(query, user):
    """Go back from modify to cleaner (Step 5)."""
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state or state.step not in [ConnectState.ADD_RULE_MODIFY, ConnectState.MODIFY_RENAME,
                                           ConnectState.MODIFY_BLOCK_WORDS, ConnectState.MODIFY_WHITELIST,
                                           ConnectState.MODIFY_REPLACE, ConnectState.MODIFY_HEADER,
                                           ConnectState.MODIFY_FOOTER, ConnectState.MODIFY_BUTTONS,
                                           ConnectState.MODIFY_DELAY, ConnectState.MODIFY_HISTORY]:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        state.step = ConnectState.ADD_RULE_CLEANER
        await show_cleaner_keyboard(query, user, state)

async def handle_modify_done(query, user):
    """Finish from modify and create the rule."""
    await finalize_rule_creation(query, user)

async def handle_modify_rename(query, user):
    """Configure file rename pattern."""
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state or state.step != ConnectState.ADD_RULE_MODIFY:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        state.step = ConnectState.MODIFY_RENAME
        current = state.modify.get('rename_pattern', '{original}')
        enabled = state.modify.get('rename_enabled', False)
        
        await query.edit_message_text(
            f"*📝 Rename Files*\n\n"
            f"Status: {'✅ Enabled' if enabled else '⬜ Disabled'}\n"
            f"Pattern: `{current}`\n\n"
            f"*Available patterns:*\n"
            f"• `{{original}}` - Original filename\n"
            f"• `{{date}}` - Current date (YYYY-MM-DD)\n"
            f"• `{{time}}` - Current time (HH-MM-SS)\n"
            f"• `{{random}}` - Random 6 characters\n"
            f"• `{{counter}}` - Incrementing number\n\n"
            f"*Example:*\n"
            f"`{{date}}_{{original}}` → `2024-01-15_photo.jpg`\n\n"
            f"Send new pattern or tap toggle:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{'🔴 Disable' if enabled else '🟢 Enable'}", callback_data="toggle_rename")],
                [InlineKeyboardButton("🔙 Back", callback_data="modify_back_to_main")]
            ]),
            parse_mode='Markdown'
        )

async def handle_modify_block(query, user):
    """Configure block words."""
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state or state.step != ConnectState.ADD_RULE_MODIFY:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        state.step = ConnectState.MODIFY_BLOCK_WORDS
        words = state.modify.get('block_words', [])
        enabled = state.modify.get('block_words_enabled', False)
        words_str = ', '.join(words[:10]) if words else 'None'
        
        await query.edit_message_text(
            f"*🚫 Block Words*\n\n"
            f"Status: {'✅ Enabled' if enabled else '⬜ Disabled'}\n"
            f"Words: {len(words)}\n"
            f"Current: `{words_str}`\n\n"
            f"Messages containing these words will be *SKIPPED*.\n\n"
            f"*Send words to block:*\n"
            f"One per line or comma-separated:\n"
            f"`spam, advertisement, promo`",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{'🔴 Disable' if enabled else '🟢 Enable'}", callback_data="toggle_block")],
                [InlineKeyboardButton("🗑️ Clear All", callback_data="clear_block")],
                [InlineKeyboardButton("🔙 Back", callback_data="modify_back_to_main")]
            ]),
            parse_mode='Markdown'
        )

async def handle_modify_whitelist(query, user):
    """Configure whitelist keywords."""
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state or state.step != ConnectState.ADD_RULE_MODIFY:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        state.step = ConnectState.MODIFY_WHITELIST
        words = state.modify.get('whitelist_words', [])
        enabled = state.modify.get('whitelist_enabled', False)
        words_str = ', '.join(words[:10]) if words else 'None'
        
        await query.edit_message_text(
            f"*✅ Whitelist Keywords*\n\n"
            f"Status: {'✅ Enabled' if enabled else '⬜ Disabled'}\n"
            f"Words: {len(words)}\n"
            f"Current: `{words_str}`\n\n"
            f"Only messages containing these words will be forwarded.\n"
            f"Messages WITHOUT these words will be *SKIPPED*.\n\n"
            f"*Send keywords:*\n"
            f"One per line or comma-separated:\n"
            f"`crypto, bitcoin, trading`",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{'🔴 Disable' if enabled else '🟢 Enable'}", callback_data="toggle_whitelist")],
                [InlineKeyboardButton("🗑️ Clear All", callback_data="clear_whitelist")],
                [InlineKeyboardButton("🔙 Back", callback_data="modify_back_to_main")]
            ]),
            parse_mode='Markdown'
        )

async def handle_modify_replace(query, user):
    """Configure word replacement."""
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state or state.step != ConnectState.ADD_RULE_MODIFY:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        state.step = ConnectState.MODIFY_REPLACE
        pairs = state.modify.get('replace_pairs', [])
        enabled = state.modify.get('replace_enabled', False)
        pairs_str = '\n'.join([f"• `{p['from']}` → `{p['to']}`" for p in pairs[:5]]) if pairs else 'None'
        
        await query.edit_message_text(
            f"*🔄 Replace Words*\n\n"
            f"Status: {'✅ Enabled' if enabled else '⬜ Disabled'}\n"
            f"Pairs: {len(pairs)}\n\n"
            f"Current:\n{pairs_str}\n\n"
            f"*Format:*\n"
            f"`old_word -> new_word`\n"
            f"or `old_word => new_word`\n\n"
            f"*Example:*\n"
            f"`@oldchannel -> @newchannel`\n"
            f"`http://old.com -> http://new.com`\n\n"
            f"Send replacement pairs (one per line):",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{'🔴 Disable' if enabled else '🟢 Enable'}", callback_data="toggle_replace")],
                [InlineKeyboardButton("🗑️ Clear All", callback_data="clear_replace")],
                [InlineKeyboardButton("🔙 Back", callback_data="modify_back_to_main")]
            ]),
            parse_mode='Markdown'
        )

async def handle_modify_header(query, user):
    """Configure header text."""
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state or state.step != ConnectState.ADD_RULE_MODIFY:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        state.step = ConnectState.MODIFY_HEADER
        header = state.modify.get('header_text', '')
        enabled = state.modify.get('header_enabled', False)
        
        await query.edit_message_text(
            f"*📌 Add Header*\n\n"
            f"Status: {'✅ Enabled' if enabled else '⬜ Disabled'}\n"
            f"Current: `{header[:50] if header else 'None'}`\n\n"
            f"This text will be added at the *BEGINNING* of every message.\n\n"
            f"*Supports formatting:*\n"
            f"• `**bold**` for bold\n"
            f"• `__italic__` for italic\n"
            f"• `{{newline}}` for line break\n\n"
            f"*Example:*\n"
            f"`📢 **ANNOUNCEMENT** {{newline}}`\n\n"
            f"Send header text:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{'🔴 Disable' if enabled else '🟢 Enable'}", callback_data="toggle_header")],
                [InlineKeyboardButton("🗑️ Clear", callback_data="clear_header")],
                [InlineKeyboardButton("🔙 Back", callback_data="modify_back_to_main")]
            ]),
            parse_mode='Markdown'
        )

async def handle_modify_footer(query, user):
    """Configure footer text."""
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state or state.step != ConnectState.ADD_RULE_MODIFY:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        state.step = ConnectState.MODIFY_FOOTER
        footer = state.modify.get('footer_text', '')
        enabled = state.modify.get('footer_enabled', False)
        
        await query.edit_message_text(
            f"*📎 Add Footer*\n\n"
            f"Status: {'✅ Enabled' if enabled else '⬜ Disabled'}\n"
            f"Current: `{footer[:50] if footer else 'None'}`\n\n"
            f"This text will be added at the *END* of every message.\n\n"
            f"*Supports formatting:*\n"
            f"• `**bold**` for bold\n"
            f"• `__italic__` for italic\n"
            f"• `{{newline}}` for line break\n\n"
            f"*Example:*\n"
            f"`{{newline}}━━━━━━{{newline}}📢 @YourChannel`\n\n"
            f"Send footer text:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{'🔴 Disable' if enabled else '🟢 Enable'}", callback_data="toggle_footer")],
                [InlineKeyboardButton("🗑️ Clear", callback_data="clear_footer")],
                [InlineKeyboardButton("🔙 Back", callback_data="modify_back_to_main")]
            ]),
            parse_mode='Markdown'
        )

async def handle_modify_buttons(query, user):
    """Configure link buttons."""
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state or state.step != ConnectState.ADD_RULE_MODIFY:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        state.step = ConnectState.MODIFY_BUTTONS
        buttons_list = state.modify.get('buttons', [])
        enabled = state.modify.get('buttons_enabled', False)
        
        buttons_str = ''
        for row in buttons_list[:3]:
            row_str = ' && '.join([f"{b['text']} - {b['url']}" for b in row])
            buttons_str += f"• {row_str}\n"
        if not buttons_str:
            buttons_str = 'None'
        
        await query.edit_message_text(
            f"*🔘 Link Buttons*\n\n"
            f"Status: {'✅ Enabled' if enabled else '⬜ Disabled'}\n"
            f"Rows: {len(buttons_list)}\n\n"
            f"Current:\n{buttons_str}\n\n"
            f"*Format:*\n"
            f"• Single button:\n"
            f"  `Button Text - https://link.com`\n\n"
            f"• Multiple buttons in one row:\n"
            f"  `Btn1 - url1 && Btn2 - url2`\n\n"
            f"• Multiple rows:\n"
            f"  `Row1 Btn - url1`\n"
            f"  `Row2 Btn - url2`\n\n"
            f"*Example:*\n"
            f"`📢 Join - https://t.me/channel && 💬 Chat - https://t.me/chat`\n\n"
            f"Send button configuration:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{'🔴 Disable' if enabled else '🟢 Enable'}", callback_data="toggle_buttons")],
                [InlineKeyboardButton("🗑️ Clear All", callback_data="clear_buttons")],
                [InlineKeyboardButton("🔙 Back", callback_data="modify_back_to_main")]
            ]),
            parse_mode='Markdown'
        )

async def handle_modify_delay(query, user):
    """Configure delay."""
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state or state.step != ConnectState.ADD_RULE_MODIFY:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        state.step = ConnectState.MODIFY_DELAY
        delay = state.modify.get('delay_seconds', 0)
        enabled = state.modify.get('delay_enabled', False)
        
        await query.edit_message_text(
            f"*⏱️ Delay*\n\n"
            f"Status: {'✅ Enabled' if enabled else '⬜ Disabled'}\n"
            f"Current: `{delay} seconds`\n\n"
            f"Messages will be delayed before forwarding.\n\n"
            f"*Quick options:*",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("5s", callback_data="delay_5"),
                    InlineKeyboardButton("30s", callback_data="delay_30"),
                    InlineKeyboardButton("1m", callback_data="delay_60"),
                    InlineKeyboardButton("5m", callback_data="delay_300")
                ],
                [
                    InlineKeyboardButton("10m", callback_data="delay_600"),
                    InlineKeyboardButton("30m", callback_data="delay_1800"),
                    InlineKeyboardButton("1h", callback_data="delay_3600"),
                    InlineKeyboardButton("Off", callback_data="delay_0")
                ],
                [InlineKeyboardButton(f"{'🔴 Disable' if enabled else '🟢 Enable'}", callback_data="toggle_delay")],
                [InlineKeyboardButton("🔙 Back", callback_data="modify_back_to_main")]
            ]),
            parse_mode='Markdown'
        )

async def handle_modify_history(query, user):
    """Configure history forwarding."""
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state or state.step != ConnectState.ADD_RULE_MODIFY:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        state.step = ConnectState.MODIFY_HISTORY
        count = state.modify.get('history_count', 0)
        enabled = state.modify.get('history_enabled', False)
        
        await query.edit_message_text(
            f"*📜 History*\n\n"
            f"Status: {'✅ Enabled' if enabled else '⬜ Disabled'}\n"
            f"Messages: `{count}`\n\n"
            f"Forward past messages when rule is created.\n"
            f"This will send the last N messages from source.\n\n"
            f"*Quick options:*",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("10", callback_data="history_10"),
                    InlineKeyboardButton("50", callback_data="history_50"),
                    InlineKeyboardButton("100", callback_data="history_100"),
                    InlineKeyboardButton("500", callback_data="history_500")
                ],
                [
                    InlineKeyboardButton("1000", callback_data="history_1000"),
                    InlineKeyboardButton("Off", callback_data="history_0")
                ],
                [InlineKeyboardButton(f"{'🔴 Disable' if enabled else '🟢 Enable'}", callback_data="toggle_history")],
                [InlineKeyboardButton("🔙 Back", callback_data="modify_back_to_main")]
            ]),
            parse_mode='Markdown'
        )

async def handle_modify_toggle(query, user, data: str):
    """Handle toggle buttons for modify options."""
    toggle_type = data.replace("toggle_", "")
    
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        # Map toggle type to modify key
        toggle_map = {
            'rename': 'rename_enabled',
            'block': 'block_words_enabled',
            'whitelist': 'whitelist_enabled',
            'replace': 'replace_enabled',
            'header': 'header_enabled',
            'footer': 'footer_enabled',
            'buttons': 'buttons_enabled',
            'delay': 'delay_enabled',
            'history': 'history_enabled',
        }
        
        key = toggle_map.get(toggle_type)
        if key:
            state.modify[key] = not state.modify.get(key, False)
        
        # Go back to modify main screen
        state.step = ConnectState.ADD_RULE_MODIFY
        await show_modify_keyboard(query, user, state)

async def handle_modify_back_to_main(query, user):
    """Go back from sub-option to modify main screen."""
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        state.step = ConnectState.ADD_RULE_MODIFY
        await show_modify_keyboard(query, user, state)

async def handle_delay_set(query, user, data: str):
    """Set delay value."""
    delay_sec = int(data.replace("delay_", ""))
    
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        state.modify['delay_seconds'] = delay_sec
        state.modify['delay_enabled'] = delay_sec > 0
        
        # Go back to modify main
        state.step = ConnectState.ADD_RULE_MODIFY
        await show_modify_keyboard(query, user, state)

async def handle_history_set(query, user, data: str):
    """Set history count."""
    count = int(data.replace("history_", ""))
    
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        state.modify['history_count'] = count
        state.modify['history_enabled'] = count > 0
        
        # Go back to modify main
        state.step = ConnectState.ADD_RULE_MODIFY
        await show_modify_keyboard(query, user, state)

async def handle_clear_option(query, user, data: str):
    """Clear specific modify option data."""
    option = data.replace("clear_", "")
    
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        clear_map = {
            'block': ('block_words', [], 'block_words_enabled'),
            'whitelist': ('whitelist_words', [], 'whitelist_enabled'),
            'replace': ('replace_pairs', [], 'replace_enabled'),
            'header': ('header_text', '', 'header_enabled'),
            'footer': ('footer_text', '', 'footer_enabled'),
            'buttons': ('buttons', [], 'buttons_enabled'),
        }
        
        if option in clear_map:
            key, default, enabled_key = clear_map[option]
            state.modify[key] = default
            state.modify[enabled_key] = False
        
        # Go back to modify main
        state.step = ConnectState.ADD_RULE_MODIFY
        await show_modify_keyboard(query, user, state)

async def handle_filters_back(query, user):
    """Go back to mode selection (Step 3)."""
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state or state.step != ConnectState.ADD_RULE_FILTERS:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        # Go back to mode selection
        state.step = ConnectState.ADD_RULE_MODE
        
        await query.edit_message_text(
            f"Step 4: Forward Mode\n\n"
            f"Sources: {len(state.sources)}\n"
            f"Destinations: {len(state.destinations)}\n\n"
            f"📤 Forward: Keep 'Forwarded from' header\n"
            f"📋 Copy: No forward header (re-upload media)\n\n"
            f"Choose mode:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📤 Forward", callback_data="mode_forward")],
                [InlineKeyboardButton("📋 Copy", callback_data="mode_copy")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
            ])
        )

async def handle_filters_done(query, user):
    """Finish from filters (skip cleaner) and create the rule."""
    await finalize_rule_creation(query, user)

async def handle_cleaner_done(query, user):
    """Finish from cleaner and create the rule."""
    await finalize_rule_creation(query, user)

async def finalize_rule_creation(query, user):
    """Create or update the rule with all settings."""
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state or state.step not in [ConnectState.ADD_RULE_FILTERS, ConnectState.ADD_RULE_CLEANER, ConnectState.ADD_RULE_MODIFY]:
            await query.edit_message_text("❌ Session expired.", reply_markup=main_menu_kb())
            return
        
        phone = state.phone
        sources = state.sources
        destinations = state.destinations
        mode = state.forward_mode
        filters_dict = state.filters
        modify_dict = state.modify
        edit_rule_id = state.edit_rule_id
        
        if edit_rule_id:
            # UPDATE existing rule
            await db.update_rule_filters(user.id, edit_rule_id, filters_dict)
            await db.update_rule_modify(user.id, edit_rule_id, modify_dict)
            rule_id = edit_rule_id
            action_text = "Updated"
        else:
            # CREATE new rule
            rule_id = await db.add_forward_rule(user.id, phone, sources, destinations, mode, filters_dict, modify_dict)
            action_text = "Created"
        
        # Ensure handler is attached/refreshed
        await session_manager.attach_forward_handler(phone)
        
        connect_states.pop(user.id, None)
        
        mode_text = "📤 Forward" if mode == "forward" else "📋 Copy"
        
        # Count enabled ignore filters
        ignore_keys = ['document', 'video', 'audio', 'sticker', 'text', 'photo', 
                       'photo_only', 'photo_with_text', 'album', 'poll', 'voice', 
                       'video_note', 'gif', 'emoji', 'forward', 'reply', 'link', 'button']
        active_ignores = sum(1 for k in ignore_keys if filters_dict.get(k, False))
        
        # Count enabled cleaner filters
        cleaner_keys = ['clean_caption', 'clean_hashtag', 'clean_mention', 'clean_link', 
                        'clean_emoji', 'clean_phone', 'clean_email']
        active_cleaners = sum(1 for k in cleaner_keys if filters_dict.get(k, False))
        
        # Count enabled modify options
        modify_keys = ['rename_enabled', 'block_words_enabled', 'whitelist_enabled',
                       'replace_enabled', 'header_enabled', 'footer_enabled',
                       'buttons_enabled', 'delay_enabled', 'history_enabled']
        active_mods = sum(1 for k in modify_keys if modify_dict.get(k, False))
        
        await query.edit_message_text(
            f"✅ *Rule {action_text}!*\n\n"
            f"📱 Phone: `{phone}`\n"
            f"📥 Sources: {len(sources)}\n"
            f"📤 Destinations: {len(destinations)}\n"
            f"⚙️ Mode: {mode_text}\n"
            f"🚫 Ignoring: {active_ignores} types\n"
            f"✂️ Cleaning: {active_cleaners} types\n"
            f"✏️ Modifying: {active_mods} options\n\n"
            "🚀 Active now!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 View Rules", callback_data="rules")],
                [InlineKeyboardButton("➕ Add Another", callback_data="add_rule")],
                [InlineKeyboardButton("🏠 Menu", callback_data="main")]
            ]),
            parse_mode='Markdown'
        )

async def show_main_menu(query):
    await query.edit_message_text(
        "🤖 Main Menu\n\nChoose an option:",
        reply_markup=main_menu_kb()
    )

async def show_help(query):
    text = """
ℹ️ *Help*

*Multiple Sources/Destinations:*
Enter IDs separated by commas:
`-1001234567890, @channel, -1009876543210`

*Format:*
• `@channelname` - public
• `-1001234567890` - ID

Get ID: forward msg to @userinfobot
    """
    await query.edit_message_text(text, reply_markup=back_kb())

async def start_connect_flow(query, user):
    if not TELETHON_AVAILABLE:
        await query.edit_message_text("❌ Telethon not installed.", reply_markup=main_menu_kb())
        return
    
    lock = await get_connect_lock(user.id)
    async with lock:
        state = ConnectState()
        state.step = ConnectState.WAITING_PHONE
        connect_states[user.id] = state
    
    await query.edit_message_text(
        "📱 *Connect Account*\n\n"
        "Send your phone number:\n"
        "Example: `+919876543210`",
        reply_markup=cancel_kb()
    )

async def show_accounts(query, user):
    accounts = await db.get_user_accounts(user.id)
    
    if not accounts:
        await query.edit_message_text(
            "📱 My Accounts\n\nNo accounts connected.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Connect", callback_data="connect")],
                [InlineKeyboardButton("🔙 Back", callback_data="main")]
            ])
        )
        return
    
    buttons = []
    for acc in accounts:
        phone = acc['phone']
        name = acc.get('display_name') or phone
        buttons.append([InlineKeyboardButton(f"📱 {name}", callback_data=f"acc_view_{phone}")])
    
    buttons.append([InlineKeyboardButton("🔗 Connect New", callback_data="connect")])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="main")])
    
    await query.edit_message_text(
        f"📱 My Accounts ({len(accounts)})\n\nSelect to manage:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def handle_account_callback(query, user, data: str):
    parts = data.split("_", 2)
    if len(parts) < 3:
        return
    
    action = parts[1]
    phone = parts[2]
    
    if action == "view":
        client = session_manager.clients.get(phone)
        status = "🟢 Connected" if client and client.is_connected() else "🔴 Disconnected"
        
        rules = await db.get_user_rules(user.id)
        rule_count = len([r for r in rules if r['phone'] == phone])
        
        await query.edit_message_text(
            f"📱 Account\n\n"
            f"Phone: {phone}\n"
            f"Status: {status}\n"
            f"Rules: {rule_count}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Rules", callback_data=f"acc_rules_{phone}")],
                [InlineKeyboardButton("🔌 Disconnect", callback_data=f"acc_disc_{phone}")],
                [InlineKeyboardButton("🔙 Back", callback_data="accounts")]
            ])
        )
    
    elif action == "rules":
        rules = await db.get_user_rules(user.id)
        phone_rules = [r for r in rules if r['phone'] == phone]
        
        if not phone_rules:
            await query.edit_message_text(
                f"📋 Rules for {phone}\n\nNo rules.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Add Rule", callback_data=f"selphone_{phone}")],
                    [InlineKeyboardButton("🔙 Back", callback_data=f"acc_view_{phone}")]
                ])
            )
            return
        
        buttons = []
        for r in phone_rules[:8]:
            status = "✅" if r['is_enabled'] else "⏸️"
            src_count = len(r.get('source_list', []))
            dst_count = len(r.get('dest_list', []))
            buttons.append([InlineKeyboardButton(
                f"{status} {src_count}src→{dst_count}dst",
                callback_data=f"rule_view_{r['id']}"
            )])
        
        buttons.append([InlineKeyboardButton("➕ Add", callback_data=f"selphone_{phone}")])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data=f"acc_view_{phone}")])
        
        await query.edit_message_text(
            f"📋 Rules for {phone}",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    
    elif action == "disc":
        await session_manager.disconnect_client(phone)
        await db.remove_account(user.id, phone)
        
        await query.edit_message_text(
            f"✅ {phone} disconnected.",
            reply_markup=back_kb("accounts")
        )

async def show_rules(query, user):
    rules = await db.get_user_rules(user.id)
    
    if not rules:
        await query.edit_message_text(
            "📋 My Rules\n\nNo rules configured.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Rule", callback_data="add_rule")],
                [InlineKeyboardButton("🔙 Back", callback_data="main")]
            ])
        )
        return
    
    buttons = []
    for idx, r in enumerate(rules[:10], 1):
        status = "✅" if r['is_enabled'] else "⏸️"
        src_count = len(r.get('source_list', []))
        dst_count = len(r.get('dest_list', []))
        mode_icon = "📤" if r.get('forward_mode', 'forward') == 'forward' else "📋"
        buttons.append([InlineKeyboardButton(
            f"{status} #{idx} | {src_count}→{dst_count} | {mode_icon}",
            callback_data=f"rule_view_{r['id']}"
        )])
    
    buttons.append([InlineKeyboardButton("➕ Add Rule", callback_data="add_rule")])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="main")])
    
    await query.edit_message_text(
        f"📋 My Rules ({len(rules)})\n\n"
        f"✅ = Active | ⏸️ = Paused\n"
        f"📤 = Forward | 📋 = Copy",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def handle_rule_callback(query, user, data: str):
    parts = data.split("_", 2)
    if len(parts) < 3:
        return
    
    action = parts[1]
    
    try:
        rule_id = int(parts[2])
    except ValueError:
        return
    
    if action == "view":
        rules = await db.get_user_rules(user.id)
        rule = next((r for r in rules if r['id'] == rule_id), None)
        
        if not rule:
            await query.edit_message_text("❌ Rule not found.", reply_markup=main_menu_kb())
            return
        
        status = "✅ Enabled" if rule['is_enabled'] else "⏸️ Paused"
        source_list = rule.get('source_list', [])
        dest_list = rule.get('dest_list', [])
        forward_mode = rule.get('forward_mode', 'forward')
        mode_text = "📤 Forward" if forward_mode == "forward" else "📋 Copy"
        filters = rule.get('filters', {})
        phone = rule['phone']
        
        # Try to get entity names
        async def get_entity_display(identifier: str) -> str:
            """Get display name for entity (name + id)."""
            try:
                client = session_manager.clients.get(phone)
                if client and client.is_connected():
                    if identifier.startswith('@'):
                        entity = await client.get_entity(identifier)
                        name = getattr(entity, 'title', None) or getattr(entity, 'first_name', '') or identifier
                        # Escape special characters in name
                        safe_name = safe_text(name)
                        return f"{safe_name} ({identifier})"
                    else:
                        try:
                            ent_id = int(identifier)
                            entity = await client.get_entity(ent_id)
                            name = getattr(entity, 'title', None) or getattr(entity, 'first_name', '') or 'Unknown'
                            # Escape special characters in name
                            safe_name = safe_text(name)
                            return f"{safe_name} ({identifier})"
                        except:
                            return identifier
            except:
                pass
            return identifier
        
        # Build source list with names
        source_displays = []
        for s in source_list[:5]:
            display = await get_entity_display(s)
            source_displays.append(display)
        
        # Build dest list with names
        dest_displays = []
        for d in dest_list[:5]:
            display = await get_entity_display(d)
            dest_displays.append(display)
        
        # Build filter summary
        filter_icons = {
            'text': '💬', 'photo': '📷', 'video': '🎥', 'document': '📄',
            'audio': '🎵', 'voice': '🎤', 'sticker': '🎨', 'gif': '🎞️',
            'video_note': '⭕', 'link': '🔗', 'hashtag': '#️⃣', 'mention': '@', 'emoji': '😀'
        }
        enabled = [filter_icons.get(k, k) for k, v in filters.items() if v]
        disabled = [filter_icons.get(k, k) for k, v in filters.items() if not v]
        
        text = f"📋 Rule #{rule_id}\n\n"
        text += f"📱 Phone: {phone}\n"
        text += f"📊 Status: {status}\n"
        text += f"⚙️ Mode: {mode_text}\n"
        text += f"📈 Forwards: {rule['forward_count']}\n\n"
        text += f"Sources ({len(source_list)}):\n"
        for s in source_displays:
            text += f"  • {s}\n"
        if len(source_list) > 5:
            text += f"  ...and {len(source_list)-5} more\n"
        text += f"\nDestinations ({len(dest_list)}):\n"
        for d in dest_displays:
            text += f"  • {d}\n"
        if len(dest_list) > 5:
            text += f"  ...and {len(dest_list)-5} more\n"
        
        # Show filters
        if enabled:
            text += f"\n🟢 Ignoring: {' '.join(enabled[:8])}"
            if len(enabled) > 8:
                text += f" +{len(enabled)-8}"
            text += "\n"
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔧 Change", callback_data=f"rule_change_{rule_id}"),
                    InlineKeyboardButton("⏯️ Toggle", callback_data=f"rule_toggle_{rule_id}"),
                ],
                [
                    InlineKeyboardButton("🗑️ Delete", callback_data=f"rule_del_{rule_id}"),
                    InlineKeyboardButton("🔙 Back", callback_data="rules")
                ]
            ])
        )
    
    elif action == "toggle":
        new_state = await db.toggle_rule(user.id, rule_id)
        if new_state is not None:
            status = "enabled ✅" if new_state else "paused ⏸️"
            await query.answer(f"Rule {status}")
            await handle_rule_callback(query, user, f"rule_view_{rule_id}")
        else:
            await query.answer("Rule not found")
    
    elif action == "del":
        success = await db.delete_rule(user.id, rule_id)
        if success:
            await query.edit_message_text("✅ Rule deleted.", reply_markup=back_kb("rules"))
        else:
            await query.answer("Failed to delete")
    
    elif action == "change":
        # Show change menu for this rule
        rules = await db.get_user_rules(user.id)
        rule = next((r for r in rules if r['id'] == rule_id), None)
        
        if not rule:
            await query.edit_message_text("❌ Rule not found.", reply_markup=main_menu_kb())
            return
        
        forward_mode = rule.get('forward_mode', 'forward')
        mode_text = "📤 Forward" if forward_mode == "forward" else "📋 Copy"
        source_list = rule.get('source_list', [])
        dest_list = rule.get('dest_list', [])
        
        # Build mode button with current mode marked as 🟢
        if forward_mode == "forward":
            mode_btn = InlineKeyboardButton("🟢 📤 Forward | 📋 Copy", callback_data=f"rule_chmode_copy_{rule_id}")
        else:
            mode_btn = InlineKeyboardButton("📤 Forward | 🟢 📋 Copy", callback_data=f"rule_chmode_forward_{rule_id}")
        
        await query.edit_message_text(
            f"🔧 Change Rule #{rule_id}\n\n"
            f"📱 Phone: {rule['phone']}\n"
            f"📥 Sources: {len(source_list)}\n"
            f"📤 Destinations: {len(dest_list)}\n"
            f"⚙️ Mode: {mode_text}\n\n"
            f"What do you want to change?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Sources", callback_data=f"rule_chsrc_{rule_id}")],
                [InlineKeyboardButton("📤 Destinations", callback_data=f"rule_chdst_{rule_id}")],
                [mode_btn],
                [InlineKeyboardButton("🚫 Filters", callback_data=f"rule_chfilter_{rule_id}")],
                [InlineKeyboardButton("✂️ Cleaner", callback_data=f"rule_chclean_{rule_id}")],
                [InlineKeyboardButton("✏️ Modify", callback_data=f"rule_chmodify_{rule_id}")],
                [InlineKeyboardButton("🔙 Back", callback_data=f"rule_view_{rule_id}")]
            ])
        )
    
    elif action == "chsrc":
        # Change sources
        rules = await db.get_user_rules(user.id)
        rule = next((r for r in rules if r['id'] == rule_id), None)
        if not rule:
            await query.answer("Rule not found")
            return
        
        lock = await get_connect_lock(user.id)
        async with lock:
            state = ConnectState()
            state.step = ConnectState.EDIT_RULE_SOURCE
            state.edit_rule_id = rule_id
            state.phone = rule['phone']
            state.sources = rule.get('source_list', [])
            state.destinations = rule.get('dest_list', [])
            state.forward_mode = rule.get('forward_mode', 'forward')
            state.filters = rule.get('filters', DEFAULT_FILTERS.copy())
            state.modify = rule.get('modify', DEFAULT_MODIFY.copy())
            connect_states[user.id] = state
        
        current_sources = ', '.join(rule.get('source_list', [])[:5])
        await query.edit_message_text(
            f"📥 Change Sources\n\n"
            f"Current sources:\n{current_sources}\n\n"
            f"Send new sources (comma-separated):\n"
            f"-1001234567890, @channel\n\n"
            f"Or send 'keep' to keep current sources.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data=f"rule_change_{rule_id}")]
            ])
        )
    
    elif action == "chdst":
        # Change destinations
        rules = await db.get_user_rules(user.id)
        rule = next((r for r in rules if r['id'] == rule_id), None)
        if not rule:
            await query.answer("Rule not found")
            return
        
        lock = await get_connect_lock(user.id)
        async with lock:
            state = ConnectState()
            state.step = ConnectState.EDIT_RULE_DEST
            state.edit_rule_id = rule_id
            state.phone = rule['phone']
            state.sources = rule.get('source_list', [])
            state.destinations = rule.get('dest_list', [])
            state.forward_mode = rule.get('forward_mode', 'forward')
            state.filters = rule.get('filters', DEFAULT_FILTERS.copy())
            state.modify = rule.get('modify', DEFAULT_MODIFY.copy())
            connect_states[user.id] = state
        
        current_dests = ', '.join(rule.get('dest_list', [])[:5])
        await query.edit_message_text(
            f"📤 Change Destinations\n\n"
            f"Current destinations:\n{current_dests}\n\n"
            f"Send new destinations (comma-separated):\n"
            f"-1001234567890, @channel\n\n"
            f"Or send 'keep' to keep current destinations.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data=f"rule_change_{rule_id}")]
            ])
        )
    
    elif action.startswith("chmode_"):
        # Change forward mode
        new_mode = action.replace("chmode_", "")  # "forward" or "copy"
        success = await db.update_rule_mode(user.id, rule_id, new_mode)
        if success:
            mode_text = "📤 Forward" if new_mode == "forward" else "📋 Copy"
            await query.answer(f"Mode changed to {mode_text}")
            # Reload handler
            rules = await db.get_user_rules(user.id)
            rule = next((r for r in rules if r['id'] == rule_id), None)
            if rule:
                await session_manager.attach_forward_handler(rule['phone'])
        await handle_rule_callback(query, user, f"rule_change_{rule_id}")
    
    elif action == "chfilter":
        # Change filters
        rules = await db.get_user_rules(user.id)
        rule = next((r for r in rules if r['id'] == rule_id), None)
        if not rule:
            await query.answer("Rule not found")
            return
        
        lock = await get_connect_lock(user.id)
        async with lock:
            state = ConnectState()
            state.step = ConnectState.ADD_RULE_FILTERS
            state.edit_rule_id = rule_id
            state.phone = rule['phone']
            state.sources = rule.get('source_list', [])
            state.destinations = rule.get('dest_list', [])
            state.forward_mode = rule.get('forward_mode', 'forward')
            state.filters = rule.get('filters', DEFAULT_FILTERS.copy())
            state.modify = rule.get('modify', DEFAULT_MODIFY.copy())
            connect_states[user.id] = state
        
        await show_filters_keyboard(query, user, state)
    
    elif action == "chclean":
        # Change cleaner
        rules = await db.get_user_rules(user.id)
        rule = next((r for r in rules if r['id'] == rule_id), None)
        if not rule:
            await query.answer("Rule not found")
            return
        
        lock = await get_connect_lock(user.id)
        async with lock:
            state = ConnectState()
            state.step = ConnectState.ADD_RULE_CLEANER
            state.edit_rule_id = rule_id
            state.phone = rule['phone']
            state.sources = rule.get('source_list', [])
            state.destinations = rule.get('dest_list', [])
            state.forward_mode = rule.get('forward_mode', 'forward')
            state.filters = rule.get('filters', DEFAULT_FILTERS.copy())
            state.modify = rule.get('modify', DEFAULT_MODIFY.copy())
            connect_states[user.id] = state
        
        await show_cleaner_keyboard(query, user, state)
    
    elif action == "chmodify":
        # Change modify content
        rules = await db.get_user_rules(user.id)
        rule = next((r for r in rules if r['id'] == rule_id), None)
        if not rule:
            await query.answer("Rule not found")
            return
        
        lock = await get_connect_lock(user.id)
        async with lock:
            state = ConnectState()
            state.step = ConnectState.ADD_RULE_MODIFY
            state.edit_rule_id = rule_id
            state.phone = rule['phone']
            state.sources = rule.get('source_list', [])
            state.destinations = rule.get('dest_list', [])
            state.forward_mode = rule.get('forward_mode', 'forward')
            state.filters = rule.get('filters', DEFAULT_FILTERS.copy())
            state.modify = rule.get('modify', DEFAULT_MODIFY.copy())
            connect_states[user.id] = state
        
        await show_modify_keyboard(query, user, state)

async def start_add_rule(query, user):
    accounts = await db.get_user_accounts(user.id)
    
    if not accounts:
        await query.edit_message_text(
            "❌ No accounts connected.\n\nConnect an account first!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Connect", callback_data="connect")],
                [InlineKeyboardButton("🔙 Back", callback_data="main")]
            ])
        )
        return
    
    buttons = []
    for acc in accounts:
        phone = acc['phone']
        buttons.append([InlineKeyboardButton(f"📱 {phone}", callback_data=f"selphone_{phone}")])
    
    buttons.append([InlineKeyboardButton("🔙 Cancel", callback_data="main")])
    
    await query.edit_message_text(
        "➕ Add Rule\n\nSelect account:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def start_add_rule_for_phone(query, user, phone: str):
    client = session_manager.clients.get(phone)
    if not client or not client.is_connected():
        await query.edit_message_text(
            f"❌ Account {phone} not connected.",
            reply_markup=back_kb("accounts")
        )
        return
    
    lock = await get_connect_lock(user.id)
    async with lock:
        state = ConnectState()
        state.step = ConnectState.ADD_RULE_SOURCE
        state.phone = phone
        state.sources = []
        connect_states[user.id] = state
    
    await query.edit_message_text(
        f"➕ Add Rule for {phone}\n\n"
        "*Step 1:* Enter SOURCES\n\n"
        "Where messages come FROM.\n"
        "You can enter multiple IDs separated by commas:\n\n"
        "`-1001234567890, -1009876543210, @channel`\n\n"
        "*Format:*\n"
        "• `@channelname`\n"
        "• `-1001234567890`",
        reply_markup=cancel_kb()
    )

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    
    user = update.effective_user
    if not user:
        return
    
    text = update.message.text.strip()
    
    if text.startswith('/'):
        return
    
    lock = await get_connect_lock(user.id)
    async with lock:
        state = connect_states.get(user.id)
        if not state:
            return
        
        try:
            if state.step == ConnectState.WAITING_PHONE:
                await handle_phone_input(update, user, state, text)
            elif state.step == ConnectState.WAITING_CODE:
                await handle_code_input(update, user, state, text)
            elif state.step == ConnectState.WAITING_PASSWORD:
                await handle_password_input(update, user, state, text)
            elif state.step == ConnectState.ADD_RULE_SOURCE:
                await handle_source_input(update, user, state, text)
            elif state.step == ConnectState.ADD_RULE_DEST:
                await handle_dest_input(update, user, state, text)
            # Modify content input handlers
            elif state.step == ConnectState.MODIFY_RENAME:
                await handle_modify_rename_input(update, user, state, text)
            elif state.step == ConnectState.MODIFY_BLOCK_WORDS:
                await handle_modify_block_input(update, user, state, text)
            elif state.step == ConnectState.MODIFY_WHITELIST:
                await handle_modify_whitelist_input(update, user, state, text)
            elif state.step == ConnectState.MODIFY_REPLACE:
                await handle_modify_replace_input(update, user, state, text)
            elif state.step == ConnectState.MODIFY_HEADER:
                await handle_modify_header_input(update, user, state, text)
            elif state.step == ConnectState.MODIFY_FOOTER:
                await handle_modify_footer_input(update, user, state, text)
            elif state.step == ConnectState.MODIFY_BUTTONS:
                await handle_modify_buttons_input(update, user, state, text)
            # Edit rule handlers
            elif state.step == ConnectState.EDIT_RULE_SOURCE:
                await handle_edit_source_input(update, user, state, text)
            elif state.step == ConnectState.EDIT_RULE_DEST:
                await handle_edit_dest_input(update, user, state, text)
        except Exception as e:
            log.exception(f"Message handler error: {e}")
            connect_states.pop(user.id, None)
            await update.message.reply_text(f"❌ Error: {e}", reply_markup=main_menu_kb())

async def handle_phone_input(update, user, state, phone: str):
    if not phone.startswith("+") or len(phone) < 8:
        await update.message.reply_text(
            "❌ Invalid format.\n\nUse: `+919876543210`",
            reply_markup=cancel_kb()
        )
        return
    
    state.phone = phone
    await update.message.reply_text(f"📤 Sending code to {phone}...")
    
    try:
        client = await session_manager.get_or_create_client(user.id, phone)
        sent = await client.send_code_request(phone)
        
        state.phone_code_hash = sent.phone_code_hash
        state.step = ConnectState.WAITING_CODE
        
        await update.message.reply_text(
            "✅ Code sent!\n\nEnter the code:",
            reply_markup=cancel_kb()
        )
    except errors.FloodWaitError as e:
        connect_states.pop(user.id, None)
        await update.message.reply_text(f"⚠️ Rate limited. Wait {e.seconds}s", reply_markup=main_menu_kb())
    except Exception as e:
        log.exception("send_code failed")
        connect_states.pop(user.id, None)
        await update.message.reply_text(f"❌ Failed: {e}", reply_markup=main_menu_kb())

async def handle_code_input(update, user, state, code: str):
    code = code.replace(" ", "").replace("-", "")
    phone = state.phone
    
    try:
        client = await session_manager.get_or_create_client(user.id, phone)
        await client.sign_in(phone=phone, code=code, phone_code_hash=state.phone_code_hash)
        
        me = await client.get_me()
        name = f"{me.first_name or ''} {me.last_name or ''}".strip() or phone
        
        await db.add_connected_account(user.id, phone, name)
        await session_manager.attach_forward_handler(phone)
        
        connect_states.pop(user.id, None)
        
        await update.message.reply_text(
            f"✅ Connected!\n\nAccount: {name}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Rule", callback_data=f"selphone_{phone}")],
                [InlineKeyboardButton("🏠 Menu", callback_data="main")]
            ])
        )
    except errors.SessionPasswordNeededError:
        state.step = ConnectState.WAITING_PASSWORD
        await update.message.reply_text("🔐 Enter 2FA password:", reply_markup=cancel_kb())
    except errors.PhoneCodeInvalidError:
        await update.message.reply_text("❌ Invalid code. Try again:", reply_markup=cancel_kb())
    except errors.PhoneCodeExpiredError:
        connect_states.pop(user.id, None)
        await update.message.reply_text("❌ Code expired.", reply_markup=main_menu_kb())
    except Exception as e:
        log.exception("sign_in failed")
        connect_states.pop(user.id, None)
        await update.message.reply_text(f"❌ Failed: {e}", reply_markup=main_menu_kb())

async def handle_password_input(update, user, state, password: str):
    phone = state.phone
    
    try:
        client = await session_manager.get_or_create_client(user.id, phone)
        await client.sign_in(password=password)
        
        me = await client.get_me()
        name = f"{me.first_name or ''} {me.last_name or ''}".strip() or phone
        
        await db.add_connected_account(user.id, phone, name)
        await session_manager.attach_forward_handler(phone)
        
        connect_states.pop(user.id, None)
        
        await update.message.reply_text(
            f"✅ Connected with 2FA!\n\nAccount: {name}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Rule", callback_data=f"selphone_{phone}")],
                [InlineKeyboardButton("🏠 Menu", callback_data="main")]
            ])
        )
    except Exception as e:
        log.exception("2FA failed")
        connect_states.pop(user.id, None)
        await update.message.reply_text(f"❌ 2FA failed: {e}", reply_markup=main_menu_kb())

async def handle_source_input(update, user, state, text: str):
    phone = state.phone
    
    # Parse multiple sources
    sources = parse_multi_ids(text)
    
    if not sources:
        await update.message.reply_text(
            "❌ No valid sources found.\n\n"
            "Enter IDs separated by commas:\n"
            "`-1001234567890, @channel, -1009876543210`",
            reply_markup=cancel_kb()
        )
        return
    
    # Validate format
    invalid = []
    valid = []
    for src in sources:
        is_username = src.startswith('@')
        is_numeric = src.lstrip('-').isdigit()
        if is_username or is_numeric:
            valid.append(src)
        else:
            invalid.append(src)
    
    if invalid:
        await update.message.reply_text(
            f"⚠️ Invalid format: {', '.join(invalid)}\n\n"
            "Use `@username` or `-1001234567890`",
            reply_markup=cancel_kb()
        )
        return
    
    # Verify sources (optional)
    await update.message.reply_text(f"🔍 Validating {len(valid)} sources...")
    
    verified = []
    failed = []
    for src in valid:
        success, entity, error = await session_manager.resolve_entity(phone, src)
        if success:
            verified.append(src)
        else:
            failed.append(f"{src}: {error}")
    
    state.sources = valid  # Keep all, even unverified
    state.step = ConnectState.ADD_RULE_DEST
    
    msg = f"✅ Sources: {len(valid)} total\n"
    if verified:
        msg += f"✓ Verified: {len(verified)}\n"
    if failed:
        msg += f"⚠️ Could not verify: {len(failed)}\n"
        for f in failed[:3]:
            msg += f"  • {f}\n"
    
    msg += "\n*Step 2:* Enter DESTINATIONS\n\n"
    msg += "Where messages will be forwarded TO.\n"
    msg += "Enter multiple IDs separated by commas:\n\n"
    msg += "`@dest1, -1001111111111, 123456789`"
    
    await update.message.reply_text(msg, reply_markup=cancel_kb())

async def handle_dest_input(update, user, state, text: str):
    phone = state.phone
    sources = state.sources
    
    # Parse multiple destinations
    destinations = parse_multi_ids(text)
    
    if not destinations:
        await update.message.reply_text(
            "❌ No valid destinations found.\n\n"
            "Enter IDs separated by commas:\n"
            "`@dest1, -1001111111111, 123456789`",
            reply_markup=cancel_kb()
        )
        return
    
    # Validate format
    invalid = []
    valid = []
    for dst in destinations:
        is_username = dst.startswith('@')
        is_numeric = dst.lstrip('-').isdigit()
        if is_username or is_numeric:
            valid.append(dst)
        else:
            invalid.append(dst)
    
    if invalid:
        await update.message.reply_text(
            f"⚠️ Invalid format: {', '.join(invalid)}\n\n"
            "Use `@username` or `-1001234567890`",
            reply_markup=cancel_kb()
        )
        return
    
    # Verify destinations
    await update.message.reply_text(f"🔍 Validating {len(valid)} destinations...")
    
    verified = []
    failed = []
    for dst in valid:
        success, entity, error = await session_manager.resolve_entity(phone, dst)
        if success:
            verified.append(dst)
        else:
            failed.append(f"{dst}: {error}")
    
    if failed:
        msg = f"⚠️ Could not verify {len(failed)} destinations:\n"
        for f in failed[:3]:
            msg += f"  • {f}\n"
        await update.message.reply_text(msg)
    
    # Save destinations and go to Step 3
    state.destinations = valid
    state.step = ConnectState.ADD_RULE_MODE
    
    await update.message.reply_text(
        f"✅ Destinations: {len(valid)} total\n\n"
        f"Step 3: Select Forward Mode\n\n"
        "Choose how messages should be sent:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Forward", callback_data="mode_forward")],
            [InlineKeyboardButton("📋 Copy", callback_data="mode_copy")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
        ])
    )
    
    # Send explanation
    await update.message.reply_text(
        "*📤 Forward:*\n"
        "• Keeps original sender info\n"
        "• Shows 'Forwarded from' header\n"
        "• Fast, no re-upload needed\n\n"
        "*📋 Copy:*\n"
        "• Sends as YOUR message\n"
        "• No forward header shown\n"
        "• Downloads & re-uploads media\n"
        "• Appears as original content"
    )

# ==================== MODIFY INPUT HANDLERS ====================

async def handle_modify_rename_input(update, user, state, text: str):
    """Handle rename pattern input."""
    state.modify['rename_pattern'] = text.strip()
    state.modify['rename_enabled'] = True
    state.step = ConnectState.ADD_RULE_MODIFY
    
    await update.message.reply_text(
        f"✅ Rename pattern set: `{text.strip()}`",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Modify", callback_data="modify_back_to_main")]
        ]),
        parse_mode='Markdown'
    )

async def handle_modify_block_input(update, user, state, text: str):
    """Handle block words input."""
    words = [w.strip() for w in text.replace('\n', ',').split(',') if w.strip()]
    existing = state.modify.get('block_words', [])
    state.modify['block_words'] = list(set(existing + words))
    state.modify['block_words_enabled'] = True
    state.step = ConnectState.ADD_RULE_MODIFY
    
    await update.message.reply_text(
        f"✅ Added {len(words)} block words.\nTotal: {len(state.modify['block_words'])}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Modify", callback_data="modify_back_to_main")]
        ])
    )

async def handle_modify_whitelist_input(update, user, state, text: str):
    """Handle whitelist keywords input."""
    words = [w.strip() for w in text.replace('\n', ',').split(',') if w.strip()]
    existing = state.modify.get('whitelist_words', [])
    state.modify['whitelist_words'] = list(set(existing + words))
    state.modify['whitelist_enabled'] = True
    state.step = ConnectState.ADD_RULE_MODIFY
    
    await update.message.reply_text(
        f"✅ Added {len(words)} whitelist keywords.\nTotal: {len(state.modify['whitelist_words'])}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Modify", callback_data="modify_back_to_main")]
        ])
    )

async def handle_modify_replace_input(update, user, state, text: str):
    """Handle word replacement input."""
    lines = text.strip().split('\n')
    pairs = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        if ' -> ' in line:
            parts = line.split(' -> ', 1)
        elif ' => ' in line:
            parts = line.split(' => ', 1)
        elif '->' in line:
            parts = line.split('->', 1)
        elif '=>' in line:
            parts = line.split('=>', 1)
        else:
            continue
        
        if len(parts) == 2:
            pairs.append({'from': parts[0].strip(), 'to': parts[1].strip(), 'regex': False})
    
    if pairs:
        existing = state.modify.get('replace_pairs', [])
        state.modify['replace_pairs'] = existing + pairs
        state.modify['replace_enabled'] = True
    
    state.step = ConnectState.ADD_RULE_MODIFY
    
    await update.message.reply_text(
        f"✅ Added {len(pairs)} replacement pairs.\nTotal: {len(state.modify.get('replace_pairs', []))}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Modify", callback_data="modify_back_to_main")]
        ])
    )

async def handle_modify_header_input(update, user, state, text: str):
    """Handle header text input."""
    header = text.strip().replace('{newline}', '\n')
    state.modify['header_text'] = header
    state.modify['header_enabled'] = True
    state.step = ConnectState.ADD_RULE_MODIFY
    
    await update.message.reply_text(
        f"✅ Header set",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Modify", callback_data="modify_back_to_main")]
        ])
    )

async def handle_modify_footer_input(update, user, state, text: str):
    """Handle footer text input."""
    footer = text.strip().replace('{newline}', '\n')
    state.modify['footer_text'] = footer
    state.modify['footer_enabled'] = True
    state.step = ConnectState.ADD_RULE_MODIFY
    
    await update.message.reply_text(
        f"✅ Footer set",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Modify", callback_data="modify_back_to_main")]
        ])
    )

async def handle_modify_buttons_input(update, user, state, text: str):
    """Handle link buttons input."""
    lines = text.strip().split('\n')
    buttons_rows = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        row_buttons = []
        parts = line.split('&&')
        
        for part in parts:
            part = part.strip()
            if ' - ' in part:
                btn_parts = part.split(' - ', 1)
                if len(btn_parts) == 2:
                    btn_text = btn_parts[0].strip()
                    btn_url = btn_parts[1].strip()
                    if btn_url.startswith('http') or btn_url.startswith('t.me') or btn_url.startswith('tg://'):
                        if not btn_url.startswith('http'):
                            btn_url = 'https://' + btn_url
                        row_buttons.append({'text': btn_text, 'url': btn_url})
        
        if row_buttons:
            buttons_rows.append(row_buttons)
    
    if buttons_rows:
        state.modify['buttons'] = buttons_rows
        state.modify['buttons_enabled'] = True
    
    state.step = ConnectState.ADD_RULE_MODIFY
    
    await update.message.reply_text(
        f"✅ Added {len(buttons_rows)} button rows",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Modify", callback_data="modify_back_to_main")]
        ])
    )

# ==================== EDIT RULE INPUT HANDLERS ====================

async def handle_edit_source_input(update, user, state, text: str):
    """Handle edited sources input."""
    rule_id = state.edit_rule_id
    
    if text.lower() == 'keep':
        # Keep current sources
        connect_states.pop(user.id, None)
        await update.message.reply_text(
            "✅ Sources unchanged.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data=f"rule_change_{rule_id}")]
            ])
        )
        return
    
    # Parse new sources
    sources = parse_multi_ids(text)
    if not sources:
        await update.message.reply_text(
            "❌ No valid sources found.\n\nTry again or send `keep`.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data=f"rule_change_{rule_id}")]
            ])
        )
        return
    
    # Update database
    success = await db.update_rule_sources(user.id, rule_id, sources)
    connect_states.pop(user.id, None)
    
    if success:
        # Refresh handler
        await session_manager.attach_forward_handler(state.phone)
        await update.message.reply_text(
            f"✅ Sources updated! ({len(sources)} sources)",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data=f"rule_view_{rule_id}")]
            ])
        )
    else:
        await update.message.reply_text("❌ Failed to update.", reply_markup=main_menu_kb())

async def handle_edit_dest_input(update, user, state, text: str):
    """Handle edited destinations input."""
    rule_id = state.edit_rule_id
    
    if text.lower() == 'keep':
        connect_states.pop(user.id, None)
        await update.message.reply_text(
            "✅ Destinations unchanged.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data=f"rule_change_{rule_id}")]
            ])
        )
        return
    
    destinations = parse_multi_ids(text)
    if not destinations:
        await update.message.reply_text(
            "❌ No valid destinations found.\n\nTry again or send `keep`.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data=f"rule_change_{rule_id}")]
            ])
        )
        return
    
    success = await db.update_rule_destinations(user.id, rule_id, destinations)
    connect_states.pop(user.id, None)
    
    if success:
        await session_manager.attach_forward_handler(state.phone)
        await update.message.reply_text(
            f"✅ Destinations updated! ({len(destinations)} destinations)",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data=f"rule_view_{rule_id}")]
            ])
        )
    else:
        await update.message.reply_text("❌ Failed to update.", reply_markup=main_menu_kb())

# ==================== ERROR HANDLER ====================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error(f"Exception: {context.error}")
    try:
        if update and hasattr(update, 'effective_message') and update.effective_message:
            await update.effective_message.reply_text("❌ An error occurred. Use /start")
    except Exception:
        pass

# ==================== LIFECYCLE ====================
async def on_startup(app):
    global db, session_manager
    
    log.info("🚀 Starting bot...")
    
    db = DatabaseManager(DATABASE_FILE)
    session_manager = UserSessionManager(db)
    
    if TELETHON_AVAILABLE:
        await session_manager.load_existing_sessions()
    
    log.info(f"✅ Ready. {len(session_manager.clients)} sessions loaded.")

async def on_shutdown(app):
    log.info("🛑 Shutting down...")
    if session_manager:
        await session_manager.cleanup()
    log.info("✅ Done.")

# ==================== MAIN ====================
def main():
    if not TELEGRAM_AVAILABLE:
        print("❌ Install: pip install python-telegram-bot")
        return 1
    
    if not TELETHON_AVAILABLE:
        print("⚠️ Install telethon for forwarding: pip install telethon")
    
    app = (ApplicationBuilder()
           .token(BOT_TOKEN)
           .post_init(on_startup)
           .post_shutdown(on_shutdown)
           .build())
    
    app.add_error_handler(error_handler)
    
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("rules", cmd_rules))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    log.info("🤖 Starting polling...")
    
    try:
        app.run_polling(drop_pending_updates=True)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log.exception("Crashed")
        return 1
    
    return 0

if __name__ == '__main__':
    import sys
    sys.exit(main())
