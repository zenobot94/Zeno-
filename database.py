import os
import sqlite3
import json
import time
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_NO_PREFIX_USERS = {1443810072265363518}

DEFAULT_ACTIONS = {
    "ban": {"enabled": True, "limit": 3, "punishment": "ban"},
    "kick": {"enabled": True, "limit": 3, "punishment": "ban"},
    "role_delete": {"enabled": True, "limit": 2, "punishment": "ban"},
    "role_create": {"enabled": True, "limit": 3, "punishment": "kick"},
    "channel_delete": {"enabled": True, "limit": 2, "punishment": "ban"},
    "channel_create": {"enabled": True, "limit": 3, "punishment": "kick"},
    "bot_add": {"enabled": True, "limit": 1, "punishment": "ban"},
    "dangerous_perms": {"enabled": True, "limit": 1, "punishment": "strip"},
    "admin_role": {"enabled": True, "limit": 1, "punishment": "ban"},
    "prune": {"enabled": True, "limit": 1, "punishment": "ban"},
    "invite_links": {"enabled": False, "limit": 2, "punishment": "kick"},
}


class Database:
    def __init__(self, path: Optional[str] = None):
        db_path = path or os.getenv("DB_PATH", "security_bot.db")
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.init_db()
        self.no_prefix_cache: Dict[int, float] = {}
        self._load_no_prefix_cache()

    def init_db(self):
        cur = self.conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                prefix TEXT DEFAULT 'z!',
                premium_until REAL DEFAULT 0,
                mod_log_id INTEGER,
                sec_log_id INTEGER,
                mute_role_id INTEGER,
                actions_json TEXT,
                whitelist_users TEXT DEFAULT '[]',
                whitelist_roles TEXT DEFAULT '[]',
                whitelist_channels TEXT DEFAULT '[]',
                verification_enabled INTEGER DEFAULT 0,
                verification_type TEXT DEFAULT 'captcha',
                verification_channel_id INTEGER,
                unverified_role_id INTEGER,
                verified_role_id INTEGER,
                captcha_color TEXT DEFAULT '#5865F2',
                captcha_length INTEGER DEFAULT 6,
                captcha_lines INTEGER DEFAULT 5,
                captcha_numbers INTEGER DEFAULT 1,
                captcha_sensitive INTEGER DEFAULT 0,
                captcha_timeout INTEGER DEFAULT 120
            )
        """)

        for col_sql in (
            "ALTER TABLE guild_settings ADD COLUMN prefix TEXT DEFAULT 'z!'",
            "ALTER TABLE guild_settings ADD COLUMN premium_until REAL DEFAULT 0",
        ):
            try:
                cur.execute(col_sql)
            except sqlite3.OperationalError:
                pass

        cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_permissions (
                guild_id INTEGER,
                user_id INTEGER,
                permission TEXT,
                PRIMARY KEY (guild_id, user_id, permission)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS temp_bans (
                guild_id INTEGER,
                user_id INTEGER,
                unban_at REAL,
                PRIMARY KEY (guild_id, user_id)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS temp_mutes (
                guild_id INTEGER,
                user_id INTEGER,
                unmute_at REAL,
                PRIMARY KEY (guild_id, user_id)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS premium_users (
                user_id INTEGER PRIMARY KEY,
                premium_until REAL DEFAULT -1,
                no_prefix INTEGER DEFAULT 1
            )
        """)

        for uid in DEFAULT_NO_PREFIX_USERS:
            cur.execute(
                "INSERT OR IGNORE INTO premium_users (user_id, premium_until, no_prefix) VALUES (?, -1.0, 1)",
                (uid,),
            )

        self.conn.commit()

    def ensure_guild(self, guild_id: int):
        cur = self.conn.cursor()
        cur.execute("SELECT guild_id FROM guild_settings WHERE guild_id = ?", (guild_id,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO guild_settings (guild_id, prefix, actions_json) VALUES (?, 'z!', ?)",
                (guild_id, json.dumps(DEFAULT_ACTIONS)),
            )
            self.conn.commit()

    def get_guild(self, guild_id: int) -> Dict[str, Any]:
        self.ensure_guild(guild_id)
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
        row = dict(cur.fetchone())
        saved_actions = json.loads(row["actions_json"] or "{}")
        row["actions"] = {**DEFAULT_ACTIONS, **saved_actions}
        row["whitelist_users"] = json.loads(row["whitelist_users"] or "[]")
        row["whitelist_roles"] = json.loads(row["whitelist_roles"] or "[]")
        row["whitelist_channels"] = json.loads(row["whitelist_channels"] or "[]")
        return row

    def update_field(self, guild_id: int, field: str, value: Any):
        self.ensure_guild(guild_id)
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        cur = self.conn.cursor()
        cur.execute(f"UPDATE guild_settings SET {field} = ? WHERE guild_id = ?", (value, guild_id))
        self.conn.commit()

    # --- Prefix Management ---
    def get_prefix(self, guild_id: int) -> str:
        self.ensure_guild(guild_id)
        cur = self.conn.cursor()
        cur.execute("SELECT prefix FROM guild_settings WHERE guild_id = ?", (guild_id,))
        row = cur.fetchone()
        return row["prefix"] if row and row["prefix"] else "z!"

    def set_prefix(self, guild_id: int, new_prefix: str):
        self.update_field(guild_id, "prefix", new_prefix)

    # --- Bot-Level Custom Permissions ---
    def add_permission(self, guild_id: int, user_id: int, perm: str):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO bot_permissions (guild_id, user_id, permission) VALUES (?, ?, ?)",
            (guild_id, user_id, perm),
        )
        self.conn.commit()

    def remove_permission(self, guild_id: int, user_id: int, perm: str):
        cur = self.conn.cursor()
        cur.execute(
            "DELETE FROM bot_permissions WHERE guild_id = ? AND user_id = ? AND permission = ?",
            (guild_id, user_id, perm),
        )
        self.conn.commit()

    def reset_user_permissions(self, guild_id: int, user_id: int):
        cur = self.conn.cursor()
        cur.execute("DELETE FROM bot_permissions WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        self.conn.commit()

    def get_user_permissions(self, guild_id: int, user_id: int) -> List[str]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT permission FROM bot_permissions WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return [r["permission"] for r in cur.fetchall()]

    def get_users_with_permission(self, guild_id: int, perm: str) -> List[int]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT user_id FROM bot_permissions WHERE guild_id = ? AND permission = ?",
            (guild_id, perm),
        )
        return [r["user_id"] for r in cur.fetchall()]

    # --- Temp Bans & Temp Mutes ---
    def add_temp_ban(self, guild_id: int, user_id: int, unban_at: float):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO temp_bans (guild_id, user_id, unban_at) VALUES (?, ?, ?)",
            (guild_id, user_id, unban_at),
        )
        self.conn.commit()

    def remove_temp_ban(self, guild_id: int, user_id: int):
        cur = self.conn.cursor()
        cur.execute("DELETE FROM temp_bans WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        self.conn.commit()

    def get_expired_bans(self, now_ts: float):
        cur = self.conn.cursor()
        cur.execute("SELECT guild_id, user_id FROM temp_bans WHERE unban_at <= ?", (now_ts,))
        return cur.fetchall()

    def add_temp_mute(self, guild_id: int, user_id: int, unmute_at: float):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO temp_mutes (guild_id, user_id, unmute_at) VALUES (?, ?, ?)",
            (guild_id, user_id, unmute_at),
        )
        self.conn.commit()

    def remove_temp_mute(self, guild_id: int, user_id: int):
        cur = self.conn.cursor()
        cur.execute("DELETE FROM temp_mutes WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        self.conn.commit()

    def get_expired_mutes(self, now_ts: float):
        cur = self.conn.cursor()
        cur.execute("SELECT guild_id, user_id FROM temp_mutes WHERE unmute_at <= ?", (now_ts,))
        return cur.fetchall()

    # --- Server Premium ---
    def set_premium(self, guild_id: int, days: Optional[int] = None):
        self.ensure_guild(guild_id)
        expires_at = -1.0 if days is None else (time.time() + days * 86400)
        self.update_field(guild_id, "premium_until", expires_at)

    def remove_premium(self, guild_id: int):
        self.ensure_guild(guild_id)
        self.update_field(guild_id, "premium_until", 0.0)

    def get_premium_info(self, guild_id: int) -> Tuple[bool, Optional[int]]:
        self.ensure_guild(guild_id)
        cur = self.conn.cursor()
        cur.execute("SELECT premium_until FROM guild_settings WHERE guild_id = ?", (guild_id,))
        row = cur.fetchone()
        until = row["premium_until"] if row and row["premium_until"] else 0.0

        if until == -1.0:
            return True, None
        if until > time.time():
            return True, int(until)
        return False, None

    # --- User Premium & No-Prefix Cache ---
    def _load_no_prefix_cache(self):
        cur = self.conn.cursor()
        cur.execute("SELECT user_id, premium_until, no_prefix FROM premium_users")
        now = time.time()
        self.no_prefix_cache.clear()
        for row in cur.fetchall():
            until = row["premium_until"]
            if row["no_prefix"] and (until == -1.0 or until > now):
                self.no_prefix_cache[row["user_id"]] = until

    def add_user_premium(self, user_id: int, days: Optional[int] = None):
        expires_at = -1.0 if days is None else (time.time() + days * 86400)
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO premium_users (user_id, premium_until, no_prefix) VALUES (?, ?, 1)",
            (user_id, expires_at),
        )
        self.conn.commit()
        self.no_prefix_cache[user_id] = expires_at

    def remove_user_premium(self, user_id: int):
        cur = self.conn.cursor()
        cur.execute("DELETE FROM premium_users WHERE user_id = ?", (user_id,))
        self.conn.commit()
        self.no_prefix_cache.pop(user_id, None)

    def set_user_no_prefix(self, user_id: int, enabled: bool):
        cur = self.conn.cursor()
        cur.execute("UPDATE premium_users SET no_prefix = ? WHERE user_id = ?", (int(enabled), user_id))
        self.conn.commit()
        self._load_no_prefix_cache()

    def can_use_no_prefix(self, user_id: int) -> bool:
        until = self.no_prefix_cache.get(user_id)
        if until is None:
            return False
        if until == -1.0 or until > time.time():
            return True
        self.remove_user_premium(user_id)
        return False

    def get_user_premium(self, user_id: int) -> Tuple[bool, Optional[int], bool]:
        cur = self.conn.cursor()
        cur.execute("SELECT premium_until, no_prefix FROM premium_users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if not row:
            return False, None, False
        until = row["premium_until"]
        np_enabled = bool(row["no_prefix"])
        if until == -1.0:
            return True, None, np_enabled
        if until > time.time():
            return True, int(until), np_enabled
        self.remove_user_premium(user_id)
        return False, None, False

    def get_all_premium_users(self) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("SELECT user_id, premium_until, no_prefix FROM premium_users")
        return [dict(r) for r in cur.fetchall()]
