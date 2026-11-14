import os
import sys
import glob
import copy

# OriHost/Pterodactyl installs PY_PACKAGES and requirements under .local.
# Add its Python 3.13 site-packages path before importing third-party modules.
_LOCAL_SITE_PACKAGES = glob.glob(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".local", "lib", "python*", "site-packages")
)
for _site_packages in _LOCAL_SITE_PACKAGES:
    if os.path.isdir(_site_packages) and _site_packages not in sys.path:
        sys.path.insert(0, _site_packages)

import json
import html
import time
BOT_STARTED_AT = time.monotonic()
import asyncio
import logging
import re
import secrets
import string
from pathlib import Path
import aiohttp
from datetime import timedelta, datetime, timezone
from collections import defaultdict, deque
import discord
from discord.ext import commands, tasks
from discord import app_commands

# ==================== EMBED STYLE ====================
_DiscordEmbed = discord.Embed
_EMBED_PURPLE = None  # Unset embed color: Discord renders no colored accent strip.
ERROR_EMOJI = "<:kanade_error:1569789238507999232>"
LOG_EMOJI = "<a:1451608282308415571:1569798026489503745>"
SUCCESS_EMOJI = "<a:kanade_success:1569789596122882190>"
LATENCY_EMOJI = "<a:kanade_ping:1569789734522191934>"
GLOBAL_SERVERS_EMOJI = "<a:kanade_globe:1569789454225244180>"
USERS_EMOJI = "<a:kanade_users:1527652434745495656>"
ANTI_NUKE_ON_EMOJI = "<a:1529696249106796618:1569799605351223399>"
ANTI_NUKE_OFF_EMOJI = "<a:1556277310737350698:1569799594190053376>"
MESSAGE_DELETE_EMOJI = "<:1569495944049721525:1569797198198341662>"
VOICE_EMOJI = "<a:1546809362457165864:1569797734007836814>"

def _clean_embed_text(value):
    """Remove legacy visual decoration while preserving readable content."""
    if not isinstance(value, str):
        return value
    value = html.unescape(value).replace("\r\n", "\n").replace("\r", "\n")
    # Ordinary Unicode emoji are intentionally disabled across Kanade embeds.
    # Discord custom emoji markup (for example <a:name:id>) remains untouched.
    value = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D\u20E3]", "", value)
    cleaned = []
    for line in value.split("\n"):
        line = re.sub(r"^\s*[╭├╰╞╮╯│┃┆┊]+\s*[・·|]?\s*", "", line)
        line = re.sub(r"^\s*(?:➤|➜|➔|▸|›|»|✦|✧|[•·▪▫◆◇◈※]+)\s*", "", line)
        line = re.sub(r"^\s*(?:🛡️?|⚒️?|🔒|⚙️?|✨|💾|📶|👤|🧰|🎉|📊|📝|🔨|✅|❌|⚠️?|🆔|📁|🔗|🌐|⏱|👥|📌|🖼️?)\s*", "", line)
        cleaned.append(line.rstrip())
    value = "\n".join(cleaned)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()

# A restrained visual language: a small diamond for plain labels and a star
# for plain titles. Existing emoji/symbol-led titles are left as written.
_EMBED_VISUAL_PREFIXES = (
    "✦", "✧", "✨", "◇", "◆", "◈", "⛨", "⚔", "⌘", "※", "➤", "➜",
    "•", "→", "✓", "✔", "✕", "✖", "⚠", "❌", "✅", "❗", "ℹ", "🔒",
    "🛡", "⚙", "🎭", "🧰", "👤", "📚", "🚀", "🗓", "🆔", "🔨", "💾",
    "📶", "🌐", "⏱", "👥", "📊", "🏠", "🛠", "🔧", "📌", "📝", "🔗",
    "🧹", "🔨", "🔊", "🎨", "🖼", "👑", "💎", "📁", "🧾", "📈", "📉",
)

def _arrow_lines(value):
    """Use ➤ only for actual command entries; keep prose and errors clean."""
    value = _clean_embed_text(value)
    if not isinstance(value, str) or not value:
        return value
    lines = []
    legacy_arrows = ("˃", "➔", "➜", "→", "▸", "›")
    for line in value.split("\n"):
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        for arrow in legacy_arrows:
            if stripped.startswith(arrow):
                stripped = stripped[len(arrow):].lstrip()
                break
        # Help pages list commands in inline code, while overview text remains plain.
        is_command_entry = stripped.startswith(("`", "/"))
        if is_command_entry and not stripped.startswith("➤"):
            stripped = f"➤ {stripped}"
        lines.append(stripped)
    return "\n".join(lines)

def _decorate_embed_label(value, prefix):
    value = _clean_embed_text(value)
    if not isinstance(value, str) or not value:
        return value
    if value.startswith(_EMBED_VISUAL_PREFIXES):
        return value
    return f"{prefix} {value}"

def _approved_embed_banner(user=None, guild=None):
    """Return the configured/banner URL only for the four approved info menus."""
    configured = os.getenv("EMBED_BANNER_URL", "").strip()
    if configured.startswith("https://"):
        return configured
    if guild is not None and getattr(guild, "banner", None):
        return guild.banner.url
    banner = getattr(user, "banner", None) if user else None
    return banner.url if banner else None

async def _render_banner_card(embed, banner_url=None, guild=None):
    """Keep the banner inside the same embed, beneath its information content."""
    banner_url = banner_url or _approved_embed_banner(bot.user, guild=guild)
    # Keep the same purple-blue accent on banner/info embeds as every other command.
    embed.color = None
    if banner_url and banner_url.startswith(("https://", "http://")):
        embed.set_image(url=banner_url)
    return embed

async def _send_banner_card(ctx, embed, view=None, banner_url=None, guild=None):
    """Send one information embed with its banner attached at the bottom."""
    try:
        rendered_embed = await _render_banner_card(embed, banner_url=banner_url, guild=guild)
        return await ctx.reply(embed=rendered_embed, view=view, mention_author=False)
    except Exception:
        logging.exception("Failed to send banner embed; falling back to information embed")
        return await ctx.reply(embed=embed, view=view, mention_author=False)

async def _edit_banner_card(interaction, embed, view, guild=None):
    """Refresh the same embed and its banner when a help-menu category changes."""
    try:
        banner_url = _approved_embed_banner(bot.user, guild=guild)
        rendered_embed = await _render_banner_card(embed, banner_url=banner_url, guild=guild)
        return await interaction.response.edit_message(embed=rendered_embed, view=view)
    except Exception:
        logging.exception("Failed to refresh banner embed; falling back to information embed")
        return await interaction.response.edit_message(embed=embed, view=view)

class StyledEmbed(_DiscordEmbed):
    """Plain, consistent embeds: action title, readable content, no branding."""
    def __init__(self, *args, **kwargs):
        kwargs["color"] = None
        title = kwargs.get("title")
        if isinstance(title, str):
            title = re.sub(r"(?i)^\s*(?:🛡️?\s*)?Kanade\s*[|—:-]\s*", "", title).strip()
            title = _clean_embed_text(title)
            if title:
                error_title = re.match(
                    r"(?i)^(?:error|cannot\b|.*\b(?:failed|failure|denied|forbidden|invalid|missing|not found|unavailable)\b)",
                    title,
                )
                if error_title and not title.startswith((ERROR_EMOJI, LOG_EMOJI, SUCCESS_EMOJI)):
                    title = f"{ERROR_EMOJI} {title}"
                else:
                    success_title = re.match(
                        r"(?i)^(?:(?:member|user|message|channel|role|server|backup|invite|giveaway|confession|ticket|reminder|settings?|configuration|command|operation|purge|lockdown|whitelist|timeout|warning|ban|kick|unban|mute|unmute|afk|automod|anti[- ]?link|anti[- ]?spam|level|xp|voice|emoji|webhook|thread|event|poll|counting|backup code)\b.*\b(?:banned|kicked|unbanned|muted|unmuted|timed out|warned|created|deleted|updated|edited|added|removed|assigned|unassigned|restored|loaded|saved|enabled|disabled|locked|unlocked|purged|cleared|sent|started|ended|configured|completed|generated|published|joined|left|granted|revoked|reset|transferred|claimed|given|changed)\b|.*\b(?:success|successful|completed|created successfully|updated successfully|saved successfully|restored successfully)\b)",
                        title,
                    )
                    if success_title and not title.startswith((ERROR_EMOJI, LOG_EMOJI, SUCCESS_EMOJI)):
                        title = f"{SUCCESS_EMOJI} {title}"
                kwargs["title"] = title
            else:
                kwargs.pop("title", None)
        if isinstance(kwargs.get("description"), str):
            kwargs["description"] = _clean_embed_text(kwargs["description"])
        # Legacy command embeds often supplied bot branding as an author/footer.
        author = kwargs.get("author")
        if isinstance(author, dict) and str(author.get("name", "")).strip().casefold() in {"kanade", "kanade bot"}:
            kwargs.pop("author", None)
        footer = kwargs.get("footer")
        if isinstance(footer, dict):
            footer_text = str(footer.get("text", "")).strip()
            if footer_text.casefold() in {"kanade", "kanade bot"} or footer_text.casefold().startswith("kanade •"):
                kwargs.pop("footer", None)
        super().__init__(*args, **kwargs)

    def add_field(self, *, name, value, inline=True):
        name = _clean_embed_text(name) or "Details"
        value = _clean_embed_text(value) or "—"
        return super().add_field(name=name, value=value, inline=inline)

    def set_footer(self, *, text=None, icon_url=None):
        text = _clean_embed_text(text) if text else None
        if text:
            text = re.sub(r"(?i)^kanade(?:\s+bot)?\s*[|•—:-]\s*", "", text).strip()
        if not text:
            return self.remove_footer()
        # Do not display the bot avatar as decorative footer artwork.
        return super().set_footer(text=text, icon_url=None)

    def set_author(self, *, name, url=None, icon_url=None):
        name = _clean_embed_text(name) if name else None
        if not name or name.strip().casefold() in {"kanade", "kanade bot"}:
            self.remove_author()
            return self
        return super().set_author(name=name, url=url, icon_url=None)

# ==================== CONFIGURATION ====================
# Railway authentication: read the token ONLY from the environment.
# Do not fall back to config.json; an old token there can cause 401 Unauthorized.
def _read_environment_token():
    value = os.getenv("DISCORD_TOKEN", "").strip()
    if value.lower().startswith("bot "):
        value = value[4:].strip()
    return value

TOKEN = _read_environment_token()
PREFIX = "!"
DEVELOPER_ID = 1524801139227361403
CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "owner_ids": [],
    "premium_guilds": [],  # legacy alias retained for compatibility
    "premium_admin_ids": [1524801139227361403],
    "premium_guild_ids": [],
    "guild_prefixes": {},
    "whitelist": [],
    "guild_whitelists": {},
    "antispam_enabled": False,
    "antispam_window_seconds": 4,
    "antispam_message_limit": 3,
    "antispam_duplicate_limit": 3,
    "antispam_mention_limit": 6,
    "antispam_timeout_seconds": 300,
    "antispam_delete_messages": True,
    "antispam_exempt_admins": False,
    "antilink_enabled": False,
    "antinuke_enabled": True,
    "raid_protection": True,
    "raid_join_threshold": 8,
    "raid_window_seconds": 10,
    "raid_lockdown_seconds": 120,
    "lockdown_on_trigger": False,
    "restore_channels": True,
    "restore_roles": True,
    "notify_owner": True,
    "log_channel_id": 0,
    "log_channels": {},
    "j2c_settings": {},
    "j2c_rooms": {},
    "confession_settings": {},
    "xp_settings": {},
    "xp_data": {},
    "giveaways": {},
    "counting_settings": {},
    "honeypot_settings": {},
    "global_staff_ids": [1524801139227361403],
    "global_no_prefix": [],
    "guild_no_prefix": {},
    "lock_states": {},
    "thresholds": {
        "guild_update": 1,
        "channel_delete": 1,
        "channel_create": 4,
        "channel_update": 4,
        "role_delete": 1,
        "role_create": 4,
        "role_update": 3,
        "dangerous_permissions": 1,
        "ban": 1,
        "kick": 1,
        "bot_add": 1,
        "webhook_create": 1,
        "webhook_update": 1,
        "webhook_delete": 1,
        "overwrite_update": 3,
        "member_role_update": 1
    }
}
DANGEROUS_PERMISSIONS = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "ban_members",
    "kick_members",
    "manage_webhooks",
    "manage_messages",
    "mention_everyone"
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
)
logger = logging.getLogger("Kanade")
# ==================== CONFIG FILE ====================
def load_config():
    saved = {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as file:
            saved = json.load(file)
            if not isinstance(saved, dict):
                saved = {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    result = json.loads(json.dumps(DEFAULT_CONFIG))
    for key, value in saved.items():
        if key not in ("thresholds", "guild_whitelists"):
            result[key] = value
    if isinstance(saved.get("thresholds"), dict):
        result["thresholds"].update(saved["thresholds"])
    result["owner_ids"] = [
        int(value)
        for value in result.get("owner_ids", [])
        if str(value).isdigit()
    ]
    result["premium_guilds"] = [
        int(value)
        for value in result.get("premium_guilds", [])
        if str(value).isdigit()
    ]
    raw_premium_admins = result.get("premium_admin_ids", [DEVELOPER_ID])
    if not isinstance(raw_premium_admins, list):
        raw_premium_admins = []
    premium_admin_ids = [DEVELOPER_ID]
    for value in raw_premium_admins:
        if str(value).isdigit() and int(value) not in premium_admin_ids:
            premium_admin_ids.append(int(value))
    result["premium_admin_ids"] = premium_admin_ids
    raw_premium_ids = result.get("premium_guild_ids", [])
    if not isinstance(raw_premium_ids, list):
        raw_premium_ids = []
    merged_premium_ids = []
    for value in list(raw_premium_ids) + list(result.get("premium_guilds", [])):
        if str(value).isdigit() and int(value) > 0 and int(value) not in merged_premium_ids:
            merged_premium_ids.append(int(value))
    result["premium_guild_ids"] = merged_premium_ids
    result["premium_guilds"] = list(merged_premium_ids)
    raw_prefixes = result.get("guild_prefixes", {})
    result["guild_prefixes"] = {
        str(guild_id): str(prefix)
        for guild_id, prefix in raw_prefixes.items()
        if str(guild_id).isdigit() and isinstance(prefix, str) and prefix
    } if isinstance(raw_prefixes, dict) else {}
    # Retain legacy global whitelist data for compatibility.
    # Regular user whitelist checks use guild_whitelists.
    result["whitelist"] = [
        int(value)
        for value in result.get("whitelist", [])
        if str(value).isdigit()
    ]
    raw_guild_whitelists = saved.get("guild_whitelists", {})
    normalized_whitelists = {}
    if isinstance(raw_guild_whitelists, dict):
        for guild_id, user_ids in raw_guild_whitelists.items():
            if str(guild_id).isdigit() and isinstance(user_ids, list):
                normalized_whitelists[str(guild_id)] = [
                    int(user_id)
                    for user_id in user_ids
                    if str(user_id).isdigit()
                ]
    result["guild_whitelists"] = normalized_whitelists
    return result
config = load_config()

# Community feature runtime state. XP cooldowns are intentionally in-memory.
XP_COOLDOWNS = {}
GIVEAWAY_LOOP_STARTED = False


# Bot credentials are intentionally not loaded from config.json.

def guild_security_settings(guild):
    """Return mutable per-server settings, with moderation features OFF by default."""
    if guild is None:
        return {}
    all_settings = config.setdefault("guild_security_settings", {})
    settings = all_settings.setdefault(str(guild.id), {})
    # Explicit per-guild defaults: each server starts with anti-spam and anti-link OFF.
    # Existing explicit ON/OFF choices are preserved.
    changed = False
    if "antispam_enabled" not in settings:
        settings["antispam_enabled"] = False
        changed = True
    if "antilink_enabled" not in settings:
        settings["antilink_enabled"] = False
        changed = True
    if changed:
        save_config()
    return settings

def get_guild_setting(guild, key, default=None):
    """Read a per-server setting, falling back to legacy global settings."""
    settings = guild_security_settings(guild)
    if key in settings:
        return settings[key]
    return config.get(key, default)

def set_guild_setting(guild, key, value):
    """Persist a setting for only the selected server."""
    settings = guild_security_settings(guild)
    settings[key] = value
    save_config()

def is_link_exempt(message):
    guild = message.guild
    if guild is None:
        return True
    settings = guild_security_settings(guild)
    exempt_ids = {int(x) for x in settings.get("antilink_exempt_ids", []) if str(x).isdigit()}
    if message.author.id in exempt_ids or message.author.id == guild.owner_id:
        return True
    if config.get("antispam_exempt_admins", False) and getattr(message.author, "guild_permissions", None) and message.author.guild_permissions.administrator:
        return True
    return False

def extract_domains(content):
    """Extract lowercase hostnames from links without requiring external packages."""
    domains = []
    for match in re.findall(r"(?i)(?:(?:https?://)|(?:www\.))[a-z0-9.-]+(?::\d+)?(?:/[^\s<>]*)?|(?:discord\.gg|discord(?:app)?\.com/invite)/[a-z0-9-]+", content or ""):
        candidate = match.strip(".,!?;:()[]{}<>")
        candidate = re.sub(r"(?i)^https?://", "", candidate)
        candidate = re.sub(r"(?i)^www\.", "", candidate)
        host = candidate.split("/", 1)[0].split(":", 1)[0].lower().rstrip(".")
        if host:
            domains.append(host)
    return domains

def domain_is_allowed(domain, allowed_domains):
    domain = domain.lower().strip().lstrip(".")
    return any(domain == item.lower().strip().lstrip(".") or domain.endswith("." + item.lower().strip().lstrip(".")) for item in allowed_domains if isinstance(item, str) and item.strip())


def bot_staff_or_server_admin(user):
    return has_admin_access(user)




# Config changes can happen during message listeners (XP/counting). Writing and
# pretty-printing the entire JSON file synchronously on every message can block
# discord.py's event loop and inflate gateway heartbeat latency. Coalesce rapid
# updates and perform serialization + disk I/O in a worker thread.
_CONFIG_SAVE_TASK = None
_CONFIG_SAVE_DIRTY = False
_CONFIG_SAVE_DEBOUNCE_SECONDS = 0.4


def _write_config_snapshot(snapshot):
    temporary = CONFIG_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(snapshot, file, indent=4)
    os.replace(temporary, CONFIG_FILE)


async def _flush_config_saves():
    global _CONFIG_SAVE_TASK, _CONFIG_SAVE_DIRTY
    try:
        while True:
            await asyncio.sleep(_CONFIG_SAVE_DEBOUNCE_SECONDS)
            _CONFIG_SAVE_DIRTY = False
            # Take a consistent event-loop-side snapshot; heavy JSON encoding
            # and file writes happen off the gateway event loop.
            snapshot = copy.deepcopy(config)
            await asyncio.to_thread(_write_config_snapshot, snapshot)
            if not _CONFIG_SAVE_DIRTY:
                break
    except Exception:
        logger.exception("Deferred config save failed")
    finally:
        _CONFIG_SAVE_TASK = None
        # A change may have arrived as the worker was finishing.
        if _CONFIG_SAVE_DIRTY:
            try:
                loop = asyncio.get_running_loop()
                _CONFIG_SAVE_TASK = loop.create_task(_flush_config_saves())
            except RuntimeError:
                pass


def save_config():
    """Persist config without blocking the Discord gateway during runtime."""
    global _CONFIG_SAVE_TASK, _CONFIG_SAVE_DIRTY
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Startup / pre-event-loop compatibility.
        _write_config_snapshot(copy.deepcopy(config))
        return

    _CONFIG_SAVE_DIRTY = True
    if _CONFIG_SAVE_TASK is None or _CONFIG_SAVE_TASK.done():
        _CONFIG_SAVE_TASK = loop.create_task(_flush_config_saves())


# ==================== LEGACY ANTI-SPAM / ANTI-LINK SETTINGS ====================
def migrate_legacy_moderation_settings():
    """Restore the original per-server anti-spam and anti-link setting names."""
    guild_settings = config.setdefault("guild_security_settings", {})
    if not isinstance(guild_settings, dict):
        config["guild_security_settings"] = {}
        guild_settings = config["guild_security_settings"]
    changed = False
    for guild_id, settings in guild_settings.items():
        if not isinstance(settings, dict):
            continue
        # Preserve an existing legacy setting; only migrate from AutoMod keys if absent.
        if "antispam_enabled" not in settings and "anti_spam_enabled" in settings:
            settings["antispam_enabled"] = bool(settings["anti_spam_enabled"])
            changed = True
        if "antilink_enabled" not in settings and "anti_link_enabled" in settings:
            settings["antilink_enabled"] = bool(settings["anti_link_enabled"])
            changed = True
        if "antispam_message_limit" not in settings and "automod_spam_limit" in settings:
            settings["antispam_message_limit"] = settings["automod_spam_limit"]
            changed = True
        if "antispam_window_seconds" not in settings and "automod_spam_window" in settings:
            settings["antispam_window_seconds"] = settings["automod_spam_window"]
            changed = True
        if "antispam_timeout_seconds" not in settings and "automod_timeout_seconds" in settings:
            settings["antispam_timeout_seconds"] = settings["automod_timeout_seconds"]
            changed = True
    if changed:
        save_config()
        logger.info("Migrated AutoMod settings to legacy anti-spam/anti-link settings")

migrate_legacy_moderation_settings()
# ==================== STATE ====================
afk_users = {}
channel_snapshots = {}
role_snapshots = {}
action_history = defaultdict(deque)
join_history = defaultdict(deque)
recent_audit_ids = deque(maxlen=1500)
recent_audit_id_set = set()
lockdown_tasks = {}
raid_lock = asyncio.Lock()
recent_deleted_messages = {}
voice_clients_requested = set()
# Prevent expensive full-guild snapshots and global slash sync from repeating
# every time Discord reconnects and fires on_ready again.
STARTUP_SNAPSHOTS_DONE = False
SLASH_COMMANDS_SYNCED = False
PERSISTENT_VIEWS_REGISTERED = False
HONEYPOT_MESSAGE_HISTORY = defaultdict(deque)
# ==================== BOT ====================
intents = discord.Intents.default()
intents.guilds = True
# Required to inspect message text for anti-spam and anti-link enforcement.
# Also enable Message Content Intent in the Discord Developer Portal.
intents.message_content = True
intents.members = True
intents.moderation = True
intents.message_content = True
intents.webhooks = True
intents.invites = True
async def get_command_prefix(client, message):
    """Return this server's saved prefix, or the default prefix outside a server."""
    if message.guild is None:
        prefix = PREFIX
    else:
        prefix = config.get("guild_prefixes", {}).get(str(message.guild.id), PREFIX)
    if client.user:
        return commands.when_mentioned_or(prefix)(client, message)
    return prefix

bot = commands.Bot(
    command_prefix=get_command_prefix,
    intents=intents,
    help_command=None
)
# ==================== PERMISSION HELPERS ====================
def is_owner_id(user_id):
    return user_id is not None and (int(user_id) == DEVELOPER_ID or int(user_id) in config["owner_ids"])
def is_whitelisted(guild, user_id):
    if user_id is None:
        return True
    if bot.user and user_id == bot.user.id:
        return True
    if guild and user_id == guild.owner_id:
        return True
    if user_id in config["owner_ids"]:
        return True
    if guild is None:
        return False
    if user_id in config.get("whitelist", []):
        return True
    guild_whitelists = config.get("guild_whitelists", {})
    user_ids = guild_whitelists.get(str(guild.id), [])
    if user_id in user_ids:
        return True
    # Whitelisted roles: any member holding one of them is whitelisted.
    role_ids = {
        int(value)
        for value in config.get("guild_whitelist_roles", {}).get(str(guild.id), [])
        if str(value).isdigit()
    }
    if role_ids:
        member = guild.get_member(user_id)
        if member is not None and any(role.id in role_ids for role in member.roles):
            return True
    return False
def is_bot_admin_id(user_id):
    """Bot-level staff access, independent of Discord server roles/permissions."""
    if user_id is None:
        return False
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return False
    bot_admin_ids = config.get("premium_admin_ids", [DEVELOPER_ID])
    return (
        user_id == DEVELOPER_ID
        or user_id in config.get("owner_ids", [])
        or user_id in bot_admin_ids
    )

def has_admin_access(member):
    """Allow bot developer/bot admins OR a server's own administrator/owner."""
    if member is None:
        return False
    if is_bot_admin_id(getattr(member, "id", None)):
        return True
    guild = getattr(member, "guild", None)
    if guild is None:
        return False
    return bool(
        getattr(member, "guild_permissions", None)
        and member.guild_permissions.administrator
    ) or member.id == guild.owner_id

async def app_bot_staff_or_server_admin(interaction: discord.Interaction):
    """Application-command check that does not require Discord server admin perms for bot staff."""
    return interaction.guild is not None and has_admin_access(interaction.user)

def is_premium_guild(guild):
    """Return whether a server has an active Premium entitlement."""
    if guild is None:
        return False
    premium_ids = set(config.get("premium_guild_ids", []))
    premium_ids.update(config.get("premium_guilds", []))
    return guild.id in premium_ids

async def is_bot_owner(user):
    """Only the developer/configured bot owners may manage Premium access."""
    if is_owner_id(user.id):
        return True
    try:
        return await bot.is_owner(user)
    except (discord.HTTPException, AttributeError):
        return False

async def require_premium(ctx):
    """Allow Premium servers, the developer, and explicitly authorized bypass admins."""
    premium_admin_ids = config.get("premium_admin_ids", [DEVELOPER_ID])
    if is_owner_id(ctx.author.id) or ctx.author.id in premium_admin_ids:
        return True
    if is_premium_guild(ctx.guild):
        return True
    embed = StyledEmbed(
        title="🛡️ Kanade | Premium Required",
        description=(
            "This command is available only in **Premium-enabled servers**.\n"
            "Ask the server owner to contact the bot developer about Premium access."
        ),
        color=discord.Color.from_rgb(128, 0, 255),
    )
    embed.set_footer(text="Kanade • Premium")
    await ctx.reply(embed=embed, mention_author=False)
    return False
def get_threshold(action):
    try:
        return max(1, int(config["thresholds"].get(action, 3)))
    except (TypeError, ValueError):
        return 3
def permissions_became_dangerous(before, after):
    if before is None or after is None:
        return False
    return any(
        not getattr(before, permission, False)
        and getattr(after, permission, False)
        for permission in DANGEROUS_PERMISSIONS
    )
# ==================== LOGGING ====================
LOG_CHANNEL_DEFINITIONS = {
    "anti_nuke": ("anti-nuke-logs", "Anti Nuke Logs", "Anti-nuke detections, suspicious actions, and protection responses."),
    "message": ("message-logs", "Message Logs", "Message edits, deletions, and message-related events."),
    "server": ("server-logs", "Server Logs", "Server and channel updates, invites not otherwise categorized, and general events."),
    "voice": ("voice-logs", "Voice Logs", "Voice channel joins, leaves, and switches."),
    "invite": ("invite-logs", "Invite Logs", "Invite creation and invite-related events."),
    "member": ("member-logs", "Member Logs", "Member joins, leaves, and member updates."),
    "role": ("role-logs", "Role Logs", "Role creation, deletion, updates, and member role changes."),
    "moderation": ("moderation-logs", "Moderation Logs", "Kicks, bans, unbans, timeouts, and other moderation actions."),
    "automod": ("automod-logs", "AutoMod Logs", "Anti-spam, anti-link, and automated moderation actions."),
    "honeypot": ("honeypot-logs", "Honeypot Logs", "Token scams, malware bait, suspicious attachments, and honeypot detections."),
}

def _log_group_for(title, description=""):
    text = f"{title} {description}".lower()
    if any(word in text for word in ("anti-nuke", "antinuke", "nuke", "dangerous permission", "mass delete", "raid detected", "suspicious action", "protection triggered")):
        return "anti_nuke"
    if "honeypot" in text or "token scam" in text or "malware bait" in text:
        return "honeypot"
    if any(word in text for word in ("anti-spam", "antispam", "anti-link", "antilink", "automod", "spam action", "blocked domain", "link filtering")):
        return "automod"
    if any(word in text for word in ("j2c", "join-to-create", "voice room")):
        return "voice"
    if "confession" in text:
        return "message"
    if any(word in text for word in ("message edited", "message deleted", "message bulk", "deleted message")):
        return "message"
    if any(word in text for word in ("voice", "joined vc", "left vc", "switched vc")):
        return "voice"
    if "invite" in text:
        return "invite"
    if any(word in text for word in ("role", "permission updated", "permissions updated")):
        return "role"
    if any(word in text for word in ("member joined", "member left", "member updated", "member roles", "possible join raid", "user joined", "user left")):
        return "member"
    if any(word in text for word in ("banned", "unbanned", "unban", "kicked", "timeout", "muted", "warned", "purge", "lockdown", "moderation")):
        return "moderation"
    return "server"

async def send_log(guild, title, description, color=None):
    """Route each log event to its purpose-specific channel when configured."""
    if guild is None:
        return
    # Ignore per-event colors: a colored embed accent strip is not wanted.
    color = None
    guild_key = str(guild.id)
    grouped = config.get("log_channel_groups", {}).get(guild_key, {})
    group = _log_group_for(title, description or "")
    channel_id = int(grouped.get(group, 0) or 0)
    # Legacy single-channel setup remains supported until the new setup is run.
    if not channel_id:
        channel_id = int(config.get("log_channels", {}).get(guild_key, 0) or 0)
    if not channel_id:
        legacy_id = int(config.get("log_channel_id", 0) or 0)
        legacy_channel = guild.get_channel(legacy_id) if legacy_id else None
        if legacy_channel is not None:
            channel_id = legacy_id
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return
    if guild.me and not channel.permissions_for(guild.me).send_messages:
        return
    # Keep the requested arrow-style event logs clean and aligned. Other
    # existing security logs retain their established compact formatting.
    raw_description = _clean_embed_text(description or "")
    if title.startswith(("➤ ", "˃ ", "➔ ", "➜ ", "→ ", "▸ ", "› ")):
        formatted_description = raw_description[:4000] or "No additional details were provided."
    else:
        detail_lines = []
        for raw_line in raw_description.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(("• ", "▸ ", "› ", "╰・", "├・", "╭・")):
                line = re.sub(r"^(?:• |▸ |› |╰・|├・|╭・)", "", line)
            if line.startswith("**") and ":**" in line:
                line = "▸ " + line
            elif not line.startswith(("▸ ", "• ", "`", "<", "**")):
                line = "▸ " + line
            detail_lines.append(line)
        formatted_description = "\n".join(detail_lines)[:4000] or "▸ No additional details were provided."
    clean_log_title = _clean_embed_text(title or "Security Log")
    if not clean_log_title.startswith((LOG_EMOJI, MESSAGE_DELETE_EMOJI, VOICE_EMOJI)):
        clean_log_title = f"{LOG_EMOJI} {clean_log_title}"
    embed = StyledEmbed(
        title=clean_log_title,
        description=formatted_description,
        color=color,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="Kanade • Security Logs")
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        logger.warning("Could not send opt-in log in guild %s", guild.id)
async def _owner_notification_invite(guild):
    """Return an existing valid invite, or create one without changing server settings."""
    vanity_url = getattr(guild, "vanity_url", None)
    if vanity_url:
        return str(vanity_url)

    # Prefer an already-created invite. This is read-only and avoids creating extras.
    try:
        for invite in await guild.invites():
            if getattr(invite, "url", None):
                return invite.url
    except (discord.Forbidden, discord.HTTPException):
        pass

    # Create a permanent, unlimited invite only in a channel where the bot
    # already has Create Instant Invite permission. No channel/server settings
    # or permissions are edited.
    candidates = []
    system_channel = getattr(guild, "system_channel", None)
    if isinstance(system_channel, discord.TextChannel):
        candidates.append(system_channel)
    candidates.extend(
        channel for channel in getattr(guild, "text_channels", [])
        if channel not in candidates
    )
    me = getattr(guild, "me", None)
    for channel in candidates:
        if me is None:
            break
        try:
            permissions = channel.permissions_for(me)
            if permissions.view_channel and permissions.create_instant_invite:
                invite = await channel.create_invite(
                    max_age=0, max_uses=0, unique=False,
                    reason="Kanade owner security notification invite"
                )
                return invite.url
        except (discord.Forbidden, discord.HTTPException):
            continue
    return None


async def notify_owners(guild, message):
    """Send minimal anti-nuke notifications to the developer and configured owners."""
    if not config.get("notify_owner", True):
        logger.info("Owner anti-nuke DM skipped: notify_owner is disabled.")
        return

    raw_owner_ids = [DEVELOPER_ID, *config.get("owner_ids", [])]
    owner_ids = []
    for value in raw_owner_ids:
        try:
            owner_id = int(value)
            if owner_id > 0 and owner_id not in owner_ids:
                owner_ids.append(owner_id)
        except (TypeError, ValueError):
            continue

    if not owner_ids:
        logger.error("Owner anti-nuke DM not sent: no valid owner IDs configured.")
        return

    invite_url = await _owner_notification_invite(guild)
    embed = StyledEmbed(
        title=guild.name[:256],
        description=(message or "An anti-nuke event was detected.")[:4000],
        color=None,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Event", value="Anti-nuke detection", inline=True)
    embed.add_field(name="Status", value="Security action triggered", inline=True)
    embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=False)
    embed.add_field(name="Server Invite", value=invite_url or "Unavailable — no valid invite could be retrieved or created.", inline=False)
    embed.set_footer(text="Owner Notification")

    for owner_id in owner_ids:
        try:
            user = bot.get_user(owner_id)
            if user is None:
                user = await bot.fetch_user(owner_id)
            await user.send(embed=embed)
            logger.info("Sent owner anti-nuke DM to user ID %s for guild %s.", owner_id, guild.id)
        except discord.Forbidden:
            logger.warning("Could not DM owner ID %s: Discord blocked the DM.", owner_id)
        except (discord.HTTPException, TypeError, ValueError):
            logger.exception("Could not send owner anti-nuke DM to user ID %s.", owner_id)

async def snapshot_guild(guild):
    channel_snapshots[guild.id] = {
        channel.id: {
            "name": channel.name,
            "type": str(channel.type),
            "category_id": channel.category_id,
            "position": channel.position,
            "topic": getattr(channel, "topic", None),
            "nsfw": getattr(channel, "nsfw", False),
            "slowmode_delay": getattr(channel, "slowmode_delay", 0)
        }
        for channel in guild.channels
    }
    role_snapshots[guild.id] = {
        role.id: {
            "name": role.name,
            "permissions": role.permissions.value,
            "color": role.color.value,
            "hoist": role.hoist,
            "mentionable": role.mentionable,
            "position": role.position
        }
        for role in guild.roles
        if role != guild.default_role
    }
async def restore_deleted_channel(guild, entry):
    if not config.get("restore_channels", True):
        return "Channel restoration is disabled."
    target = getattr(entry, "target", None)
    target_id = getattr(target, "id", None)
    snapshot = channel_snapshots.get(guild.id, {}).get(target_id)
    if not snapshot:
        return "No saved snapshot was available for this channel."
    name = snapshot["name"]
    category = guild.get_channel(snapshot.get("category_id"))
    try:
        channel_type = snapshot["type"]
        if channel_type == "category":
            await guild.create_category(
                name,
                position=snapshot.get("position", 0),
                reason="Kanade anti-nuke recovery"
            )
        elif channel_type == "voice":
            await guild.create_voice_channel(
                name,
                category=category,
                position=snapshot.get("position", 0),
                reason="Kanade anti-nuke recovery"
            )
        elif channel_type == "stage_voice":
            await guild.create_stage_channel(
                name,
                category=category,
                reason="Kanade anti-nuke recovery"
            )
        elif channel_type == "forum":
            await guild.create_forum(
                name,
                category=category,
                reason="Kanade anti-nuke recovery"
            )
        else:
            await guild.create_text_channel(
                name,
                category=category,
                topic=snapshot.get("topic"),
                nsfw=snapshot.get("nsfw", False),
                slowmode_delay=snapshot.get("slowmode_delay", 0),
                position=snapshot.get("position", 0),
                reason="Kanade anti-nuke recovery"
            )
        return (
            f"Recreated **{name}**. Original channel IDs, messages, "
            "and all permission overwrites are not guaranteed to be recoverable."
        )
    except (
        discord.Forbidden,
        discord.HTTPException,
        TypeError
    ) as error:
        return f"Channel recovery failed: {error}"
async def restore_deleted_role(guild, entry):
    if not config.get("restore_roles", True):
        return "Role restoration is disabled."
    target = getattr(entry, "target", None)
    target_id = getattr(target, "id", None)
    snapshot = role_snapshots.get(guild.id, {}).get(target_id)
    if not snapshot:
        return "No saved snapshot was available for this role."
    try:
        role = await guild.create_role(
            name=snapshot["name"],
            permissions=discord.Permissions(snapshot["permissions"]),
            colour=discord.Colour(snapshot["color"]),
            hoist=snapshot["hoist"],
            mentionable=snapshot["mentionable"],
            reason="Kanade anti-nuke recovery"
        )
        return (
            f"Recreated role **{role.name}**. Original role ID, "
            "member assignments, and exact role hierarchy may not be restored."
        )
    except (discord.Forbidden, discord.HTTPException) as error:
        return f"Role recovery failed: {error}"
# ==================== KICK RESPONSE ====================
async def punish_actor(guild, actor, reason):
    if actor is None:
        return "Could not identify the actor; no kick attempted."
    if is_whitelisted(guild, actor.id):
        return f"{actor} is protected by the whitelist."
    try:
        member = guild.get_member(actor.id)
        if member is None:
            member = await guild.fetch_member(actor.id)
        if member.id == guild.owner_id:
            return "The server owner cannot be kicked by the bot."
        if guild.me and member.top_role >= guild.me.top_role:
            return (
                f"Could not kick {member}: their top role is equal to "
                "or above the bot's top role."
            )
        await member.kick(
            reason=f"Kanade anti-nuke: {reason}"[:480]
        )
        return f"**Kicked:** {member} (`{member.id}`)"
    except discord.NotFound:
        return "The actor is no longer in the server."
    except discord.Forbidden:
        return (
            "Kick failed. Check Kick Members permission "
            "and role hierarchy."
        )
    except discord.HTTPException as error:
        return f"Kick failed: {error}"
# ==================== LOCKDOWN ====================
async def lockdown_guild(guild, reason="Security lockdown"):
    if (
        guild.id in lockdown_tasks
        and not lockdown_tasks[guild.id].done()
    ):
        return
    if (
        guild.me is None
        or not guild.me.guild_permissions.manage_channels
    ):
        await send_log(
            guild,
            "Lockdown failed",
            "The bot needs Manage Channels permission.",
            discord.Color.red()
        )
        return
    locked = 0
    for channel in guild.text_channels:
        try:
            overwrite = channel.overwrites_for(guild.default_role)
            if overwrite.send_messages is not False:
                overwrite.send_messages = False
                await channel.set_permissions(
                    guild.default_role,
                    overwrite=overwrite,
                    reason=reason
                )
                locked += 1
        except (discord.Forbidden, discord.HTTPException):
            continue
    async def unlock_after_delay():
        try:
            await asyncio.sleep(
                max(15, int(config.get("raid_lockdown_seconds", 120)))
            )
            for channel in guild.text_channels:
                try:
                    overwrite = channel.overwrites_for(guild.default_role)
                    if overwrite.send_messages is False:
                        overwrite.send_messages = None
                        await channel.set_permissions(
                            guild.default_role,
                            overwrite=overwrite,
                            reason="Kanade timed lockdown ended"
                        )
                except (discord.Forbidden, discord.HTTPException):
                    continue
            await send_log(
                guild,
                "Timed lockdown ended",
                "The timed lockdown has ended.",
                discord.Color.green()
            )
        except asyncio.CancelledError:
            raise
    lockdown_tasks[guild.id] = asyncio.create_task(unlock_after_delay())
    await send_log(
        guild,
        "SERVER LOCKDOWN",
        f"{reason}\nText channels updated: {locked}",
        discord.Color.red()
    )
# ==================== AUDIT LOG ANTI-NUKE ====================
def action_name(entry):
    mapping = {
        discord.AuditLogAction.guild_update: "guild_update",
        discord.AuditLogAction.channel_create: "channel_create",
        discord.AuditLogAction.channel_delete: "channel_delete",
        discord.AuditLogAction.channel_update: "channel_update",
        discord.AuditLogAction.role_create: "role_create",
        discord.AuditLogAction.role_delete: "role_delete",
        discord.AuditLogAction.role_update: "role_update",
        discord.AuditLogAction.ban: "ban",
        discord.AuditLogAction.kick: "kick",
        discord.AuditLogAction.bot_add: "bot_add",
        discord.AuditLogAction.webhook_create: "webhook_create",
        discord.AuditLogAction.webhook_update: "webhook_update",
        discord.AuditLogAction.webhook_delete: "webhook_delete",
        discord.AuditLogAction.overwrite_create: "overwrite_update",
        discord.AuditLogAction.overwrite_update: "overwrite_update",
        discord.AuditLogAction.overwrite_delete: "overwrite_update",
        discord.AuditLogAction.member_role_update: "member_role_update"
    }
    return mapping.get(
        entry.action,
        getattr(entry.action, "name", "unknown")
    )
def mark_audit_entry(entry_id):
    if entry_id in recent_audit_id_set:
        return False
    if len(recent_audit_ids) == recent_audit_ids.maxlen:
        recent_audit_id_set.discard(recent_audit_ids[0])
    recent_audit_ids.append(entry_id)
    recent_audit_id_set.add(entry_id)
    return True
async def process_audit_entry(entry):
    guild = entry.guild
    if guild is None or not mark_audit_entry(entry.id):
        return
    if not get_guild_setting(guild, "antinuke_enabled", config.get("antinuke_enabled", True)):
        return
    actor = getattr(entry, "user", None)
    if actor is None:
        try:
            actor = await bot.fetch_user(entry.user_id)
        except (discord.HTTPException, AttributeError):
            actor = None
    before = getattr(entry, "before", None)
    after = getattr(entry, "after", None)
    server_renamed = (
        entry.action == discord.AuditLogAction.guild_update
        and getattr(before, "name", None) is not None
        and getattr(after, "name", None) is not None
        and before.name != after.name
    )
    action = action_name(entry)
    dangerous_permission_change = False
    if entry.action == discord.AuditLogAction.role_update:
        dangerous_permission_change = permissions_became_dangerous(
            getattr(before, "permissions", None),
            getattr(after, "permissions", None)
        )
    # Per-server whitelist check.
    if is_whitelisted(guild, getattr(actor, "id", None)):
        if server_renamed:
            await send_log(
                guild,
                "Server name changed by trusted user",
                f"{actor} changed **{before.name}** "
                f"to **{after.name}**."
            )
        return
    now = time.monotonic()
    key = (guild.id, getattr(actor, "id", 0), action)
    history = action_history[key]
    history.append(now)
    while history and now - history[0] > 10:
        history.popleft()
    threshold_key = (
        "dangerous_permissions"
        if dangerous_permission_change
        else action
    )
    threshold = get_threshold(threshold_key)
    triggered = (
        server_renamed
        or dangerous_permission_change
        or len(history) >= threshold
    )
    if not triggered:
        return
    details = [
        f"**Actor:** {actor or 'Unknown'} "
        f"(`{getattr(actor, 'id', 'unknown')}`)",
        f"**Action:** `{action}`",
        f"**Matching actions in 10 seconds:** "
        f"{len(history)} / {threshold}"
    ]
    if server_renamed:
        old_name = before.name
        new_name = after.name
        try:
            await guild.edit(
                name=old_name,
                reason="Kanade anti-nuke: revert unauthorized server rename"
            )
            details.append(
                f"**Server rename reverted:** "
                f"`{new_name}` → `{old_name}`"
            )
        except (discord.Forbidden, discord.HTTPException) as error:
            details.append(f"**Could not revert server rename:** {error}")
    if entry.action == discord.AuditLogAction.channel_delete:
        details.append(await restore_deleted_channel(guild, entry))
    if entry.action == discord.AuditLogAction.role_delete:
        details.append(await restore_deleted_role(guild, entry))
    if dangerous_permission_change:
        target = getattr(entry, "target", None)
        role = guild.get_role(getattr(target, "id", 0))
        old_permissions = getattr(before, "permissions", None)
        if role is not None and old_permissions is not None:
            try:
                await role.edit(
                    permissions=old_permissions,
                    reason=(
                        "Kanade anti-nuke: "
                        "revert dangerous permission grant"
                    )
                )
                details.append(
                    f"Reverted dangerous permission changes "
                    f"on **{role.name}**."
                )
            except (discord.Forbidden, discord.HTTPException) as error:
                details.append(f"Permission rollback failed: {error}")
    details.append(await punish_actor(guild, actor, action))
    await send_log(
        guild,
        "ANTI-NUKE TRIGGERED",
        "\n".join(details),
        discord.Color.red()
    )
    await notify_owners(guild, "\n".join(details))
    if config.get("lockdown_on_trigger", False):
        await lockdown_guild(guild, "Kanade anti-nuke trigger")
# ==================== EVENTS ====================
@bot.event
async def on_ready():
    global STARTUP_SNAPSHOTS_DONE, SLASH_COMMANDS_SYNCED, PERSISTENT_VIEWS_REGISTERED
    logger.info("Logged in as %s (%s)", bot.user, bot.user.id)

    # Snapshot once per process. Re-running this for every gateway reconnect
    # can add avoidable CPU/memory work on small hosting plans.
    if not STARTUP_SNAPSHOTS_DONE:
        for guild in bot.guilds:
            try:
                await snapshot_guild(guild)
            except Exception:
                logger.exception("Could not snapshot guild %s", guild.id)
            # Yield between guilds so a large bot can keep heartbeats and
            # incoming gateway events responsive during startup.
            await asyncio.sleep(0)
        STARTUP_SNAPSHOTS_DONE = True

    # Global command sync is an API operation; don't repeat it on reconnect.
    if not SLASH_COMMANDS_SYNCED:
        try:
            synced = await bot.tree.sync()
            logger.info("Synced %s slash commands", len(synced))
            SLASH_COMMANDS_SYNCED = True
        except Exception:
            logger.exception("Slash command sync failed; it can retry on next reconnect")
    # Restore persistent J2C control buttons after reconnect/restart.
    if not PERSISTENT_VIEWS_REGISTERED:
        try:
            bot.add_view(J2CControlView())
            bot.add_view(ConfessionReportView())
            bot.add_view(ConfessionSubmitView())
            bot.add_view(TicketOpenButtonView())
            bot.add_view(TicketTypeSelectView())
            bot.add_view(TicketControlView())
            bot.add_view(TicketClosedView())
            for _giveaway_id, _giveaway in config.get("giveaways", {}).items():
                if not _giveaway.get("ended"):
                    bot.add_view(GiveawayJoinView(_giveaway_id))
            PERSISTENT_VIEWS_REGISTERED = True
        except Exception:
            logger.exception("Could not register persistent Kanade controls")
    if not giveaway_expiry_loop.is_running():
        giveaway_expiry_loop.start()
    if not ticket_autoclose_loop.is_running():
        ticket_autoclose_loop.start()
    await update_presence()
    if not presence_refresh_loop.is_running():
        presence_refresh_loop.start()

async def update_presence():
    """Show the live number of servers Kanade is protecting."""
    try:
        server_count = len(bot.guilds)
        await bot.change_presence(
            status=discord.Status.dnd,
            activity=discord.Streaming(
                name=f"Watching over {server_count:,} servers",
                url="https://www.youtube.com/watch?v=Os4WFOsJE_I"
            )
        )
    except discord.HTTPException:
        logger.exception("Presence update failed")

@tasks.loop(minutes=10)
async def presence_refresh_loop():
    await update_presence()

@presence_refresh_loop.before_loop
async def before_presence_refresh_loop():
    await bot.wait_until_ready()

@bot.event
async def on_guild_join(guild):
    await snapshot_guild(guild)
    await update_presence()
@bot.event
async def on_guild_remove(guild):
    await update_presence()
@bot.event
async def on_audit_log_entry_create(entry):
    try:
        await process_audit_entry(entry)
    except Exception:
        logger.exception("Audit log processing failed")
@bot.event
async def on_message_edit(before, after):
    # Message edits are logged only when the message text actually changed.
    if after.guild is None or after.author.bot or before.content == after.content:
        return
    before_text = before.content.strip() or "[empty message]"
    after_text = after.content.strip() or "[empty message]"
    await send_log(
        after.guild,
        "▸ Message Edited",
        f"**Before** {before_text[:1500]}\n\n"
        f"**After** {after_text[:1500]}\n\n"
        f"**Channel** **Text** {after.channel.mention}",
    )

@bot.event
async def on_guild_channel_delete(channel):
    await send_log(
        channel.guild,
        "▸ Channel Deleted",
        f"{channel.mention if hasattr(channel, 'mention') else '#' + channel.name}",
    )
    try:
        channel_snapshots.get(channel.guild.id, {}).pop(channel.id, None)
    except Exception:
        logger.exception("Could not update channel snapshot after deletion")

@bot.event
async def on_invite_create(invite):
    guild = invite.guild
    if guild is None:
        return
    inviter = invite.inviter.mention if invite.inviter else "Unknown user"
    await send_log(
        guild,
        "▸ Invite Created",
        f"`{invite.code}` by **{inviter}**",
    )

@bot.event
async def on_member_join(member):
    # Log every member join independently from raid-protection settings.
    account_age = discord.utils.utcnow() - member.created_at
    age_days = max(0, account_age.days)
    await send_log(
        member.guild,
        "✅ Member Joined",
        f"**User:** {member.mention} (`{member.id}`)\n"
        f"**Account Created:** {discord.utils.format_dt(member.created_at, style='R')}\n"
        f"**Account Age:** {age_days} days\n"
        f"**Member Count:** {member.guild.member_count or 'Unknown'}",
        discord.Color.green()
    )
    if member.bot or not config.get("raid_protection", True):
        return
    now = time.monotonic()
    history = join_history[member.guild.id]
    history.append(now)
    window = max(3, int(config.get("raid_window_seconds", 10)))
    while history and now - history[0] > window:
        history.popleft()
    threshold = max(2, int(config.get("raid_join_threshold", 8)))
    if len(history) >= threshold:
        await send_log(
            member.guild,
            "⚠️ Possible Join Raid",
            f"{len(history)} members joined within {window} seconds. Review the join activity.",
            discord.Color.orange()
        )
        history.clear()

@bot.event
async def on_member_remove(member):
    await send_log(
        member.guild,
        "🚪 Member Left",
        f"**User:** {member} (`{member.id}`)\n**Member Count:** {member.guild.member_count or 'Unknown'}",
        discord.Color.orange()
    )

@bot.event
async def on_member_update(before, after):
    """Log member role changes in a compact, readable security-log embed."""
    if before.roles == after.roles:
        return
    before_ids = {role.id for role in before.roles}
    after_ids = {role.id for role in after.roles}
    added = [role for role in after.roles if role.id not in before_ids and role.name != "@everyone"]
    removed = [role for role in before.roles if role.id not in after_ids and role.name != "@everyone"]
    if not added and not removed:
        return
    lines = [f"**User:** {after.mention} (`{after.id}`)"]
    if added:
        lines.append("**Added Roles:** " + ", ".join(role.mention for role in added))
    if removed:
        lines.append("**Removed Roles:** " + ", ".join(role.mention for role in removed))
    await send_log(after.guild, "◇ Member Roles Updated", "\n".join(lines), discord.Color.blurple())

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return
    # Join-to-create is handled before ordinary voice logging.
    if after.channel is not None:
        await handle_j2c_join(member, after.channel)
    if before.channel is not None:
        await handle_j2c_leave(member, before.channel)
    if before.channel == after.channel:
        return
    if before.channel is None and after.channel is not None:
        title = f"{VOICE_EMOJI} Member Joined VC"
        detail = f"**User:** {member.mention} (`{member.id}`)\n**Voice Channel:** {after.channel.mention}"
        color = discord.Color.green()
    elif before.channel is not None and after.channel is None:
        title = f"{VOICE_EMOJI} Member Left VC"
        detail = f"**User:** {member.mention} (`{member.id}`)\n**Voice Channel:** {before.channel.mention}"
        color = discord.Color.orange()
    else:
        title = f"{LOG_EMOJI} Member Switched VC"
        detail = f"**User:** {member.mention} (`{member.id}`)\n**From:** {before.channel.mention}\n**To:** {after.channel.mention}"
        color = discord.Color.blurple()
    await send_log(member.guild, title, detail, color)
@bot.event
async def on_guild_channel_create(channel):
    await send_log(channel.guild, "▸ Channel Created", f"{channel.mention if hasattr(channel, 'mention') else '#' + channel.name}")
    # Refresh only the changed channel instead of rebuilding the whole guild snapshot.
    try:
        guild = channel.guild
        if guild.id not in channel_snapshots:
            await snapshot_guild(guild)
            return
        channel_snapshots[guild.id][channel.id] = {
            "name": channel.name,
            "type": str(channel.type),
            "category_id": channel.category_id,
            "position": channel.position,
            "topic": getattr(channel, "topic", None),
            "nsfw": getattr(channel, "nsfw", False),
            "slowmode_delay": getattr(channel, "slowmode_delay", 0),
        }
    except Exception:
        logger.exception("Could not update channel snapshot for guild %s", getattr(channel.guild, "id", "unknown"))

@bot.event
async def on_guild_role_create(role):
    await send_log(role.guild, "🆕 Role Created", f"**Name:** {role.name}\n**ID:** `{role.id}`\n**Color:** `{role.color}`\n**Mentionable:** `{role.mentionable}`", discord.Color.green())
    # Refresh only the new role to avoid repeated full-guild scans during bursts.
    try:
        guild = role.guild
        if guild.id not in role_snapshots:
            await snapshot_guild(guild)
            return
        if role != guild.default_role:
            role_snapshots[guild.id][role.id] = {
                "name": role.name,
                "permissions": role.permissions.value,
                "color": role.color.value,
                "hoist": role.hoist,
                "mentionable": role.mentionable,
                "position": role.position,
            }
    except Exception:
        logger.exception("Could not update role snapshot for guild %s", getattr(role.guild, "id", "unknown"))
# ==================== COMMAND HELPERS ====================
async def manager_check(ctx):
    if ctx.guild is None:
        await ctx.reply(
            "This command can only be used in a server.",
            mention_author=False
        )
        return False
    if not has_admin_access(ctx.author):
        await ctx.reply(
            "Administrator permission or bot-owner access is required.",
            mention_author=False
        )
        return False
    return True
# ==================== KANADE COMMUNITY FEATURES ====================
ROLE_PAGE_SIZE = 10

class RoleListView(discord.ui.View):
    def __init__(self, ctx, pages):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.pages = pages
        self.index = 0
        self.message = None
        self._sync()

    def _sync(self):
        self.previous.disabled = self.index <= 0
        self.next.disabled = self.index >= len(self.pages) - 1

    async def interaction_check(self, interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Only the person who ran `roles` can use these controls.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, custom_id="kanade_roles_previous")
    async def previous(self, interaction, button):
        self.index -= 1
        self._sync()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, custom_id="kanade_roles_next")
    async def next(self, interaction, button):
        self.index += 1
        self._sync()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)


@bot.command(name="roles", aliases=["rolelist", "serverroles"])
@commands.guild_only()
async def roles_command(ctx):
    """Display every server role from highest to lowest in Discord hierarchy order."""
    guild = ctx.guild
    roles = sorted(guild.roles, key=lambda role: (role.position, role.id), reverse=True)
    pages = []
    total = len(roles)
    chunks = [roles[i:i + ROLE_PAGE_SIZE] for i in range(0, total, ROLE_PAGE_SIZE)] or [[]]
    for page_index, chunk in enumerate(chunks):
        lines = []
        for role in chunk:
            if role.is_default():
                kind = "@everyone"
            elif role.managed:
                kind = "Managed / integration"
            else:
                flags = []
                if role.hoist: flags.append("Displayed separately")
                if role.mentionable: flags.append("Mentionable")
                if role.permissions.administrator: flags.append("Administrator")
                if role.permissions.manage_roles: flags.append("Manage Roles")
                kind = ", ".join(flags) if flags else "Standard role"
            member_count = len(role.members)
            role_label = "@everyone" if role.is_default() else role.mention
            lines.append(f"**{role.position}. {discord.utils.escape_markdown(role.name)}** — {discord.utils.escape_mentions(role_label)}\n`ID: {role.id}` • {member_count} member(s) • {kind}")
        embed = StyledEmbed(
            title=f"{guild.name} — Roles",
            description="\n\n".join(lines) or "No roles found.",
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"{total} roles • Highest to lowest • Page {page_index + 1}/{len(chunks)}")
        pages.append(embed)
    view = RoleListView(ctx, pages)
    await ctx.reply(embed=pages[0], view=view if len(pages) > 1 else None, mention_author=False, allowed_mentions=discord.AllowedMentions.none())


def _guild_feature_settings(key, guild):
    return config.setdefault(key, {}).setdefault(str(guild.id), {})


def _j2c_room_data(guild):
    rooms = config.setdefault("j2c_rooms", {}).setdefault(str(guild.id), {})
    if not isinstance(rooms, dict):
        rooms = {}
        config["j2c_rooms"][str(guild.id)] = rooms
    return rooms


def j2c_voice_channel_for_interaction(interaction):
    if not interaction.guild:
        return None
    if isinstance(interaction.channel, discord.VoiceChannel):
        return interaction.channel
    for voice_id, room in _j2c_room_data(interaction.guild).items():
        if room.get("control_channel_id") == interaction.channel_id:
            channel = interaction.guild.get_channel(int(voice_id))
            if isinstance(channel, discord.VoiceChannel):
                return channel
    return None


def _j2c_is_owner(interaction):
    channel = j2c_voice_channel_for_interaction(interaction)
    room = _j2c_room_data(interaction.guild).get(str(channel.id), {}) if channel else {}
    return room.get("owner_id") == interaction.user.id


class J2CActionModal(discord.ui.Modal):
    def __init__(self, action):
        super().__init__(title={"limit":"Set user limit", "kick":"Kick from voice", "ban":"Ban from this room", "unban":"Unban from this room", "permit":"Allow a user", "transfer":"Transfer ownership"}.get(action, "J2C Control"))
        self.action = action
        label = "User limit (0 = unlimited)" if action == "limit" else "User ID"
        placeholder = "0–99" if action == "limit" else "Paste the Discord user ID"
        self.target = discord.ui.TextInput(label=label, placeholder=placeholder, required=True, max_length=25)
        self.add_item(self.target)

    async def on_submit(self, interaction):
        channel = j2c_voice_channel_for_interaction(interaction)
        if channel is None:
            return await interaction.response.send_message("Use the private controls channel created for your J2C room.", ephemeral=True)
        room = _j2c_room_data(interaction.guild).get(str(channel.id), {})
        if room.get("owner_id") != interaction.user.id:
            return await interaction.response.send_message("Only this room's owner can use its controls.", ephemeral=True)
        raw = str(self.target.value).strip()
        try:
            value = int(raw)
        except ValueError:
            return await interaction.response.send_message("Enter a valid numeric ID or limit.", ephemeral=True)
        if self.action == "limit":
            if not 0 <= value <= 99:
                return await interaction.response.send_message("The limit must be between 0 and 99.", ephemeral=True)
            await channel.edit(user_limit=value, reason=f"J2C owner {interaction.user.id} changed the limit")
            return await interaction.response.send_message(f"Voice limit set to **{'Unlimited' if value == 0 else value}**.", ephemeral=True)
        if self.action == "transfer":
            target = interaction.guild.get_member(value)
            if target is None or target not in channel.members:
                return await interaction.response.send_message("The new owner must be a member currently in this voice channel.", ephemeral=True)
            previous_owner = interaction.user
            room["owner_id"] = target.id
            save_config()
            await channel.set_permissions(target, view_channel=True, connect=True, speak=True, send_messages=True)
            await channel.set_permissions(previous_owner, overwrite=None)
            if isinstance(interaction.channel, discord.TextChannel):
                await interaction.channel.set_permissions(target, view_channel=True, send_messages=True, read_message_history=True)
                await interaction.channel.set_permissions(previous_owner, overwrite=None)
            return await interaction.response.send_message(f"Ownership transferred to {target.mention}.", ephemeral=True)
        target = interaction.guild.get_member(value)
        banned = set(int(x) for x in room.get("banned_ids", []) if str(x).isdigit())
        if self.action == "unban":
            if target is None:
                return await interaction.response.send_message("That user must be in this server before Kanade can clear their channel permission override. The ban remains active.", ephemeral=True)
            banned.discard(value)
            room["banned_ids"] = sorted(banned)
            await channel.set_permissions(target, overwrite=None)
            save_config()
            return await interaction.response.send_message(f"{target.mention} is unbanned from this room.", ephemeral=True)
        if target is None:
            return await interaction.response.send_message("That member is not in this server.", ephemeral=True)
        if self.action == "kick":
            if target not in channel.members:
                return await interaction.response.send_message("That member is not in this voice channel.", ephemeral=True)
            await target.move_to(None, reason=f"Removed by J2C room owner {interaction.user.id}")
            return await interaction.response.send_message(f"Disconnected {target.mention} from the room.", ephemeral=True)
        if self.action == "ban":
            banned.add(target.id)
            room["banned_ids"] = sorted(banned)
            await channel.set_permissions(target, view_channel=True, connect=False)
            if target in channel.members:
                await target.move_to(None, reason=f"J2C room ban by {interaction.user.id}")
            save_config()
            return await interaction.response.send_message(f"{target.mention} is banned from this room.", ephemeral=True)
        if self.action == "permit":
            await channel.set_permissions(target, view_channel=True, connect=True, speak=True, send_messages=True)
            return await interaction.response.send_message(f"{target.mention} can now join this room.", ephemeral=True)


class J2CControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _check_owner(self, interaction):
        if not interaction.guild or j2c_voice_channel_for_interaction(interaction) is None:
            await interaction.response.send_message("These controls only work in the private controls channel for a J2C room.", ephemeral=True)
            return False
        if not _j2c_is_owner(interaction):
            await interaction.response.send_message("Only the owner of this voice channel can use these controls.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Lock", emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="kanade_j2c_lock")
    async def lock(self, interaction, button):
        if not await self._check_owner(interaction): return
        await j2c_voice_channel_for_interaction(interaction).set_permissions(interaction.guild.default_role, connect=False, view_channel=False)
        await interaction.response.send_message("Your voice channel is now locked.", ephemeral=True)

    @discord.ui.button(label="Unlock", emoji="🔓", style=discord.ButtonStyle.secondary, custom_id="kanade_j2c_unlock")
    async def unlock(self, interaction, button):
        if not await self._check_owner(interaction): return
        await j2c_voice_channel_for_interaction(interaction).set_permissions(interaction.guild.default_role, connect=True, view_channel=True)
        await interaction.response.send_message("Your voice channel is now unlocked.", ephemeral=True)

    @discord.ui.button(label="Limit", emoji="👥", style=discord.ButtonStyle.secondary, custom_id="kanade_j2c_limit")
    async def limit(self, interaction, button):
        if await self._check_owner(interaction): await interaction.response.send_modal(J2CActionModal("limit"))

    @discord.ui.button(label="Permit", emoji="➕", style=discord.ButtonStyle.secondary, custom_id="kanade_j2c_permit")
    async def permit(self, interaction, button):
        if await self._check_owner(interaction): await interaction.response.send_modal(J2CActionModal("permit"))

    @discord.ui.button(label="Kick", emoji="👢", style=discord.ButtonStyle.danger, custom_id="kanade_j2c_kick")
    async def kick(self, interaction, button):
        if await self._check_owner(interaction): await interaction.response.send_modal(J2CActionModal("kick"))

    @discord.ui.button(label="Ban", emoji="⛔", style=discord.ButtonStyle.danger, custom_id="kanade_j2c_ban")
    async def ban(self, interaction, button):
        if await self._check_owner(interaction): await interaction.response.send_modal(J2CActionModal("ban"))

    @discord.ui.button(label="Unban", emoji="♻️", style=discord.ButtonStyle.success, custom_id="kanade_j2c_unban")
    async def unban(self, interaction, button):
        if await self._check_owner(interaction): await interaction.response.send_modal(J2CActionModal("unban"))

    @discord.ui.button(label="Banned", emoji="📋", style=discord.ButtonStyle.secondary, custom_id="kanade_j2c_banned")
    async def banned(self, interaction, button):
        if not await self._check_owner(interaction): return
        channel = j2c_voice_channel_for_interaction(interaction)
        room = _j2c_room_data(interaction.guild).get(str(channel.id), {})
        ids = [str(x) for x in room.get("banned_ids", [])]
        listing = "\n".join(f"• `{user_id}`" for user_id in ids) if ids else "No users are banned from this room."
        await interaction.response.send_message(embed=feature_embed("Room Ban List", listing), ephemeral=True)

    @discord.ui.button(label="Transfer", emoji="👑", style=discord.ButtonStyle.primary, custom_id="kanade_j2c_transfer")
    async def transfer(self, interaction, button):
        if await self._check_owner(interaction): await interaction.response.send_modal(J2CActionModal("transfer"))


async def handle_j2c_join(member, joined_channel):
    settings = config.get("j2c_settings", {}).get(str(member.guild.id), {})
    if not settings or joined_channel.id != settings.get("lobby_channel_id"):
        return
    category = member.guild.get_channel(settings.get("category_id", 0)) if settings.get("category_id") else joined_channel.category
    overwrites = {
        member.guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
        member: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, send_messages=True),
    }
    if member.guild.me:
        overwrites[member.guild.me] = discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True, move_members=True, send_messages=True, read_message_history=True)
    room_name = f"{member.display_name[:85]}'s room"
    room_channel = None
    control_channel = None
    try:
        room_channel = await member.guild.create_voice_channel(room_name, category=category, overwrites=overwrites, reason=f"J2C room for {member} ({member.id})")
        control_overwrites = {
            member.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        if member.guild.me:
            control_overwrites[member.guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, read_message_history=True, manage_channels=True)
        safe_name = re.sub(r"[^a-z0-9-]+", "-", member.display_name.lower()).strip("-")[:40] or str(member.id)
        control_channel = await member.guild.create_text_channel(f"{safe_name}-controls", category=category, overwrites=control_overwrites, reason=f"Private J2C controls for {member} ({member.id})")
        rooms = _j2c_room_data(member.guild)
        rooms[str(room_channel.id)] = {"owner_id": member.id, "banned_ids": [], "control_channel_id": control_channel.id, "created_at": int(time.time())}
        save_config()
        try:
            await member.move_to(room_channel, reason="Join-to-create voice channel")
        except discord.HTTPException:
            await control_channel.delete(reason="J2C member could not be moved into their room")
            await room_channel.delete(reason="J2C member could not be moved into their room")
            rooms.pop(str(room_channel.id), None)
            save_config()
            return
        embed = feature_embed("Your Private Voice Channel", f"Welcome {member.mention}! You own this room. Use the controls below to manage access, limits, and members.\n\n**Owner:** {member.mention}\n**Voice Channel:** {room_channel.mention}\n**Private Controls:** {control_channel.mention}")
        await control_channel.send(embed=embed, view=J2CControlView())
        await send_log(member.guild, "J2C Room Created", f"**Owner:** {member.mention} (`{member.id}`)\n**Channel:** {room_channel.mention}", discord.Color.green())
    except (discord.Forbidden, discord.HTTPException):
        logger.exception("J2C room creation failed in guild %s", member.guild.id)
        for partial_channel in (control_channel, room_channel):
            if partial_channel is not None:
                try:
                    await partial_channel.delete(reason="Cleaning up incomplete J2C room creation")
                except (discord.Forbidden, discord.HTTPException):
                    pass


async def handle_j2c_leave(member, left_channel):
    rooms = _j2c_room_data(member.guild)
    if str(left_channel.id) not in rooms or left_channel.members:
        return
    room_data = rooms.get(str(left_channel.id), {})
    control_channel = member.guild.get_channel(room_data.get("control_channel_id", 0)) if room_data else None
    try:
        if control_channel:
            await control_channel.delete(reason="J2C room became empty")
        await left_channel.delete(reason="J2C room became empty")
    except (discord.Forbidden, discord.HTTPException):
        logger.exception("Could not delete empty J2C channel %s", left_channel.id)
        return
    rooms.pop(str(left_channel.id), None)
    save_config()
    await send_log(member.guild, "J2C Room Deleted", f"**Channel:** `{left_channel.name}`\n**Last member:** {member.mention} (`{member.id}`)", discord.Color.orange())


@bot.group(name="j2c", invoke_without_command=True)
@commands.guild_only()
async def j2c_group(ctx):
    if not await require_premium(ctx): return
    await ctx.reply(embed=feature_embed("Join-to-Create", "`!j2c setup [category]` — Create the permanent `j2c` lobby.\n`!j2c status` — Show the current setup.\n`!j2c disable` — Disable join-to-create.\n\nJoin the lobby and Kanade will create your private voice channel automatically."), mention_author=False)

@j2c_group.command(name="setup")
async def j2c_setup(ctx, category: discord.CategoryChannel = None):
    if not await require_premium(ctx): return
    if not await manager_check(ctx): return
    try:
        category = category or await ctx.guild.create_category("Join to Create")
        lobby = discord.utils.get(category.voice_channels, name="j2c")
        if lobby is None:
            lobby = await ctx.guild.create_voice_channel("j2c", category=category, reason="Kanade J2C lobby")
        await lobby.set_permissions(ctx.guild.default_role, view_channel=True, connect=True)
        settings = {"category_id": category.id, "lobby_channel_id": lobby.id}
        config.setdefault("j2c_settings", {})[str(ctx.guild.id)] = settings
        save_config()
        await ctx.reply(embed=feature_embed("J2C Enabled", f"Permanent lobby: {lobby.mention}\nCategory: {category.name}\n\nJoin the lobby to create your own private voice channel."), mention_author=False)
    except discord.Forbidden:
        await ctx.reply("Kanade needs Manage Channels, Move Members, and Connect permissions to set up J2C.", mention_author=False)
    except discord.HTTPException as exc:
        await ctx.reply(f"J2C setup failed: `{str(exc)[:300]}`", mention_author=False)

@j2c_group.command(name="status")
async def j2c_status(ctx):
    if not await require_premium(ctx): return
    settings = config.get("j2c_settings", {}).get(str(ctx.guild.id), {})
    lobby = ctx.guild.get_channel(settings.get("lobby_channel_id", 0)) if settings else None
    await ctx.reply(embed=feature_embed("J2C Status", f"Lobby: {lobby.mention if lobby else 'Not configured'}"), mention_author=False)

@j2c_group.command(name="disable")
async def j2c_disable(ctx):
    if not await require_premium(ctx): return
    if not await manager_check(ctx): return
    config.setdefault("j2c_settings", {}).pop(str(ctx.guild.id), None)
    save_config()
    await ctx.reply(embed=feature_embed("J2C Disabled", "Join-to-create is disabled. Existing user rooms will remain until empty."), mention_author=False)


async def add_confession_reactions(message):
    """Add lightweight public reactions to a published anonymous confession."""
    for emoji in ("❤️", "😂", "😭"):
        try:
            await message.add_reaction(emoji)
        except (discord.Forbidden, discord.HTTPException):
            break


class ConfessionReportModal(discord.ui.Modal):
    def __init__(self, confession_id):
        super().__init__(title="Report confession")
        self.confession_id = confession_id
        self.reason = discord.ui.TextInput(label="Why are you reporting this confession?", placeholder="Explain the concern", style=discord.TextStyle.paragraph, required=True, max_length=800)
        self.add_item(self.reason)

    async def on_submit(self, interaction):
        settings = config.get("confession_settings", {}).get(str(interaction.guild_id), {})
        staff_channel = interaction.guild.get_channel(settings.get("staff_channel_id", 0)) if interaction.guild and settings else None
        if staff_channel is None:
            return await interaction.response.send_message("Confession reports are not configured with a staff review channel.", ephemeral=True)
        report_embed = feature_embed("Confession Report", f"**Confession ID:** `{self.confession_id}`\n**Reported by:** {interaction.user.mention} (`{interaction.user.id}`)\n**Reason:** {discord.utils.escape_mentions(self.reason.value)}")
        await staff_channel.send(embed=report_embed, allowed_mentions=discord.AllowedMentions.none())
        await interaction.response.send_message("Your report has been sent to the server's staff team.", ephemeral=True)


class ConfessionReplyModal(discord.ui.Modal):
    def __init__(self, confession_id):
        super().__init__(title="Reply anonymously")
        self.confession_id = confession_id
        self.reply_text = discord.ui.TextInput(label="Your anonymous reply", style=discord.TextStyle.paragraph, min_length=2, max_length=1200, required=True)
        self.add_item(self.reply_text)

    async def on_submit(self, interaction):
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("Use this inside the server.", ephemeral=True)
        if not (is_owner_id(interaction.user.id) or interaction.user.id in config.get("premium_admin_ids", []) or is_premium_guild(guild)):
            return await interaction.response.send_message("Anonymous replies are available in Premium-enabled servers.", ephemeral=True)
        body = discord.utils.escape_mentions(self.reply_text.value.strip())
        embed = feature_embed("Anonymous Reply", f"**Reply to:** `{self.confession_id}`\n\n{body}")
        embed.set_footer(text="Kanade Confessions • Anonymous reply")
        try:
            published = await interaction.channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            await add_confession_reactions(published)
            await interaction.response.send_message("Your anonymous reply has been posted.", ephemeral=True)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message("Kanade could not post your reply in this channel.", ephemeral=True)


class ConfessionReportView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Reply Anonymously", emoji="💬", style=discord.ButtonStyle.primary, custom_id="kanade_confession_reply")
    async def reply(self, interaction, button):
        confession_id = "Unknown"
        if interaction.message and interaction.message.embeds:
            for field in interaction.message.embeds[0].fields:
                if field.name.lower() == "confession id":
                    confession_id = field.value.strip("`")
                    break
        await interaction.response.send_modal(ConfessionReplyModal(confession_id))

    @discord.ui.button(label="Report", emoji="🚩", style=discord.ButtonStyle.secondary, custom_id="kanade_confession_report")
    async def report(self, interaction, button):
        confession_id = "Unknown"
        if interaction.message and interaction.message.embeds:
            for field in interaction.message.embeds[0].fields:
                if field.name.lower() == "confession id":
                    confession_id = field.value.strip("`")
                    break
        await interaction.response.send_modal(ConfessionReportModal(confession_id))


class ConfessionSubmitModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Submit an anonymous confession")
        self.confession_text = discord.ui.TextInput(
            label="Your confession", placeholder="Write what you want to share...",
            style=discord.TextStyle.paragraph, required=True, min_length=3, max_length=1800
        )
        self.add_item(self.confession_text)

    async def on_submit(self, interaction):
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("Use this form inside a server.", ephemeral=True)
        if not (is_owner_id(interaction.user.id) or interaction.user.id in config.get("premium_admin_ids", []) or is_premium_guild(guild)):
            return await interaction.response.send_message("Confessions are available in Premium-enabled servers.", ephemeral=True)
        settings = config.get("confession_settings", {}).get(str(guild.id), {})
        target = guild.get_channel(settings.get("channel_id", 0)) if settings else None
        if not settings.get("enabled") or target is None:
            return await interaction.response.send_message("Confessions are not configured in this server.", ephemeral=True)
        confession_id = secrets.token_hex(4).upper()
        body = discord.utils.escape_mentions(self.confession_text.value.strip())
        embed = feature_embed("Anonymous Confession", body)
        embed.add_field(name="Confession ID", value=f"`{confession_id}`", inline=False)
        embed.set_footer(text="Kanade Confessions • Be respectful")
        try:
            published = await target.send(embed=embed, view=ConfessionReportView(), allowed_mentions=discord.AllowedMentions.none())
            await add_confession_reactions(published)
            staff_channel = guild.get_channel(settings.get("staff_channel_id", 0))
            if staff_channel:
                await staff_channel.send(embed=feature_embed("Confession Submitted", f"**ID:** `{confession_id}`\n**Author ID:** `{interaction.user.id}`\n**Public channel:** {target.mention}"))
            await interaction.response.send_message("Your confession was submitted anonymously.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("Kanade cannot send embeds in the configured confession channel.", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message("Discord could not publish your confession. Please try again.", ephemeral=True)


class ConfessionSubmitView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Submit Confession", emoji="📝", style=discord.ButtonStyle.primary, custom_id="kanade_confession_submit")
    async def submit(self, interaction, button):
        await interaction.response.send_modal(ConfessionSubmitModal())


async def post_confession_panel(channel):
    panel = feature_embed("Anonymous Confessions", "Have something you want to share? Submit it privately using the button below. Your name will not appear on the public confession.\n\nReact to confessions with ❤️ 😂 😭 to show your support.")
    panel.set_footer(text="Kanade Confessions • Keep it respectful")
    await channel.send(embed=panel, view=ConfessionSubmitView(), allowed_mentions=discord.AllowedMentions.none())


@bot.group(name="confession", invoke_without_command=True)
@commands.guild_only()
async def confession_group(ctx, confession_channel: discord.TextChannel = None):
    if not await require_premium(ctx): return
    if confession_channel is not None:
        if ctx.author.id != ctx.guild.owner_id:
            return await ctx.reply(embed=feature_embed("⚒ Error", "Only the server owner can assign the public confession channel with `!confession #channel`."), mention_author=False)
        perms = confession_channel.permissions_for(ctx.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            return await ctx.reply(embed=feature_embed("⚒ Error", "Kanade needs View Channel, Send Messages, and Embed Links in that channel."), mention_author=False)
        settings = config.setdefault("confession_settings", {}).setdefault(str(ctx.guild.id), {})
        settings.update({"channel_id": confession_channel.id, "enabled": True})
        save_config()
        try:
            await post_confession_panel(confession_channel)
        except (discord.Forbidden, discord.HTTPException):
            return await ctx.reply(embed=feature_embed("Channel Saved, Panel Failed", f"{confession_channel.mention} is configured, but Kanade could not post the confession panel. Check Send Messages and Embed Links permissions.", color=discord.Color.orange()), mention_author=False)
        return await ctx.reply(embed=feature_embed("Confession Channel Set", f"Anonymous confessions will now be posted in {confession_channel.mention}. A Submit Confession button has been posted there.", color=discord.Color.green()), mention_author=False)
    await ctx.reply(embed=feature_embed("Confessions", "`!confession #channel` — Set the public confession channel (server owner only).\n`!confession setup #channel [#staff-channel]` — Configure the channel and optional private staff review.\n`!confession status` — Show configuration.\n`!confession prompt` — Post a community prompt.\n`!confession off` — Disable submissions.\n`!confess <message>` or `/confess` — Submit anonymously.\n\nConfessions support anonymous replies, reactions, private reports, and community prompts. The public post does not reveal the sender."), mention_author=False)

@confession_group.command(name="setup")
async def confession_setup(ctx, confession_channel: discord.TextChannel, staff_channel: discord.TextChannel = None):
    if not await require_premium(ctx): return
    if ctx.author.id != ctx.guild.owner_id:
        return await ctx.reply(embed=feature_embed("⚒ Error", "Only the server owner can configure the confession destination."), mention_author=False)
    config.setdefault("confession_settings", {})[str(ctx.guild.id)] = {"channel_id": confession_channel.id, "staff_channel_id": staff_channel.id if staff_channel else 0, "enabled": True}
    save_config()
    try:
        await post_confession_panel(confession_channel)
    except (discord.Forbidden, discord.HTTPException):
        return await ctx.reply(embed=feature_embed("Configuration Saved, Panel Failed", "The channel was saved, but Kanade could not post the panel. Check Send Messages and Embed Links permissions."), mention_author=False)
    await ctx.reply(embed=feature_embed("Confessions Configured", f"Public channel: {confession_channel.mention}\nStaff review: {staff_channel.mention if staff_channel else 'Not configured'}\nA Submit Confession button has been posted in the public channel."), mention_author=False)

@confession_group.command(name="prompt")
async def confession_prompt(ctx):
    if not await require_premium(ctx): return
    if not await manager_check(ctx): return
    settings = config.get("confession_settings", {}).get(str(ctx.guild.id), {})
    target = ctx.guild.get_channel(settings.get("channel_id", 0)) if settings else None
    if not settings.get("enabled") or target is None:
        return await ctx.reply("Confessions are not configured in this server yet.", mention_author=False)
    prompts = [
        "What's something you've wanted to say but never had the courage to?",
        "What's one small thing that made your week better?",
        "What's a lesson you learned the hard way?",
        "If you could clear up one misunderstanding, what would it be?",
        "What's an unpopular opinion you have?",
        "What's one thing you wish people understood about you?",
    ]
    await target.send(embed=feature_embed("Confession Prompt", secrets.choice(prompts)), allowed_mentions=discord.AllowedMentions.none())
    await ctx.reply(f"Posted a new anonymous confession prompt in {target.mention}.", mention_author=False)


@confession_group.command(name="status")
async def confession_status(ctx):
    if not await require_premium(ctx): return
    settings = config.get("confession_settings", {}).get(str(ctx.guild.id), {})
    channel = ctx.guild.get_channel(settings.get("channel_id", 0)) if settings else None
    staff = ctx.guild.get_channel(settings.get("staff_channel_id", 0)) if settings else None
    await ctx.reply(embed=feature_embed("Confession Status", f"Status: **{'Enabled' if settings.get('enabled') and channel else 'Disabled'}**\nPublic channel: {channel.mention if channel else 'Not configured'}\nStaff review: {staff.mention if staff else 'Not configured'}"), mention_author=False)

@confession_group.command(name="off")
async def confession_off(ctx):
    if not await require_premium(ctx): return
    if not await manager_check(ctx): return
    settings = config.setdefault("confession_settings", {}).setdefault(str(ctx.guild.id), {})
    settings["enabled"] = False
    save_config()
    await ctx.reply("Anonymous confession submissions are now disabled.", mention_author=False)

@bot.command(name="confess")
@commands.guild_only()
async def confess_command(ctx, *, message: str):
    if not await require_premium(ctx): return
    settings = config.get("confession_settings", {}).get(str(ctx.guild.id), {})
    target = ctx.guild.get_channel(settings.get("channel_id", 0)) if settings else None
    if not settings.get("enabled") or target is None:
        return await ctx.reply("Confessions are not configured in this server yet.", mention_author=False)
    message = message.strip()
    if len(message) < 3 or len(message) > 1800:
        return await ctx.reply("Your confession must be between 3 and 1,800 characters.", mention_author=False)
    if ctx.message:
        try: await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException): pass
    confession_id = secrets.token_hex(4).upper()
    embed = feature_embed("Anonymous Confession", discord.utils.escape_mentions(message))
    embed.add_field(name="Confession ID", value=f"`{confession_id}`", inline=False)
    embed.set_footer(text="Kanade Confessions • Be respectful")
    try:
        published = await target.send(embed=embed, view=ConfessionReportView(), allowed_mentions=discord.AllowedMentions.none())
        await add_confession_reactions(published)
        staff_channel = ctx.guild.get_channel(settings.get("staff_channel_id", 0))
        if staff_channel:
            staff_embed = feature_embed("Confession Submitted", f"**ID:** `{confession_id}`\n**Author ID:** `{ctx.author.id}`\n**Public channel:** {target.mention}")
            await staff_channel.send(embed=staff_embed)
        await ctx.reply("Your confession was submitted anonymously.", delete_after=6, mention_author=False)
    except discord.Forbidden:
        await ctx.reply("Kanade cannot send messages in the configured confession channel.", mention_author=False)


# ==================== COMMUNITY FEATURES ====================

def _guild_feature_map(config_key, guild_id):
    mapping = config.setdefault(config_key, {})
    if not isinstance(mapping, dict):
        mapping = {}
        config[config_key] = mapping
    return mapping.setdefault(str(guild_id), {})


def _parse_duration(value):
    match = re.fullmatch(r"(\d+)([smhd])", value.strip().lower())
    if not match:
        return None
    amount = int(match.group(1))
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
    seconds = amount * multiplier
    return seconds if 30 <= seconds <= 30 * 86400 else None


def _xp_level(total_xp):
    # Smooth progression: level N requires 100*N*(N+1)/2 total XP.
    level = 0
    while total_xp >= 100 * (level + 1) * (level + 2) // 2:
        level += 1
    current_floor = 100 * level * (level + 1) // 2
    next_floor = 100 * (level + 1) * (level + 2) // 2
    return level, total_xp - current_floor, next_floor - current_floor


async def _award_message_xp(message):
    if message.guild is None or message.author.bot or not message.content.strip():
        return
    guild_id, user_id = str(message.guild.id), str(message.author.id)
    settings = config.get("xp_settings", {}).get(guild_id, {})
    if settings.get("enabled", True) is False:
        return
    cooldown = max(5, min(300, int(settings.get("cooldown", 60))))
    now = time.monotonic()
    key = (guild_id, user_id)
    if now - XP_COOLDOWNS.get(key, 0) < cooldown:
        return
    XP_COOLDOWNS[key] = now
    data = config.setdefault("xp_data", {}).setdefault(guild_id, {})
    record = data.setdefault(user_id, {"xp": 0, "messages": 0})
    old_level = _xp_level(int(record.get("xp", 0)))[0]
    gained = secrets.randbelow(11) + 15
    record["xp"] = int(record.get("xp", 0)) + gained
    record["messages"] = int(record.get("messages", 0)) + 1
    new_level = _xp_level(record["xp"])[0]
    save_config()
    if new_level > old_level and settings.get("announce", True):
        try:
            await message.channel.send(
                f"🎉 {message.author.mention} reached **Level {new_level}**! Keep chatting.",
                allowed_mentions=discord.AllowedMentions(users=True)
            )
        except (discord.Forbidden, discord.HTTPException):
            pass


@bot.listen("on_message")
async def kanade_xp_listener(message):
    try:
        await _award_message_xp(message)
    except Exception:
        logger.exception("XP listener failed")


@bot.command(name="rank", aliases=["level"])
@commands.guild_only()
async def rank_command(ctx, member: discord.Member = None):
    member = member or ctx.author
    guild_data = config.get("xp_data", {}).get(str(ctx.guild.id), {})
    record = guild_data.get(str(member.id), {"xp": 0, "messages": 0})
    total = int(record.get("xp", 0))
    level, progress, needed = _xp_level(total)
    ranked = sorted(guild_data.items(), key=lambda item: int(item[1].get("xp", 0)), reverse=True)
    position = next((i for i, (uid, _) in enumerate(ranked, 1) if uid == str(member.id)), None)
    embed = feature_embed(f"{member.display_name} • Rank", f"**Level:** {level}\n**XP:** {total:,}\n**Progress:** {progress:,} / {needed:,}\n**Messages counted:** {int(record.get('messages', 0)):,}\n**Server rank:** #{position or '—'}")
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="leaderboard", aliases=["lb"])
@commands.guild_only()
async def leaderboard_command(ctx):
    guild_data = config.get("xp_data", {}).get(str(ctx.guild.id), {})
    ranked = sorted(guild_data.items(), key=lambda item: int(item[1].get("xp", 0)), reverse=True)[:10]
    if not ranked:
        return await ctx.reply("No XP rankings yet. Start chatting to earn XP!", mention_author=False)
    lines = []
    for index, (user_id, record) in enumerate(ranked, 1):
        member = ctx.guild.get_member(int(user_id))
        name = member.display_name if member else f"Unknown user ({user_id})"
        level = _xp_level(int(record.get("xp", 0)))[0]
        lines.append(f"**{index}.** {discord.utils.escape_markdown(name)} — Level **{level}** · {int(record.get('xp', 0)):,} XP")
    await ctx.reply(embed=feature_embed("Server XP Leaderboard", "\n".join(lines)), mention_author=False)


@bot.command(name="xp")
@commands.guild_only()
async def xp_settings_command(ctx, action: str = "status", value: str = None):
    if not await manager_check(ctx):
        return
    settings = _guild_feature_map("xp_settings", ctx.guild.id)
    action = action.lower()
    if action in ("on", "enable"):
        settings["enabled"] = True
        description = "XP and leveling are enabled. Members earn 15–25 XP per eligible message, subject to cooldown."
    elif action in ("off", "disable"):
        settings["enabled"] = False
        description = "XP earning is disabled. Existing XP and ranks are preserved."
    elif action == "announce":
        settings["announce"] = value not in ("off", "false", "0")
        description = f"Level-up announcements are **{'enabled' if settings['announce'] else 'disabled'}**."
    elif action == "status":
        description = f"XP earning: **{'Disabled' if settings.get('enabled') is False else 'Enabled'}**\nLevel-up announcements: **{'Disabled' if settings.get('announce') is False else 'Enabled'}**\nMessage cooldown: **{int(settings.get('cooldown', 60))} seconds**\nCommands: `!rank`, `!leaderboard`, `!xp on/off`, `!xp announce on/off`."
    else:
        description = "Usage: `!xp status`, `!xp on`, `!xp off`, or `!xp announce on/off`."
    save_config()
    await ctx.reply(embed=feature_embed("XP Settings", description), mention_author=False)


class GiveawayJoinView(discord.ui.View):
    def __init__(self, giveaway_id):
        super().__init__(timeout=None)
        self.giveaway_id = str(giveaway_id)
        button = discord.ui.Button(
            label="Enter Giveaway", emoji="🎉", style=discord.ButtonStyle.success,
            custom_id=f"kanade_giveaway_join:{self.giveaway_id}"
        )
        button.callback = self.enter
        self.add_item(button)

    async def enter(self, interaction: discord.Interaction):
        giveaway = config.get("giveaways", {}).get(self.giveaway_id)
        if not giveaway or giveaway.get("ended") or float(giveaway.get("ends_at", 0)) <= time.time():
            return await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
        if interaction.guild_id != int(giveaway.get("guild_id", 0)):
            return await interaction.response.send_message("This giveaway belongs to another server.", ephemeral=True)
        entrants = giveaway.setdefault("entrants", [])
        user_id = str(interaction.user.id)
        if user_id in entrants:
            entrants.remove(user_id)
            message = "Your giveaway entry has been removed."
        else:
            entrants.append(user_id)
            message = "You are entered! Good luck."
        save_config()
        await interaction.response.send_message(message, ephemeral=True)


async def _finish_giveaway(giveaway_id, reroll=False):
    giveaway = config.get("giveaways", {}).get(str(giveaway_id))
    if not giveaway:
        return None, "Giveaway not found."
    guild = bot.get_guild(int(giveaway.get("guild_id", 0)))
    if guild is None:
        return None, "The giveaway server is not available."
    channel = guild.get_channel(int(giveaway.get("channel_id", 0)))
    if channel is None:
        return None, "The giveaway channel is not available."
    try:
        message = await channel.fetch_message(int(giveaway.get("message_id", 0)))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None, "Could not find the original giveaway message."
    entrants = [uid for uid in giveaway.get("entrants", [])]
    if reroll:
        previous = giveaway.get("winner_ids", [])
        entrants = [uid for uid in entrants if uid not in previous]
    valid = []
    for uid in entrants:
        member = guild.get_member(int(uid))
        if member and not member.bot:
            valid.append(member)
    winners_count = max(1, min(20, int(giveaway.get("winners", 1))))
    winners = secrets.SystemRandom().sample(valid, min(winners_count, len(valid))) if valid else []
    if not reroll:
        giveaway["ended"] = True
    giveaway["winner_ids"] = [str(member.id) for member in winners]
    save_config()
    embed = feature_embed("Giveaway Ended" if not reroll else "Giveaway Rerolled", f"**Prize:** {discord.utils.escape_markdown(giveaway.get('prize', 'Prize'))}\n**Entries:** {len(giveaway.get('entrants', []))}\n**Winner(s):** {', '.join(member.mention for member in winners) if winners else 'No eligible entries'}")
    if not reroll:
        await message.edit(embed=embed, view=None)
    else:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions(users=True))
    return winners, None


@tasks.loop(seconds=15)
async def giveaway_expiry_loop():
    now = time.time()
    for giveaway_id, giveaway in list(config.get("giveaways", {}).items()):
        if not giveaway.get("ended") and float(giveaway.get("ends_at", 0)) <= now:
            try:
                await _finish_giveaway(giveaway_id)
            except Exception:
                logger.exception("Could not finish giveaway %s", giveaway_id)


@giveaway_expiry_loop.before_loop
async def before_giveaway_expiry_loop():
    await bot.wait_until_ready()


@bot.command(name="giveaway")
@commands.guild_only()
async def giveaway_command(ctx, action: str = "help", *, arguments: str = ""):
    action = action.lower()
    if action == "start":
        if not await manager_check(ctx):
            return
        parts = arguments.split(maxsplit=2)
        if len(parts) < 3:
            return await ctx.reply("Usage: `!giveaway start <duration> <winner-count> <prize>` — duration examples: `10m`, `2h`, `1d`.", mention_author=False)
        duration = _parse_duration(parts[0])
        if duration is None or not parts[1].isdigit() or not 1 <= int(parts[1]) <= 20:
            return await ctx.reply("Use a duration from 30 seconds to 30 days and a winner count from 1 to 20.", mention_author=False)
        prize = parts[2].strip()
        if not prize or len(prize) > 200:
            return await ctx.reply("The prize must be between 1 and 200 characters.", mention_author=False)
        giveaway_id = secrets.token_hex(5).upper()
        ends_at = time.time() + duration
        embed = feature_embed("🎉 Giveaway", f"**Giveaway ID:** `{giveaway_id}`\n**Prize:** {discord.utils.escape_markdown(prize)}\n**Hosted by:** {ctx.author.mention}\n**Winners:** {int(parts[1])}\n**Entries:** 0\n**Ends:** <t:{int(ends_at)}:R>\n\nPress the button below to enter. You can leave by pressing it again.")
        message = await ctx.send(embed=embed, view=GiveawayJoinView(giveaway_id))
        config.setdefault("giveaways", {})[giveaway_id] = {"guild_id": ctx.guild.id, "channel_id": ctx.channel.id, "message_id": message.id, "prize": prize, "winners": int(parts[1]), "ends_at": ends_at, "entrants": [], "winner_ids": [], "ended": False}
        save_config()
        await ctx.reply(f"Giveaway created. ID: `{giveaway_id}`", mention_author=False)
        return
    if action in ("end", "reroll"):
        if not await manager_check(ctx):
            return
        giveaway_id = arguments.strip()
        if not giveaway_id:
            return await ctx.reply(f"Usage: `!giveaway {action} <giveaway-id>`.", mention_author=False)
        winners, error = await _finish_giveaway(giveaway_id, reroll=(action == "reroll"))
        return await ctx.reply(error or f"Giveaway {action} complete.", mention_author=False)
    await ctx.reply(embed=feature_embed("Giveaways", "`!giveaway start 1h 1 Nitro` — Start a giveaway\n`!giveaway end <id>` — End it early\n`!giveaway reroll <id>` — Pick a new winner\nMembers enter using the button. Only server managers can manage giveaways."), mention_author=False)


@bot.command(name="counting")
@commands.guild_only()
async def counting_command(ctx, action: str = "status", channel: discord.TextChannel = None):
    if not await manager_check(ctx):
        return
    settings = _guild_feature_map("counting_settings", ctx.guild.id)
    action = action.lower()
    if action in ("setup", "set"):
        target = channel or (ctx.message.channel if ctx.message else None)
        settings.update({"channel_id": target.id, "current": 0, "last_user_id": 0, "enabled": True})
        save_config()
        return await ctx.reply(f"Counting game set up in {target.mention}. Start at **1**; each member must take turns.", mention_author=False)
    if action in ("off", "disable"):
        settings["enabled"] = False
        save_config()
        return await ctx.reply("Counting game disabled. Your count has been saved.", mention_author=False)
    if action == "reset":
        settings.update({"current": 0, "last_user_id": 0, "enabled": True})
        save_config()
        target = ctx.guild.get_channel(int(settings.get("channel_id", 0)))
        return await ctx.reply(f"Counting has been reset to **0** in {target.mention if target else 'the configured channel'}.", mention_author=False)
    target = ctx.guild.get_channel(int(settings.get("channel_id", 0))) if settings else None
    await ctx.reply(embed=feature_embed("Counting Game", f"Status: **{'Enabled' if settings.get('enabled') and target else 'Disabled'}**\nChannel: {target.mention if target else 'Not configured'}\nCurrent count: **{int(settings.get('current', 0))}**\nCommands: `!counting setup [#channel]`, `!counting reset`, `!counting off`."), mention_author=False)


@bot.listen("on_message")
async def kanade_counting_listener(message):
    if message.guild is None or message.author.bot:
        return
    settings = config.get("counting_settings", {}).get(str(message.guild.id), {})
    if not settings.get("enabled") or int(settings.get("channel_id", 0)) != message.channel.id:
        return
    content = (message.content or "").strip()
    if not re.fullmatch(r"\d+", content):
        return
    expected = int(settings.get("current", 0)) + 1
    last_user = int(settings.get("last_user_id", 0))
    if int(content) == expected and message.author.id != last_user:
        settings["current"] = expected
        settings["last_user_id"] = message.author.id
        save_config()
        if expected % 100 == 0:
            try:
                await message.add_reaction("🏆")
            except (discord.Forbidden, discord.HTTPException):
                pass
        return
    try:
        await message.delete()
    except (discord.Forbidden, discord.HTTPException):
        pass
    if int(content) == expected and message.author.id == last_user:
        reason = "You cannot count twice in a row."
    else:
        reason = f"The next number was **{expected}**. The count has reset to **0**."
        settings["current"] = 0
        settings["last_user_id"] = 0
        save_config()
    try:
        await message.channel.send(f"{message.author.mention}, {reason}", delete_after=8, allowed_mentions=discord.AllowedMentions(users=True))
    except (discord.Forbidden, discord.HTTPException):
        pass


# Honeypot is heuristic anti-scam moderation; it never executes or downloads attachments.
HONEYPOT_TOKEN_RE = re.compile(r"(?i)\b(?:mfa\.[\w-]{20,}|[\w-]{20,}\.[\w-]{5,}\.[\w-]{20,})\b")
HONEYPOT_SCAM_TERMS = (
    "free nitro", "nitro gift", "claim your nitro", "steam gift", "free robux",
    "crypto giveaway", "wallet connect", "verify your discord",
    "discord token", "grab your token", "paste your token", "token grabber",
    "download this executor", "security verification", "airdrop claim", "free gift card",
)
HONEYPOT_DANGEROUS_EXTENSIONS = (".exe", ".scr", ".bat", ".cmd", ".ps1", ".js", ".vbs", ".hta", ".msi", ".dll", ".com")
HONEYPOT_SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "cutt.ly", "rb.gy", "shorturl.at"}

def honeypot_reason(message):
    content = (message.content or "").lower()
    if HONEYPOT_TOKEN_RE.search(message.content or ""):
        return "A Discord-token-shaped string was posted."
    if ("mrbeast" in content or "mr beast" in content) and any(word in content for word in ("token", "claim", "giveaway", "crypto", "free money", "connect wallet")):
        return "MrBeast-branded token or giveaway bait detected."
    matched = [term for term in HONEYPOT_SCAM_TERMS if term in content]
    if matched:
        return "Suspicious token, giveaway, or verification bait: " + ", ".join(matched[:3])
    domains = extract_domains(message.content or "")
    if any(domain in HONEYPOT_SHORTENERS for domain in domains) and any(word in content for word in ("claim", "verify", "gift", "free", "token", "nitro", "wallet")):
        return "A shortened link was combined with giveaway or account-verification bait."
    suspicious_image_names = [a.filename for a in message.attachments if re.search(r"(?i)(mr.?beast|token|giveaway|claim|nitro|wallet)", a.filename) and (a.content_type or "").startswith("image/")]
    if suspicious_image_names:
        return "Suspicious giveaway/token image filename: " + ", ".join(suspicious_image_names[:3])
    risky = [a.filename for a in message.attachments if any(a.filename.lower().endswith(ext) for ext in HONEYPOT_DANGEROUS_EXTENSIONS)]
    if risky:
        return "Potentially executable attachment(s): " + ", ".join(risky[:3])
    if message.attachments and any(word in content for word in ("run this", "open this", "verify here", "claim here", "token", "giveaway")):
        if any(a.content_type and a.content_type.startswith("image/") for a in message.attachments):
            return "Image attachment paired with suspicious token/giveaway instructions; image contents were not executed or trusted."
    return None

@bot.command(name="honeypot")
@commands.guild_only()
async def honeypot_command(ctx, action: str = "status"):
    if not await manager_check(ctx): return
    action = action.lower()
    settings = _guild_feature_settings("honeypot_settings", ctx.guild)
    if action in ("on", "enable"):
        settings["enabled"] = True
        save_config()
        description = "Honeypot detection is enabled. Suspicious messages will be removed and logged."
    elif action in ("off", "disable"):
        settings["enabled"] = False
        save_config()
        description = "Honeypot detection is disabled."
    elif action == "status":
        description = f"Status: **{'Enabled' if settings.get('enabled', False) else 'Disabled'}**\nDetection: token-shaped strings, scam/verification bait, risky executable attachments, suspicious shortened links, image-plus-bait captions, and burst/repeated-message spam.\nAction: delete and log; no automatic bans. Image pixels are not OCR-scanned, so suspicious image-only posts may need staff review."
    else:
        description = "Usage: `!honeypot on`, `!honeypot off`, or `!honeypot status`."
    await ctx.reply(embed=feature_embed("Honeypot", description), mention_author=False)

@bot.listen("on_message")
async def kanade_honeypot_listener(message):
    if message.guild is None or (message.author.bot and message.webhook_id is None):
        return
    settings = config.get("honeypot_settings", {}).get(str(message.guild.id), {})
    if not settings.get("enabled", False):
        return
    if has_admin_access(message.author) or is_whitelisted(message.guild, message.author.id):
        return
    reason = honeypot_reason(message)
    history_key = (message.guild.id, message.author.id)
    now = time.monotonic()
    recent = HONEYPOT_MESSAGE_HISTORY[history_key]
    normalized = re.sub(r"\s+", " ", (message.content or "").strip().lower())
    recent.append((now, normalized))
    while recent and now - recent[0][0] > 12:
        recent.popleft()
    nonempty = [content for _, content in recent if content]
    if reason is None and len(recent) >= 7:
        reason = "High-volume message burst (7 or more messages within 12 seconds)."
    if reason is None and normalized and nonempty.count(normalized) >= 3:
        reason = "Repeated identical-message spam (3 or more copies within 12 seconds)."
    if not reason:
        return
    deleted = False
    try:
        await message.delete()
        deleted = True
    except (discord.Forbidden, discord.HTTPException):
        logger.warning("Honeypot detected message %s but could not delete it", message.id)
    await send_log(message.guild, "HONEYPOT DETECTION", f"**Author:** {message.author.mention} (`{message.author.id}`)\n**Channel:** {message.channel.mention}\n**Detection:** {discord.utils.escape_markdown(reason)}\n**Deleted:** {'Yes' if deleted else 'No — missing permissions or API failure'}\n**Message ID:** `{message.id}`", discord.Color.red())
    try:
        action_text = "removed" if deleted else "flagged, but could not be deleted"
        await message.channel.send(f"{message.author.mention}, Kanade {action_text} a message that matched suspicious scam or malware-bait patterns.", delete_after=8, allowed_mentions=discord.AllowedMentions(users=True))
    except (discord.Forbidden, discord.HTTPException):
        pass

# ==================== PREFIX COMMANDS ====================
class HelpCategorySelect(discord.ui.Select):
    def __init__(self, ctx, prefix):
        self.ctx = ctx
        options = [
            discord.SelectOption(label="Home", value="home", description="Overview of Kanade", default=True),
            discord.SelectOption(label="Moderation", value="moderation", description="Kick, ban, purge, and channel controls"),
            discord.SelectOption(label="Roles", value="roles", description="Manage member roles"),
            discord.SelectOption(label="Protection", value="protection", description="Anti-nuke, anti-spam, anti-link, and whitelist"),
            discord.SelectOption(label="Backups & Logs", value="backups", description="Backups and security logging"),
            discord.SelectOption(label="Information", value="utilities", description="Bot/server information and utility commands"),
            discord.SelectOption(label="Music", value="music", description="Play, queue, filters, DJ controls"),
            discord.SelectOption(label="Tickets", value="tickets", description="Ticket panels, claiming, transcripts"),
            discord.SelectOption(label="Autoresponder", value="autoresponder", description="Trigger-based automatic replies"),
            discord.SelectOption(label="Premium", value="premium", description="Premium customization commands"),
            discord.SelectOption(label="Configuration", value="configuration", description="Prefix and help commands"),
        ]
        super().__init__(placeholder="Choose a category", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Only the person who ran `help` can use this menu.", ephemeral=True)
            return
        self.view.update_embed(self.values[0])
        await _edit_banner_card(interaction, self.view.embed, self.view, guild=getattr(self.ctx, "guild", None))


class HelpView(discord.ui.View):
    def __init__(self, ctx, prefix):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.prefix = prefix
        self.add_item(HelpCategorySelect(ctx, prefix))
        self.embed = None
        self.update_embed("home")

    def update_embed(self, category):
        p = self.prefix
        pages = {
            "home": ("Overview",
                     f"Prefix: `{p}`\nDeveloper: Wxre Development\n\n"
                     "Choose a category to view commands. Bot staff commands are in `admhelp`."),
            "moderation": ("Moderation",
                           f"`{p}kick @member [reason]` — Kick a member\n"
                           f"`{p}ban @member|<user ID> [reason]` — Ban a member\n"
                           f"`{p}timeout @member|<user ID> <1min|1d|1w> [reason]` — Timeout a member\n"
                           f"`{p}unban <user ID>` — Unban a user by ID\n"
                           f"`{p}unban all` — Unban everyone after confirmation\n"
                           f"`{p}purge <amount>` — Delete recent messages\n"
                           f"`{p}purge all` — Clear accessible channel history\n"
                           f"`{p}purge @user <amount>` — Purge one member's messages\n"
                           f"`{p}lock` — Lock the current channel\n"
                           f"`{p}unlock` — Restore saved channel permissions\n"
                           f"`{p}lockdown` / `{p}unlockdown` — Lock or restore server channels"),
            "roles": ("Roles",
                      f"`{p}roles` — List every server role from highest to lowest\n"
                      f"`{p}roledeletes` — Review and remove roles with dangerous permissions\n"
                      f"`{p}role @member|<user ID> <role name|role ID>` — Give a role\n"
                      f"`{p}unrole @member|<user ID> <role name|role ID>` — Remove a role\n"
                      "`/roleall` — Give a role to eligible members\n"
                      "`/unroleall` — Remove a role from eligible members"),
            "protection": ("Protection",
                           f"`{p}antinuke on/off` — Enable or disable anti-nuke\n"
                           f"`{p}security` — View security status\n"
                           f"`{p}antispam on/off/status` — Configure per-server anti-spam\n"
                           f"`{p}antispam settings <window|messages|duplicates|mentions|timeout|delete> <value>` — Tune spam protection\n"
                           f"`{p}antilink on/off/status` — Configure per-server link filtering\n"
                           f"`{p}antilink allow <domain>` / `{p}antilink deny <domain>` — Manage domains\n"
                           f"`{p}antilink exempt @member` / `{p}antilink unexempt @member` — Manage exemptions\n"
                           f"`{p}whitelist add @member` / `{p}whitelist remove @member` — Edit whitelist\n"
                           f"`{p}whitelist list` — View whitelist\n"
                           f"`{p}whitelist roleadd @role` / `{p}whitelist roleremove @role` — Whitelist a role\n"
                           f"`{p}whitelist rolelist` — View whitelisted roles\n"
                           f"`{p}honeypot on/off/status` — Detect suspicious token scams and malware bait (logs to #honeypot-logs)\n"
                           "`/lockdown` / `/unlockdown` — Slash-command lockdown controls"),
            "backups": ("Backups & Logs",
                        f"`{p}backup create` — Create a server backup (Premium)\n"
                        f"`{p}backup load` — Restore a server backup (Premium)\n"
                        f"`{p}setuplogs` — Create the Kanade logs category and separate log channels\n"
                        f"`{p}disablelogs` — Disable security logs\n"
                        f"`{p}snipe` — View the latest deleted message"),
            "utilities": ("Information",
                          f"`{p}afk [message]` — Set your AFK status\n"
                          f"`{p}server` — View server information\n"
                          f"`{p}info` — Explore Kanade information\n"
                          f"`{p}av [@user]` — View a user's avatar (Premium)\n"
                          f"`{p}banner [@user|user ID]` — View a user's Discord banner (Premium)\n"
                          f"`{p}ping` — Show bot gateway latency\n"
                          f"`{p}joinvc` — Join your current voice channel\n"
                          f"`{p}honeypot` — View honeypot status and commands\n"
                          f"`{p}rank [@member]` — View XP and level progress\n"
                          f"`{p}leaderboard` — View the server XP leaderboard\n"
                          f"`{p}giveaway start 1h 1 Prize` — Start a giveaway\n"
                          f"`{p}counting setup [#channel]` — Set up the counting game"),
            "music": ("Music",
                      f"`{p}play <song|link>` (`{p}p`) — Play or queue a song, link or playlist\n"
                      f"`{p}playnext <song>` (`{p}pn`) — Queue a song at the top\n"
                      f"`{p}playskip <song>` (`{p}ps`) — Play a song right now\n"
                      f"`{p}search <query>` — Pick from search results\n"
                      f"`{p}join` / `{p}leave` (`{p}dc`) — Join or leave your voice channel\n"
                      f"`{p}pause` / `{p}resume` / `{p}stop` — Playback controls\n"
                      f"`{p}skip` (`{p}s`) — Skip (vote skip when others are listening)\n"
                      f"`{p}forceskip` (`{p}fs`) — Skip instantly (DJ)\n"
                      f"`{p}skipto <position>` / `{p}previous` — Jump around the queue\n"
                      f"`{p}queue [page]` (`{p}q`) — Show the queue\n"
                      f"`{p}np` (`{p}nowplaying`) — Show the current track\n"
                      f"`{p}volume [0-200]` — Show or set the volume\n"
                      f"`{p}loop [off|track|queue]` — Loop mode\n"
                      f"`{p}shuffle` / `{p}clear` — Shuffle or clear the queue\n"
                      f"`{p}remove <pos|range>` / `{p}move <from> <to>` — Edit the queue\n"
                      f"`{p}seek <time>` / `{p}forward [s]` / `{p}rewind [s]` / `{p}replay` — Seek\n"
                      f"`{p}filter <name>` — Audio filters; also `{p}bassboost`, `{p}nightcore`, `{p}8d`, `{p}vaporwave`\n"
                      f"`{p}autoplay` — Queue related tracks automatically\n"
                      f"`{p}history` / `{p}grab` — Recent tracks, or DM yourself the current one\n"
                      f"`{p}djrole @role` / `{p}djoff` — Set or remove the DJ role (admin)\n"
                      f"`{p}247` — Stay in the voice channel (admin)"),
            "tickets": ("Tickets",
                        f"`{p}ticket setup` — Create ticket categories and the log channel\n"
                        f"`{p}ticket panel [#channel]` — Post the ticket panel\n"
                        f"`{p}ticket config` — View ticket settings\n"
                        f"`{p}ticket category <#category>` / `{p}ticket closedcategory <#category>`\n"
                        f"`{p}ticket logs <#channel>` — Set the log and transcript channel\n"
                        f"`{p}ticket support add|remove|list [@role]` — Support roles\n"
                        f"`{p}ticket type add|remove|list` — Ticket types (dropdown panel)\n"
                        f"`{p}ticket max|format|welcome|panelset|toggle|autoclose` — Customize tickets\n"
                        f"`{p}ticket blacklist add|remove|list [@user]` — Block users\n"
                        f"`{p}ticket stats` — Ticket statistics\n"
                        f"`{p}close [reason]` / `{p}claim` / `{p}unclaim` — Inside a ticket\n"
                        f"`{p}ticket add|remove @user` / `{p}ticket rename <name>` / `{p}ticket priority <level>`\n"
                        f"`{p}ticket transfer @staff` / `{p}ticket transcript` / `{p}ticket reopen` / `{p}ticket delete`"),
            "autoresponder": ("Autoresponder",
                              f"`{p}ar add [mode] <trigger> | <response>` — Create an autoresponder\n"
                              f"`{p}ar remove <id|trigger>` / `{p}ar edit <id> <response>` — Manage one\n"
                              f"`{p}ar list` / `{p}ar info <id>` — View autoresponders\n"
                              f"`{p}ar toggle <id>` — Enable or disable\n"
                              f"`{p}ar mode <id> <exact|contains|word|starts|ends|wildcard>` — Match mode\n"
                              f"`{p}ar cooldown <id> <seconds>` — Per-channel cooldown\n"
                              f"`{p}ar reply|embed|deletetrigger|case <id> on|off` — Behavior options\n"
                              f"`{p}ar channels <id> add|remove|clear [#channel]` — Limit to channels\n"
                              f"`{p}ar clear` — Remove all\n"
                              "`/autoresponder add|remove|list|toggle` — Slash versions\n\n"
                              "Variables: `{user}` `{user.mention}` `{server}` `{membercount}` `{channel}` `{random:1-100}` `{choose:a|b|c}`"),
            "premium": ("Premium",
                        f"`{p}cb <image URL>` — Change the bot's banner (Premium)\n"
                        f"`{p}cp <image URL>` — Change the bot's profile picture (Premium)\n"
                        f"`{p}cn <new name>` — Change the bot's nickname in this server (Premium)\n"
                        f"`{p}cn reset` — Reset the bot's server nickname (Premium)\n"
                        f"`{p}steal <custom emoji> [name]` — Add a custom emoji (Premium)\n"
                        f"`{p}j2c setup [category]` — Set up private join-to-create voice rooms (Premium)\n"
                        f"`{p}confession #channel` — Set the public confession channel (server owner, Premium)\n"
                        f"`{p}confession setup #channel [#staff-channel]` — Configure anonymous confessions (Premium)\n"
                        f"`{p}confess <message>` — Submit an anonymous confession (Premium)\n"
                        f"`{p}confession prompt` — Post a community prompt (Premium)\n\n"
                        "You can attach an image when using the banner or profile-picture command."),
            "configuration": ("Configuration",
                             f"`{p}prefix <new_prefix>` — Change this server's prefix (Premium)\n"
                             f"`{p}help` — Open this interactive command guide\n"
                             f"`{p}admhelp` — Open the restricted bot-staff guide"),
        }
        title, description = pages.get(category, pages["home"])
        description = _arrow_lines(description)
        self.embed = StyledEmbed(title=title, description=description,
                                 color=discord.Color.from_rgb(35, 0, 55),
                                 timestamp=discord.utils.utcnow())
        if bot.user:
            self.embed.set_author(name="Kanade", icon_url=bot.user.display_avatar.url)
            self.embed.set_thumbnail(url=bot.user.display_avatar.url)
            self.embed.set_footer(text="Kanade • Developed by Wxre Development", icon_url=bot.user.display_avatar.url)
        for item in self.children:
            if isinstance(item, HelpCategorySelect):
                for option in item.options:
                    option.default = option.value == category


@bot.command(name="help")
async def help_command(ctx):
    """Show the interactive regular server command guide."""
    active_prefix = config.get("guild_prefixes", {}).get(str(ctx.guild.id), PREFIX) if ctx.guild else PREFIX
    view = HelpView(ctx, active_prefix)
    await _send_banner_card(ctx, view.embed, view=view, guild=ctx.guild)

class AdminHelpSelect(discord.ui.Select):
    def __init__(self, ctx, prefix):
        self.ctx = ctx
        self.prefix = prefix
        options = [
            discord.SelectOption(label="Home", value="home", description="Overview of restricted staff commands", default=True),
            discord.SelectOption(label="Premium", value="premium", description="Manage Premium access and bot admins"),
            discord.SelectOption(label="Prefix Access", value="prefix", description="Manage no-prefix permissions"),
            discord.SelectOption(label="Bot Admin", value="admin", description="Manage staff and view bot servers"),
        ]
        super().__init__(placeholder="Choose a category", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Only the person who ran `admhelp` can use this menu.", ephemeral=True)
            return
        self.view.update_embed(self.values[0])
        await _edit_banner_card(interaction, self.view.embed, self.view, guild=getattr(self.ctx, "guild", None))


class AdminHelpView(discord.ui.View):
    def __init__(self, ctx, prefix):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.prefix = prefix
        self.add_item(AdminHelpSelect(ctx, prefix))
        self.embed = None
        self.update_embed("home")

    def update_embed(self, category):
        p = self.prefix
        pages = {
            "home": ("Overview",
                     "Restricted bot-staff tools for Premium access, no-prefix permissions, and bot administration.\n\n"
                     f"**Prefix:** `{p}`\n**Access:** Global bot staff only\n\n"
                     "Select a category from the dropdown below to view command usage.\n"
                     "These commands are not intended for ordinary server administrators."),
            "premium": ("Premium & Admins",
                        f"`{p}premium add <server_id>` — Enable Premium for a server (developer only)\n"
                        f"`{p}premium remove <server_id>` — Remove Premium from a server (developer only)\n"
                        f"`{p}premium list` — List Premium servers (developer only)\n"
                        f"`{p}adm add <user ID|@user>` — Grant bot-admin/Premium-bypass access (developer only)\n"
                        f"`{p}adm remove <user ID|@user>` — Remove bot-admin/Premium-bypass access (developer only)\n"
                        f"`{p}admlist` — View the bot-admin list\n\n"
                        "Use IDs when a user mention cannot be resolved. Only authorized staff can run these commands."),
            "prefix": ("Prefix Access",
                       f"`{p}nplaying <user ID|@user>` — Allow no-prefix commands in this server\n"
                       f"`{p}npremove <user ID|@user>` — Remove this server's no-prefix access\n"
                       f"`{p}gnp <user ID|@user>` — Allow no-prefix commands globally\n"
                       f"`{p}gnpremove <user ID|@user>` — Remove global no-prefix access\n\n"
                       "Grant access only to trusted users. Global access applies across servers where the bot is present."),
            "admin": ("Bot Admin",
                      f"`{p}adminlist` — View registered global bot staff\n"
                      f"`/adminlist` — Slash-command version, when synced and available\n"
                      f"`{p}generatelink <server_id>` — Generate an invite for a server the bot has joined\n"
                      f"`{p}serverlists` — List servers the bot has joined\n\n"
                      "Some operations may be developer-only or require the bot to have the appropriate permissions."),
        }
        title, description = pages.get(category, pages["home"])
        description = _arrow_lines(description)
        self.embed = StyledEmbed(title=title, description=description,
                                 color=discord.Color.from_rgb(35, 0, 55),
                                 timestamp=discord.utils.utcnow())
        if bot.user:
            self.embed.set_author(name="Kanade", icon_url=bot.user.display_avatar.url)
            self.embed.set_thumbnail(url=bot.user.display_avatar.url)
            self.embed.set_footer(text="Kanade • Restricted Staff Help", icon_url=bot.user.display_avatar.url)
        for item in self.children:
            if isinstance(item, AdminHelpSelect):
                for option in item.options:
                    option.default = option.value == category


@bot.command(name="admhelp", aliases=["adminhelp", "staffhelp"])
async def admin_help_command(ctx):
    """Show the interactive restricted administrator and global-staff command guide."""
    if not is_global_staff(ctx.author):
        embed = StyledEmbed(
            title="🚫 Access Denied",
            description="This command is restricted to authorized bot staff.",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        if bot.user:
            embed.set_thumbnail(url=bot.user.display_avatar.url)
            embed.set_footer(text="Kanade • Staff only")
        return await ctx.reply(embed=embed, mention_author=False)

    active_prefix = config.get("guild_prefixes", {}).get(str(ctx.guild.id), PREFIX) if ctx.guild else PREFIX
    view = AdminHelpView(ctx, active_prefix)
    await _send_banner_card(ctx, view.embed, view=view, guild=ctx.guild)

@bot.command(name="av", aliases=["avatar", "pfp"])
@commands.guild_only()
async def avatar_command(ctx, user: discord.User = None):
    """Show a user's avatar; this command is available to Premium servers only."""
    if not await require_premium(ctx):
        return
    target = user or ctx.author
    avatar_url = target.display_avatar.url
    embed = StyledEmbed(
        title=f"Avatar: {discord.utils.escape_markdown(target.name)}",
        description=f"**User:** {target.mention if isinstance(target, discord.Member) else target.name}\n**User ID:** `{target.id}`\n[Open full-size avatar]({avatar_url})",
        color=discord.Color.from_rgb(128, 0, 255),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_image(url=avatar_url)
    embed.set_footer(text="Kanade • Premium Feature", icon_url=ctx.bot.user.display_avatar.url if ctx.bot.user else None)
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="banner")
@commands.guild_only()
async def banner_command(ctx, user: discord.User = None):
    """Show a user's Discord profile banner; Premium servers only."""
    if not await require_premium(ctx):
        return

    target = user or ctx.author
    try:
        # Fetch the full user profile because cached users may not include banner data.
        fetched_user = await ctx.bot.fetch_user(target.id)
    except discord.NotFound:
        await ctx.reply("That user could not be found.", mention_author=False)
        return
    except (discord.Forbidden, discord.HTTPException):
        await ctx.reply("I couldn't retrieve that user's banner. Please try again.", mention_author=False)
        return

    banner_asset = fetched_user.banner
    if banner_asset is None:
        embed = StyledEmbed(
            title=f"Banner: {discord.utils.escape_markdown(target.name)}",
            description=f"**User:** {target.mention if isinstance(target, discord.Member) else target.name}\n**User ID:** `{target.id}`\nThis user does not have a profile banner set.",
            color=discord.Color.from_rgb(128, 0, 255),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Kanade • Premium Feature", icon_url=ctx.bot.user.display_avatar.url if ctx.bot.user else None)
        await ctx.reply(embed=embed, mention_author=False)
        return

    banner_url = banner_asset.url
    embed = StyledEmbed(
        title=f"Banner: {discord.utils.escape_markdown(target.name)}",
        description=f"**User:** {target.mention if isinstance(target, discord.Member) else target.name}\n**User ID:** `{target.id}`\n[Open full-size banner]({banner_url})",
        color=discord.Color.from_rgb(128, 0, 255),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_image(url=banner_url)
    embed.set_footer(text="Kanade • Premium Feature", icon_url=ctx.bot.user.display_avatar.url if ctx.bot.user else None)
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="server", aliases=["sinfo", "serverinfo", "server-info"])
@commands.guild_only()
async def server_info_command(ctx):
    guild = ctx.guild
    purple = discord.Color.from_rgb(35, 0, 55)
    owner = guild.owner
    if owner is None:
        try:
            owner = await guild.fetch_member(guild.owner_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            owner = None

    categories = len(guild.categories)
    text_channels = len(guild.text_channels)
    voice_channels = len(guild.voice_channels)
    # discord.py exposes cached active threads on Guild. Archived threads may not be cached.
    thread_count = len(getattr(guild, "threads", []))
    boosts = guild.premium_subscription_count or 0
    tier = guild.premium_tier
    created_ts = int(guild.created_at.timestamp())

    embed = StyledEmbed(
        title=f"{guild.name}",
        description=(
            f"Server information for **{guild.name}**\n"
            f"**Owner:** {owner.mention if owner else f'Unknown (`{guild.owner_id}`)'}\n"
            f"**Members:** {guild.member_count or len(guild.members):,}\n"
            f"**Roles:** {len(guild.roles):,}"
        ),
        color=purple,
        timestamp=discord.utils.utcnow()
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(
        name="📚 Channels",
        value=(
            f"**Category Channels:** {categories}\n"
            f"**Text Channels:** {text_channels}\n"
            f"**Voice Channels:** {voice_channels}\n"
            f"**Threads:** {thread_count}"
        ),
        inline=True
    )
    embed.add_field(
        name="🚀 Server Boosts",
        value=f"**Boost Count:** {boosts} Boosts (Tier {tier})",
        inline=True
    )
    embed.add_field(
        name="🗓️ Server Created",
        value=f"<t:{created_ts}:F>\n(<t:{created_ts}:R>)",
        inline=False
    )
    embed.add_field(name="🆔 Server ID", value=f"`{guild.id}`", inline=False)
    # This command is server information; show only this guild's banner, never the bot banner.
    if guild.banner is not None:
        embed.set_image(url=guild.banner.url)
    embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
    await ctx.reply(embed=embed, mention_author=False)


class BotInfoCategorySelect(discord.ui.Select):
    def __init__(self, ctx):
        self.ctx = ctx
        options = [
            discord.SelectOption(label="Home", value="home", description="Overview of Kanade", default=True),
            discord.SelectOption(label="Security", value="security", description="Anti-nuke, anti-raid, and whitelist"),
            discord.SelectOption(label="Moderation", value="moderation", description="Member moderation and channel controls"),
            discord.SelectOption(label="Backups & Logs", value="backups", description="Backups, logs, and customization"),
            discord.SelectOption(label="Statistics", value="stats", description="Bot runtime statistics"),
        ]
        super().__init__(placeholder="Choose a category", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Only the person who ran the command can use this menu.", ephemeral=True)
            return
        self.view.update_embed(self.values[0])
        await _edit_banner_card(interaction, self.view.embed, self.view, guild=getattr(self.ctx, "guild", None))


class BotInfoView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.add_item(BotInfoCategorySelect(ctx))
        self.embed = None
        self.update_embed("home")

    def update_embed(self, category):
        bot_user = bot.user
        latency_ms = round(bot.latency * 1000) if bot.is_ready() else None
        uptime_seconds = int(time.monotonic() - BOT_STARTED_AT)
        uptime_days, remainder = divmod(uptime_seconds, 86400)
        uptime_hours, remainder = divmod(remainder, 3600)
        uptime_minutes, uptime_secs = divmod(remainder, 60)
        uptime_text = f"{uptime_days}d {uptime_hours}h {uptime_minutes}m {uptime_secs}s"
        latency_text = f"{latency_ms} ms" if latency_ms is not None else "Connecting…"
        total_users = sum(g.member_count or 0 for g in bot.guilds)
        labels = {
            "home": ("Overview", f"**Latency:** `{latency_text}`\n**Uptime:** `{uptime_text}`\n**Users:** `{total_users:,}`\n**Servers:** `{len(bot.guilds):,}`\n\nA security and moderation bot built to help protect Discord communities.\n\nDeveloped by **Wxre Development**\n\nSelect a category below to explore features."),
            "security": ("Security", "• Anti-nuke monitoring and configurable thresholds\n• Anti-raid join detection and optional lockdown\n• Whitelist management and security status checks\n• Security logs and owner notifications\n• Channel/role restore support where configured"),
            "moderation": ("Moderation", "• Kick, ban, timeout, and remove timeouts\n• Assign or remove roles\n• Purge messages and view deleted messages with `!snipe`\n• Lock/unlock channels and server lockdown\n• AFK status and voice-channel join support"),
            "backups": ("Backups & Logs", "• Create and restore server backups (Premium)\n• Configure security log channels\n• Avatar and banner lookup commands\n• Configure server-specific bot profile (Premium)\n• Configure server command prefix (Premium)\n• Prefix and slash commands"),
            "stats": ("Statistics", f"**Latency:** `{latency_text}`\n**Uptime:** `{uptime_text}`\n**Status:** `{'Online' if bot.is_ready() else 'Connecting'}`\n**Users:** `{total_users:,}` *(approximate; users across servers may overlap)*\n**Servers:** `{len(bot.guilds):,}`\n• **Prefix commands:** `{len(bot.commands):,}`\n• **Slash commands:** `{len(bot.tree.get_commands()):,}`\n• **Bot ID:** `{bot_user.id if bot_user else 'Unavailable'}`"),
        }
        title, description = labels.get(category, labels["home"])
        description = _arrow_lines(description)
        self.embed = StyledEmbed(title=title, description=description, color=discord.Color.from_rgb(35, 0, 55), timestamp=discord.utils.utcnow())
        if bot_user:
            self.embed.set_author(name="Kanade", icon_url=bot_user.display_avatar.url)
            self.embed.set_thumbnail(url=bot_user.display_avatar.url)
            self.embed.set_footer(text="Developed by Wxre Development")
        for item in self.children:
            if isinstance(item, BotInfoCategorySelect):
                for option in item.options:
                    option.default = option.value == category


@bot.command(name="info", aliases=["botinfo", "about"])
async def bot_info_command(ctx):
    if bot.user is None:
        return await ctx.reply("Bot info is not available yet.", mention_author=False)
    view = BotInfoView(ctx)
    await _send_banner_card(ctx, view.embed, view=view, guild=ctx.guild)


@bot.command(name="ping")
async def ping(ctx):
    latency = round(bot.latency * 1000)
    embed = StyledEmbed(
        description=f"{LATENCY_EMOJI} **Latency is {latency}ms!**",
        color=discord.Color.dark_purple()
    )
    await ctx.send(embed=embed)

# ==================== SERVER-SPECIFIC BOT PROFILE ====================
PROFILE_IMAGE_MAX_BYTES = 8 * 1024 * 1024
PROFILE_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

async def read_profile_image(ctx, image_url=None):
    """Read an image attachment or HTTPS URL for the bot's guild-specific profile."""
    if ctx.message.attachments:
        attachment = ctx.message.attachments[0]
        if attachment.size > PROFILE_IMAGE_MAX_BYTES:
            raise ValueError("Image must be 8 MB or smaller.")
        content_type = (attachment.content_type or "").split(";", 1)[0].lower()
        if content_type and content_type not in PROFILE_IMAGE_TYPES:
            raise ValueError("Use a PNG, JPG/JPEG, GIF, or WEBP image.")
        data = await attachment.read()
    else:
        if not image_url:
            raise ValueError("Attach an image or provide a direct HTTPS image URL.")
        from urllib.parse import urlparse
        parsed = urlparse(image_url.strip())
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Please provide a direct HTTPS image URL, or attach the image.")
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(image_url.strip(), allow_redirects=True) as response:
                if response.status != 200:
                    raise ValueError(f"The image URL returned HTTP {response.status}.")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                # Some image hosts (including Discord CDN proxies) return a generic
                # content type. Validate the actual file signature below instead.
                if content_type and content_type.startswith("text/"):
                    raise ValueError("That URL returned a web page, not an image. Use a direct image URL or attach the file.")
                declared_size = response.headers.get("Content-Length")
                if declared_size and declared_size.isdigit() and int(declared_size) > PROFILE_IMAGE_MAX_BYTES:
                    raise ValueError("Image must be 8 MB or smaller.")
                data = await response.content.read(PROFILE_IMAGE_MAX_BYTES + 1)
    if not data:
        raise ValueError("The image is empty or could not be read.")
    if len(data) > PROFILE_IMAGE_MAX_BYTES:
        raise ValueError("Image must be 8 MB or smaller.")
    # Verify actual image signatures instead of trusting only a file extension/header.
    signatures = (
        data.startswith(b"\x89PNG\r\n\x1a\n"),
        data.startswith(b"\xff\xd8\xff"),
        data.startswith((b"GIF87a", b"GIF89a")),
        data.startswith(b"RIFF") and data[8:12] == b"WEBP",
    )
    if not any(signatures):
        raise ValueError("That file doesn't look like a supported PNG, JPG/JPEG, GIF, or WEBP image.")
    return data

async def change_guild_profile_image(ctx, field_name, image_url=None):
    if ctx.guild is None:
        return await ctx.reply("Use this command in a server.", mention_author=False)
    if not has_admin_access(ctx.author):
        embed = StyledEmbed(
            title="🛡️ Kanade | Permission Denied",
            description="Server admins only.",
            color=discord.Color.from_rgb(128, 0, 255),
        )
        return await ctx.reply(embed=embed, mention_author=False)
    bot_member = ctx.guild.get_member(bot.user.id) if bot.user else None
    if bot_member is None:
        try:
            bot_member = await ctx.guild.fetch_member(bot.user.id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            bot_member = None
    if bot_member is None:
        return await ctx.reply("I couldn't find my bot member profile in this server.", mention_author=False)
    try:
        image_data = await read_profile_image(ctx, image_url)
        # Use Discord's Modify Current Member endpoint directly. Older discord.py
        # releases do not expose the `banner`/`avatar` keyword on Member.edit(),
        # even though the Discord API endpoint supports these current-member fields.
        # This edits the bot's profile for this guild only; it does not edit the guild's banner.
        image_data_uri = discord.utils._bytes_to_base64_data(image_data)
        route = discord.http.Route(
            "PATCH",
            "/guilds/{guild_id}/members/@me",
            guild_id=ctx.guild.id,
        )
        try:
            await bot.http.request(route, json={field_name: image_data_uri})
        except discord.HTTPException as first_error:
            # If Discord rejects a GIF banner format, retry as a PNG first frame.
            # Animated banners may not be accepted for every account/server profile.
            if field_name != "banner" or not image_data.startswith((b"GIF87a", b"GIF89a")):
                raise
            try:
                from io import BytesIO
                from PIL import Image
                with Image.open(BytesIO(image_data)) as gif_image:
                    gif_image.seek(0)
                    converted = BytesIO()
                    gif_image.convert("RGBA").save(converted, format="PNG")
                    png_data = converted.getvalue()
                await bot.http.request(route, json={field_name: discord.utils._bytes_to_base64_data(png_data)})
            except ImportError:
                raise first_error
        if field_name == "banner":
            label = "Server-specific profile banner"
        else:
            label = "Server-specific profile picture"
        embed = StyledEmbed(
            title="🛡️ Kanade | Profile Updated",
            description=f"Updated the bot profile for **{ctx.guild.name}**.",
            color=discord.Color.from_rgb(128, 0, 255),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_image(url=ctx.message.attachments[0].url if ctx.message.attachments else image_url)
        embed.set_footer(text="Kanade • Developed by Wxre Development", icon_url=bot.user.display_avatar.url if bot.user else None)
        await ctx.reply(embed=embed, mention_author=False)
    except ValueError as exc:
        embed = StyledEmbed(
            title="🛡️ Kanade | Invalid Image",
            description=str(exc),
            color=discord.Color.from_rgb(128, 0, 255),
        )
        await ctx.reply(embed=embed, mention_author=False)
    except TypeError:
        await ctx.reply(
            "Update discord.py to version 2.7 or newer, then restart the bot.",
            mention_author=False,
        )
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning("Could not fetch profile image: %r", exc)
        await ctx.reply("I couldn't download that image. Check the URL and try again.", mention_author=False)
    except discord.HTTPException as exc:
        logger.warning("Discord rejected server-specific profile change: %r", exc)
        embed = StyledEmbed(
            title="🛡️ Kanade | Profile Update Failed",
            description=(
                "Discord rejected the profile change. Check that the image is supported and that Discord "
                "allows guild-specific profile banners for this bot. "
                f"API response: {getattr(exc, 'text', '') or str(exc)}"
            ),
            color=discord.Color.from_rgb(128, 0, 255),
        )
        await ctx.reply(embed=embed, mention_author=False)

@bot.command(name="cb", aliases=["changebanner", "serverbanner"])
@commands.guild_only()
async def change_bot_server_banner(ctx, *, image_url: str = None):
    if not await require_premium(ctx):
        return
    """Change the bot's server-specific profile banner using an image URL or attachment."""
    await change_guild_profile_image(ctx, "banner", image_url)

@bot.command(name="cp", aliases=["changepicture", "serveravatar"])
@commands.guild_only()
async def change_bot_server_avatar(ctx, *, image_url: str = None):
    if not await require_premium(ctx):
        return
    """Change the bot's server-specific profile picture using an image URL or attachment."""
    await change_guild_profile_image(ctx, "avatar", image_url)

_CUSTOM_EMOJI_RE = re.compile(r"<(?P<animated>a?):(?P<name>[A-Za-z0-9_]{2,32}):(?P<id>\d+)>")

@bot.command(name="steal", aliases=["emojiadd", "copyemoji"])
@commands.guild_only()
async def steal_custom_emoji(ctx, *, arguments: str = ""):
    if not await require_premium(ctx):
        return
    """Copy accessible custom emoji(s) into this server; source emojis are not removed."""
    purple = discord.Color.from_rgb(128, 0, 255)

    if not arguments.strip():
        embed = StyledEmbed(
            title="🛡️ Kanade | Steal Emoji",
            description=(
                "Mention a custom emoji from another server.\n"
                "Usage: `!steal <:emoji_name:emoji_id>`\n"
                "Optional custom name for one emoji: `!steal <:emoji_name:emoji_id> new_name`\n"
                "You need **Manage Expressions** permission in this server."
            ),
            color=purple,
        )
        return await ctx.reply(embed=embed, mention_author=False)

    member_perms = ctx.author.guild_permissions
    can_manage = (
        has_admin_access(ctx.author)
        or getattr(member_perms, "manage_emojis_and_stickers", False)
        or getattr(member_perms, "manage_expressions", False)
    )
    if not can_manage:
        embed = StyledEmbed(
            title="🛡️ Kanade | Permission Denied",
            description="You need Manage Expressions permission.",
            color=purple,
        )
        return await ctx.reply(embed=embed, mention_author=False)

    bot_member = ctx.guild.me
    bot_perms = bot_member.guild_permissions if bot_member else None
    bot_can_manage = bot_perms and (
        getattr(bot_perms, "manage_emojis_and_stickers", False)
        or getattr(bot_perms, "manage_expressions", False)
    )
    if not bot_can_manage:
        embed = StyledEmbed(
            title="🛡️ Kanade | Missing Permission",
            description="I need Manage Expressions permission.",
            color=purple,
        )
        return await ctx.reply(embed=embed, mention_author=False)

    matches = list(_CUSTOM_EMOJI_RE.finditer(arguments))
    if not matches:
        embed = StyledEmbed(
            title="🛡️ Kanade | No Custom Emoji Found",
            description=(
                "Mention a custom Discord emoji to copy it."
            ),
            color=purple,
        )
        return await ctx.reply(embed=embed, mention_author=False)

    remainder = _CUSTOM_EMOJI_RE.sub(" ", arguments).strip()
    requested_name = None
    if len(matches) == 1 and remainder:
        requested_name = re.sub(r"[^A-Za-z0-9_]", "_", remainder).strip("_")[:32]
        if len(requested_name) < 2:
            requested_name = None

    # Avoid duplicate uploads if the same emoji mention appears more than once.
    unique_matches = []
    seen_ids = set()
    for match in matches:
        emoji_id = match.group("id")
        if emoji_id not in seen_ids:
            seen_ids.add(emoji_id)
            unique_matches.append(match)

    success_names = []
    failures = []
    timeout = aiohttp.ClientTimeout(total=15, connect=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for match in unique_matches:
            emoji_id = match.group("id")
            original_name = match.group("name")
            animated = bool(match.group("animated"))
            extension = "gif" if animated else "png"
            emoji_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{extension}?quality=lossless"
            emoji_name = requested_name if len(unique_matches) == 1 and requested_name else original_name
            emoji_name = re.sub(r"[^A-Za-z0-9_]", "_", emoji_name).strip("_")[:32]
            if len(emoji_name) < 2:
                emoji_name = "copied_emoji"

            try:
                async with session.get(emoji_url, allow_redirects=True) as response:
                    if response.status != 200:
                        failures.append(f"`{original_name}` — source image unavailable (HTTP {response.status}).")
                        continue
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                    if not content_type.startswith("image/"):
                        failures.append(f"`{original_name}` — Discord CDN did not return an image.")
                        continue
                    image_data = await response.content.read(10 * 1024 * 1024 + 1)
                    if not image_data or len(image_data) > 10 * 1024 * 1024:
                        failures.append(f"`{original_name}` — image was empty or larger than 10 MB.")
                        continue

                created = await ctx.guild.create_custom_emoji(
                    name=emoji_name,
                    image=image_data,
                    reason=f"Emoji copied by {ctx.author} ({ctx.author.id}) using !steal",
                )
                success_names.append(f"{created} → `{created.name}`")
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.warning("Failed to download custom emoji %s: %r", emoji_id, exc)
                failures.append(f"`{original_name}` — download failed; try again.")
            except discord.Forbidden:
                failures.append(f"`{original_name}` — Discord denied the upload. Check bot permissions and emoji capacity.")
            except discord.HTTPException as exc:
                detail = getattr(exc, "text", None) or str(exc)
                failures.append(f"`{original_name}` — Discord rejected it: {detail[:180]}")

    embed = StyledEmbed(
        title="🛡️ Kanade | Emoji Import Results",
        description=f"Copied **{len(success_names)}/{len(unique_matches)}** emoji(s).",
        color=purple,
        timestamp=discord.utils.utcnow(),
    )
    if success_names:
        embed.add_field(name="Added", value="\n".join(success_names)[:1024], inline=False)
    if failures:
        embed.add_field(name="Not Added", value="\n".join(failures)[:1024], inline=False)
    embed.set_footer(text="Kanade • Developed by Wxre Development")
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="cn", aliases=["changenick", "botnick", "nickname"])
@commands.guild_only()
async def change_bot_server_nickname(ctx, *, nickname: str):
    if not await require_premium(ctx):
        return
    """Change the bot's nickname in this server. Use !cn reset to clear it."""
    purple = discord.Color.from_rgb(128, 0, 255)

    if not has_admin_access(ctx.author):
        embed = StyledEmbed(
            title="🛡️ Kanade | Permission Denied",
            description="Server admins only.",
            color=purple,
        )
        return await ctx.reply(embed=embed, mention_author=False)

    bot_member = ctx.guild.me
    if bot_member is None:
        return await ctx.reply("I couldn't find my member profile in this server.", mention_author=False)
    if not bot_member.guild_permissions.change_nickname:
        embed = StyledEmbed(
            title="🛡️ Kanade | Missing Permission",
            description="I need Change Nickname permission.",
            color=purple,
        )
        return await ctx.reply(embed=embed, mention_author=False)

    reset = nickname.strip().casefold() == "reset"
    new_nickname = None if reset else nickname.strip()
    if not reset and not new_nickname:
        return await ctx.reply("Provide a nickname, for example `!cn Kanade Guardian`, or use `!cn reset`.", mention_author=False)
    if new_nickname is not None and len(new_nickname) > 32:
        return await ctx.reply("Discord nicknames must be 32 characters or fewer.", mention_author=False)

    try:
        await bot_member.edit(
            nick=new_nickname,
            reason=f"Server nickname changed by {ctx.author} ({ctx.author.id})",
        )
    except discord.Forbidden:
        description = "Nickname change failed. Check my permissions and role position."
        embed = StyledEmbed(title="🛡️ Kanade | Nickname Change Failed", description=description, color=purple)
        return await ctx.reply(embed=embed, mention_author=False)
    except discord.HTTPException as exc:
        embed = StyledEmbed(
            title="🛡️ Kanade | Nickname Change Failed",
            description="Could not update nickname. Check permissions.",
            color=purple,
        )
        return await ctx.reply(embed=embed, mention_author=False)

    if reset:
        description = f"Reset my server nickname in **{ctx.guild.name}**. I'll use my default bot name here."
    else:
        description = f"My server nickname in **{ctx.guild.name}** is now **{discord.utils.escape_markdown(new_nickname)}**."
    embed = StyledEmbed(
        title="🛡️ Kanade | Nickname Updated",
        description=description,
        color=purple,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text="Kanade • Developed by Wxre Development")
    await ctx.reply(embed=embed, mention_author=False)

# ==================== AFK SYSTEM ====================
@bot.command(name="purge")
@commands.guild_only()
@commands.check(lambda ctx: has_admin_access(ctx.author))
async def purge_command(ctx, target: str = None, amount: int = None):
    """Delete messages: !purge 20, !purge all, or !purge @user 20."""
    purple = discord.Color.from_rgb(128, 0, 255)

    async def purge_embed(title, description, *, color=purple, delete_after=10):
        embed = StyledEmbed(
            title=title,
            description=description,
            color=color,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text=f"Requested by {ctx.author}")
        return await ctx.reply(embed=embed, mention_author=False, delete_after=delete_after)

    if not ctx.channel.permissions_for(ctx.me).manage_messages:
        return await purge_embed(
            "Missing Permission",
            "I need the **Manage Messages** permission in this channel.",
            color=discord.Color.red()
        )

    if target is None:
        return await purge_embed(
            "Purge Usage",
            "Use one of these commands:\n"
            "`!purge <amount>` — delete a number of recent messages\n"
            "`!purge all` — clear the channel's accessible message history\n"
            "`!purge @user <amount>` — delete recent messages from one member."
        )

    member = None
    purge_entire_channel = target.lower() == "all"
    if purge_entire_channel:
        delete_count = None
        mode_text = "the entire accessible channel history"
    elif target.isdigit():
        if amount is not None:
            return await purge_embed(
                "Invalid Usage",
                "Use `!purge <amount>` or `!purge @user <amount>`."
            )
        delete_count = int(target)
        mode_text = f"up to {delete_count} recent messages"
    else:
        try:
            member = await commands.MemberConverter().convert(ctx, target)
        except commands.MemberNotFound:
            return await purge_embed(
                "User Not Found",
                "Mention a member or use their ID. Example: `!purge @user 20`.",
                color=discord.Color.red()
            )
        if amount is None:
            return await purge_embed(
                "Missing Amount",
                "Specify how many messages to delete. Example: `!purge @user 20`."
            )
        delete_count = amount
        mode_text = f"up to {delete_count} recent messages from {member.mention}"

    if not purge_entire_channel and not 1 <= delete_count <= 100:
        return await purge_embed(
            "Invalid Amount",
            "Amount must be between **1 and 100**. Use `!purge all` to clear the entire accessible channel history.",
            color=discord.Color.red()
        )

    deleted_count = 0
    try:
        recent_cutoff = discord.utils.utcnow() - timedelta(days=14)
        recent_batch = []

        async for message in ctx.channel.history(limit=None):
            if message.id == ctx.message.id:
                continue
            if member is not None and message.author.id != member.id:
                continue

            if purge_entire_channel:
                if message.created_at < recent_cutoff:
                    if recent_batch:
                        if len(recent_batch) == 1:
                            await recent_batch[0].delete()
                        else:
                            await ctx.channel.delete_messages(recent_batch)
                        deleted_count += len(recent_batch)
                        recent_batch.clear()
                    await message.delete()
                    deleted_count += 1
                else:
                    recent_batch.append(message)
                    if len(recent_batch) >= 100:
                        await ctx.channel.delete_messages(recent_batch)
                        deleted_count += len(recent_batch)
                        recent_batch.clear()
            else:
                recent_batch.append(message)
                if len(recent_batch) >= delete_count:
                    break

        if recent_batch:
            if purge_entire_channel:
                if len(recent_batch) == 1:
                    await recent_batch[0].delete()
                else:
                    await ctx.channel.delete_messages(recent_batch)
                deleted_count += len(recent_batch)
            else:
                recent = [m for m in recent_batch if m.created_at >= recent_cutoff]
                old = [m for m in recent_batch if m.created_at < recent_cutoff]
                for start_idx in range(0, len(recent), 100):
                    batch = recent[start_idx:start_idx + 100]
                    if len(batch) == 1:
                        await batch[0].delete()
                    elif batch:
                        await ctx.channel.delete_messages(batch)
                    deleted_count += len(batch)
                for old_message in old:
                    await old_message.delete()
                    deleted_count += 1

        result = StyledEmbed(
            title="Kanade | Purge Complete",
            description=f"Deleted **{deleted_count:,}** message(s). ({mode_text})",
            color=purple,
            timestamp=discord.utils.utcnow()
        )
        result.set_footer(text=f"Requested by {ctx.author}")
        await ctx.send(embed=result, delete_after=10)
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
    except discord.Forbidden:
        await purge_embed(
            "Permission Error",
            "Missing permissions to read or delete messages.",
            color=discord.Color.red(),
            delete_after=12
        )
    except discord.HTTPException as exc:
        logger.warning("Purge failed in channel %s: %r", getattr(ctx.channel, "id", "unknown"), exc)
        await purge_embed(
            "Purge Failed",
            "Purge failed. Check bot permissions.",
            color=discord.Color.red(),
            delete_after=12
        )


@bot.command(name="afk")
@commands.guild_only()
async def afk_command(ctx, *, message="AFK"):
    key = (ctx.guild.id, ctx.author.id)
    afk_message = message[:500]
    afk_users[key] = {
        "message": afk_message,
        "since": discord.utils.utcnow()
    }
    embed = StyledEmbed(
        title="Kanade | AFK Enabled",
        description=(
            f"{ctx.author.mention} is now AFK: {discord.utils.escape_markdown(afk_message)}"
        ),
        color=discord.Color.from_rgb(128, 0, 255),
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="Kanade")
    await ctx.reply(embed=embed, mention_author=False)
# ==================== PREFIX / NO-PREFIX ACCESS HELPER ====================
def has_no_prefix_access(message):
    """Return True when the author is allowed to invoke commands without a prefix."""
    if message.guild is None:
        return False

    user_id = message.author.id
    global_values = config.get("global_no_prefix", [])
    if not isinstance(global_values, (list, tuple, set)):
        global_values = []
    global_ids = {int(value) for value in global_values if str(value).isdigit()}

    guild_mapping = config.get("guild_no_prefix", {})
    if not isinstance(guild_mapping, dict):
        guild_mapping = {}
    guild_values = guild_mapping.get(str(message.guild.id), [])
    if not isinstance(guild_values, (list, tuple, set)):
        guild_values = []
    guild_ids = {int(value) for value in guild_values if str(value).isdigit()}

    return user_id in global_ids or user_id in guild_ids


@bot.listen("on_message")
async def afk_message_listener(message):
    if message.author.bot or message.guild is None:
        return
    # Keep AFK enabled when the user sets or updates it. Resolve the command
    # through discord.py so this works with the configured per-guild prefix
    # (for example, "$afk kain"), aliases, and the default prefix. A literal
    # "!afk" check incorrectly cleared AFK immediately on servers using "$".
    try:
        # First recognize a normal prefixed AFK command using this server's
        # configured prefix. If the author is allowed to use no-prefix mode,
        # also resolve a synthetic prefixed copy so `afk busy` does not
        # immediately clear the AFK status it just set/updated.
        ctx = await bot.get_context(message)
        if ctx.command is not None and ctx.command.qualified_name.lower() == "afk":
            return

        if has_no_prefix_access(message):
            raw_content = message.content.strip()
            if raw_content:
                active_prefix = config.get("guild_prefixes", {}).get(
                    str(message.guild.id), PREFIX
                )
                if not isinstance(active_prefix, str) or not active_prefix:
                    active_prefix = PREFIX
                synthetic_message = copy.copy(message)
                synthetic_message.content = f"{active_prefix}{raw_content}"
                no_prefix_ctx = await bot.get_context(synthetic_message)
                if (
                    no_prefix_ctx.command is not None
                    and no_prefix_ctx.command.qualified_name.lower() == "afk"
                ):
                    return
    except (discord.DiscordException, AttributeError, TypeError, ValueError):
        # AFK notifications must never prevent normal messages from being handled.
        pass
    guild_id = message.guild.id
    author_id = message.author.id
    author_key = (guild_id, author_id)
    # Clear the sender's AFK status when they speak again.
    if author_key in afk_users:
        afk_data = afk_users.pop(author_key)
        elapsed = discord.utils.utcnow() - afk_data["since"]
        total_seconds = max(0, int(elapsed.total_seconds()))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            duration = f"{hours}h {minutes}m"
        elif minutes:
            duration = f"{minutes}m {seconds}s"
        else:
            duration = f"{seconds}s"
        embed = StyledEmbed(
            title="Kanade | AFK Removed",
            description=(
                f"Welcome back, {message.author.mention}!\n\n"
                "Your AFK status has been removed automatically because you sent a message.\n\n"
                f"**Time away:** {duration}\n"
                f"**Previous AFK message:** {discord.utils.escape_markdown(afk_data['message'])}"
            ),
            color=discord.Color.from_rgb(128, 0, 255),
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text="Kanade")
        try:
            await message.channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=[message.author])
            )
        except discord.HTTPException:
            pass
    # Notify users who mention someone currently AFK.
    notified = set()
    for mentioned_user in message.mentions:
        if mentioned_user.id == author_id:
            continue
        key = (guild_id, mentioned_user.id)
        if key not in afk_users or mentioned_user.id in notified:
            continue
        notified.add(mentioned_user.id)
        afk_data = afk_users[key]
        since = discord.utils.format_dt(afk_data["since"], style="R")
        embed = StyledEmbed(
            title=f"Kanade | {mentioned_user.display_name} is AFK",
            description=(
                f"**Message:** {discord.utils.escape_markdown(afk_data['message'])}\n"
                f"**Since:** {since}"
            ),
            color=discord.Color.from_rgb(128, 0, 255),
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text="Kanade")
        try:
            await message.channel.send(embed=embed)
        except discord.HTTPException:
            pass
@bot.command(name="kick")
@commands.guild_only()
@commands.check(lambda ctx: has_admin_access(ctx.author))
async def kick_command(ctx, target: str, *, reason="No reason provided"):
    """Kick a server member by mention or numeric Discord user ID."""
    raw = target.strip()
    match = re.fullmatch(r"<@!?(\d+)>", raw)
    raw_id = match.group(1) if match else (raw if raw.isdigit() else None)
    member = None
    if raw_id:
        member = ctx.guild.get_member(int(raw_id))
        if member is None:
            try:
                member = await ctx.guild.fetch_member(int(raw_id))
            except discord.NotFound:
                member = None
            except discord.HTTPException:
                return await ctx.reply(embed=role_embed(
                    "Member Lookup Failed", "╰・Discord couldn't look up that member. Please try again.", discord.Color.red()
                ), mention_author=False)
    else:
        try:
            member = await commands.MemberConverter().convert(ctx, raw)
        except commands.MemberNotFound:
            member = None
    if member is None:
        return await ctx.reply(embed=role_embed(
            "Member Not Found", "╭・I couldn't find that member in this server.\n╰・Use a mention or numeric user ID.\n\n◇ Usage: `!kick <@user/user ID> [reason]`", discord.Color.red()
        ), mention_author=False)
    if member.id == ctx.guild.owner_id or (not is_bot_admin_id(ctx.author.id) and member.top_role >= ctx.author.top_role):
        return await ctx.reply(embed=role_embed(
            "Cannot Kick Member", "╰・You can't kick the server owner or a member with an equal/higher role.", discord.Color.red()
        ), mention_author=False)
    try:
        await member.kick(reason=f"{ctx.author} ({ctx.author.id}): {reason}")
        embed = role_embed(
            "Member Kicked",
            f"╭・**User:** {discord.utils.escape_markdown(member.name)}\n"
            f"├・**User ID:** `{member.id}`\n"
            f"├・**Reason:** {discord.utils.escape_markdown(reason)}\n"
            f"╰・**Moderator:** {discord.utils.escape_markdown(ctx.author.name)}",
            discord.Color.from_rgb(128, 0, 255),
        )
        await ctx.reply(embed=embed, mention_author=False)
        await send_log(ctx.guild, "Member kicked", f"{member} (`{member.id}`) was kicked by {ctx.author} (`{ctx.author.id}`).\nReason: {reason}")
    except discord.Forbidden:
        await ctx.reply(embed=role_embed(
            "Kick Failed", "╰・I need **Kick Members** permission, and my role must be above the target's role.", discord.Color.red()
        ), mention_author=False)
    except discord.HTTPException:
        await ctx.reply(embed=role_embed("Kick Failed", "╰・Discord couldn't complete the kick. Please try again.", discord.Color.red()), mention_author=False)

@bot.command(name="timeout", aliases=["mute"])
@commands.guild_only()
@commands.check(lambda ctx: has_admin_access(ctx.author))
async def timeout_command(ctx, target: str, duration: str, *, reason="No reason provided"):
    """Timeout a server member by mention or user ID for 1min, 1d, or 1w."""
    duration_key = duration.strip().lower()
    durations = {
        "1min": (timedelta(minutes=1), "1 minute"),
        "1d": (timedelta(days=1), "1 day"),
        "1w": (timedelta(weeks=1), "1 week"),
    }
    if duration_key not in durations:
        return await ctx.reply(
            embed=role_embed(
                "Invalid Timeout Duration",
                "╭・Choose a supported duration: `1min`, `1d`, or `1w`.\n"
                "╰・Usage: `!timeout <@user/user ID> <1min/1d/1w> [reason]`",
                discord.Color.orange(),
            ),
            mention_author=False,
        )

    raw = target.strip()
    match = re.fullmatch(r"<@!?(\d+)>", raw)
    raw_id = match.group(1) if match else (raw if raw.isdigit() else None)
    member = None
    if raw_id:
        member = ctx.guild.get_member(int(raw_id))
        if member is None:
            try:
                member = await ctx.guild.fetch_member(int(raw_id))
            except discord.NotFound:
                member = None
            except discord.HTTPException:
                return await ctx.reply(
                    embed=role_embed("Lookup Failed", "Discord couldn't look up that member. Please try again.", discord.Color.red()),
                    mention_author=False,
                )
    else:
        try:
            member = await commands.MemberConverter().convert(ctx, raw)
        except commands.MemberNotFound:
            member = None

    if member is None:
        return await ctx.reply(
            embed=role_embed(
                "Member Not Found",
                "╭・I couldn't find that member in this server.\n"
                "╰・Use their mention or numeric user ID.",
                discord.Color.red(),
            ),
            mention_author=False,
        )

    if member.id == ctx.guild.owner_id or (
        not is_bot_admin_id(ctx.author.id) and member.top_role >= ctx.author.top_role
    ):
        return await ctx.reply(
            embed=role_embed(
                "Cannot Timeout Member",
                "╰・You can't timeout the server owner or a member with an equal/higher role.",
                discord.Color.red(),
            ),
            mention_author=False,
        )

    timeout_length, duration_label = durations[duration_key]
    try:
        await member.timeout(timeout_length, reason=f"{ctx.author} ({ctx.author.id}): {reason}")
        embed = role_embed(
            "Member Timed Out",
            f"╭・**User:** {discord.utils.escape_markdown(member.name)}\n"
            f"├・**User ID:** `{member.id}`\n"
            f"├・**Duration:** {duration_label}\n"
            f"├・**Reason:** {discord.utils.escape_markdown(reason)}\n"
            f"╰・**Moderator:** {discord.utils.escape_markdown(ctx.author.name)}",
            discord.Color.from_rgb(128, 0, 255),
        )
        await ctx.reply(embed=embed, mention_author=False)
        await send_log(
            ctx.guild,
            "Member timed out",
            f"{member} (`{member.id}`) was timed out by {ctx.author} (`{ctx.author.id}`).\n"
            f"Duration: {duration_label}\nReason: {reason}",
            discord.Color.orange(),
        )
    except discord.Forbidden:
        await ctx.reply(
            embed=role_embed(
                "Timeout Failed",
                "╰・I need **Moderate Members** permission, and my role must be above the target's role.",
                discord.Color.red(),
            ),
            mention_author=False,
        )
    except discord.HTTPException:
        await ctx.reply(
            embed=role_embed("Timeout Failed", "╰・Discord couldn't apply the timeout. Please try again.", discord.Color.red()),
            mention_author=False,
        )


@bot.command(name="untimeout", aliases=["unmute", "removetimeout"])
@commands.guild_only()
@commands.check(lambda ctx: has_admin_access(ctx.author))
async def untimeout_command(ctx, target: str, *, reason="Timeout removed by moderator"):
    """Remove a member's active timeout using a mention or numeric user ID."""
    raw = target.strip()
    match = re.fullmatch(r"<@!?(\d+)>", raw)
    raw_id = match.group(1) if match else (raw if raw.isdigit() else None)
    member = None
    if raw_id:
        member = ctx.guild.get_member(int(raw_id))
        if member is None:
            try:
                member = await ctx.guild.fetch_member(int(raw_id))
            except discord.NotFound:
                member = None
            except discord.HTTPException:
                return await ctx.reply(embed=role_embed("Member Lookup Failed", "╰・Discord couldn't look up that member. Please try again.", discord.Color.red()), mention_author=False)
    else:
        try:
            member = await commands.MemberConverter().convert(ctx, raw)
        except commands.MemberNotFound:
            member = None
    if member is None:
        return await ctx.reply(embed=role_embed(
            "Member Not Found", "╭・I couldn't find that member in this server.\n╰・Use a mention or numeric user ID.\n\n◇ Usage: `!untimeout <@user/user ID> [reason]`", discord.Color.red()
        ), mention_author=False)
    if member.id == ctx.guild.owner_id or (not is_bot_admin_id(ctx.author.id) and member.top_role >= ctx.author.top_role):
        return await ctx.reply(embed=role_embed(
            "Cannot Remove Timeout", "╰・You can't remove a timeout from the server owner or a member with an equal/higher role.", discord.Color.red()
        ), mention_author=False)
    try:
        if member.timed_out_until is None:
            return await ctx.reply(embed=role_embed(
                "No Active Timeout", f"╰・**{discord.utils.escape_markdown(member.name)}** does not currently have an active timeout.", discord.Color.orange()
            ), mention_author=False)
        await member.timeout(None, reason=f"{ctx.author} ({ctx.author.id}): {reason}")
        embed = role_embed(
            "Timeout Removed",
            f"╭・**User:** {discord.utils.escape_markdown(member.name)}\n"
            f"├・**User ID:** `{member.id}`\n"
            f"├・**Reason:** {discord.utils.escape_markdown(reason)}\n"
            f"╰・**Moderator:** {discord.utils.escape_markdown(ctx.author.name)}",
            discord.Color.from_rgb(128, 0, 255),
        )
        await ctx.reply(embed=embed, mention_author=False)
        await send_log(ctx.guild, "Member timeout removed", f"Timeout removed from {member} (`{member.id}`) by {ctx.author} (`{ctx.author.id}`).\nReason: {reason}", discord.Color.green())
    except discord.Forbidden:
        await ctx.reply(embed=role_embed("Remove Timeout Failed", "╰・I need **Moderate Members** permission, and my role must be above the target's role.", discord.Color.red()), mention_author=False)
    except discord.HTTPException:
        await ctx.reply(embed=role_embed("Remove Timeout Failed", "╰・Discord couldn't remove the timeout. Please try again.", discord.Color.red()), mention_author=False)


@bot.command(name="ban")
@commands.guild_only()
@commands.check(lambda ctx: has_admin_access(ctx.author))
async def ban_command(ctx, target: str, *, reason="No reason provided"):
    """Ban a member by mention or numeric Discord user ID."""
    raw = target.strip()
    match = re.fullmatch(r"<@!?(\d+)>", raw)
    raw_id = match.group(1) if match else (raw if raw.isdigit() else None)
    member = None
    if raw_id:
        member = ctx.guild.get_member(int(raw_id))
        if member is None:
            try:
                member = await ctx.guild.fetch_member(int(raw_id))
            except discord.NotFound:
                member = None
            except discord.HTTPException:
                return await ctx.reply(embed=role_embed("Member Lookup Failed", "╰・Discord couldn't look up that member. Please try again.", discord.Color.red()), mention_author=False)
        if member is None:
            try:
                await ctx.guild.ban(discord.Object(id=int(raw_id)), reason=f"{ctx.author} ({ctx.author.id}): {reason}")
                embed = role_embed(
                    "Member Banned",
                    f"╭・**User:** Unknown / not in server\n├・**User ID:** `{raw_id}`\n├・**Reason:** {discord.utils.escape_markdown(reason)}\n╰・**Moderator:** {discord.utils.escape_markdown(ctx.author.name)}",
                    discord.Color.from_rgb(128, 0, 255),
                )
                await ctx.reply(embed=embed, mention_author=False)
                await send_log(ctx.guild, "Member banned", f"User ID `{raw_id}` was banned by {ctx.author} (`{ctx.author.id}`).\nReason: {reason}", discord.Color.red())
            except discord.Forbidden:
                await ctx.reply(embed=role_embed("Ban Failed", "╰・I need **Ban Members** permission to ban this user.", discord.Color.red()), mention_author=False)
            except discord.HTTPException:
                await ctx.reply(embed=role_embed("Ban Failed", "╰・Discord couldn't complete the ban. Please try again.", discord.Color.red()), mention_author=False)
            return
    else:
        try:
            member = await commands.MemberConverter().convert(ctx, raw)
        except commands.MemberNotFound:
            return await ctx.reply(embed=role_embed(
                "Member Not Found", "╭・I couldn't find that member.\n╰・Use a mention or numeric user ID.\n\n◇ Usage: `!ban <@user/user ID> [reason]`", discord.Color.red()
            ), mention_author=False)
    if member.id == ctx.guild.owner_id or (not is_bot_admin_id(ctx.author.id) and member.top_role >= ctx.author.top_role):
        return await ctx.reply(embed=role_embed("Cannot Ban Member", "╰・You can't ban the server owner or a member with an equal/higher role.", discord.Color.red()), mention_author=False)
    try:
        await member.ban(reason=f"{ctx.author} ({ctx.author.id}): {reason}")
        embed = role_embed(
            "Member Banned",
            f"╭・**User:** {discord.utils.escape_markdown(member.name)}\n"
            f"├・**User ID:** `{member.id}`\n"
            f"├・**Reason:** {discord.utils.escape_markdown(reason)}\n"
            f"╰・**Moderator:** {discord.utils.escape_markdown(ctx.author.name)}",
            discord.Color.from_rgb(128, 0, 255),
        )
        await ctx.reply(embed=embed, mention_author=False)
        await send_log(ctx.guild, "Member banned", f"{member} (`{member.id}`) was banned by {ctx.author} (`{ctx.author.id}`).\nReason: {reason}", discord.Color.red())
    except discord.Forbidden:
        await ctx.reply(embed=role_embed("Ban Failed", "╰・I need **Ban Members** permission, and my role must be above the target's role.", discord.Color.red()), mention_author=False)
    except discord.HTTPException:
        await ctx.reply(embed=role_embed("Ban Failed", "╰・Discord couldn't complete the ban. Please try again.", discord.Color.red()), mention_author=False)

@bot.command(name="unban")
@commands.guild_only()
@commands.check(lambda ctx: has_admin_access(ctx.author))
async def unban_command(ctx, user_id: str = None):
    """Unban one user by ID, or all currently banned users after confirmation."""
    if user_id is None:
        return await ctx.reply(embed=role_embed("Missing Argument", "Usage: `!unban <user ID>` or `!unban all`."), mention_author=False)
    raw_id = user_id.strip().strip("<@!>")
    if raw_id.lower() == "all":
        if not ctx.guild.me.guild_permissions.ban_members:
            return await ctx.reply(embed=role_embed("Permission Denied", "I need **Ban Members** permission to unban users.", discord.Color.red()), mention_author=False)
        try:
            banned_entries = [entry async for entry in ctx.guild.bans(limit=None)]
        except discord.Forbidden:
            return await ctx.reply(embed=role_embed("Permission Denied", "I cannot view the server ban list. Check my **Ban Members** permission.", discord.Color.red()), mention_author=False)
        except discord.HTTPException as error:
            return await ctx.reply(embed=role_embed("Unban Failed", f"Could not retrieve the ban list: `{str(error)[:300]}`", discord.Color.red()), mention_author=False)
        if not banned_entries:
            return await ctx.reply(embed=role_embed("No Bans Found", "There are no banned users in this server."), mention_author=False)
        prompt = await ctx.reply(embed=role_embed("Confirm Unban All", f"This will unban **{len(banned_entries)} users** from this server.\nReact with ✅ within 30 seconds to confirm, or ❌ to cancel.", discord.Color.orange()), mention_author=False)
        try:
            for emoji in ("✅", "❌"):
                await prompt.add_reaction(emoji)
        except discord.HTTPException:
            return await ctx.reply(embed=role_embed("Confirmation Unavailable", "I couldn't add confirmation reactions. Nothing was unbanned.", discord.Color.red()), mention_author=False)
        def _unban_all_check(reaction, user):
            return user.id == ctx.author.id and reaction.message.id == prompt.id and str(reaction.emoji) in ("✅", "❌")
        try:
            reaction, _ = await bot.wait_for("reaction_add", timeout=30.0, check=_unban_all_check)
        except asyncio.TimeoutError:
            return await prompt.edit(embed=role_embed("Unban All Cancelled", "No confirmation received. No users were unbanned.", discord.Color.orange()))
        if str(reaction.emoji) != "✅":
            return await prompt.edit(embed=role_embed("Unban All Cancelled", "You cancelled the operation. No users were unbanned.", discord.Color.orange()))
        succeeded, failed = 0, 0
        for entry in banned_entries:
            try:
                await ctx.guild.unban(entry.user, reason=f"Bulk unban requested by {ctx.author} ({ctx.author.id})")
                succeeded += 1
            except (discord.Forbidden, discord.HTTPException):
                failed += 1
        await send_log(ctx.guild, "Bulk Unban Completed", f"**Moderator:** {ctx.author.mention} (`{ctx.author.id}`)\n**Successfully unbanned:** {succeeded}\n**Failed:** {failed}", discord.Color.orange())
        return await prompt.edit(embed=role_embed("Unban All Complete", f"Successfully unbanned: **{succeeded}**\nFailed: **{failed}**", discord.Color.green() if failed == 0 else discord.Color.orange()))
    if not raw_id.isdigit():
        return await ctx.reply(embed=role_embed("Invalid User ID", "Usage: `!unban <user ID>` — provide the numeric ID of a banned user."), mention_author=False)
    try:
        entry = await ctx.guild.fetch_ban(discord.Object(id=int(raw_id)))
        await ctx.guild.unban(entry.user, reason=f"Unban requested by {ctx.author} ({ctx.author.id})")
        embed = role_embed("Member Unbanned", f"**User:** {entry.user}\n**User ID:** `{entry.user.id}`\n**Unbanned by:** {ctx.author.mention}", discord.Color.green())
        await ctx.reply(embed=embed, mention_author=False)
        await send_log(ctx.guild, "Member unbanned", f"{entry.user} (`{entry.user.id}`) was unbanned by {ctx.author} (`{ctx.author.id}`).", discord.Color.green())
    except discord.NotFound:
        await ctx.reply(embed=role_embed("Ban Not Found", f"No active ban was found for user ID `{raw_id}`.", discord.Color.orange()), mention_author=False)
    except discord.Forbidden:
        await ctx.reply(embed=role_embed("Permission Denied", "I need **Ban Members** permission to unban users.", discord.Color.red()), mention_author=False)
    except discord.HTTPException as error:
        await ctx.reply(embed=role_embed("Unban Failed", f"Discord could not complete the unban: {error}", discord.Color.red()), mention_author=False)

# Emergency cleanup: removes only roles carrying high-risk permissions, never every role.
@bot.command(name="roledeletes")
@commands.guild_only()
async def roledeletes_command(ctx):
    if not await manager_check(ctx):
        return
    if not ctx.guild.me.guild_permissions.manage_roles:
        return await ctx.reply(embed=role_embed("Permission Denied", "I need **Manage Roles** permission.", discord.Color.red()), mention_author=False)
    dangerous_flags = ("administrator", "manage_guild", "manage_roles", "manage_channels", "manage_webhooks", "ban_members", "kick_members", "manage_messages", "mention_everyone")
    bot_top = ctx.guild.me.top_role
    candidates = [role for role in ctx.guild.roles if role != ctx.guild.default_role and not role.managed and role < bot_top and any(getattr(role.permissions, flag, False) for flag in dangerous_flags)]
    if not candidates:
        return await ctx.reply(embed=role_embed("No Risky Roles Found", "No deletable roles with high-risk permissions were found below Kanade's highest role."), mention_author=False)
    preview = "\n".join(f"• {discord.utils.escape_markdown(role.name)} (`{role.id}`)" for role in sorted(candidates, key=lambda r: r.position, reverse=True)[:20])
    if len(candidates) > 20:
        preview += f"\n…and {len(candidates) - 20} more."
    prompt = await ctx.reply(embed=role_embed("Confirm Risky Role Cleanup", f"This will delete **{len(candidates)} roles** that have administrator, server-management, moderation, or other high-risk permissions.\n\n{preview}\n\nThis can remove legitimate staff roles. React with ✅ within 30 seconds to confirm, or ❌ to cancel.", discord.Color.orange()), mention_author=False)
    try:
        for emoji in ("✅", "❌"):
            await prompt.add_reaction(emoji)
    except discord.HTTPException:
        return await ctx.reply(embed=role_embed("Confirmation Unavailable", "I couldn't add confirmation reactions. No roles were deleted.", discord.Color.red()), mention_author=False)
    def _role_cleanup_check(reaction, user):
        return user.id == ctx.author.id and reaction.message.id == prompt.id and str(reaction.emoji) in ("✅", "❌")
    try:
        reaction, _ = await bot.wait_for("reaction_add", timeout=30.0, check=_role_cleanup_check)
    except asyncio.TimeoutError:
        return await prompt.edit(embed=role_embed("Role Cleanup Cancelled", "No confirmation received. No roles were deleted.", discord.Color.orange()))
    if str(reaction.emoji) != "✅":
        return await prompt.edit(embed=role_embed("Role Cleanup Cancelled", "You cancelled the operation. No roles were deleted.", discord.Color.orange()))
    deleted, failed = 0, 0
    deleted_names = []
    for role in candidates:
        try:
            deleted_names.append(f"{role.name} ({role.id})")
            await role.delete(reason=f"Kanade emergency risky-role cleanup requested by {ctx.author} ({ctx.author.id})")
            deleted += 1
        except (discord.Forbidden, discord.HTTPException):
            failed += 1
    await send_log(ctx.guild, "Risky Roles Bulk Deleted", f"**Moderator:** {ctx.author.mention} (`{ctx.author.id}`)\n**Deleted:** {deleted}\n**Failed:** {failed}\n**Role names:** {', '.join(deleted_names[:15]) or 'None'}", discord.Color.orange())
    await prompt.edit(embed=role_embed("Role Cleanup Complete", f"Deleted: **{deleted}**\nFailed: **{failed}**\nOnly roles with high-risk permissions were targeted.", discord.Color.green() if failed == 0 else discord.Color.orange()))

# ==================== ROLE MANAGEMENT ====================
def role_embed(title, description, color=None):
    if color is None:
        color = discord.Color.from_rgb(128, 0, 255)
    embed = StyledEmbed(
        title=f"Kanade | {title}",
        description=description,
        color=color,
        timestamp=discord.utils.utcnow()
    )
    return embed

def find_role_by_name(guild, role_name):
    """Resolve a role by mention, numeric ID, or exact name."""
    raw = str(role_name).strip()
    match = re.fullmatch(r"<@&?(\d+)>", raw)
    role_id = int(match.group(1)) if match else (int(raw) if raw.isdigit() else None)
    if role_id is not None:
        role = guild.get_role(role_id)
        if role is not None:
            return role
    wanted = raw.casefold()
    return discord.utils.find(lambda item: item.name.casefold() == wanted, guild.roles)

async def bulk_role_operation(guild, actor, role, remove=False):
    """Apply or remove one role from members the bot can manage."""
    bot_member = guild.me
    action = "remove" if remove else "assign"

    if not actor.guild_permissions.manage_roles:
        return role_embed("Permission Denied", "You need **Manage Roles** permission.", discord.Color.red())
    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        return role_embed("Permission Denied", "I need **Manage Roles** permission.", discord.Color.red())
    if role.is_default() or role.managed:
        return role_embed("Invalid Role", "The @everyone role and managed integration roles cannot be changed.", discord.Color.red())
    if role >= bot_member.top_role:
        return role_embed("Role Hierarchy", "Move my bot role above the role you want to manage.", discord.Color.red())
    if not is_bot_admin_id(actor.id) and actor.id != guild.owner_id and role >= actor.top_role:
        return role_embed("Role Hierarchy", "You cannot manage a role equal to or above your highest role.", discord.Color.red())

    try:
        await guild.chunk(cache=True)
    except (discord.HTTPException, discord.Forbidden):
        pass

    updated = 0
    skipped = 0
    failed = 0
    members = list(guild.members)
    for member in members:
        if member.id == (bot.user.id if bot.user else 0):
            skipped += 1
            continue
        if member.id == guild.owner_id or member.top_role >= bot_member.top_role:
            skipped += 1
            continue
        if not is_bot_admin_id(actor.id) and actor.id != guild.owner_id and member.top_role >= actor.top_role:
            skipped += 1
            continue
        already_has_role = role in member.roles
        if (remove and not already_has_role) or (not remove and already_has_role):
            skipped += 1
            continue
        try:
            if remove:
                await member.remove_roles(role, reason=f"Bulk role removal by {actor} ({actor.id})")
            else:
                await member.add_roles(role, reason=f"Bulk role assignment by {actor} ({actor.id})")
            updated += 1
            await asyncio.sleep(0.25)
        except discord.Forbidden:
            failed += 1
        except discord.HTTPException:
            failed += 1
            await asyncio.sleep(1)

    verb = "Removed" if remove else "Assigned"
    result = role_embed(
        f"Bulk Role {verb}",
        f"**Role:** {role.mention}\n"
        f"**Moderator:** {actor.mention}\n"
        f"**Successfully updated:** {updated}\n"
        f"**Skipped:** {skipped}\n"
        f"**Failed:** {failed}",
        discord.Color.green() if failed == 0 else discord.Color.orange()
    )
    await send_log(
        guild,
        f"Bulk Role {verb}",
        f"Role: {role.name} ({role.id})\nModerator: {actor} ({actor.id})\n"
        f"Updated: {updated} | Skipped: {skipped} | Failed: {failed}"
    )
    return result

@bot.command(name="role")
@commands.guild_only()
@commands.check(lambda ctx: has_admin_access(ctx.author))
async def role_command(ctx, target: str, *, role_name: str):
    guild = ctx.guild
    role = find_role_by_name(guild, role_name)
    if role is None:
        return await ctx.reply(
            embed=role_embed("Role Not Found", f"No role named **{discord.utils.escape_markdown(role_name)}** exists in this server.", discord.Color.red()),
            mention_author=False
        )
    if target.casefold() == "all":
        result = await bulk_role_operation(guild, ctx.author, role, remove=False)
        return await ctx.reply(embed=result, mention_author=False)

    member = None
    id_match = re.fullmatch(r"<@!?(\d+)>", target.strip())
    target_id = int(id_match.group(1)) if id_match else (int(target.strip()) if target.strip().isdigit() else None)
    if target_id is not None:
        member = guild.get_member(target_id)
        if member is None:
            try:
                member = await guild.fetch_member(target_id)
            except (discord.NotFound, discord.Forbidden):
                member = None
            except discord.HTTPException as error:
                return await ctx.reply(embed=role_embed("Member Lookup Failed", f"Discord could not look up that member: {error}", discord.Color.red()), mention_author=False)
    else:
        try:
            member = await commands.MemberConverter().convert(ctx, target)
        except commands.MemberNotFound:
            member = None
    if member is None:
        return await ctx.reply(
            embed=role_embed("Member Not Found", "Mention a member or provide their ID. Use `!role all Role Name` to assign a role in bulk.", discord.Color.red()),
            mention_author=False
        )

    bot_member = guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        return await ctx.reply(embed=role_embed("Permission Denied", "I need **Manage Roles** permission.", discord.Color.red()), mention_author=False)
    if role.is_default() or role.managed:
        return await ctx.reply(embed=role_embed("Invalid Role", "That role cannot be assigned manually.", discord.Color.red()), mention_author=False)
    if role in member.roles:
        return await ctx.reply(embed=role_embed("Role Already Assigned", f"{member.mention} already has {role.mention}."), mention_author=False)
    if member.id == guild.owner_id or (not is_bot_admin_id(ctx.author.id) and ctx.author.id != guild.owner_id and member.top_role >= ctx.author.top_role):
        return await ctx.reply(embed=role_embed("Role Assignment Failed", "You cannot assign roles to the server owner or a member with an equal or higher top role.", discord.Color.red()), mention_author=False)
    if member.top_role >= bot_member.top_role or role >= bot_member.top_role:
        return await ctx.reply(embed=role_embed("Role Hierarchy", "The member or role is at or above my highest role. Move my bot role higher.", discord.Color.red()), mention_author=False)
    if not is_bot_admin_id(ctx.author.id) and ctx.author.id != guild.owner_id and role >= ctx.author.top_role:
        return await ctx.reply(embed=role_embed("Role Hierarchy", "You cannot assign a role equal to or above your highest role.", discord.Color.red()), mention_author=False)
    try:
        await member.add_roles(role, reason=f"Role assigned by {ctx.author} ({ctx.author.id})")
    except (discord.Forbidden, discord.HTTPException) as error:
        return await ctx.reply(embed=role_embed("Role Assignment Failed", f"Discord could not complete the assignment: {error}", discord.Color.red()), mention_author=False)
    await ctx.reply(embed=role_embed("Role Added Successfully", f"**User:** {member.mention}\n**Role:** {role.mention}\n**Assigned by:** {ctx.author.mention}"), mention_author=False)

@bot.command(name="unrole")
@commands.guild_only()
@commands.check(lambda ctx: has_admin_access(ctx.author))
async def unrole_command(ctx, target: str, *, role_name: str):
    guild = ctx.guild
    role = find_role_by_name(guild, role_name)
    if role is None:
        return await ctx.reply(embed=role_embed("Role Not Found", f"No role named **{discord.utils.escape_markdown(role_name)}** exists in this server.", discord.Color.red()), mention_author=False)
    if target.casefold() == "all":
        result = await bulk_role_operation(guild, ctx.author, role, remove=True)
        return await ctx.reply(embed=result, mention_author=False)
    member = None
    id_match = re.fullmatch(r"<@!?(\d+)>", target.strip())
    target_id = int(id_match.group(1)) if id_match else (int(target.strip()) if target.strip().isdigit() else None)
    if target_id is not None:
        member = guild.get_member(target_id)
        if member is None:
            try:
                member = await guild.fetch_member(target_id)
            except (discord.NotFound, discord.Forbidden):
                member = None
            except discord.HTTPException as error:
                return await ctx.reply(embed=role_embed("Member Lookup Failed", f"Discord could not look up that member: {error}", discord.Color.red()), mention_author=False)
    else:
        try:
            member = await commands.MemberConverter().convert(ctx, target)
        except commands.MemberNotFound:
            member = None
    if member is None:
        return await ctx.reply(embed=role_embed("Member Not Found", "Mention a member or provide their ID. Use `!unrole all Role Name` to remove a role in bulk.", discord.Color.red()), mention_author=False)
    bot_member = guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        return await ctx.reply(embed=role_embed("Permission Denied", "I need **Manage Roles** permission.", discord.Color.red()), mention_author=False)
    if role.is_default() or role.managed:
        return await ctx.reply(embed=role_embed("Invalid Role", "That role cannot be removed manually.", discord.Color.red()), mention_author=False)
    if role not in member.roles:
        return await ctx.reply(embed=role_embed("Role Not Assigned", f"{member.mention} does not have {role.mention}."), mention_author=False)
    if member.id == guild.owner_id or (not is_bot_admin_id(ctx.author.id) and ctx.author.id != guild.owner_id and member.top_role >= ctx.author.top_role):
        return await ctx.reply(embed=role_embed("Role Removal Failed", "You cannot remove roles from the server owner or a member with an equal or higher top role.", discord.Color.red()), mention_author=False)
    if member.top_role >= bot_member.top_role or role >= bot_member.top_role:
        return await ctx.reply(embed=role_embed("Role Hierarchy", "The member or role is at or above my highest role. Move my bot role higher.", discord.Color.red()), mention_author=False)
    if not is_bot_admin_id(ctx.author.id) and ctx.author.id != guild.owner_id and role >= ctx.author.top_role:
        return await ctx.reply(embed=role_embed("Role Hierarchy", "You cannot remove a role equal to or above your highest role.", discord.Color.red()), mention_author=False)
    try:
        await member.remove_roles(role, reason=f"Role removed by {ctx.author} ({ctx.author.id})")
    except (discord.Forbidden, discord.HTTPException) as error:
        return await ctx.reply(embed=role_embed("Role Removal Failed", f"Discord could not complete the removal: {error}", discord.Color.red()), mention_author=False)
    await ctx.reply(embed=role_embed("Role Removed Successfully", f"**User:** {member.mention}\n**Role:** {role.mention}\n**Removed by:** {ctx.author.mention}"), mention_author=False)

@bot.command(name="security")
@commands.guild_only()
async def security_command(ctx):
    if ctx.guild is None:
        return await ctx.reply(
            "Use this in a server.",
            mention_author=False
        )
    guild_whitelist = config.get(
        "guild_whitelists", {}
    ).get(str(ctx.guild.id), [])
    embed = StyledEmbed(
        title="Kanade Status",
        color=discord.Color.green()
    )
    embed.add_field(
        name="Anti-nuke",
        value="Enabled" if get_guild_setting(ctx.guild, "antinuke_enabled", config["antinuke_enabled"]) else "Disabled"
    )
    embed.add_field(
        name="Raid protection",
        value="Enabled" if config["raid_protection"] else "Disabled"
    )
    embed.add_field(
        name="Whitelisted users",
        value=str(len(guild_whitelist))
    )
    embed.add_field(
        name="Servers protected",
        value=str(len(bot.guilds))
    )
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="prefix")
@commands.guild_only()
async def prefix_command(ctx, *, new_prefix: str = None):
    """Set or view this server's prefix; custom prefixes require Premium."""
    purple = discord.Color.from_rgb(35, 0, 55)
    guild_key = str(ctx.guild.id)
    current_prefix = config.get("guild_prefixes", {}).get(guild_key, PREFIX)
    premium_admin_ids = config.get("premium_admin_ids", [DEVELOPER_ID])

    if not is_owner_id(ctx.author.id) and ctx.author.id not in premium_admin_ids and not is_premium_guild(ctx.guild):
        embed = StyledEmbed(
            title="Kanade | Premium Feature",
            description=(
                "Custom prefixes require Premium."
            ),
            color=purple
        )
        embed.set_footer(text="Kanade • Developed by Wxre Development")
        return await ctx.reply(embed=embed, mention_author=False)

    if not has_admin_access(ctx.author) and not is_owner_id(ctx.author.id):
        embed = StyledEmbed(
            title="Kanade | Permission Denied",
            description="Administrator permission required.",
            color=purple
        )
        embed.set_footer(text="Kanade • Developed by Wxre Development")
        return await ctx.reply(embed=embed, mention_author=False)

    if new_prefix is None:
        embed = StyledEmbed(
            title="Kanade | Server Prefix",
            description=(
                f"Prefix: `{current_prefix}`\nUsage: `{current_prefix}prefix <new_prefix>`"
            ),
            color=purple
        )
        embed.set_footer(text="Kanade • Developed by Wxre Development")
        return await ctx.reply(embed=embed, mention_author=False)

    new_prefix = new_prefix.strip()
    if (not 1 <= len(new_prefix) <= 32
            or any(char.isspace() for char in new_prefix)
            or "\n" in new_prefix or "\r" in new_prefix):
        embed = StyledEmbed(
            title="Kanade | Invalid Prefix",
            description=(
                "Use 1–32 characters with no spaces. Example: `!` or `$`."
            ),
            color=purple
        )
        embed.set_footer(text="Kanade • Developed by Wxre Development")
        return await ctx.reply(embed=embed, mention_author=False)

    config.setdefault("guild_prefixes", {})[guild_key] = new_prefix
    save_config()
    embed = StyledEmbed(
        title="Kanade | Prefix Updated",
        description=(
            f"Prefix changed to `{new_prefix}`. Use `{new_prefix}help`."
        ),
        color=purple
    )
    embed.add_field(name="Previous prefix", value=f"`{current_prefix}`", inline=True)
    embed.add_field(name="New prefix", value=f"`{new_prefix}`", inline=True)
    embed.set_footer(text="Kanade • Developed by Wxre Development")
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="premium")
async def premium_command(ctx, action: str = None, guild_id: str = None):
    """Allow only the developer and explicitly configured bot admins to manage Premium."""
    # This check intentionally ignores guild owner/admin permissions.
    if not is_bot_admin_id(ctx.author.id):
        embed = StyledEmbed(
            title="🚫 Access Denied",
            description="Only the bot developer and authorized bot admins can manage Premium.",
            color=discord.Color.red(),
        )
        return await ctx.reply(embed=embed, mention_author=False)

    action = (action or "help").lower()
    if action in ("help", "status"):
        embed = StyledEmbed(
            title="🛡️ Kanade | Premium Manager",
            description=(
                "`!premium add [server_id]` — Enable Premium\n`!premium remove [server_id]` — Remove Premium\n`!premium list` — List Premium servers"
            ),
            color=discord.Color.from_rgb(128, 0, 255),
        )
        return await ctx.reply(embed=embed, mention_author=False)

    if action == "list":
        ids = sorted({int(value) for value in config.get("premium_guild_ids", config.get("premium_guilds", [])) if str(value).isdigit()})
        if not ids:
            listing = "No servers currently have Premium."
        else:
            rows = []
            for gid in ids:
                guild = bot.get_guild(gid)
                server_name = guild.name if guild else "Server not found in bot cache"
                rows.append(f"• **{discord.utils.escape_markdown(server_name)}**\n  Server ID: `{gid}`")
            listing = "\n".join(rows)
            if len(listing) > 3900:
                listing = listing[:3850].rsplit("\n", 1)[0] + "\n… list shortened to fit Discord's embed limit."
        embed = StyledEmbed(
            title="🛡️ Kanade | Premium Servers",
            description=listing,
            color=discord.Color.from_rgb(128, 0, 255),
        )
        embed.set_footer(text=f"Kanade • {len(ids)} Premium server(s)")
        return await ctx.reply(embed=embed, mention_author=False)

    if action not in ("add", "remove"):
        return await ctx.reply("Usage: `!premium add [server_id]`, `!premium remove [server_id]`, or `!premium list`.", mention_author=False)

    target_text = guild_id
    if target_text is None:
        if ctx.guild is None:
            return await ctx.reply("In DMs, provide a server ID: `!premium add 123456789012345678`.", mention_author=False)
        target_id = ctx.guild.id
    elif target_text.isdigit():
        target_id = int(target_text)
    else:
        return await ctx.reply("Server ID must contain digits only.", mention_author=False)

    premium_ids = set(config.get("premium_guild_ids", config.get("premium_guilds", [])))
    if action == "add":
        if target_id in premium_ids:
            message = f"Server `{target_id}` already has Premium enabled."
        else:
            premium_ids.add(target_id)
            message = f"Premium has been **enabled** for server `{target_id}`."
    else:
        if target_id not in premium_ids:
            message = f"Server `{target_id}` does not currently have Premium."
        else:
            premium_ids.remove(target_id)
            message = f"Premium has been **removed** from server `{target_id}`."
    config["premium_guild_ids"] = sorted(premium_ids)
    config["premium_guilds"] = list(config["premium_guild_ids"])
    save_config()
    embed = StyledEmbed(
        title="🛡️ Kanade | Premium Updated",
        description=message,
        color=discord.Color.from_rgb(128, 0, 255),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text="Kanade • Premium Manager")
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="adm")
async def premium_admin_command(ctx, action: str = None, user: discord.User = None):
    """Manage bot-admin access; restricted strictly to the configured primary developer ID."""
    if ctx.author.id != DEVELOPER_ID:
        embed = StyledEmbed(
            title="🚫 Access Denied",
            description="Developer only.",
            color=discord.Color.red(),
        )
        return await ctx.reply(embed=embed, mention_author=False)

    action = (action or "help").lower()
    if action not in ("add", "remove") or user is None:
        embed = StyledEmbed(
            title="🛡️ Kanade | Premium Admin Manager",
            description=(
                "`!adm add @user` — Grant bot-admin access to restricted commands and Premium features\n"
                "`!adm remove @user` — Remove that bypass access\n"
                "`!admlist` — Show the bot-admin list\n\n"
                "The bot developer is permanently listed first and cannot be removed."
            ),
            color=discord.Color.from_rgb(128, 0, 255),
        )
        return await ctx.reply(embed=embed, mention_author=False)

    admin_ids = list(dict.fromkeys(int(value) for value in config.get("premium_admin_ids", []) if str(value).isdigit()))
    owner_id = DEVELOPER_ID
    if action == "add":
        if user.id in admin_ids:
            message = f"{user.mention} is already on the bot-admin list."
        else:
            admin_ids.append(user.id)
            message = f"{user.mention} can now use bot-admin commands and Premium features without needing server administrator permissions."
    else:
        if user.id == owner_id or user.id in config.get("owner_ids", []):
            return await ctx.reply("The bot owner is permanently protected and cannot be removed from the admin list.", mention_author=False)
        if user.id not in admin_ids:
            message = f"{user.mention} is not on the bot-admin list."
        else:
            admin_ids.remove(user.id)
            message = f"Bot-admin access has been removed from {user.mention}."

    # Pin the developer first, then retain the order in which other admins were added.
    config["premium_admin_ids"] = [owner_id] + [uid for uid in admin_ids if uid != owner_id]
    save_config()
    embed = StyledEmbed(
        title="🛡️ Kanade | Admin List Updated",
        description=message,
        color=discord.Color.from_rgb(128, 0, 255),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text="Kanade • Developer / Staff")
    await ctx.reply(embed=embed, mention_author=False)


async def _bot_admin_username_line(user_id: int, index: int = None) -> str:
    """Return only the Discord account username and numeric user ID (never a server nickname)."""
    user = bot.get_user(user_id)
    if user is None:
        try:
            user = await bot.fetch_user(user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            user = None
    username = user.name if user is not None else "Unknown username"
    prefix = f"{index}. " if index is not None else ""
    return f"{prefix}`{username}` — `{user_id}`"


@bot.command(name="admlist")
async def premium_admin_list_command(ctx):
    """Display account usernames and IDs for bot admins to authorized bot staff only."""
    if not is_global_staff(ctx.author):
        embed = StyledEmbed(
            title="🚫 Access Denied",
            description="This command is restricted to authorized bot staff.",
            color=discord.Color.red(),
        )
        return await ctx.reply(embed=embed, mention_author=False)
    owner_id = DEVELOPER_ID
    admin_ids = list(dict.fromkeys(
        int(value)
        for value in config.get("premium_admin_ids", [])
        if str(value).isdigit()
    ))
    ordered_ids = [owner_id] + [uid for uid in admin_ids if uid != owner_id]
    regular_admins = ordered_ids[1:]
    purple = discord.Color.from_rgb(35, 0, 55)

    embed = StyledEmbed(
        title="🛡️ Kanade | Staff List",
        description=(
            "Need help with Premium access? Contact the developer or an authorized staff member for support and assistance."
        ),
        color=purple,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="👑 Developer",
        value=await _bot_admin_username_line(owner_id),
        inline=False,
    )

    if regular_admins:
        # Discord embed fields have a 1,024-character limit. Split long lists safely.
        chunks = []
        current = []
        for index, uid in enumerate(regular_admins, start=1):
            line = await _bot_admin_username_line(uid, index)
            candidate = "\n".join(current + [line])
            if len(candidate) > 1000 and current:
                chunks.append(current)
                current = [line]
            else:
                current.append(line)
        if current:
            chunks.append(current)

        for page, chunk in enumerate(chunks, start=1):
            label = "🛡️ Staff" if len(chunks) == 1 else f"🛡️ Staff · {page}/{len(chunks)}"
            embed.add_field(name=label, value="\n".join(chunk), inline=False)
    else:
        embed.add_field(
            name="🛡️ Staff",
            value="No additional admins have been added.",
            inline=False,
        )

    embed.set_footer(text="Developer / Staff")
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="antinuke")
@commands.guild_only()
async def antinuke_command(ctx, mode: str = None):
    """Enable or disable anti-nuke protection with a purple status embed."""
    if not await manager_check(ctx):
        return

    mode = mode.lower().strip() if mode else ""
    purple = discord.Color.from_rgb(35, 0, 55)

    if mode not in ("on", "off"):
        enabled = bool(get_guild_setting(ctx.guild, "antinuke_enabled", config.get("antinuke_enabled", True)))
        embed = StyledEmbed(
            title="Kanade | Anti-Nuke",
            description=(
                f"Anti-nuke protection is currently **{'ON' if enabled else 'OFF'}**.\n\n"
                "Use `!antinuke on` to enable protection or `!antinuke off` to disable it."
            ),
            color=purple
        )
        embed.set_footer(text="Kanade • Developed by Wxre Development")
        return await ctx.reply(embed=embed, mention_author=False)

    enabled = mode == "on"
    set_guild_setting(ctx.guild, "antinuke_enabled", enabled)

    embed = StyledEmbed(
        title=f"Kanade | Anti-Nuke {'Enabled' if enabled else 'Disabled'}",
        description=(
            f"{ANTI_NUKE_ON_EMOJI if enabled else ANTI_NUKE_OFF_EMOJI} "
            f"Anti-nuke protection has been **{'ENABLED' if enabled else 'DISABLED'}** "
            f"for **{ctx.guild.name}**."
        ),
        color=purple
    )
    embed.add_field(
        name="Status",
        value="Protection is active." if enabled else "Protection is inactive.",
        inline=False
    )
    embed.add_field(
        name="Changed by",
        value=ctx.author.mention,
        inline=True
    )
    embed.set_footer(text="Kanade • Developed by Wxre Development")
    await ctx.reply(embed=embed, mention_author=False)
# ==================== PER-SERVER PREFIX WHITELIST ====================
@bot.group(name="whitelist", invoke_without_command=True)
@commands.guild_only()
async def whitelist_command(ctx):
    await ctx.reply(
        "Usage: `!whitelist add @member`, "
        "`!whitelist remove @member`, or `!whitelist list`. "
        "For roles: `!whitelist roleadd @role`, `!whitelist roleremove @role`, or `!whitelist rolelist`",
        mention_author=False
    )
@whitelist_command.command(name="add")
@commands.guild_only()
async def whitelist_add(ctx, member: discord.Member):
    if not await manager_check(ctx):
        return
    guild_key = str(ctx.guild.id)
    guild_whitelists = config.setdefault("guild_whitelists", {})
    user_ids = guild_whitelists.setdefault(guild_key, [])
    if member.id in user_ids:
        return await ctx.reply(
            "User is already whitelisted.",
            mention_author=False
        )
    user_ids.append(member.id)
    save_config()
    await send_log(
        ctx.guild,
        "▸ Server Whitelist Added",
        f"{member.mention} (`{member.id}`) added by {ctx.author.mention} (`{ctx.author.id}`).",
    )
    await ctx.reply(
        "User added to whitelist.",
        mention_author=False
    )
@whitelist_command.command(name="remove")
@commands.guild_only()
async def whitelist_remove(ctx, member: discord.Member):
    if not await manager_check(ctx):
        return
    guild_key = str(ctx.guild.id)
    guild_whitelists = config.setdefault("guild_whitelists", {})
    user_ids = guild_whitelists.get(guild_key, [])
    if member.id not in user_ids:
        return await ctx.reply(
            "User is not whitelisted.",
            mention_author=False
        )
    user_ids.remove(member.id)
    if not user_ids:
        guild_whitelists.pop(guild_key, None)
    save_config()
    await send_log(
        ctx.guild,
        "▸ Server Whitelist Removed",
        f"{member.mention} (`{member.id}`) removed by {ctx.author.mention} (`{ctx.author.id}`).",
    )
    await ctx.reply(
        "User removed from whitelist.",
        mention_author=False
    )
@whitelist_command.command(name="list")
@commands.guild_only()
async def whitelist_list(ctx):
    if not await manager_check(ctx):
        return
    user_ids = config.get("guild_whitelists", {}).get(
        str(ctx.guild.id), []
    )
    embed = StyledEmbed(
        title="Kanade | Whitelist",
        description=f"Whitelisted members in **{ctx.guild.name}**",
        color=discord.Color.from_rgb(128, 0, 255)
    )
    if not user_ids:
        embed.add_field(
            name="Members",
            value="No users are whitelisted.",
            inline=False
        )
    else:
        lines = [
            f"`{index}.` <@{user_id}> (`{user_id}`)"
            for index, user_id in enumerate(user_ids, start=1)
        ]
        description = "\n".join(lines)
        if len(description) > 4000:
            description = description[:3900].rsplit("\n", 1)[0]
            description += "\n\nList shortened due to Discord's embed limit."
        embed.add_field(
            name="Members",
            value=description,
            inline=False
        )
    embed.add_field(
        name="Total",
        value=str(len(user_ids)),
        inline=False
    )
    embed.set_footer(text="Kanade")
    await ctx.reply(embed=embed, mention_author=False)
# ==================== ADDED SERVER TOOLS ====================
PURPLE = _EMBED_PURPLE
BACKUP_DIR = "backups"


def feature_embed(title, description, *, color=PURPLE):
    embed = StyledEmbed(title=title, description=description, color=PURPLE, timestamp=discord.utils.utcnow())
    return embed


def is_global_staff(user):
    staff_ids = {DEVELOPER_ID}
    staff_ids.update(int(x) for x in config.get("global_staff_ids", []) if str(x).isdigit())
    # Existing bot admins are already registered by the existing !adm system.
    staff_ids.update(int(x) for x in config.get("premium_admin_ids", []) if str(x).isdigit())
    return user.id in staff_ids or is_owner_id(user.id)


async def global_staff_check(ctx):
    if not is_global_staff(ctx.author):
        await ctx.reply(embed=feature_embed("Access Denied", "Staff only.", color=discord.Color.red()), mention_author=False)
        return False
    return True


def premium_server_id_set():
    # Reuse the existing Premium storage exactly; do not create a separate entitlement system.
    ids = set()
    for value in list(config.get("premium_guild_ids", [])) + list(config.get("premium_guilds", [])):
        if str(value).isdigit():
            ids.add(int(value))
    return ids


def backup_path(guild_id):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    return os.path.join(BACKUP_DIR, f"{guild_id}.json")


def backup_codes_dir():
    path = os.path.join(BACKUP_DIR, "codes")
    os.makedirs(path, exist_ok=True)
    return path


def backup_code_path(code):
    # Codes are normalized and validated before being used as filenames.
    normalized = re.sub(r"[^A-Z0-9]", "", str(code).upper())
    if not re.fullmatch(r"[A-Z0-9]{8}", normalized):
        raise ValueError("Invalid backup code. Codes are 8 letters/numbers.")
    return os.path.join(backup_codes_dir(), f"{normalized}.json")


def publish_backup_code(data):
    """Publish a portable backup under a short code usable in another server."""
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(20):
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        path = backup_code_path(code)
        if not os.path.exists(path):
            temporary = path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=2)
            os.replace(temporary, path)
            return code
    raise OSError("Could not generate a unique backup code. Try again.")


def serialize_overwrites(channel):
    entries = []
    for target, overwrite in channel.overwrites.items():
        target_type = "role" if isinstance(target, discord.Role) else "member"
        allow, deny = overwrite.pair()
        entries.append({"type": target_type, "id": target.id, "allow": allow.value, "deny": deny.value})
    return entries


def serialize_channel(channel):
    data = {
        "id": channel.id, "name": channel.name, "type": str(channel.type),
        "position": channel.position, "category_id": channel.category_id,
        "overwrites": serialize_overwrites(channel),
    }
    if isinstance(channel, discord.TextChannel):
        data.update(topic=channel.topic, nsfw=channel.nsfw, slowmode_delay=channel.slowmode_delay, default_auto_archive_duration=getattr(channel, "default_auto_archive_duration", 1440))
    if isinstance(channel, discord.VoiceChannel):
        data.update(bitrate=channel.bitrate, user_limit=channel.user_limit, rtc_region=str(channel.rtc_region) if channel.rtc_region else None)
    return data


async def create_backup(guild):
    data = {
        "format": 2, "guild_id": guild.id, "guild_name": guild.name,
        "everyone_role_id": guild.default_role.id,
        "created_at": discord.utils.utcnow().isoformat(),
        "roles": [
            {"id": role.id, "name": role.name, "permissions": role.permissions.value,
             "color": role.color.value, "hoist": role.hoist, "mentionable": role.mentionable,
             "position": role.position}
            for role in guild.roles if role != guild.default_role and not role.managed
        ],
        "channels": [serialize_channel(channel) for channel in guild.channels],
    }
    with open(backup_path(guild.id), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data


class RestoreReport:
    """Result of a backup restore. Unpacks as (roles_count, channels_count) for older callers."""
    def __init__(self):
        self.roles_created = 0
        self.roles_reused = 0
        self.channels_created = 0
        self.channels_deleted = 0
        self.skipped_roles = []      # roles Discord would not let the bot delete
        self.failed_roles = []       # saved roles that could not be recreated
        self.failed_deletes = []     # channels that could not be deleted
        self.failed_channels = []    # saved channels that could not be recreated
        self.notes = []

    def __iter__(self):
        return iter((self.roles_created + self.roles_reused + 1, self.channels_created))

    def summary(self):
        def names(items, limit=8):
            shown = ", ".join(f"`{i}`" for i in items[:limit])
            return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")
        lines = [
            f"Roles recreated: **{self.roles_created}**" + (f" (reused {self.roles_reused} existing)" if self.roles_reused else ""),
            f"Channels recreated: **{self.channels_created}**",
        ]
        if self.skipped_roles:
            lines.append(f"Skipped roles (could not delete): {names(self.skipped_roles)}. Move Kanade's highest role above them to replace them next time.")
        if self.failed_roles:
            lines.append(f"Roles that could not be recreated: {names(self.failed_roles)}")
        if self.failed_deletes:
            lines.append(f"Channels that could not be deleted: {names(self.failed_deletes)}")
        if self.failed_channels:
            lines.append(f"Channels that could not be restored: {names(self.failed_channels)}")
        lines.extend(self.notes)
        return "\n".join(lines)[:3800]


async def restore_backup(guild, code=None, *, replace_channels=True):
    """Restore a backup as completely as Discord permissions allow.

    Roles/channels Discord will not let the bot delete are skipped and reported;
    the restore never stops because of one of them.
    """
    if code:
        path = backup_code_path(code)
        if not os.path.exists(path):
            raise FileNotFoundError("That backup code was not found. Check the code and try again.")
    else:
        path = backup_path(guild.id)
        if not os.path.exists(path):
            raise FileNotFoundError("No backup found. Use `!backup create` or load a backup code.")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict) or not isinstance(data.get("channels"), list) or not isinstance(data.get("roles"), list):
        raise ValueError("The backup file is invalid or incomplete.")

    me = guild.me
    if me is None:
        raise RuntimeError("I could not verify my server permissions. Try again after the bot is ready.")
    if not me.guild_permissions.manage_channels:
        raise RuntimeError("Kanade needs the Manage Channels permission to restore channels.")

    report = RestoreReport()
    can_manage_roles = me.guild_permissions.manage_roles
    channel_items = data.get("channels", [])

    # ---- 1. Roles: delete what we can, remember what must stay ----
    role_map = {
        int(data.get("everyone_role_id", guild.default_role.id)): guild.default_role,
        guild.default_role.id: guild.default_role,
    }
    kept_roles = []  # existing roles that cannot be deleted (managed, above the bot, no permission)
    current_roles = [r for r in guild.roles if r != guild.default_role]
    for role in sorted(current_roles, key=lambda r: r.position, reverse=True):
        if role.managed:
            kept_roles.append(role)
            continue
        if not can_manage_roles or role >= me.top_role:
            kept_roles.append(role)
            report.skipped_roles.append(role.name)
            continue
        try:
            await role.delete(reason="Kanade backup restore: replacing server roles")
        except (discord.Forbidden, discord.HTTPException):
            kept_roles.append(role)
            report.skipped_roles.append(role.name)

    # ---- 2. Recreate saved roles (reuse same-named roles that had to stay) ----
    created_roles = []
    available_kept = list(kept_roles)
    saved_roles = sorted(data.get("roles", []), key=lambda x: int(x.get("position", 0)))
    for item in saved_roles:
        old_id = int(item.get("id", 0))
        name = str(item.get("name", "Restored Role"))[:100]
        existing = next((r for r in available_kept if r.name == name), None)
        if existing is not None:
            available_kept.remove(existing)
            if old_id:
                role_map[old_id] = existing
            report.roles_reused += 1
            continue
        if not can_manage_roles:
            report.failed_roles.append(name)
            continue
        try:
            role = await guild.create_role(
                name=name,
                permissions=discord.Permissions(int(item.get("permissions", 0))),
                colour=discord.Colour(int(item.get("color", 0))),
                hoist=bool(item.get("hoist", False)),
                mentionable=bool(item.get("mentionable", False)),
                reason="Kanade backup restore: recreating saved roles",
            )
        except (discord.Forbidden, discord.HTTPException, TypeError, ValueError):
            report.failed_roles.append(name)
            continue
        if old_id:
            role_map[old_id] = role
        created_roles.append((role, int(item.get("position", 1))))
        report.roles_created += 1

    # Restore the saved hierarchy, clamped below Kanade's top role. A failure here is only a note.
    if created_roles:
        max_position = max(1, me.top_role.position - 1)
        positions = {role: max(1, min(saved, max_position)) for role, saved in created_roles}
        try:
            await guild.edit_role_positions(positions=positions, reason="Kanade backup restore: restoring role hierarchy")
        except (discord.Forbidden, discord.HTTPException):
            report.notes.append("Roles were recreated, but their saved order could not be fully applied. Check Kanade's role position.")

    # ---- 3. Channels: delete what we can, continue past failures ----
    channel_map = {}
    if replace_channels:
        current_channels = list(guild.channels)
        current_channels.sort(key=lambda c: isinstance(c, discord.CategoryChannel))
        for channel in current_channels:
            if channel.name.casefold() == "moderators" or getattr(channel, "managed", False):
                continue
            try:
                await channel.delete(reason="Kanade backup restore: replacing server channels")
                report.channels_deleted += 1
            except (discord.Forbidden, discord.HTTPException):
                report.failed_deletes.append(channel.name)
    else:
        channel_map = {channel.id: channel for channel in guild.channels}

    # ---- 4. Recreate categories first, then their children ----
    ordered = sorted(
        channel_items,
        key=lambda x: (0 if x.get("type") == "category" else 1, int(x.get("position", 0))),
    )
    restored_channels = []
    for item in ordered:
        old_id = int(item.get("id", 0))
        ctype = item.get("type", "text")
        name = str(item.get("name", "restored-channel"))[:100]
        category = channel_map.get(int(item.get("category_id") or 0))
        if category is None and item.get("category_id"):
            old_cat = next((c for c in channel_items if int(c.get("id", 0)) == int(item["category_id"])), None)
            if old_cat:
                category = discord.utils.get(guild.categories, name=old_cat.get("name"))

        overwrites = {}
        for ow in item.get("overwrites", []):
            target_id = int(ow.get("id", 0))
            target = role_map.get(target_id) if ow.get("type") == "role" else guild.get_member(target_id)
            if target is None:
                continue
            allow = discord.Permissions(int(ow.get("allow", 0)))
            deny = discord.Permissions(int(ow.get("deny", 0)))
            overwrites[target] = discord.PermissionOverwrite.from_pair(allow, deny)

        reason = "Kanade backup restore: recreating saved channel layout"
        common = {"name": name, "category": category, "overwrites": overwrites, "reason": reason}
        try:
            protected_moderators = None
            if name.casefold() == "moderators" and ctype in ("text", "news"):
                protected_moderators = discord.utils.get(guild.text_channels, name=name)
            if protected_moderators is not None:
                channel = protected_moderators
                await channel.edit(
                    category=category,
                    topic=item.get("topic"),
                    nsfw=bool(item.get("nsfw", False)),
                    slowmode_delay=int(item.get("slowmode_delay", 0)),
                    overwrites=overwrites,
                    reason="Kanade backup restore: applying saved settings to protected moderators channel",
                )
            elif ctype == "category":
                channel = await guild.create_category(name, overwrites=overwrites, reason=reason)
            elif ctype == "voice":
                kwargs = {"bitrate": min(int(item.get("bitrate", 64000)), int(guild.bitrate_limit)),
                          "user_limit": int(item.get("user_limit", 0))}
                if item.get("rtc_region") and item.get("rtc_region") != "None":
                    kwargs["rtc_region"] = item["rtc_region"]
                channel = await guild.create_voice_channel(**common, **kwargs)
            elif ctype == "stage_voice":
                channel = await guild.create_stage_channel(**common, topic=item.get("topic") or "Stage")
            elif ctype == "forum":
                channel = await guild.create_forum(**common)
            else:
                text_kwargs = dict(topic=item.get("topic"), nsfw=bool(item.get("nsfw", False)),
                                   slowmode_delay=int(item.get("slowmode_delay", 0)))
                try:
                    channel = await guild.create_text_channel(**common, news=(ctype == "news"), **text_kwargs)
                except discord.HTTPException:
                    if ctype != "news":
                        raise
                    # Announcement channels need Community; fall back to a normal text channel.
                    channel = await guild.create_text_channel(**common, **text_kwargs)
            channel_map[old_id] = channel
            restored_channels.append((channel, int(item.get("position", 0)), ctype))
        except (discord.Forbidden, discord.HTTPException, TypeError, ValueError):
            report.failed_channels.append(f"{name} ({ctype})")
            logger.warning("Could not restore channel %s (%s) in guild %s", name, ctype, guild.id)
            continue
    report.channels_created = len(restored_channels)

    # ---- 5. Apply saved ordering; failures here are cosmetic ----
    for channel, saved_position, ctype in restored_channels:
        try:
            await channel.edit(position=saved_position, reason="Kanade backup restore: restoring channel order")
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("Could not apply position %s to restored channel %s", saved_position, channel.name)

    return report


@bot.group(name="backup", invoke_without_command=True)
async def backup_group(ctx):
    await ctx.reply(embed=feature_embed("Premium Backups", "`!backup create` — Save server setup\n`!backup load <code>` — Restore a transfer code\nPremium required."), mention_author=False)

@backup_group.command(name="create")
async def backup_create(ctx):
    if ctx.guild is None:
        return await ctx.reply(embed=feature_embed("Server Only", "Use this command in a server.", color=discord.Color.red()), mention_author=False)
    if not has_admin_access(ctx.author):
        return await ctx.reply(embed=feature_embed("Access Denied", "Administrator permission is required.", color=discord.Color.red()), mention_author=False)
    if not await require_premium(ctx):
        return
    try:
        data = await create_backup(ctx.guild)
        code = publish_backup_code(data)
        await ctx.reply(embed=feature_embed(
            "Backup Created",
            f"Saved **{len(data['roles'])} roles** and **{len(data['channels'])} channels**.\n"
            f"**Transfer code:** `{code}`\n"
            f"Load it in any server with `!backup load {code}`."
        ), mention_author=False)
    except (OSError, discord.HTTPException) as exc:
        logger.exception("Backup creation failed for guild %s", ctx.guild.id)
        await ctx.reply(embed=feature_embed("Backup Failed", "Could not create backup. Check permissions and try again.", color=discord.Color.red()), mention_author=False)

@backup_group.command(name="load")
async def backup_load(ctx, code: str = None):
    if ctx.guild is None:
        return await ctx.reply(embed=feature_embed("Server Only", "Use this command in a server.", color=discord.Color.red()), mention_author=False)
    if not has_admin_access(ctx.author):
        return await ctx.reply(embed=feature_embed("Access Denied", "Administrator permission is required.", color=discord.Color.red()), mention_author=False)
    if not await require_premium(ctx):
        return
    # Explicit confirmation is required because this replaces channels and removable roles.
    prompt = await ctx.reply(
        embed=feature_embed(
            "Confirm Channel Replacement",
            "This will **delete existing channels and removable roles**, then rebuild the saved channels, role permissions, and role hierarchy. `@everyone`, Discord-managed roles, roles above Kanade's highest role (skipped), and the built-in `#moderators` channel are preserved.\n"
            "React with ✅ within 30 seconds to continue, or ❌ to cancel.",
            color=discord.Color.orange(),
        ),
        mention_author=False,
    )
    for emoji in ("✅", "❌"):
        try:
            await prompt.add_reaction(emoji)
        except discord.HTTPException:
            await ctx.reply(embed=feature_embed("Confirmation Unavailable", "I cannot add confirmation reactions here, so the restore was cancelled safely.", color=discord.Color.red()), mention_author=False)
            return
    def confirm_check(reaction, user):
        return user.id == ctx.author.id and reaction.message.id == prompt.id and str(reaction.emoji) in ("✅", "❌")
    try:
        reaction, _user = await bot.wait_for("reaction_add", timeout=30.0, check=confirm_check)
    except asyncio.TimeoutError:
        try:
            await prompt.edit(embed=feature_embed("Restore Cancelled", "No confirmation received within 30 seconds. Nothing was changed.", color=discord.Color.orange()))
        except discord.HTTPException:
            pass
        return
    if str(reaction.emoji) != "✅":
        try:
            await prompt.edit(embed=feature_embed("Restore Cancelled", "You cancelled the restore. Nothing was changed.", color=discord.Color.orange()))
        except discord.HTTPException:
            pass
        return
    try:
        restore_report = await restore_backup(ctx.guild, code=code, replace_channels=True)
        report = feature_embed("Backup Restored", restore_report.summary() + "\nExisting channels/categories were replaced.", color=discord.Color.green())
        # The command's original channel may have been deleted during the restore.
        target = ctx.guild.system_channel
        if target is None or not target.permissions_for(ctx.guild.me).send_messages:
            target = next((c for c in ctx.guild.text_channels if c.permissions_for(ctx.guild.me).send_messages), None)
        if target is not None:
            await target.send(embed=report)
    except FileNotFoundError:
        await ctx.reply(embed=feature_embed("No Backup Found", "Check the code or run `!backup create` first.", color=discord.Color.orange()), mention_author=False)
    except (ValueError, OSError, RuntimeError, json.JSONDecodeError, discord.HTTPException) as exc:
        # The original channel may already have been deleted; send the error wherever possible.
        error_embed = feature_embed("Restore Failed", str(exc)[:1000] or "Check permissions and try again.", color=discord.Color.red())
        target = ctx.guild.system_channel
        if target is None or not target.permissions_for(ctx.guild.me).send_messages:
            target = next((c for c in ctx.guild.text_channels if c.permissions_for(ctx.guild.me).send_messages), None)
        if target is not None:
            await target.send(embed=error_embed)


async def setup_guild_log_channels(guild):
    """Create/reuse Kanade logs category and purpose-specific text channels."""
    me = guild.me
    if me is None:
        return None, {}, ["Kanade could not resolve its server member object."]
    category = discord.utils.get(guild.categories, name="Kanade logs")
    created = []
    if category is None:
        # Keep security logs private by default; admins bypass overwrites, and
        # moderators with log-management permissions are explicitly granted access.
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
        overwrites[me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, read_message_history=True, manage_channels=True)
        for role in guild.roles:
            if role != guild.default_role and (role.permissions.administrator or role.permissions.manage_guild or role.permissions.manage_messages):
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        category = await guild.create_category("Kanade logs", overwrites=overwrites, reason="Set up Kanade security log channels")
        created.append("Kanade logs category")
    mapping = {}
    for key, (channel_name, display_name, topic) in LOG_CHANNEL_DEFINITIONS.items():
        channel = discord.utils.get(category.text_channels, name=channel_name)
        if channel is None:
            channel = await guild.create_text_channel(
                channel_name,
                category=category,
                topic=topic,
                reason="Set up Kanade security log channels",
            )
            created.append(display_name)
        mapping[key] = channel.id
    config.setdefault("log_channel_groups", {})[str(guild.id)] = mapping
    # Clear any legacy destination so it cannot override the purpose-specific channels.
    config.setdefault("log_channels", {}).pop(str(guild.id), None)
    save_config()
    return category, mapping, created

@bot.command(name="setuplogs")
async def setuplogs_command(ctx):
    if not await manager_check(ctx):
        return
    try:
        category, mapping, created = await setup_guild_log_channels(ctx.guild)
    except discord.Forbidden:
        return await ctx.reply(embed=feature_embed("⚒ Error", "Kanade needs **Manage Channels**, **View Channels**, **Send Messages**, and **Embed Links** permissions to set up logs."), mention_author=False)
    except discord.HTTPException as exc:
        return await ctx.reply(embed=feature_embed("⚒ Error", f"Could not finish creating the log channels: `{str(exc)[:700]}`"), mention_author=False)
    channel_lines = "\n".join(f"• <#{channel_id}>" for channel_id in mapping.values())
    status = f"Created {len(created)} item(s)." if created else "The category and channels already existed; they were reused."
    await ctx.reply(embed=feature_embed("Kanade Logs Configured", f"Category: {category.mention}\n{status}\n\n{channel_lines}\n\nEach event will now be sent to its matching log channel."), mention_author=False)

@bot.command(name="disablelogs")
async def disablelogs_command(ctx):
    if not await manager_check(ctx):
        return
    config.setdefault("log_channel_groups", {}).pop(str(ctx.guild.id), None)
    config.setdefault("log_channels", {}).pop(str(ctx.guild.id), None)
    save_config()
    await ctx.reply(embed=feature_embed("Security Logs Disabled", "Automatic security logs are now disabled for this server. Existing log channels were left in place."), mention_author=False)


async def apply_manual_lock(guild, actor):
    states = config.setdefault("lock_states", {})
    key = str(guild.id)
    saved = states.setdefault(key, {})
    changed = 0
    for channel in guild.channels:
        if not isinstance(channel, (discord.TextChannel, discord.ForumChannel, discord.VoiceChannel, discord.StageChannel)):
            continue
        overwrites = channel.overwrites_for(guild.default_role)
        if channel.id not in saved:
            saved[str(channel.id)] = {"send_messages": overwrites.send_messages, "add_reactions": overwrites.add_reactions, "connect": overwrites.connect, "speak": overwrites.speak, "view_channel": overwrites.view_channel}
        try:
            if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                overwrites.send_messages = False
                if isinstance(channel, discord.TextChannel):
                    overwrites.add_reactions = False
            elif isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                overwrites.connect = False
            await channel.set_permissions(guild.default_role, overwrite=overwrites, reason=f"Manual lock by {actor}")
            changed += 1
        except (discord.Forbidden, discord.HTTPException):
            continue
    save_config()
    return changed

async def apply_manual_lock_channel(guild, channel, actor):
    """Lock only the specified channel and save its prior default-role overwrites."""
    if not isinstance(channel, (discord.TextChannel, discord.ForumChannel, discord.VoiceChannel, discord.StageChannel)):
        return False, "Cannot lock this channel type."
    states = config.setdefault("lock_states", {}).setdefault(str(guild.id), {})
    channel_key = str(channel.id)
    overwrite = channel.overwrites_for(guild.default_role)
    if channel_key not in states:
        states[channel_key] = {
            "send_messages": overwrite.send_messages,
            "add_reactions": overwrite.add_reactions,
            "connect": overwrite.connect,
            "speak": overwrite.speak,
            "view_channel": overwrite.view_channel,
        }
    if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
        overwrite.send_messages = False
        if isinstance(channel, discord.TextChannel):
            overwrite.add_reactions = False
    elif isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
        overwrite.connect = False
    try:
        await channel.set_permissions(guild.default_role, overwrite=overwrite, reason=f"Channel lock by {actor}")
        save_config()
        return True, "Channel locked."
    except (discord.Forbidden, discord.HTTPException) as exc:
        return False, "Could not lock this channel. Check my permissions."

async def restore_manual_lock_channel(guild, channel, actor):
    """Restore only the specified channel's saved default-role overwrites."""
    guild_states = config.setdefault("lock_states", {}).get(str(guild.id), {})
    saved = guild_states.get(str(channel.id))
    if not saved:
        return False, "This channel is not locked by the bot."
    overwrite = channel.overwrites_for(guild.default_role)
    for key, value in saved.items():
        setattr(overwrite, key, value)
    try:
        await channel.set_permissions(guild.default_role, overwrite=overwrite, reason=f"Channel unlock by {actor}")
        guild_states.pop(str(channel.id), None)
        if not guild_states:
            config.setdefault("lock_states", {}).pop(str(guild.id), None)
        save_config()
        return True, "Channel unlocked."
    except (discord.Forbidden, discord.HTTPException) as exc:
        return False, "Could not unlock this channel. Check my permissions."

async def restore_manual_lock(guild, actor):
    states = config.setdefault("lock_states", {}).get(str(guild.id), {})
    changed = 0
    for channel_id, saved in list(states.items()):
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            continue
        overwrite = channel.overwrites_for(guild.default_role)
        for key, value in saved.items():
            setattr(overwrite, key, value)
        try:
            await channel.set_permissions(guild.default_role, overwrite=overwrite, reason=f"Manual unlock by {actor}")
            changed += 1
            states.pop(channel_id, None)
        except (discord.Forbidden, discord.HTTPException):
            continue
    if not states:
        config.setdefault("lock_states", {}).pop(str(guild.id), None)
    save_config()
    return changed

@bot.command(name="lock")
async def lock_command(ctx):
    if not await manager_check(ctx):
        return
    success, message = await apply_manual_lock_channel(ctx.guild, ctx.channel, ctx.author)
    await ctx.reply(embed=feature_embed("Channel Locked" if success else "Lock Failed", message, color=discord.Color.green() if success else discord.Color.red()), mention_author=False)

@bot.group(name="unlock", invoke_without_command=True)
async def unlock_command(ctx):
    if not await manager_check(ctx):
        return
    success, message = await restore_manual_lock_channel(ctx.guild, ctx.channel, ctx.author)
    await ctx.reply(embed=feature_embed("Channel Unlocked" if success else "Unlock Failed", message, color=discord.Color.green() if success else discord.Color.orange()), mention_author=False)

@bot.command(name="lockdown")
async def lockdown_command(ctx):
    if not await manager_check(ctx):
        return
    changed = await apply_manual_lock(ctx.guild, ctx.author)
    await ctx.reply(embed=feature_embed("Server Lockdown", f"Locked **{changed} channels**."), mention_author=False)

@bot.command(name="unlockdown")
async def unlockdown_command(ctx):
    if not await manager_check(ctx):
        return
    task = lockdown_tasks.get(ctx.guild.id)
    if task and not task.done():
        task.cancel()
    changed = await restore_manual_lock(ctx.guild, ctx.author)
    await ctx.reply(embed=feature_embed("Server Lockdown Removed", f"Unlocked **{changed} channels**."), mention_author=False)

@bot.command(name="joinvc", aliases=["joinvoice"])
async def join_voice_command(ctx):
    if not await manager_check(ctx):
        return
    if not isinstance(ctx.author, discord.Member) or not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.reply(embed=feature_embed("Voice Connection", "Join a voice channel first.", color=discord.Color.orange()), mention_author=False)
    channel = ctx.author.voice.channel
    try:
        if ctx.guild.voice_client and ctx.guild.voice_client.is_connected():
            await ctx.guild.voice_client.move_to(channel)
        else:
            await channel.connect()
        await ctx.reply(embed=feature_embed("Voice Connected", f"Joined {channel.mention}."), mention_author=False)
    except (discord.Forbidden, discord.HTTPException, discord.ClientException) as exc:
        await ctx.reply(embed=feature_embed("Voice Connection Failed", "Could not join. Check voice permissions and dependencies.", color=discord.Color.red()), mention_author=False)

@bot.event
async def on_message_delete(message):
    if message.guild is None or message.author.bot:
        return
    content = (message.content or "[No text content]").strip()
    await send_log(
        message.guild,
        f"{MESSAGE_DELETE_EMOJI} Message Deleted",
        f"**User:** {message.author.mention} (`{message.author.id}`)\n"
        f"**Channel:** {message.channel.mention}\n**Content:**\n{content[:2500]}",
        discord.Color.red()
    )
    recent_deleted_messages[(message.guild.id, message.channel.id)] = {
        "author_id": message.author.id, "author_name": str(message.author),
        "content": message.content or "[No text content]", "created_at": message.created_at.isoformat(),
        "attachments": [a.url for a in message.attachments],
    }

@bot.command(name="snipe")
async def snipe_command(ctx):
    item = recent_deleted_messages.get((ctx.guild.id, ctx.channel.id)) if ctx.guild else None
    if not item:
        return await ctx.reply(embed=feature_embed("Snipe", "No deleted message found.", color=discord.Color.orange()), mention_author=False)
    embed = feature_embed("Recently Deleted Message", f"**Author:** {item['author_name']} (`{item['author_id']}`)\n**Content:**\n{item['content'][:2500]}")
    if item.get("attachments"):
        embed.add_field(name="Attachments", value="\n".join(item["attachments"])[:1000], inline=False)
    embed.set_footer(text="Kanade")
    await ctx.reply(embed=embed, mention_author=False)

@bot.command(name="nplaying")
async def no_prefix_guild_command(ctx, user: discord.User = None):
    if not is_bot_admin_id(ctx.author.id):
        return await ctx.reply(embed=feature_embed("Access Denied", "Only the bot owner and authorized bot admins can use this command.", color=discord.Color.red()), mention_author=False)
    if ctx.guild is None or user is None:
        return await ctx.reply(embed=feature_embed("Usage", "Usage: `!nplaying @user`"), mention_author=False)
    mapping = config.setdefault("guild_no_prefix", {})
    ids = set(int(x) for x in mapping.get(str(ctx.guild.id), []) if str(x).isdigit())
    ids.add(user.id)
    mapping[str(ctx.guild.id)] = sorted(ids)
    save_config()
    await ctx.reply(embed=feature_embed("No-Prefix Enabled", f"{user.mention} can now use commands without a prefix in this server."), mention_author=False)

@bot.command(name="npremove")
async def no_prefix_guild_remove_command(ctx, user: discord.User = None):
    if not is_bot_admin_id(ctx.author.id):
        return await ctx.reply(embed=feature_embed("Access Denied", "Only the bot owner and authorized bot admins can use this command.", color=discord.Color.red()), mention_author=False)
    if ctx.guild is None or user is None: return await ctx.reply(embed=feature_embed("Usage", "`!npremove @user`"), mention_author=False)
    mapping = config.setdefault("guild_no_prefix", {})
    ids = set(int(x) for x in mapping.get(str(ctx.guild.id), []) if str(x).isdigit()); ids.discard(user.id)
    if ids: mapping[str(ctx.guild.id)] = sorted(ids)
    else: mapping.pop(str(ctx.guild.id), None)
    save_config()
    await ctx.reply(embed=feature_embed("No-Prefix Removed", f"{user.mention} must use a prefix in this server."), mention_author=False)

@bot.command(name="gnp")
async def no_prefix_global_command(ctx, user: discord.User = None):
    if not is_bot_admin_id(ctx.author.id):
        return await ctx.reply(embed=feature_embed("Access Denied", "Only the bot owner and authorized bot admins can use this command.", color=discord.Color.red()), mention_author=False)
    if user is None: return await ctx.reply(embed=feature_embed("Usage", "Usage: `!gnp @user`"), mention_author=False)
    ids = set(int(x) for x in config.get("global_no_prefix", []) if str(x).isdigit()); ids.add(user.id)
    config["global_no_prefix"] = sorted(ids); save_config()
    await ctx.reply(embed=feature_embed("Global No-Prefix Enabled", f"{user.mention} can now use commands without a prefix in every server."), mention_author=False)

@bot.command(name="gnpremove")
async def no_prefix_global_remove_command(ctx, user: discord.User = None):
    if not is_bot_admin_id(ctx.author.id):
        return await ctx.reply(embed=feature_embed("Access Denied", "Only the bot owner and authorized bot admins can use this command.", color=discord.Color.red()), mention_author=False)
    if user is None: return await ctx.reply(embed=feature_embed("Usage", "`!gnpremove @user`"), mention_author=False)
    ids = set(int(x) for x in config.get("global_no_prefix", []) if str(x).isdigit()); ids.discard(user.id)
    config["global_no_prefix"] = sorted(ids); save_config()
    await ctx.reply(embed=feature_embed("Global No-Prefix Removed", f"{user.mention} must use a prefix in every server."), mention_author=False)

@bot.command(name="generatelink")
async def generate_server_invite(ctx, server_id: str = None):
    if not await global_staff_check(ctx): return
    if not server_id or not server_id.isdigit(): return await ctx.reply(embed=feature_embed("Usage", "`!generatelink <serverid>`"), mention_author=False)
    guild = bot.get_guild(int(server_id))
    if guild is None: return await ctx.reply(embed=feature_embed("Server Not Found", "Server not found.", color=discord.Color.red()), mention_author=False)
    for channel in guild.text_channels:
        try:
            perms = channel.permissions_for(guild.me)
            if perms.create_instant_invite and perms.view_channel:
                invite = await channel.create_invite(max_age=86400, max_uses=0, unique=True, reason=f"Invite generated by bot staff {ctx.author}")
                return await ctx.reply(embed=feature_embed("Server Invite Generated", f"**{guild.name}**\n{invite.url}\nExpires in 24 hours."), mention_author=False)
        except (discord.Forbidden, discord.HTTPException):
            continue
    await ctx.reply(embed=feature_embed("Invite Failed", "Could not create an invite. Check bot permissions.", color=discord.Color.red()), mention_author=False)

@bot.command(name="serverlists")
async def server_lists_command(ctx):
    if not await global_staff_check(ctx): return
    lines = [f"• **{g.name}** — `{g.id}` — {g.member_count or 0} members" for g in sorted(bot.guilds, key=lambda x: x.name.lower())]
    description = "\n".join(lines) if lines else "The bot is not in any servers."
    if len(description) > 4000: description = description[:3900] + "\n… list truncated"
    await ctx.reply(embed=feature_embed("Connected Servers", description), mention_author=False)

@bot.tree.command(name="adminlist", description="List registered global bot staff")
async def slash_adminlist(interaction: discord.Interaction):
    if not is_global_staff(interaction.user):
        return await interaction.response.send_message(
            "This command is restricted to authorized bot staff.",
            ephemeral=True,
        )
    ids = sorted({DEVELOPER_ID} | {int(x) for x in config.get("global_staff_ids", []) if str(x).isdigit()} | {int(x) for x in config.get("premium_admin_ids", []) if str(x).isdigit()})
    lines = [f"• <@{uid}> (`{uid}`){' — Developer' if uid == DEVELOPER_ID else ' — Registered staff'}" for uid in ids]
    await interaction.response.send_message(embed=feature_embed("Global Bot Staff", "\n".join(lines)), ephemeral=True)

# ==================== SLASH COMMANDS ====================
antinuke_group = app_commands.Group(
    name="antinuke",
    description="Anti-nuke controls"
)
@antinuke_group.command(
    name="status",
    description="Show anti-nuke status"
)
async def slash_antinuke_status(interaction: discord.Interaction):
    embed = StyledEmbed(
        title="Kanade Status",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="Anti-nuke",
        value="ON" if get_guild_setting(interaction.guild, "antinuke_enabled", config["antinuke_enabled"]) else "OFF"
    )
    embed.add_field(
        name="Raid protection",
        value="ON" if config["raid_protection"] else "OFF"
    )
    embed.add_field(name="Servers", value=str(len(bot.guilds)))
    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )
@antinuke_group.command(
    name="on",
    description="Enable anti-nuke"
)
async def slash_antinuke_on(interaction: discord.Interaction):
    if (
        not interaction.guild
        or not has_admin_access(interaction.user)
    ):
        return await interaction.response.send_message(
            "Administrator access required.",
            ephemeral=True
        )
    set_guild_setting(interaction.guild, "antinuke_enabled", True)
    await interaction.response.send_message(
        "Anti-nuke enabled.",
        ephemeral=True
    )
@antinuke_group.command(
    name="off",
    description="Disable anti-nuke"
)
async def slash_antinuke_off(interaction: discord.Interaction):
    if (
        not interaction.guild
        or not has_admin_access(interaction.user)
    ):
        return await interaction.response.send_message(
            "Administrator access required.",
            ephemeral=True
        )
    set_guild_setting(interaction.guild, "antinuke_enabled", False)
    await interaction.response.send_message(
        "Anti-nuke disabled.",
        ephemeral=True
    )
bot.tree.add_command(antinuke_group)
@bot.tree.command(
    name="status",
    description="Show Kanade status"
)
async def slash_status(interaction: discord.Interaction):
    guild = interaction.guild
    antinuke_state = get_guild_setting(guild, "antinuke_enabled", config.get("antinuke_enabled", True)) if guild else config.get("antinuke_enabled", True)
    await interaction.response.send_message(
        f"**Kanade online**\n"
        f"Servers protected: `{len(bot.guilds)}`\n"
        f"Anti-nuke: `{'ON' if antinuke_state else 'OFF'}`",
        ephemeral=True
    )













@bot.tree.command(name="roleall", description="Assign a role to eligible members")
@app_commands.check(app_bot_staff_or_server_admin)
@app_commands.describe(role="Role to assign to members")
async def slash_roleall(interaction: discord.Interaction, role: discord.Role):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(embed=feature_embed("Server Only", "Use this command in a server.", color=discord.Color.red()), ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    result = await bulk_role_operation(guild, interaction.user, role, remove=False)
    await interaction.followup.send(embed=result, ephemeral=True)

@bot.tree.command(name="unroleall", description="Remove a role from eligible members")
@app_commands.check(app_bot_staff_or_server_admin)
@app_commands.describe(role="Role to remove from members")
async def slash_unroleall(interaction: discord.Interaction, role: discord.Role):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(embed=feature_embed("Server Only", "Use this command in a server.", color=discord.Color.red()), ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    result = await bulk_role_operation(guild, interaction.user, role, remove=True)
    await interaction.followup.send(embed=result, ephemeral=True)

@bot.tree.command(
    name="lockdown",
    description="Temporarily lock text channels"
)
@app_commands.check(app_bot_staff_or_server_admin)
async def slash_lockdown(interaction: discord.Interaction):
    if interaction.guild is None:
        return await interaction.response.send_message(
            "Server-only command.",
            ephemeral=True
        )
    await interaction.response.defer(ephemeral=True, thinking=True)
    changed = await apply_manual_lock(interaction.guild, interaction.user)
    await interaction.followup.send(
        embed=feature_embed("Server Lockdown", f"Locked **{changed} channels**."),
        ephemeral=True
    )
@bot.tree.command(name="unlockdown", description="Restore permissions saved by server lockdown")
@app_commands.check(app_bot_staff_or_server_admin)
async def slash_unlockdown(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(embed=feature_embed("Server Only", "Use this command in a server.", color=discord.Color.red()), ephemeral=True)
    task = lockdown_tasks.get(guild.id)
    if task and not task.done():
        task.cancel()
    changed = await restore_manual_lock(guild, interaction.user)
    await interaction.response.send_message(
        embed=feature_embed("Server Lockdown Removed", f"Unlocked **{changed} channels**."),
        ephemeral=True
    )

@bot.tree.command(name="lock", description="Lock server channels and save previous permissions")
async def slash_lock(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(embed=feature_embed("Server Only", "Use this command in a server.", color=discord.Color.red()), ephemeral=True)
    if not has_admin_access(interaction.user):
        return await interaction.response.send_message(embed=feature_embed("Access Denied", "Administrator permission is required.", color=discord.Color.red()), ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    success, message = await apply_manual_lock_channel(guild, interaction.channel, interaction.user)
    await interaction.followup.send(embed=feature_embed("Channel Locked" if success else "Lock Failed", message, color=discord.Color.green() if success else discord.Color.red()), ephemeral=True)

@bot.tree.command(
    name="unlock",
    description="Unlock text channels"
)
@app_commands.check(app_bot_staff_or_server_admin)
async def slash_unlock(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(
            "Server-only command.",
            ephemeral=True
        )
    success, message = await restore_manual_lock_channel(guild, interaction.channel, interaction.user)
    await interaction.response.send_message(
        embed=feature_embed("Channel Unlocked" if success else "Unlock Failed", message, color=discord.Color.green() if success else discord.Color.orange()),
        ephemeral=True
    )
# ==================== PREMIUM BACKUP / OPT-IN LOG SLASH COMMANDS ====================
backup_slash_group = app_commands.Group(name="backup", description="Premium server backups")

@backup_slash_group.command(name="create", description="Create a Premium backup of this server")
async def slash_backup_create(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(embed=feature_embed("Server Only", "Use this command in a server.", color=discord.Color.red()), ephemeral=True)
    if not (interaction.user.id in config.get("premium_admin_ids", []) or is_owner_id(interaction.user.id) or is_premium_guild(guild)):
        return await interaction.response.send_message(embed=feature_embed("Premium Required", "This command is available only in Premium-enabled servers."), ephemeral=True)
    if not has_admin_access(interaction.user):
        return await interaction.response.send_message(embed=feature_embed("Access Denied", "Administrator permission is required.", color=discord.Color.red()), ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        data = await create_backup(guild)
        code = publish_backup_code(data)
        await interaction.followup.send(embed=feature_embed(
            "Backup Created",
            f"Saved **{len(data['roles'])} roles** and **{len(data['channels'])} channels**.\n"
            f"**Transfer code:** `{code}`\nUse `/backup load` with this code in the destination server."
        ), ephemeral=True)
    except (OSError, discord.HTTPException) as exc:
        await interaction.followup.send(embed=feature_embed("Backup Failed", str(exc)[:3000], color=discord.Color.red()), ephemeral=True)

@backup_slash_group.command(name="load", description="Restore a saved backup, optionally using a transfer code")
@app_commands.describe(
    code="8-character backup transfer code; leave blank for this server's local backup",
    confirm="Set to True to confirm replacing channels and removable roles",
)
async def slash_backup_load(interaction: discord.Interaction, code: str = None, confirm: bool = False):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(embed=feature_embed("Server Only", "Use this command in a server.", color=discord.Color.red()), ephemeral=True)
    if not (interaction.user.id in config.get("premium_admin_ids", []) or is_owner_id(interaction.user.id) or is_premium_guild(guild)):
        return await interaction.response.send_message(embed=feature_embed("Premium Required", "This command is available only in Premium-enabled servers."), ephemeral=True)
    if not has_admin_access(interaction.user):
        return await interaction.response.send_message(embed=feature_embed("Access Denied", "Administrator permission is required.", color=discord.Color.red()), ephemeral=True)
    if not confirm:
        return await interaction.response.send_message(
            embed=feature_embed(
                "Confirmation Required",
                "This restore will **delete existing channels and removable roles**, then rebuild the saved channels, role permissions, and role hierarchy. `@everyone`, Discord-managed roles, roles above Kanade's highest role (skipped), and the built-in `#moderators` channel are preserved.\n"
                "Run `/backup load` again with the same code and set **confirm** to **True** to proceed.",
                color=discord.Color.orange(),
            ), ephemeral=True
        )
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        restore_report = await restore_backup(guild, code=code, replace_channels=True)
        await interaction.followup.send(embed=feature_embed("Backup Restored", restore_report.summary() + "\nExisting channels/categories were replaced."), ephemeral=True)
    except FileNotFoundError:
        await interaction.followup.send(embed=feature_embed("No Backup Found", "Check the code or create a backup first.", color=discord.Color.orange()), ephemeral=True)
    except (ValueError, OSError, RuntimeError, json.JSONDecodeError, discord.HTTPException) as exc:
        await interaction.followup.send(embed=feature_embed("Restore Failed", str(exc)[:3000], color=discord.Color.red()), ephemeral=True)

bot.tree.add_command(backup_slash_group)

@bot.tree.command(name="setuplogs", description="Create Kanade's category and separate security log channels")
async def slash_setuplogs(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(embed=feature_embed("Server Only", "Use this command in a server.", color=discord.Color.red()), ephemeral=True)
    if not has_admin_access(interaction.user):
        return await interaction.response.send_message(embed=feature_embed("Access Denied", "Administrator permission is required.", color=discord.Color.red()), ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        category, mapping, created = await setup_guild_log_channels(guild)
        lines = "\n".join(f"• <#{channel_id}>" for channel_id in mapping.values())
        status = f"Created {len(created)} item(s)." if created else "Existing category and channels were reused."
        await interaction.followup.send(embed=feature_embed("Kanade Logs Configured", f"Category: {category.mention}\n{status}\n\n{lines}\n\nEach event will be routed to its matching channel."), ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(embed=feature_embed("⚒ Error", "Kanade needs **Manage Channels**, **View Channels**, **Send Messages**, and **Embed Links** permissions."), ephemeral=True)
    except discord.HTTPException as exc:
        await interaction.followup.send(embed=feature_embed("⚒ Error", f"Could not finish creating the log channels: `{str(exc)[:700]}`"), ephemeral=True)

@bot.tree.command(name="disablelogs", description="Disable automatic security logs for this server")
async def slash_disablelogs(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(embed=feature_embed("Server Only", "Use this command in a server.", color=discord.Color.red()), ephemeral=True)
    if not has_admin_access(interaction.user):
        return await interaction.response.send_message(embed=feature_embed("Access Denied", "Administrator permission is required.", color=discord.Color.red()), ephemeral=True)
    config.setdefault("log_channel_groups", {}).pop(str(guild.id), None)
    config.setdefault("log_channels", {}).pop(str(guild.id), None)
    save_config()
    await interaction.response.send_message(embed=feature_embed("Security Logs Disabled", "Automatic security logs are now disabled for this server. Existing log channels were left in place."), ephemeral=True)

# ==================== PER-SERVER SLASH WHITELIST ====================
@bot.tree.command(
    name="whitelist",
    description="Manage this server's whitelist"
)
@app_commands.describe(
    action="Choose add, remove, or list",
    user="The user to add or remove"
)
@app_commands.choices(action=[
    app_commands.Choice(name="add", value="add"),
    app_commands.Choice(name="remove", value="remove"),
    app_commands.Choice(name="list", value="list")
])
async def slash_whitelist(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    user: discord.User = None
):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True
        )
    if not has_admin_access(interaction.user):
        return await interaction.response.send_message(
            "Administrator access required.",
            ephemeral=True
        )
    guild_key = str(guild.id)
    guild_whitelists = config.setdefault("guild_whitelists", {})
    user_ids = guild_whitelists.get(guild_key, [])
    # LIST
    if action.value == "list":
        embed = StyledEmbed(
            title="Kanade | Whitelist",
            description=f"Whitelisted members in **{guild.name}**",
            color=discord.Color.from_rgb(128, 0, 255)
        )
        if not user_ids:
            embed.add_field(
                name="Members",
                value="No users are whitelisted.",
                inline=False
            )
        else:
            lines = [
                f"`{index}.` <@{user_id}> (`{user_id}`)"
                for index, user_id in enumerate(user_ids, start=1)
            ]
            description = "\n".join(lines)
            if len(description) > 4000:
                description = description[:3900].rsplit("\n", 1)[0]
                description += "\n\nList shortened due to Discord's embed limit."
            embed.add_field(
                name="Members",
                value=description,
                inline=False
            )
        embed.add_field(
            name="Total",
            value=str(len(user_ids)),
            inline=False
        )
        embed.set_footer(text="Kanade")
        return await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )
    if user is None:
        return await interaction.response.send_message(
            "Select a user for the add or remove action.",
            ephemeral=True
        )
    # ADD
    if action.value == "add":
        if user.id in user_ids:
            return await interaction.response.send_message(
                "User is already whitelisted.",
                ephemeral=True
            )
        user_ids.append(user.id)
        guild_whitelists[guild_key] = user_ids
        save_config()
        await send_log(
            guild,
            "▸ Server Whitelist Added",
            f"{user.mention} (`{user.id}`) added by {interaction.user.mention} (`{interaction.user.id}`).",
        )
        return await interaction.response.send_message(
            "User added to whitelist.",
            ephemeral=True
        )
    # REMOVE
    if user.id not in user_ids:
        return await interaction.response.send_message(
            "User is not whitelisted.",
            ephemeral=True
        )
    user_ids.remove(user.id)
    if user_ids:
        guild_whitelists[guild_key] = user_ids
    else:
        guild_whitelists.pop(guild_key, None)
    save_config()
    await send_log(
        guild,
        "▸ Server Whitelist Removed",
        f"{user.mention} (`{user.id}`) removed by {interaction.user.mention} (`{interaction.user.id}`).",
    )
    await interaction.response.send_message(
        "User removed from whitelist.",
        ephemeral=True
    )
# ==================== EXTENDED SLASH COMMAND COVERAGE ====================
# Keep the prefix interface for compatibility while exposing the main remaining
# feature controls as native Discord application commands.
@bot.tree.command(name="ping", description="Check Kanade's gateway latency.")
async def slash_ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    embed = StyledEmbed(
        description=f"{LATENCY_EMOJI} **Latency is {latency}ms!**",
        color=discord.Color.dark_purple()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="rank", description="View a member's XP and server rank")
@app_commands.describe(member="Member to inspect; defaults to you")
async def slash_rank(interaction: discord.Interaction, member: discord.Member = None):
    if interaction.guild is None:
        return await interaction.response.send_message("Use this command in a server.", ephemeral=True)
    member = member or interaction.user
    data = config.get("xp_data", {}).get(str(interaction.guild.id), {})
    record = data.get(str(member.id), {"xp": 0, "messages": 0})
    total = int(record.get("xp", 0)); level, progress, needed = _xp_level(total)
    ranked = sorted(data.items(), key=lambda item: int(item[1].get("xp", 0)), reverse=True)
    position = next((i for i, (uid, _) in enumerate(ranked, 1) if uid == str(member.id)), None)
    embed = feature_embed(f"{member.display_name} • Rank", f"**Level:** {level}\n**XP:** {total:,}\n**Progress:** {progress:,} / {needed:,}\n**Messages counted:** {int(record.get('messages', 0)):,}\n**Server rank:** #{position or '—'}")
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leaderboard", description="Show the server's top XP members")
async def slash_leaderboard(interaction: discord.Interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("Use this command in a server.", ephemeral=True)
    data = config.get("xp_data", {}).get(str(interaction.guild.id), {})
    ranked = sorted(data.items(), key=lambda item: int(item[1].get("xp", 0)), reverse=True)[:10]
    lines=[]
    for i,(uid,record) in enumerate(ranked,1):
        member=interaction.guild.get_member(int(uid)); name=member.display_name if member else f"Unknown user ({uid})"
        level=_xp_level(int(record.get("xp",0)))[0]
        lines.append(f"**{i}.** {discord.utils.escape_markdown(name)} — Level **{level}** · {int(record.get('xp',0)):,} XP")
    await interaction.response.send_message(embed=feature_embed("Server XP Leaderboard", "\n".join(lines) if lines else "No XP rankings yet. Start chatting to earn XP!"))

@bot.tree.command(name="xp", description="Enable, disable, or inspect this server's XP system")
@app_commands.describe(action="Choose status, on, off, or announce", value="For announce, choose on or off")
@app_commands.choices(action=[app_commands.Choice(name=x, value=x) for x in ("status","on","off","announce")], value=[app_commands.Choice(name="on",value="on"),app_commands.Choice(name="off",value="off")])
async def slash_xp(interaction: discord.Interaction, action: app_commands.Choice[str], value: app_commands.Choice[str] = None):
    guild=interaction.guild
    if guild is None: return await interaction.response.send_message("Use this command in a server.",ephemeral=True)
    if not has_admin_access(interaction.user): return await interaction.response.send_message("Administrator access required.",ephemeral=True)
    settings=_guild_feature_map("xp_settings",guild.id)
    if action.value=="on": settings["enabled"]=True; description="XP and leveling enabled."
    elif action.value=="off": settings["enabled"]=False; description="XP earning disabled; existing ranks are preserved."
    elif action.value=="announce":
        if value is None: return await interaction.response.send_message("Choose announce on or off.",ephemeral=True)
        settings["announce"]=value.value=="on"; description=f"Level-up announcements {'enabled' if settings['announce'] else 'disabled'}."
    else: description=f"XP: **{'Disabled' if settings.get('enabled') is False else 'Enabled'}**\nAnnouncements: **{'Disabled' if settings.get('announce') is False else 'Enabled'}**\nCooldown: **{int(settings.get('cooldown',60))}s**"
    save_config(); await interaction.response.send_message(embed=feature_embed("XP Settings",description),ephemeral=True)

@bot.tree.command(name="counting", description="Configure the server counting game")
@app_commands.describe(action="status, setup, reset, or off", channel="Channel for the counting game")
@app_commands.choices(action=[app_commands.Choice(name=x,value=x) for x in ("status","setup","reset","off")])
async def slash_counting(interaction: discord.Interaction, action: app_commands.Choice[str], channel: discord.TextChannel = None):
    guild=interaction.guild
    if guild is None: return await interaction.response.send_message("Use this command in a server.",ephemeral=True)
    if not has_admin_access(interaction.user): return await interaction.response.send_message("Administrator access required.",ephemeral=True)
    settings=_guild_feature_map("counting_settings",guild.id)
    if action.value=="setup":
        target=channel or interaction.channel; settings.update({"channel_id":target.id,"current":0,"last_user_id":0,"enabled":True}); save_config()
        return await interaction.response.send_message(f"Counting game set up in {target.mention}. Start at **1**; members must take turns.",ephemeral=True)
    if action.value=="off": settings["enabled"]=False; save_config(); return await interaction.response.send_message("Counting disabled; progress saved.",ephemeral=True)
    if action.value=="reset": settings.update({"current":0,"last_user_id":0,"enabled":True}); save_config()
    target=guild.get_channel(int(settings.get("channel_id",0)))
    await interaction.response.send_message(embed=feature_embed("Counting Game",f"Status: **{'Enabled' if settings.get('enabled') and target else 'Disabled'}**\nChannel: {target.mention if target else 'Not configured'}\nCurrent count: **{int(settings.get('current',0))}**"),ephemeral=True)

@bot.tree.command(name="honeypot", description="Enable, disable, or inspect honeypot scam detection")
@app_commands.describe(action="Choose status, on, or off")
@app_commands.choices(action=[app_commands.Choice(name=x,value=x) for x in ("status","on","off")])
async def slash_honeypot(interaction: discord.Interaction, action: app_commands.Choice[str]):
    guild=interaction.guild
    if guild is None: return await interaction.response.send_message("Use this command in a server.",ephemeral=True)
    if not has_admin_access(interaction.user): return await interaction.response.send_message("Administrator access required.",ephemeral=True)
    settings=_guild_feature_settings("honeypot_settings",guild)
    if action.value=="on": settings["enabled"]=True; save_config()
    elif action.value=="off": settings["enabled"]=False; save_config()
    description=f"Honeypot is **{'Enabled' if settings.get('enabled',False) else 'Disabled'}**. Detects token-shaped strings, scam/verification bait, risky executable attachments, and suspicious shortened links. Suspicious content is deleted and logged; no automatic bans."
    await interaction.response.send_message(embed=feature_embed("Honeypot",description),ephemeral=True)

@bot.tree.command(name="antispam", description="Configure per-server anti-spam protection")
@app_commands.describe(action="Choose status, on, or off")
@app_commands.choices(action=[app_commands.Choice(name=x,value=x) for x in ("status","on","off")])
async def slash_antispam(interaction: discord.Interaction, action: app_commands.Choice[str]):
    guild=interaction.guild
    if guild is None: return await interaction.response.send_message("Use this command in a server.",ephemeral=True)
    if not has_admin_access(interaction.user): return await interaction.response.send_message("Administrator access required.",ephemeral=True)
    if action.value in ("on","off"): set_guild_setting(guild,"antispam_enabled",action.value=="on")
    enabled=bool(get_guild_setting(guild,"antispam_enabled",False))
    embed=feature_embed("Anti-Spam",f"Anti-spam is **{'ON' if enabled else 'OFF'}** for **{guild.name}**.\nMessages/window: {get_guild_setting(guild,'antispam_message_limit',3)} / {get_guild_setting(guild,'antispam_window_seconds',4)}s\nDuplicate limit: {get_guild_setting(guild,'antispam_duplicate_limit',3)}\nMention limit: {get_guild_setting(guild,'antispam_mention_limit',6)}\nTimeout: {get_guild_setting(guild,'antispam_timeout_seconds',300)}s")
    await interaction.response.send_message(embed=embed,ephemeral=True)

@bot.tree.command(name="antilink", description="Enable, disable, or inspect anti-link protection")
@app_commands.describe(action="Choose status, on, or off")
@app_commands.choices(action=[app_commands.Choice(name=x,value=x) for x in ("status","on","off")])
async def slash_antilink(interaction: discord.Interaction, action: app_commands.Choice[str]):
    guild=interaction.guild
    if guild is None: return await interaction.response.send_message("Use this command in a server.",ephemeral=True)
    if not has_admin_access(interaction.user): return await interaction.response.send_message("Administrator access required.",ephemeral=True)
    if action.value in ("on","off"): set_guild_setting(guild,"antilink_enabled",action.value=="on")
    settings=guild_security_settings(guild)
    enabled=bool(settings.get("antilink_enabled",False)); domains=settings.get("antilink_whitelist",[])
    await interaction.response.send_message(embed=feature_embed("Anti-Link",f"Anti-link is **{'ON' if enabled else 'OFF'}**.\nAllowed domains: {', '.join('`'+d+'`' for d in domains[:15]) or 'None'}"),ephemeral=True)

@bot.tree.command(name="timeout", description="Timeout a member for 1 minute, 1 day, or 1 week")
@app_commands.describe(member="Member to timeout", duration="Timeout duration", reason="Reason for the timeout")
@app_commands.choices(duration=[app_commands.Choice(name="1 minute",value="1min"),app_commands.Choice(name="1 day",value="1d"),app_commands.Choice(name="1 week",value="1w")])
async def slash_timeout(interaction: discord.Interaction, member: discord.Member, duration: app_commands.Choice[str], reason: str = "No reason provided"):
    guild=interaction.guild
    if guild is None: return await interaction.response.send_message("Use this command in a server.",ephemeral=True)
    if not has_admin_access(interaction.user): return await interaction.response.send_message("Administrator access required.",ephemeral=True)
    if member.id==guild.owner_id or (not is_bot_admin_id(interaction.user.id) and member.top_role>=interaction.user.top_role): return await interaction.response.send_message("You cannot timeout the server owner or a member with an equal/higher role.",ephemeral=True)
    delta={"1min":timedelta(minutes=1),"1d":timedelta(days=1),"1w":timedelta(weeks=1)}[duration.value]
    try:
        await member.timeout(delta,reason=f"{interaction.user} ({interaction.user.id}): {reason}")
        await interaction.response.send_message(embed=role_embed("Member Timed Out",f"**User:** {member.mention}\n**Duration:** {duration.name}\n**Reason:** {discord.utils.escape_markdown(reason)}",discord.Color.from_rgb(128,0,255)))
        await send_log(guild,"Member timed out",f"{member} (`{member.id}`) timed out by {interaction.user} (`{interaction.user.id}`).\nDuration: {duration.name}\nReason: {reason}",discord.Color.orange())
    except discord.Forbidden: await interaction.response.send_message("I need Moderate Members permission and a role above the target.",ephemeral=True)
    except discord.HTTPException: await interaction.response.send_message("Discord could not apply the timeout. Please try again.",ephemeral=True)

@bot.tree.command(name="untimeout", description="Remove a member's active timeout")
@app_commands.describe(member="Member whose timeout should be removed", reason="Reason for removing the timeout")
async def slash_untimeout(interaction: discord.Interaction, member: discord.Member, reason: str = "Timeout removed by moderator"):
    guild=interaction.guild
    if guild is None: return await interaction.response.send_message("Use this command in a server.",ephemeral=True)
    if not has_admin_access(interaction.user): return await interaction.response.send_message("Administrator access required.",ephemeral=True)
    if member.id==guild.owner_id or (not is_bot_admin_id(interaction.user.id) and member.top_role>=interaction.user.top_role): return await interaction.response.send_message("You cannot modify the server owner or a member with an equal/higher role.",ephemeral=True)
    if member.timed_out_until is None: return await interaction.response.send_message("That member does not currently have an active timeout.",ephemeral=True)
    try:
        await member.timeout(None,reason=f"{interaction.user} ({interaction.user.id}): {reason}")
        await interaction.response.send_message(embed=role_embed("Timeout Removed",f"**User:** {member.mention}\n**Reason:** {discord.utils.escape_markdown(reason)}",discord.Color.green()))
        await send_log(guild,"Member timeout removed",f"Timeout removed from {member} (`{member.id}`) by {interaction.user} (`{interaction.user.id}`).\nReason: {reason}",discord.Color.green())
    except discord.Forbidden: await interaction.response.send_message("I need Moderate Members permission and a role above the target.",ephemeral=True)
    except discord.HTTPException: await interaction.response.send_message("Discord could not remove the timeout. Please try again.",ephemeral=True)

@bot.tree.command(name="roles", description="List this server's roles")
async def slash_roles(interaction: discord.Interaction):
    guild=interaction.guild
    if guild is None: return await interaction.response.send_message("Use this command in a server.",ephemeral=True)
    roles=sorted(guild.roles,key=lambda r:(r.position,r.id),reverse=True)
    lines=[f"**{r.position}. {discord.utils.escape_markdown(r.name)}** — `{r.id}` · {len(r.members)} member(s)" for r in roles[:35]]
    await interaction.response.send_message(embed=feature_embed(f"{guild.name} • Roles","\n".join(lines) or "No roles found."),ephemeral=True)

# ==================== ADDITIONAL SLASH COMMAND COVERAGE ====================
@bot.tree.command(name="commands", description="List the available Kanade slash commands")
async def slash_commands(interaction: discord.Interaction):
    description = (
        "**Protection:** `/antinuke status`, `/antinuke on`, `/antinuke off`, `/antispam`, `/antilink`, `/honeypot`\n\n"
        "**Moderation:** `/kick`, `/ban`, `/unban`, `/timeout`, `/untimeout`, `/purge`, `/role`, `/unrole`, `/lock`, `/unlock`, `/lockdown`, `/unlockdown`\n\n"
        "**Utilities:** `/ping`, `/rank`, `/leaderboard`, `/xp`, `/counting`, `/roles`, `/afk`, `/snipe`, `/security`, `/status`, `/info`, `/serverinfo`, `/avatar`, `/prefix`, `/joinvc`, `/setuplogs`, `/disablelogs`, `/backup create`, `/backup load`, `/roleall`, `/unroleall`, `/whitelist`, `/whitelistrole`, `/autoresponder`, `/adminlist`, `/admlist`, `/changebanner`, `/changepicture`, `/steal`, `/botnick`, `/confess`, `/help`\n"
        "**Bot staff:** `/premium`, `/np`, `/npremove`, `/gnp`, `/gnpremove`, `/generatelink`, `/serverlists`\n\n"
        "Prefix commands remain available. Use `/help` if it is registered by your client, or `!help` / your server prefix plus `help` for the full guide."
    )
    await interaction.response.send_message(embed=feature_embed("Kanade | Slash Commands", description), ephemeral=True)

@bot.tree.command(name="confess", description="Submit an anonymous confession (Premium)")
@app_commands.describe(message="Your anonymous confession (up to 1,800 characters)")
async def slash_confess(interaction: discord.Interaction, message: str):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("Use this command in a server.", ephemeral=True)
    if not (is_owner_id(interaction.user.id) or interaction.user.id in config.get("premium_admin_ids", []) or is_premium_guild(guild)):
        return await interaction.response.send_message("Confessions are available in Premium-enabled servers.", ephemeral=True)
    settings = config.get("confession_settings", {}).get(str(guild.id), {})
    target = guild.get_channel(settings.get("channel_id", 0)) if settings else None
    if not settings.get("enabled") or target is None:
        return await interaction.response.send_message("Confessions are not configured in this server yet.", ephemeral=True)
    message = message.strip()
    if len(message) < 3 or len(message) > 1800:
        return await interaction.response.send_message("Your confession must be between 3 and 1,800 characters.", ephemeral=True)
    confession_id = secrets.token_hex(4).upper()
    embed = feature_embed("Anonymous Confession", discord.utils.escape_mentions(message))
    embed.add_field(name="Confession ID", value=f"`{confession_id}`", inline=False)
    embed.set_footer(text="Kanade Confessions • Be respectful")
    try:
        published = await target.send(embed=embed, view=ConfessionReportView(), allowed_mentions=discord.AllowedMentions.none())
        await add_confession_reactions(published)
        staff_channel = guild.get_channel(settings.get("staff_channel_id", 0))
        if staff_channel:
            await staff_channel.send(embed=feature_embed("Confession Submitted", f"**ID:** `{confession_id}`\n**Author ID:** `{interaction.user.id}`\n**Public channel:** {target.mention}"))
        await interaction.response.send_message("Your confession was submitted anonymously.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("Kanade cannot send messages in the configured confession channel.", ephemeral=True)

@bot.tree.command(name="security", description="Show this server's protection status")
async def slash_security(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("Use this command in a server.", ephemeral=True)
    embed = feature_embed("Kanade | Server Protection",
        f"Anti-nuke: **{'ON' if get_guild_setting(guild, 'antinuke_enabled', config.get('antinuke_enabled', True)) else 'OFF'}**")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="afk", description="Set your AFK status")
@app_commands.describe(message="Optional AFK message")
async def slash_afk(interaction: discord.Interaction, message: str = "AFK"):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("Use this command in a server.", ephemeral=True)
    afk_users[(guild.id, interaction.user.id)] = {"message": message[:500], "since": discord.utils.utcnow()}
    await interaction.response.send_message(embed=feature_embed("Kanade | AFK Enabled", f"You are now AFK: {discord.utils.escape_markdown(message[:500])}"), ephemeral=True)

@bot.tree.command(name="kick", description="Kick a member from this server")
@app_commands.describe(member="Member to kick", reason="Reason for the kick")
async def slash_kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    guild = interaction.guild
    if guild is None or not has_admin_access(interaction.user):
        return await interaction.response.send_message("Bot staff or server administrators only.", ephemeral=True)
    if member.id == guild.owner_id or (not is_bot_admin_id(interaction.user.id) and member.top_role >= interaction.user.top_role):
        return await interaction.response.send_message("You cannot kick the server owner or a member with an equal/higher role.", ephemeral=True)
    try:
        await member.kick(reason=f"{interaction.user}: {reason}")
        await interaction.response.send_message(embed=feature_embed("Member Kicked", f"Kicked {member.mention}. Reason: {reason}"), ephemeral=True)
        await send_log(guild, "Member kicked", f"{member} was kicked by {interaction.user}.\nReason: {reason}")
    except discord.Forbidden:
        await interaction.response.send_message("I cannot kick that member. Check my permissions and role hierarchy.", ephemeral=True)

@bot.tree.command(name="ban", description="Ban a user by Discord user ID")
@app_commands.describe(user_id="Numeric Discord user ID to ban", reason="Reason for the ban")
async def slash_ban(interaction: discord.Interaction, user_id: str, reason: str = "No reason provided"):
    guild = interaction.guild
    if guild is None or not has_admin_access(interaction.user):
        return await interaction.response.send_message("Bot staff or server administrators only.", ephemeral=True)
    raw = user_id.strip()
    match = re.fullmatch(r"<@!?(\d+)>", raw)
    raw_id = match.group(1) if match else raw
    if not raw_id.isdigit():
        return await interaction.response.send_message("Provide a numeric Discord user ID, e.g. `/ban user_id:123456789012345678`.", ephemeral=True)
    target_id = int(raw_id)
    member = guild.get_member(target_id)
    if member is None:
        try:
            member = await guild.fetch_member(target_id)
        except discord.NotFound:
            member = None
        except discord.HTTPException as error:
            return await interaction.response.send_message(f"Member lookup failed: {error}", ephemeral=True)
    if member is not None and (member.id == guild.owner_id or (not is_bot_admin_id(interaction.user.id) and member.top_role >= interaction.user.top_role)):
        return await interaction.response.send_message("You cannot ban the server owner or a member with an equal/higher role.", ephemeral=True)
    try:
        target = member if member is not None else discord.Object(id=target_id)
        await guild.ban(target, reason=f"{interaction.user} ({interaction.user.id}): {reason}")
        display = f"{member} (`{member.id}`)" if member is not None else f"User ID `{target_id}` (not currently in the server)"
        await interaction.response.send_message(embed=feature_embed("Member Banned", f"**User:** {display}\n**Reason:** {reason}"), ephemeral=True)
        await send_log(guild, "Member banned", f"{display} was banned by {interaction.user} (`{interaction.user.id}`).\nReason: {reason}", discord.Color.red())
    except discord.Forbidden:
        await interaction.response.send_message("I cannot ban that user. Check my Ban Members permission and role hierarchy.", ephemeral=True)
    except discord.HTTPException as error:
        await interaction.response.send_message(f"Ban failed: {error}", ephemeral=True)

@bot.tree.command(name="unban", description="Unban a user by Discord user ID")
@app_commands.describe(user_id="Numeric Discord user ID of the banned user", reason="Reason for the unban")
async def slash_unban(interaction: discord.Interaction, user_id: str, reason: str = "No reason provided"):
    guild = interaction.guild
    if guild is None or not has_admin_access(interaction.user):
        return await interaction.response.send_message("Bot staff or server administrators only.", ephemeral=True)
    raw_id = user_id.strip().strip("<@!>")
    if not raw_id.isdigit():
        return await interaction.response.send_message("Provide the numeric Discord user ID, e.g. `/unban user_id:123456789012345678`.", ephemeral=True)
    try:
        entry = await guild.fetch_ban(discord.Object(id=int(raw_id)))
        await guild.unban(entry.user, reason=f"{interaction.user} ({interaction.user.id}): {reason}")
        await interaction.response.send_message(embed=feature_embed("Member Unbanned", f"**User:** {entry.user}\n**User ID:** `{entry.user.id}`\n**Reason:** {reason}"), ephemeral=True)
        await send_log(guild, "Member unbanned", f"{entry.user} (`{entry.user.id}`) was unbanned by {interaction.user} (`{interaction.user.id}`).\nReason: {reason}", discord.Color.green())
    except discord.NotFound:
        await interaction.response.send_message(f"No active ban was found for user ID `{raw_id}`.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("I need Ban Members permission to unban users.", ephemeral=True)
    except discord.HTTPException as error:
        await interaction.response.send_message(f"Unban failed: {error}", ephemeral=True)

@bot.tree.command(name="purge", description="Delete recent messages in this channel")
@app_commands.describe(amount="Number of recent messages to delete (1-100)", member="Optional member whose messages should be deleted")
async def slash_purge(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100], member: discord.Member = None):
    guild = interaction.guild
    if guild is None or not has_admin_access(interaction.user):
        return await interaction.response.send_message("Bot staff or server administrators only.", ephemeral=True)
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel) or not channel.permissions_for(guild.me).manage_messages:
        return await interaction.response.send_message("I need Manage Messages in this channel.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        deleted = await channel.purge(limit=amount, check=(lambda m: m.author.id == member.id) if member else None, reason=f"Purge requested by {interaction.user}")
        await interaction.followup.send(embed=feature_embed("Messages Purged", f"Deleted **{len(deleted)}** message(s)."), ephemeral=True)
    except discord.HTTPException as exc:
        await interaction.followup.send(f"Purge failed: {exc}", ephemeral=True)

@bot.tree.command(name="role", description="Give a role to a member")
@app_commands.describe(member="Member receiving the role", role="Role to assign")
async def slash_role(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    guild = interaction.guild
    if guild is None or not has_admin_access(interaction.user):
        return await interaction.response.send_message("Bot staff or server administrators only.", ephemeral=True)
    bot_member = guild.me
    if not bot_member.guild_permissions.manage_roles or role >= bot_member.top_role or role.is_default():
        return await interaction.response.send_message("I cannot assign that role. Check Manage Roles and role hierarchy.", ephemeral=True)
    if member.id == guild.owner_id or (not is_bot_admin_id(interaction.user.id) and member.top_role >= interaction.user.top_role):
        return await interaction.response.send_message("You cannot manage roles for that member due to role hierarchy.", ephemeral=True)
    try:
        await member.add_roles(role, reason=f"Role assigned by {interaction.user}")
        await interaction.response.send_message(embed=feature_embed("Role Assigned", f"Assigned {role.mention} to {member.mention}."), ephemeral=True)
    except discord.HTTPException as exc:
        await interaction.response.send_message(f"Role assignment failed: {exc}", ephemeral=True)

@bot.tree.command(name="unrole", description="Remove a role from a member")
@app_commands.describe(member="Member losing the role", role="Role to remove")
async def slash_unrole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    guild = interaction.guild
    if guild is None or not has_admin_access(interaction.user):
        return await interaction.response.send_message("Bot staff or server administrators only.", ephemeral=True)
    bot_member = guild.me
    if not bot_member.guild_permissions.manage_roles or role >= bot_member.top_role or role.is_default():
        return await interaction.response.send_message("I cannot remove that role. Check Manage Roles and role hierarchy.", ephemeral=True)
    try:
        await member.remove_roles(role, reason=f"Role removed by {interaction.user}")
        await interaction.response.send_message(embed=feature_embed("Role Removed", f"Removed {role.mention} from {member.mention}."), ephemeral=True)
    except discord.HTTPException as exc:
        await interaction.response.send_message(f"Role removal failed: {exc}", ephemeral=True)

@bot.tree.command(name="snipe", description="Show the latest deleted message in this channel")
async def slash_snipe(interaction: discord.Interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("Use this command in a server.", ephemeral=True)
    data = recent_deleted_messages.get(interaction.channel.id)
    if not data:
        return await interaction.response.send_message("No recently deleted message is stored for this channel.", ephemeral=True)
    embed = feature_embed("Kanade | Snipe", f"**Author:** {data.get('author', 'Unknown')}\n**Message:** {discord.utils.escape_markdown(data.get('content') or '[No text content]')}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="prefix", description="View or change this server's command prefix")
@app_commands.describe(new_prefix="New prefix (1-5 characters); omit to view current prefix")
async def slash_prefix(interaction: discord.Interaction, new_prefix: str = None):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("Use this command in a server.", ephemeral=True)
    current = config.get("guild_prefixes", {}).get(str(guild.id), PREFIX)
    if new_prefix is None:
        return await interaction.response.send_message(f"Current prefix: `{current}`", ephemeral=True)
    if not has_admin_access(interaction.user):
        return await interaction.response.send_message("Bot staff or server administrators only.", ephemeral=True)
    if not 1 <= len(new_prefix) <= 5 or any(ch.isspace() for ch in new_prefix):
        return await interaction.response.send_message("Prefix must be 1-5 non-space characters.", ephemeral=True)
    config.setdefault("guild_prefixes", {})[str(guild.id)] = new_prefix
    save_config()
    await interaction.response.send_message(f"Prefix changed from `{current}` to `{new_prefix}`.", ephemeral=True)

# Additional slash equivalents for existing utility and bot-staff commands.
@bot.tree.command(name="info", description="Show bot information and public features")
async def slash_info(interaction: discord.Interaction):
    user = bot.user
    embed = feature_embed(
        "Kanade | Bot Information",
        "A Discord security and moderation toolkit. Use `/help` or the server's configured prefix to view command guidance."
    )
    embed.add_field(
        name="🛡️ Server Security",
        value=(
            "• Anti-nuke monitoring and configurable punishment\n"
            "• Anti-raid join detection and optional lockdown\n"
            "• Whitelist management, security status, and security logs\n"
            "• Owner notifications"
        ),
        inline=False,
    )
    embed.add_field(
        name="🔨 Moderation & Utilities",
        value=(
            "• Kick/ban members and manage member roles\n"
            "• Bulk role assignment/removal and message purge\n"
            "• Channel lock/unlock and server lockdown/unlockdown\n"
            "• Deleted-message snipe, AFK status, and voice-channel join\n"
            "• Server information and avatar lookup (Premium may be required)"
        ),
        inline=False,
    )
    embed.add_field(
        name="💾 Backups & Customization",
        value=(
            "• Create and restore server backups (Premium)\n"
            "• Configure or disable security log channels\n"
            "• Change server-specific bot banner, profile picture, or nickname (Premium)\n"
            "• Add custom emojis and configure the server prefix (Premium)\n"
            "• Available through prefix and slash commands"
        ),
        inline=False,
    )
    embed.add_field(
        name="📊 Runtime Information",
        value=(
            f"**Servers:** `{len(bot.guilds):,}`\n"
            f"**Bot ID:** `{user.id if user else 'Unavailable'}`\n"
            f"**Developer:** <@{DEVELOPER_ID}>"
        ),
        inline=False,
    )
    if user:
        embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text="Kanade • Developed by Wxre Development", icon_url=user.display_avatar.url if user else None)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="serverinfo", description="Show information about this server")
async def slash_serverinfo(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("Use this command in a server.", ephemeral=True)
    embed = feature_embed(f"Kanade | {guild.name}",
        f"**Server ID:** `{guild.id}`\n**Owner:** <@{guild.owner_id}>\n**Members:** `{guild.member_count or 0}`\n**Text channels:** `{len(guild.text_channels)}`\n**Voice channels:** `{len(guild.voice_channels)}`\n**Categories:** `{len(guild.categories)}`\n**Roles:** `{len(guild.roles)}`\n**Created:** <t:{int(guild.created_at.timestamp())}:D>")
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="avatar", description="Show a user's avatar")
@app_commands.describe(user="User whose avatar you want to view")
async def slash_avatar(interaction: discord.Interaction, user: discord.User = None):
    if interaction.guild and not (is_owner_id(interaction.user.id) or interaction.user.id in config.get("premium_admin_ids", []) or is_premium_guild(interaction.guild)):
        return await interaction.response.send_message(embed=feature_embed("Premium Required", "Avatar lookup is available in Premium-enabled servers.", color=discord.Color.orange()), ephemeral=True)
    target = user or interaction.user
    avatar_url = target.display_avatar.url
    embed = feature_embed(f"Kanade | Avatar — {discord.utils.escape_markdown(target.name)}", f"**User ID:** `{target.id}`\n[Open full-size avatar]({avatar_url})")
    embed.set_image(url=avatar_url)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="joinvc", description="Join your current voice channel")
async def slash_joinvc(interaction: discord.Interaction):
    guild = interaction.guild
    member = interaction.user
    if guild is None or not has_admin_access(member):
        return await interaction.response.send_message("Bot staff or server administrators only.", ephemeral=True)
    if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
        return await interaction.response.send_message("Join a voice channel first.", ephemeral=True)
    channel = member.voice.channel
    try:
        voice = guild.voice_client
        if voice and voice.is_connected():
            await voice.move_to(channel)
        else:
            await channel.connect()
        voice_clients_requested.add(guild.id)
        await interaction.response.send_message(f"Joined {channel.mention}.", ephemeral=True)
    except (discord.Forbidden, discord.HTTPException) as exc:
        await interaction.response.send_message(f"Could not join the voice channel: {exc}", ephemeral=True)

@bot.tree.command(name="admlist", description="List authorized bot staff")
async def slash_admlist(interaction: discord.Interaction):
    if not is_global_staff(interaction.user):
        return await interaction.response.send_message("Bot staff only.", ephemeral=True)

    owner_id = DEVELOPER_ID
    admin_ids = list(dict.fromkeys(
        int(value) for value in config.get("premium_admin_ids", []) if str(value).isdigit()
    ))
    ordered_ids = [owner_id] + [uid for uid in admin_ids if uid != owner_id]
    purple = discord.Color.from_rgb(35, 0, 55)
    embed = StyledEmbed(
        title="🛡️ Kanade | Staff List",
        description="Need help with Premium access? Contact the developer or an authorized staff member for support and assistance.",
        color=purple,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="👑 Developer", value=await _bot_admin_username_line(owner_id), inline=False)
    staff_ids = ordered_ids[1:]
    if staff_ids:
        chunks = []
        current = []
        for index, uid in enumerate(staff_ids, start=1):
            line = await _bot_admin_username_line(uid, index)
            if len("\n".join(current + [line])) > 1000 and current:
                chunks.append(current)
                current = [line]
            else:
                current.append(line)
        if current:
            chunks.append(current)
        for page, chunk in enumerate(chunks, start=1):
            label = "🛡️ Staff" if len(chunks) == 1 else f"🛡️ Staff · {page}/{len(chunks)}"
            embed.add_field(name=label, value="\n".join(chunk), inline=False)
    else:
        embed.add_field(name="🛡️ Staff", value="No staff members have been added.", inline=False)
    embed.set_footer(text="Developer / Staff")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="premium", description="Manage Premium access (bot staff only)")
@app_commands.choices(action=[app_commands.Choice(name="add", value="add"), app_commands.Choice(name="remove", value="remove"), app_commands.Choice(name="list", value="list")])
@app_commands.describe(action="Premium action", server_id="Server ID required for add/remove")
async def slash_premium(interaction: discord.Interaction, action: app_commands.Choice[str], server_id: str = None):
    # Server owners and server administrators are not automatically bot staff.
    if not is_bot_admin_id(interaction.user.id):
        return await interaction.response.send_message(
            "Only the bot developer and authorized bot admins can manage Premium.",
            ephemeral=True
        )
    current = set(int(x) for x in config.get("premium_guild_ids", config.get("premium_guilds", [])) if str(x).isdigit())
    if action.value == "list":
        if not current:
            text_value = "No servers currently have Premium."
        else:
            rows = []
            for gid in sorted(current):
                guild = bot.get_guild(gid)
                server_name = guild.name if guild else "Server not found in bot cache"
                rows.append(f"• **{discord.utils.escape_markdown(server_name)}**\n  Server ID: `{gid}`")
            text_value = "\n".join(rows)
            if len(text_value) > 3900:
                text_value = text_value[:3850].rsplit("\n", 1)[0] + "\n… list shortened to fit Discord's embed limit."
    else:
        if not server_id or not server_id.isdigit():
            return await interaction.response.send_message("Provide a valid server ID.", ephemeral=True)
        gid = int(server_id)
        if action.value == "add": current.add(gid)
        else: current.discard(gid)
        text_value = f"Premium {'enabled for' if action.value == 'add' else 'removed from'} server `{gid}`."
    config["premium_guild_ids"] = sorted(current)
    config["premium_guilds"] = sorted(current)
    save_config()
    await interaction.response.send_message(embed=feature_embed("Kanade | Premium Manager", text_value), ephemeral=True)

@bot.tree.command(name="np", description="Allow a user to use commands without a prefix in this server")
@app_commands.describe(user="User to authorize")
async def slash_np(interaction: discord.Interaction, user: discord.User):
    if not is_bot_admin_id(interaction.user.id) or interaction.guild is None:
        return await interaction.response.send_message("Only the bot owner and authorized bot admins can use this command in a server.", ephemeral=True)
    ids = config.setdefault("guild_no_prefix", {}).setdefault(str(interaction.guild.id), [])
    if user.id not in ids: ids.append(user.id)
    save_config()
    await interaction.response.send_message(f"{user.mention} can now use no-prefix commands in this server.", ephemeral=True)

@bot.tree.command(name="npremove", description="Remove a user's no-prefix access in this server")
@app_commands.describe(user="User to revoke")
async def slash_npremove(interaction: discord.Interaction, user: discord.User):
    if not is_bot_admin_id(interaction.user.id) or interaction.guild is None:
        return await interaction.response.send_message("Only the bot owner and authorized bot admins can use this command in a server.", ephemeral=True)
    key = str(interaction.guild.id)
    config.setdefault("guild_no_prefix", {})[key] = [int(x) for x in config.get("guild_no_prefix", {}).get(key, []) if str(x) != str(user.id)]
    save_config()
    await interaction.response.send_message(f"Removed {user.mention} from this server's no-prefix list.", ephemeral=True)

@bot.tree.command(name="gnp", description="Allow a user to use no-prefix commands globally")
@app_commands.describe(user="User to authorize globally")
async def slash_gnp(interaction: discord.Interaction, user: discord.User):
    if not is_bot_admin_id(interaction.user.id):
        return await interaction.response.send_message("Only the bot owner and authorized bot admins can use this command.", ephemeral=True)
    ids = config.setdefault("global_no_prefix", [])
    if user.id not in ids: ids.append(user.id)
    save_config()
    await interaction.response.send_message(f"{user.mention} can now use no-prefix commands in every server.", ephemeral=True)

@bot.tree.command(name="gnpremove", description="Revoke global no-prefix access")
@app_commands.describe(user="User to revoke")
async def slash_gnpremove(interaction: discord.Interaction, user: discord.User):
    if not is_bot_admin_id(interaction.user.id):
        return await interaction.response.send_message("Only the bot owner and authorized bot admins can use this command.", ephemeral=True)
    config["global_no_prefix"] = [int(x) for x in config.get("global_no_prefix", []) if str(x) != str(user.id)]
    save_config()
    await interaction.response.send_message(f"Revoked global no-prefix access from {user.mention}.", ephemeral=True)

@bot.tree.command(name="serverlists", description="List servers the bot has joined")
async def slash_serverlists(interaction: discord.Interaction):
    if not is_global_staff(interaction.user):
        return await interaction.response.send_message("Global bot staff only.", ephemeral=True)
    lines = [f"• **{g.name}** — `{g.id}` — {g.member_count or 0} members" for g in sorted(bot.guilds, key=lambda x: x.name.lower())]
    description = "\n".join(lines) if lines else "The bot is not in any servers."
    await interaction.response.send_message(embed=feature_embed("Kanade | Connected Servers", description[:4000]), ephemeral=True)

@bot.tree.command(name="generatelink", description="Generate an invite for a server the bot has joined")
@app_commands.describe(server_id="ID of a server the bot is in")
async def slash_generatelink(interaction: discord.Interaction, server_id: str):
    if not is_global_staff(interaction.user):
        return await interaction.response.send_message("Global bot staff only.", ephemeral=True)
    if not server_id.isdigit():
        return await interaction.response.send_message("Enter a valid server ID.", ephemeral=True)
    guild = bot.get_guild(int(server_id))
    if guild is None:
        return await interaction.response.send_message("The bot is not in that server.", ephemeral=True)
    target = next((ch for ch in guild.text_channels if ch.permissions_for(guild.me).create_instant_invite), None)
    if target is None:
        return await interaction.response.send_message("The bot cannot create an invite in that server.", ephemeral=True)
    try:
        invite = await target.create_invite(max_age=3600, max_uses=1, reason=f"Invite generated by bot staff {interaction.user}")
        await interaction.response.send_message(f"Invite for **{guild.name}** (expires in 1 hour): {invite.url}", ephemeral=True)
    except discord.HTTPException as exc:
        await interaction.response.send_message(f"Could not create invite: {exc}", ephemeral=True)

# Slash equivalents for remaining high-use prefix utilities.
@bot.tree.command(name="help", description="Show the command guide")
async def slash_help(interaction: discord.Interaction):
    await interaction.response.send_message(embed=feature_embed(
        "Kanade | Command Guide",
        "Use `/commands` for the slash command index.\n\n"
        "**Server Protection:** `/antinuke`, `/security`, `/lockdown`, `/unlockdown`, `!antispam on/off/status`, `!antispam settings <setting> <value>`, `!antilink on/off/status`, `!antilink allow <domain>`, `!antilink deny <domain>`, `!antilink exempt @member`.\n"
        "**Moderation:** `/kick`, `/ban`, `/unban`, `/purge`, `/role`, `/unrole`, `/lock`, `/unlock`.\n"
        "**Utilities:** `/afk`, `/snipe`, `/serverinfo`, `/info`, `/avatar`, `/prefix`."
    ), ephemeral=True)

@bot.tree.command(name="admhelp", description="Show restricted bot-staff commands")
async def slash_admhelp(interaction: discord.Interaction):
    if not is_global_staff(interaction.user):
        return await interaction.response.send_message("Bot staff only.", ephemeral=True)
    await interaction.response.send_message(embed=feature_embed("Kanade | Bot Staff Guide", "`/premium` — manage Premium server access\n`/adm` — grant/revoke bot-admin access\n`/admlist` and `/adminlist` — list staff\n`/np`, `/npremove` — per-server no-prefix access\n`/gnp`, `/gnpremove` — global no-prefix access\n`/serverlists` — list connected servers\n`/generatelink` — generate a server invite"), ephemeral=True)

@bot.tree.command(name="adm", description="Grant or revoke bot-admin access (developer only)")
@app_commands.choices(action=[app_commands.Choice(name="add", value="add"), app_commands.Choice(name="remove", value="remove")])
@app_commands.describe(action="Grant or revoke bot-admin access", user="Target user")
async def slash_adm(interaction: discord.Interaction, action: app_commands.Choice[str], user: discord.User):
    if interaction.user.id != DEVELOPER_ID:
        return await interaction.response.send_message("Developer only.", ephemeral=True)
    ids = list(dict.fromkeys(int(x) for x in config.get("premium_admin_ids", []) if str(x).isdigit()))
    if action.value == "add":
        if user.id not in ids:
            ids.append(user.id)
        message = f"{user.mention} now has bot-admin access."
    else:
        if user.id == DEVELOPER_ID or user.id in config.get("owner_ids", []):
            return await interaction.response.send_message("The developer/configured bot owner cannot be removed.", ephemeral=True)
        ids = [x for x in ids if x != user.id]
        message = f"Bot-admin access revoked for {user.mention}."
    config["premium_admin_ids"] = [DEVELOPER_ID] + [x for x in ids if x != DEVELOPER_ID]
    save_config()
    await interaction.response.send_message(embed=feature_embed("Kanade | Bot Admin Updated", message), ephemeral=True)

async def slash_change_profile(interaction: discord.Interaction, field_name: str, attachment: discord.Attachment):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("Use this command in a server.", ephemeral=True)
    if not (is_owner_id(interaction.user.id) or interaction.user.id in config.get("premium_admin_ids", []) or is_premium_guild(guild)):
        return await interaction.response.send_message("This is a Premium feature.", ephemeral=True)
    if not has_admin_access(interaction.user):
        return await interaction.response.send_message("Bot staff or server administrators only.", ephemeral=True)
    if attachment.size > PROFILE_IMAGE_MAX_BYTES:
        return await interaction.response.send_message("Image must be 8 MB or smaller.", ephemeral=True)
    content_type = (attachment.content_type or "").split(";", 1)[0].lower()
    if content_type and content_type not in PROFILE_IMAGE_TYPES:
        return await interaction.response.send_message("Use a PNG, JPG, GIF, or WEBP image.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        data = await attachment.read()
        if not data or len(data) > PROFILE_IMAGE_MAX_BYTES:
            return await interaction.followup.send("Image is empty or exceeds 8 MB.", ephemeral=True)
        signatures = (data.startswith(b"\x89PNG\r\n\x1a\n"), data.startswith(b"\xff\xd8\xff"), data.startswith((b"GIF87a", b"GIF89a")), data.startswith(b"RIFF") and data[8:12] == b"WEBP")
        if not any(signatures):
            return await interaction.followup.send("That file does not appear to be a supported image.", ephemeral=True)
        image_uri = discord.utils._bytes_to_base64_data(data)
        route = discord.http.Route("PATCH", "/guilds/{guild_id}/members/@me", guild_id=guild.id)
        await bot.http.request(route, json={field_name: image_uri})
        label = "server-specific profile banner" if field_name == "banner" else "server-specific profile picture"
        await interaction.followup.send(embed=feature_embed("Kanade | Profile Updated", f"Updated the bot's {label} in **{guild.name}**."), ephemeral=True)
    except (discord.HTTPException, TypeError, aiohttp.ClientError) as exc:
        await interaction.followup.send(f"Profile update failed. Check image support and bot permissions. Details: {str(exc)[:500]}", ephemeral=True)

@bot.tree.command(name="changebanner", description="Change the bot's server-specific profile banner")
@app_commands.describe(image="PNG, JPG, GIF, or WEBP image (8 MB max)")
async def slash_changebanner(interaction: discord.Interaction, image: discord.Attachment):
    await slash_change_profile(interaction, "banner", image)

@bot.tree.command(name="changepicture", description="Change the bot's server-specific profile picture")
@app_commands.describe(image="PNG, JPG, GIF, or WEBP image (8 MB max)")
async def slash_changepicture(interaction: discord.Interaction, image: discord.Attachment):
    await slash_change_profile(interaction, "avatar", image)

@bot.tree.command(name="botnick", description="Change or reset the bot's nickname in this server")
@app_commands.describe(nickname="New nickname, or enter reset to clear it")
async def slash_botnick(interaction: discord.Interaction, nickname: str):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("Use this command in a server.", ephemeral=True)
    if not (is_owner_id(interaction.user.id) or interaction.user.id in config.get("premium_admin_ids", []) or is_premium_guild(guild)):
        return await interaction.response.send_message("This is a Premium feature.", ephemeral=True)
    if not has_admin_access(interaction.user):
        return await interaction.response.send_message("Bot staff or server administrators only.", ephemeral=True)
    bot_member = guild.me
    if bot_member is None or not bot_member.guild_permissions.change_nickname:
        return await interaction.response.send_message("I need Change Nickname permission.", ephemeral=True)
    new_nickname = None if nickname.strip().casefold() == "reset" else nickname.strip()
    if new_nickname is not None and (not new_nickname or len(new_nickname) > 32):
        return await interaction.response.send_message("Nickname must be 1-32 characters, or use `reset`.", ephemeral=True)
    try:
        await bot_member.edit(nick=new_nickname, reason=f"Nickname changed by {interaction.user} ({interaction.user.id})")
        await interaction.response.send_message(embed=feature_embed("Kanade | Nickname Updated", f"Bot nickname {'reset' if new_nickname is None else 'changed to **' + discord.utils.escape_markdown(new_nickname) + '**'}."), ephemeral=True)
    except discord.HTTPException as exc:
        await interaction.response.send_message(f"Nickname update failed: {str(exc)[:500]}", ephemeral=True)

@bot.tree.command(name="steal", description="Copy a custom emoji into this server")
@app_commands.describe(emoji="Custom emoji mention, e.g. <:name:123456789>", name="Optional new name for the copied emoji")
async def slash_steal(interaction: discord.Interaction, emoji: str, name: str = None):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("Use this command in a server.", ephemeral=True)
    if not (is_owner_id(interaction.user.id) or interaction.user.id in config.get("premium_admin_ids", []) or is_premium_guild(guild)):
        return await interaction.response.send_message("This is a Premium feature.", ephemeral=True)
    if not has_admin_access(interaction.user):
        return await interaction.response.send_message("Bot staff or server administrators only.", ephemeral=True)
    if not guild.me.guild_permissions.manage_emojis_and_stickers:
        return await interaction.response.send_message("I need Manage Expressions permission.", ephemeral=True)
    match = _CUSTOM_EMOJI_RE.search(emoji)
    if not match:
        return await interaction.response.send_message("Provide a custom emoji mention such as `<:name:123456789>`.", ephemeral=True)
    emoji_id, original_name, animated = match.group("id"), match.group("name"), bool(match.group("animated"))
    new_name = re.sub(r"[^A-Za-z0-9_]", "_", (name or original_name)).strip("_")[:32] or "copied_emoji"
    if len(new_name) < 2:
        new_name = "copied_emoji"
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        ext = "gif" if animated else "png"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15, connect=5)) as session:
            async with session.get(f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}?quality=lossless") as response:
                if response.status != 200:
                    return await interaction.followup.send("Source emoji image could not be downloaded.", ephemeral=True)
                data = await response.content.read(10 * 1024 * 1024 + 1)
        if not data or len(data) > 10 * 1024 * 1024:
            return await interaction.followup.send("Emoji image is empty or exceeds 10 MB.", ephemeral=True)
        created = await guild.create_custom_emoji(name=new_name, image=data, reason=f"Emoji copied by {interaction.user} ({interaction.user.id})")
        await interaction.followup.send(embed=feature_embed("Kanade | Emoji Added", f"Added {created} as `{created.name}`."), ephemeral=True)
    except (aiohttp.ClientError, asyncio.TimeoutError, discord.HTTPException) as exc:
        await interaction.followup.send(f"Could not add the emoji. Check permissions and emoji capacity. {str(exc)[:300]}", ephemeral=True)

# ==================== ANTI-SPAM ENFORCEMENT ====================
# Keep a short per-member message history and prevent repeated punishment.
spam_message_history = defaultdict(deque)
spam_action_cooldowns = {}

@bot.listen("on_message")
async def antispam_message_listener(message):
    """Delete detected spam and timeout the sender when permissions allow."""
    if message.guild is None or message.author.bot:
        return
    guild = message.guild
    if not bool(get_guild_setting(guild, "antispam_enabled", False)):
        return

    member = message.author
    if not isinstance(member, discord.Member):
        member = guild.get_member(message.author.id)
    if member is None:
        return
    # Anti-spam is evaluated independently of the anti-nuke whitelist.
    # Whitelisted users can still be detected for spam; administrator exemptions
    # remain controlled by antispam_exempt_admins below.
    if member.id == guild.owner_id:
        return
    if config.get("antispam_exempt_admins", False) and member.guild_permissions.administrator:
        return

    now = time.monotonic()
    # Fast-response defaults: trigger after 3 messages in a 4-second window.
    # Cap legacy config thresholds at 3 so older config.json values do not keep
    # the detector unnecessarily slow; lower configured values are respected.
    window = max(1, min(30, int(get_guild_setting(guild, "antispam_window_seconds", 4))))
    message_limit = max(2, min(20, int(get_guild_setting(guild, "antispam_message_limit", 3))))
    duplicate_limit = max(2, min(20, int(get_guild_setting(guild, "antispam_duplicate_limit", 3))))
    mention_limit = max(1, int(get_guild_setting(guild, "antispam_mention_limit", 6)))
    timeout_seconds = max(1, int(get_guild_setting(guild, "antispam_timeout_seconds", 300)))

    key = (guild.id, message.channel.id, member.id)
    history = spam_message_history[key]
    while history and now - history[0][0] > window:
        history.popleft()
    # Normalize whitespace and case so TESTING, testing, and extra spaces match.
    normalized_content = " ".join((message.content or "").split()).casefold()
    history.append((now, normalized_content, message))

    duplicate_count = sum(
        1 for _, content, _ in history
        if normalized_content and content == normalized_content
    )
    mention_count = len(message.mentions) + len(message.role_mentions)
    if message.mention_everyone:
        mention_count = max(mention_count, mention_limit)

    reason = None
    if mention_count >= mention_limit:
        reason = f"mention spam ({mention_count} mentions in one message)"
    elif duplicate_count >= duplicate_limit:
        reason = f"repeated-message spam ({duplicate_count} matching messages in {window}s)"
    elif len(history) >= message_limit:
        reason = f"rapid-message spam ({len(history)} messages in {window}s)"

    if reason is None:
        return

    cooldown_key = (guild.id, member.id)
    if now - spam_action_cooldowns.get(cooldown_key, 0) < 10:
        return
    spam_action_cooldowns[cooldown_key] = now
    history_to_clean = list(history)
    history.clear()

    bot_member = guild.me
    if bot_member is None:
        logger.warning("Anti-spam: bot member unavailable in guild %s", guild.id)
        return

    deleted = 0

    async def delete_spam_burst():
        """Delete the burst efficiently, using Discord bulk-delete when possible."""
        nonlocal deleted
        if not bool(get_guild_setting(guild, "antispam_delete_messages", True)):
            return
        if not bot_member.guild_permissions.manage_messages:
            logger.warning("Anti-spam detected in %s but bot lacks Manage Messages", guild.id)
            return

        candidates = []
        seen_ids = set()
        for _, _, spam_message in history_to_clean:
            if spam_message.id not in seen_ids:
                seen_ids.add(spam_message.id)
                # Discord bulk deletion cannot include messages older than 14 days.
                age = (discord.utils.utcnow() - spam_message.created_at).total_seconds()
                if age < 14 * 24 * 60 * 60:
                    candidates.append(spam_message)

        if not candidates:
            return
        try:
            if len(candidates) >= 2 and hasattr(message.channel, "delete_messages"):
                await message.channel.delete_messages(candidates, reason="Kanade anti-spam cleanup")
                deleted += len(candidates)
            else:
                await candidates[0].delete(reason="Kanade anti-spam cleanup")
                deleted += 1
        except discord.NotFound:
            # Some messages may already have been deleted by a moderator/other bot.
            pass
        except discord.Forbidden:
            logger.warning("Anti-spam could not delete messages in guild %s", guild.id)
        except discord.HTTPException as error:
            # Bulk deletion can fail if the batch contains an invalid/old message;
            # fall back to individual deletes rather than abandoning cleanup.
            logger.warning("Anti-spam bulk delete failed in guild %s: %s; falling back", guild.id, error)
            for spam_message in candidates:
                try:
                    await spam_message.delete(reason="Kanade anti-spam cleanup")
                    deleted += 1
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue

    async def apply_timeout():
        nonlocal timed_out
        if not bot_member.guild_permissions.moderate_members:
            logger.warning("Anti-spam detected in %s but bot lacks Moderate Members", guild.id)
            return
        if member.top_role >= bot_member.top_role:
            logger.warning("Anti-spam cannot timeout %s: member role is above/equal to bot role", member.id)
            return
        try:
            await member.timeout(
                timedelta(seconds=timeout_seconds),
                reason=f"Kanade anti-spam: {reason}"
            )
            timed_out = True
        except (discord.Forbidden, discord.HTTPException) as error:
            logger.warning("Anti-spam timeout failed for %s: %s", member.id, error)

    # Start enforcement actions together so message cleanup does not delay timeout.
    timed_out = False
    await asyncio.gather(delete_spam_burst(), apply_timeout())

    status = (
        f"**Member:** {member.mention} (`{member.id}`)\n"
        f"**Detection:** {reason}\n"
        f"**Messages deleted:** {deleted}\n"
        f"**Timeout:** {'applied for ' + str(timeout_seconds) + ' seconds' if timed_out else 'not applied; check bot permissions and role hierarchy'}"
    )
    await send_log(guild, "Anti-Spam Action", status, discord.Color.orange())
    logger.warning("Anti-spam action in guild %s for member %s: %s; deleted=%s; timeout=%s", guild.id, member.id, reason, deleted, timed_out)

@bot.listen("on_message")
async def antilink_message_listener(message):
    """Delete links in servers that have independently enabled anti-link."""
    guild = message.guild
    if guild is None or message.author.bot or not bool(get_guild_setting(guild, "antilink_enabled", False)):
        return
    if is_link_exempt(message):
        return
    domains = extract_domains(message.content or "")
    if not domains:
        return
    allowed = guild_security_settings(guild).get("antilink_whitelist", [])
    blocked = [domain for domain in domains if not domain_is_allowed(domain, allowed)]
    if not blocked:
        return
    try:
        if guild.me and guild.me.guild_permissions.manage_messages:
            await message.delete()  # PartialMessage.delete() does not accept a reason argument.
        else:
            logger.warning("Anti-link detected a link in guild %s but bot lacks Manage Messages", guild.id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass
    try:
        warning = await message.channel.send(f"{message.author.mention}, links are not allowed in this server.")
        await asyncio.sleep(5)
        await warning.delete()
    except (discord.Forbidden, discord.HTTPException):
        pass
    await send_log(guild, "Anti-link triggered", f"Removed a link from {message.author.mention} in {message.channel.mention}. Blocked domain(s): {', '.join(blocked)}", discord.Color.orange())


@bot.command(name="antispam")
@commands.guild_only()
async def antispam_command(ctx, mode: str = None, setting: str = None, value: str = None):
    """Configure anti-spam independently for the current server."""
    if not has_admin_access(ctx.author):
        return await ctx.reply("Bot staff or server administrators only.", mention_author=False)
    mode = (mode or "status").lower()
    if mode in ("on", "enable", "enabled"):
        set_guild_setting(ctx.guild, "antispam_enabled", True)
        description = f"Anti-spam is now **ON** for **{ctx.guild.name}**."
    elif mode in ("off", "disable", "disabled"):
        set_guild_setting(ctx.guild, "antispam_enabled", False)
        description = f"Anti-spam is now **OFF** for **{ctx.guild.name}**."
    elif mode == "settings":
        allowed = {"window": ("antispam_window_seconds", 1, 30), "messages": ("antispam_message_limit", 2, 20), "duplicates": ("antispam_duplicate_limit", 2, 20), "mentions": ("antispam_mention_limit", 1, 50), "timeout": ("antispam_timeout_seconds", 1, 2419200), "delete": ("antispam_delete_messages", None, None)}
        if setting and setting.lower() in allowed and value is not None:
            key, minimum, maximum = allowed[setting.lower()]
            try:
                parsed = value.lower() in ("true", "yes", "on", "1") if key.endswith("delete_messages") else int(value)
                if minimum is not None and not minimum <= parsed <= maximum:
                    raise ValueError
                set_guild_setting(ctx.guild, key, parsed)
                description = f"Updated this server's anti-spam setting **{setting}** to **{parsed}**."
            except ValueError:
                return await ctx.reply("Invalid value. Use window 1–30, messages/duplicates 2–20, mentions 1–50, timeout 1–2419200, or delete true/false.", mention_author=False)
        else:
            description = "Settings: `window 1-30`, `messages 2-20`, `duplicates 2-20`, `mentions 1-50`, `timeout 1-2419200`, `delete true/false`. Example: `antispam settings messages 4`."
    elif mode != "status":
        description = "Use `antispam on`, `antispam off`, `antispam status`, or `antispam settings <setting> <value>`."
    else:
        description = "Anti-spam status for this server."
    enabled = bool(get_guild_setting(ctx.guild, "antispam_enabled", False))
    embed = feature_embed("Anti-Spam", description, color=discord.Color.from_rgb(35, 0, 55))
    embed.add_field(name="Status", value="ON" if enabled else "OFF", inline=True)
    embed.add_field(name="Messages / window", value=f"{get_guild_setting(ctx.guild, 'antispam_message_limit', 3)} / {get_guild_setting(ctx.guild, 'antispam_window_seconds', 4)}s", inline=True)
    embed.add_field(name="Repeated messages", value=str(get_guild_setting(ctx.guild, "antispam_duplicate_limit", 3)), inline=True)
    embed.add_field(name="Mention limit", value=str(get_guild_setting(ctx.guild, "antispam_mention_limit", 6)), inline=True)
    embed.add_field(name="Timeout", value=f"{get_guild_setting(ctx.guild, 'antispam_timeout_seconds', 300)}s", inline=True)
    await ctx.reply(embed=embed, mention_author=False)

@bot.command(name="antilink")
@commands.guild_only()
async def antilink_command(ctx, mode: str = None, *, argument: str = None):
    """Toggle per-server link filtering and manage allowed domains/exemptions."""
    if not has_admin_access(ctx.author):
        return await ctx.reply("Bot staff or server administrators only.", mention_author=False)
    mode = (mode or "status").lower()
    if mode in ("on", "enable", "enabled"):
        set_guild_setting(ctx.guild, "antilink_enabled", True)
        description = f"Anti-link is now **ON** for **{ctx.guild.name}**."
    elif mode in ("off", "disable", "disabled"):
        set_guild_setting(ctx.guild, "antilink_enabled", False)
        description = f"Anti-link is now **OFF** for **{ctx.guild.name}**."
    elif mode in ("allow", "whitelist-add") and argument:
        domain = argument.lower().strip().replace("https://", "").replace("http://", "").split("/", 1)[0].removeprefix("www.")
        if not re.fullmatch(r"[a-z0-9.-]+", domain):
            return await ctx.reply("Enter a domain such as `example.com`.", mention_author=False)
        settings = guild_security_settings(ctx.guild)
        domains = settings.setdefault("antilink_whitelist", [])
        if domain not in domains:
            domains.append(domain)
        save_config()
        description = f"Allowed domain added: `{domain}`."
    elif mode in ("deny", "whitelist-remove") and argument:
        settings = guild_security_settings(ctx.guild)
        domains = settings.setdefault("antilink_whitelist", [])
        domain = argument.lower().strip().replace("https://", "").replace("http://", "").split("/", 1)[0].removeprefix("www.")
        settings["antilink_whitelist"] = [d for d in domains if d.lower() != domain]
        save_config()
        description = f"Allowed domain removed: `{domain}`."
    elif mode == "exempt" and argument:
        try: user_id = int(argument.strip(" <@!>"))
        except ValueError: return await ctx.reply("Mention a user or provide a user ID.", mention_author=False)
        settings = guild_security_settings(ctx.guild)
        ids = settings.setdefault("antilink_exempt_ids", [])
        if user_id not in ids: ids.append(user_id)
        save_config()
        description = f"User `{user_id}` is exempt from anti-link in this server."
    elif mode == "unexempt" and argument:
        try: user_id = int(argument.strip(" <@!>"))
        except ValueError: return await ctx.reply("Mention a user or provide a user ID.", mention_author=False)
        settings = guild_security_settings(ctx.guild)
        settings["antilink_exempt_ids"] = [x for x in settings.get("antilink_exempt_ids", []) if str(x) != str(user_id)]
        save_config()
        description = f"User `{user_id}` is no longer exempt from anti-link in this server."
    elif mode == "status":
        description = "Use `antilink on/off/status`, `antilink allow <domain>`, `antilink deny <domain>`, `antilink exempt @user`, or `antilink unexempt @user`."
    else:
        description = "Invalid option. Use `antilink on/off/status`, `allow <domain>`, `deny <domain>`, `exempt @user`, or `unexempt @user`."
    settings = guild_security_settings(ctx.guild)
    enabled = bool(settings.get("antilink_enabled", False))
    domains = settings.get("antilink_whitelist", [])
    embed = feature_embed("Anti-Link", description, color=discord.Color.from_rgb(35, 0, 55))
    embed.add_field(name="Status", value="ON" if enabled else "OFF", inline=True)
    embed.add_field(name="Allowed domains", value=", ".join(f"`{d}`" for d in domains[:15]) or "None", inline=False)
    await ctx.reply(embed=embed, mention_author=False)


# ==================== KANADE UPDATE: WHITELIST ROLES / TICKETS / AUTORESPONDER / MUSIC ====================
# Everything below is additive. Existing commands, listeners and security systems above are unchanged,
# apart from the small hooks noted in the update summary (role-aware is_whitelisted, !nplaying, help pages).
import io
import math
import random
import shutil
import tempfile
from urllib.parse import urlparse

try:
    import yt_dlp
except Exception:  # yt-dlp is optional; only music commands need it.
    yt_dlp = None
    logger.warning("yt-dlp is not installed. Music commands will report that playback is unavailable.")


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# WHITELIST ROLES
# ---------------------------------------------------------------------------
@whitelist_command.command(name="roleadd", aliases=["addrole", "ra"])
@commands.guild_only()
async def whitelist_role_add(ctx, role: discord.Role):
    if not await manager_check(ctx):
        return
    if role.is_default():
        return await ctx.reply("The @everyone role cannot be whitelisted.", mention_author=False)
    mapping = config.setdefault("guild_whitelist_roles", {})
    role_ids = mapping.setdefault(str(ctx.guild.id), [])
    if role.id in role_ids:
        return await ctx.reply("That role is already whitelisted.", mention_author=False)
    role_ids.append(role.id)
    save_config()
    await send_log(
        ctx.guild,
        "▸ Server Whitelist Added",
        f"Role {role.mention} (`{role.id}`) added by {ctx.author.mention} (`{ctx.author.id}`). "
        "Members holding this role are now whitelisted.",
    )
    await ctx.reply("Role added to whitelist. Members with this role are now whitelisted.", mention_author=False)


@whitelist_command.command(name="roleremove", aliases=["removerole", "rr"])
@commands.guild_only()
async def whitelist_role_remove(ctx, role: discord.Role):
    if not await manager_check(ctx):
        return
    mapping = config.setdefault("guild_whitelist_roles", {})
    guild_key = str(ctx.guild.id)
    role_ids = mapping.get(guild_key, [])
    if role.id not in role_ids:
        return await ctx.reply("That role is not whitelisted.", mention_author=False)
    role_ids.remove(role.id)
    if not role_ids:
        mapping.pop(guild_key, None)
    save_config()
    await send_log(
        ctx.guild,
        "▸ Server Whitelist Removed",
        f"Role {role.mention} (`{role.id}`) removed by {ctx.author.mention} (`{ctx.author.id}`).",
    )
    await ctx.reply("Role removed from whitelist.", mention_author=False)


def _whitelist_roles_embed(guild):
    role_ids = [int(x) for x in config.get("guild_whitelist_roles", {}).get(str(guild.id), []) if str(x).isdigit()]
    embed = StyledEmbed(
        title="Kanade | Whitelisted Roles",
        description=f"Whitelisted roles in **{guild.name}**",
        color=discord.Color.from_rgb(128, 0, 255),
    )
    if not role_ids:
        embed.add_field(name="Roles", value="No roles are whitelisted.", inline=False)
    else:
        lines = [f"`{i}.` <@&{rid}> (`{rid}`)" for i, rid in enumerate(role_ids, start=1)]
        text = "\n".join(lines)
        if len(text) > 1000:
            text = text[:950].rsplit("\n", 1)[0] + "\n\nList shortened due to Discord's embed limit."
        embed.add_field(name="Roles", value=text, inline=False)
    embed.add_field(name="Total", value=str(len(role_ids)), inline=False)
    embed.set_footer(text="Kanade")
    return embed


@whitelist_command.command(name="rolelist", aliases=["roles", "rl"])
@commands.guild_only()
async def whitelist_role_list(ctx):
    if not await manager_check(ctx):
        return
    await ctx.reply(embed=_whitelist_roles_embed(ctx.guild), mention_author=False)


@bot.listen("on_guild_role_delete")
async def kanade_whitelist_role_cleanup(role):
    """Drop deleted roles from the whitelist so stale IDs never linger."""
    mapping = config.get("guild_whitelist_roles", {})
    key = str(role.guild.id)
    if isinstance(mapping, dict) and role.id in mapping.get(key, []):
        mapping[key].remove(role.id)
        if not mapping[key]:
            mapping.pop(key, None)
        save_config()


@bot.tree.command(name="whitelistrole", description="Add, remove, or list whitelisted roles")
@app_commands.describe(action="What to do", role="Role to add or remove")
@app_commands.choices(action=[
    app_commands.Choice(name="add", value="add"),
    app_commands.Choice(name="remove", value="remove"),
    app_commands.Choice(name="list", value="list"),
])
async def slash_whitelist_role(interaction: discord.Interaction, action: app_commands.Choice[str], role: discord.Role = None):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
    if not has_admin_access(interaction.user):
        return await interaction.response.send_message("Administrator access required.", ephemeral=True)
    mapping = config.setdefault("guild_whitelist_roles", {})
    guild_key = str(guild.id)
    role_ids = mapping.get(guild_key, [])
    if action.value == "list":
        return await interaction.response.send_message(embed=_whitelist_roles_embed(guild), ephemeral=True)
    if role is None:
        return await interaction.response.send_message("Select a role for the add or remove action.", ephemeral=True)
    if action.value == "add":
        if role.is_default():
            return await interaction.response.send_message("The @everyone role cannot be whitelisted.", ephemeral=True)
        if role.id in role_ids:
            return await interaction.response.send_message("That role is already whitelisted.", ephemeral=True)
        role_ids.append(role.id)
        mapping[guild_key] = role_ids
        save_config()
        await send_log(guild, "▸ Server Whitelist Added",
                       f"Role {role.mention} (`{role.id}`) added by {interaction.user.mention} (`{interaction.user.id}`).")
        return await interaction.response.send_message("Role added to whitelist.", ephemeral=True)
    if role.id not in role_ids:
        return await interaction.response.send_message("That role is not whitelisted.", ephemeral=True)
    role_ids.remove(role.id)
    if role_ids:
        mapping[guild_key] = role_ids
    else:
        mapping.pop(guild_key, None)
    save_config()
    await send_log(guild, "▸ Server Whitelist Removed",
                   f"Role {role.mention} (`{role.id}`) removed by {interaction.user.mention} (`{interaction.user.id}`).")
    await interaction.response.send_message("Role removed from whitelist.", ephemeral=True)


# ---------------------------------------------------------------------------
# TICKETS (TicketKing-style: panels, types, claim, priority, transcripts, feedback, auto-close)
# ---------------------------------------------------------------------------
TICKET_PRIORITIES = ("low", "normal", "high", "urgent")
TICKET_TOGGLES = {
    "claim": ("claim", "Claim button and claiming"),
    "transcripts": ("transcripts", "Transcripts on close"),
    "dm": ("dm_transcript", "DM transcript to the ticket opener"),
    "feedback": ("feedback", "Feedback rating request after close"),
    "reason": ("ask_reason", "Ask for a subject/details when opening"),
}


class TicketError(Exception):
    """Raised for user-facing ticket problems."""


def _ticket_settings(guild_id):
    root = config.setdefault("ticket_settings", {})
    if not isinstance(root, dict):
        root = {}
        config["ticket_settings"] = root
    settings = root.setdefault(str(guild_id), {})
    defaults = {
        "category_id": 0,
        "closed_category_id": 0,
        "log_channel_id": 0,
        "support_role_ids": [],
        "max_open": 1,
        "counter": 0,
        "name_format": "ticket-{number}",
        "welcome": "Thanks for opening a ticket, {user}! A staff member will be with you shortly.",
        "panel_title": "Support Tickets",
        "panel_description": "Need help? Press the button below to open a ticket and our staff will assist you.",
        "button_label": "Open Ticket",
        "types": [],
        "claim": True,
        "transcripts": True,
        "dm_transcript": True,
        "feedback": True,
        "ask_reason": True,
        "autoclose_hours": 0,
        "blacklist": [],
        "opened_total": 0,
        "closed_total": 0,
        "rating_total": 0,
        "rating_count": 0,
    }
    for key, value in defaults.items():
        if key not in settings:
            settings[key] = copy.deepcopy(value)
    return settings


def _ticket_records():
    root = config.setdefault("tickets", {})
    if not isinstance(root, dict):
        root = {}
        config["tickets"] = root
    return root


def _ticket_record(channel_id):
    return _ticket_records().get(str(channel_id))


def _ticket_is_staff(member, settings=None):
    if member is None:
        return False
    if has_admin_access(member):
        return True
    settings = settings or _ticket_settings(member.guild.id)
    support = {int(x) for x in settings.get("support_role_ids", []) if str(x).isdigit()}
    return any(role.id in support for role in getattr(member, "roles", []))


def _ticket_require(channel, actor, *, staff=False, owner_ok=False, system=False):
    record = _ticket_record(channel.id)
    if record is None:
        raise TicketError("This channel is not a ticket.")
    settings = _ticket_settings(channel.guild.id)
    if system or _ticket_is_staff(actor, settings):
        return record, settings
    if owner_ok and actor.id == record.get("owner_id"):
        return record, settings
    raise TicketError("Only ticket staff can do that." if staff else "You can't manage this ticket.")


def _ticket_format(text, member, guild, ticket_type=None):
    mention = member.mention if member else "there"
    name = member.display_name if member else "there"
    return (
        str(text)
        .replace("{user}", mention)
        .replace("{user.name}", name)
        .replace("{server}", guild.name)
        .replace("{type}", ticket_type or "Support")
    )[:2000]


def _ticket_channel_name(settings, member, ticket_type, number):
    name = (
        (settings.get("name_format") or "ticket-{number}")
        .replace("{number}", f"{number:04d}")
        .replace("{user}", member.name)
        .replace("{type}", ticket_type or "ticket")
    )
    name = re.sub(r"[^a-z0-9\-_]+", "-", name.lower()).strip("-")
    return (name or f"ticket-{number:04d}")[:90]


def _ticket_find_type(settings, label):
    if not label:
        return None
    for item in settings.get("types", []):
        if item.get("label", "").lower() == str(label).lower():
            return item
    return None


def _ticket_embed(guild, record, settings=None):
    settings = settings or _ticket_settings(guild.id)
    owner = guild.get_member(record["owner_id"])
    description = _ticket_format(settings.get("welcome", ""), owner, guild, record.get("type"))
    embed = StyledEmbed(
        title=f"Ticket #{record['number']:04d}",
        description=description,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Opened by", value=owner.mention if owner else f"<@{record['owner_id']}>", inline=True)
    embed.add_field(name="Type", value=record.get("type") or "Support", inline=True)
    embed.add_field(name="Priority", value=str(record.get("priority", "normal")).title(), inline=True)
    claimed = record.get("claimed_by")
    embed.add_field(name="Claimed by", value=f"<@{claimed}>" if claimed else "Unclaimed", inline=True)
    if record.get("subject"):
        embed.add_field(name="Subject", value=record["subject"][:1024], inline=False)
    if record.get("details"):
        embed.add_field(name="Details", value=record["details"][:1024], inline=False)
    embed.set_footer(text=f"Ticket #{record['number']:04d}")
    return embed


async def _ticket_refresh_message(channel, record):
    message_id = record.get("message_id")
    if not message_id:
        return
    try:
        message = await channel.fetch_message(int(message_id))
        await message.edit(embed=_ticket_embed(channel.guild, record))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


async def _ticket_build_transcript(channel, record):
    rows = []
    async for message in channel.history(limit=2000, oldest_first=True):
        stamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        author = html.escape(f"{message.author.display_name} ({message.author.id})")
        content = html.escape(message.clean_content or "").replace("\n", "<br>")
        extras = []
        for attachment in message.attachments:
            extras.append(f'<div class="att">Attachment: <a href="{html.escape(attachment.url)}">{html.escape(attachment.filename)}</a></div>')
        for embed in message.embeds:
            bits = [part for part in (embed.title, embed.description) if part]
            if bits:
                extras.append(f'<div class="emb">{html.escape(" - ".join(bits)[:1500]).replace(chr(10), "<br>")}</div>')
        rows.append(
            f'<div class="msg"><span class="who">{author}</span> <span class="ts">{stamp}</span>'
            f'<div class="body">{content or "&nbsp;"}</div>{"".join(extras)}</div>'
        )
    title = html.escape(f"Ticket #{record['number']:04d} - {channel.guild.name}")
    document = (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>" + title + "</title><style>"
        "body{font-family:Arial,sans-serif;background:#1e1f22;color:#dbdee1;margin:0;padding:24px}"
        "h1{font-size:20px}.msg{padding:8px 0;border-bottom:1px solid #2b2d31}.who{font-weight:bold;color:#fff}"
        ".ts{color:#949ba4;font-size:12px;margin-left:8px}.body{margin-top:4px;word-wrap:break-word}"
        ".att,.emb{margin-top:4px;font-size:13px;color:#b5bac1}a{color:#00a8fc}"
        "</style></head><body><h1>" + title + "</h1>"
        f"<p>Opened by {html.escape(str(record.get('owner_id')))} | Type: {html.escape(str(record.get('type') or 'Support'))}</p>"
        + "".join(rows) + "</body></html>"
    )
    return document.encode("utf-8")


def _ticket_transcript_file(data, record):
    return discord.File(io.BytesIO(data), filename=f"transcript-{record['number']:04d}.html")


async def _ticket_archive(channel, record, actor, reason):
    """Send the transcript to the log channel and (optionally) DM the opener."""
    guild = channel.guild
    settings = _ticket_settings(guild.id)
    data = None
    if settings.get("transcripts", True):
        try:
            data = await _ticket_build_transcript(channel, record)
        except Exception:
            logger.exception("Could not build ticket transcript")
    log_channel = guild.get_channel(int(settings.get("log_channel_id") or 0))
    claimed = record.get("claimed_by")
    embed = StyledEmbed(title="Ticket Closed", timestamp=discord.utils.utcnow())
    embed.add_field(name="Ticket", value=f"#{record['number']:04d} ({channel.name})", inline=True)
    embed.add_field(name="Opened by", value=f"<@{record['owner_id']}>", inline=True)
    embed.add_field(name="Closed by", value=actor.mention, inline=True)
    embed.add_field(name="Claimed by", value=f"<@{claimed}>" if claimed else "Nobody", inline=True)
    embed.add_field(name="Type", value=record.get("type") or "Support", inline=True)
    embed.add_field(name="Reason", value=(reason or "No reason provided")[:1024], inline=False)
    if isinstance(log_channel, discord.TextChannel) and log_channel.permissions_for(guild.me).send_messages:
        try:
            await log_channel.send(embed=embed, files=[_ticket_transcript_file(data, record)] if data else [])
        except discord.HTTPException:
            logger.warning("Could not send ticket log in guild %s", guild.id)
    if settings.get("dm_transcript", True):
        owner = guild.get_member(record["owner_id"])
        if owner is not None:
            dm_embed = StyledEmbed(
                title="Your ticket was closed",
                description=f"Your ticket #{record['number']:04d} in **{guild.name}** has been closed.\n**Reason:** {(reason or 'No reason provided')[:900]}",
                timestamp=discord.utils.utcnow(),
            )
            view = TicketFeedbackView(guild.id, record["number"]) if settings.get("feedback", True) else None
            try:
                await owner.send(embed=dm_embed, files=[_ticket_transcript_file(data, record)] if data else [], view=view)
            except (discord.Forbidden, discord.HTTPException):
                pass


async def ticket_close(channel, actor, reason=None, *, system=False):
    record, settings = _ticket_require(channel, actor, owner_ok=True, system=system)
    if record.get("status") != "open":
        raise TicketError("This ticket is already closed.")
    reason = (reason or "No reason provided").strip()[:500]
    record.update(status="closed", closed_by=actor.id, closed_at=_now_iso(), close_reason=reason)
    settings["closed_total"] = int(settings.get("closed_total", 0)) + 1
    save_config()
    guild = channel.guild
    closed_embed = StyledEmbed(
        title="Ticket Closed",
        description=f"Closed by {actor.mention}.\n**Reason:** {reason}\n\nStaff can reopen or delete this ticket with the buttons below.",
        timestamp=discord.utils.utcnow(),
    )
    try:
        await channel.send(embed=closed_embed, view=TicketClosedView())
    except discord.HTTPException:
        pass
    await _ticket_archive(channel, record, actor, reason)
    # Remove the opener's (and added users') access after the transcript is captured.
    revoke = [record.get("owner_id")] + list(record.get("added", []))
    for user_id in revoke:
        member = guild.get_member(int(user_id)) if user_id else None
        if member is not None and not _ticket_is_staff(member, settings):
            try:
                await channel.set_permissions(member, overwrite=None, reason="Ticket closed")
            except (discord.Forbidden, discord.HTTPException):
                pass
    closed_category = guild.get_channel(int(settings.get("closed_category_id") or 0))
    if isinstance(closed_category, discord.CategoryChannel):
        try:
            await channel.edit(category=closed_category, sync_permissions=False, reason="Ticket closed")
        except (discord.Forbidden, discord.HTTPException):
            pass


async def ticket_reopen(channel, actor):
    record, settings = _ticket_require(channel, actor, staff=True)
    if record.get("status") == "open":
        raise TicketError("This ticket is already open.")
    guild = channel.guild
    record.update(status="open", closed_by=None, closed_at=None, close_reason=None)
    save_config()
    owner = guild.get_member(record["owner_id"])
    if owner is not None:
        try:
            await channel.set_permissions(owner, view_channel=True, send_messages=True, read_message_history=True,
                                          attach_files=True, embed_links=True, reason="Ticket reopened")
        except (discord.Forbidden, discord.HTTPException):
            pass
    for user_id in record.get("added", []):
        member = guild.get_member(int(user_id))
        if member is not None:
            try:
                await channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True,
                                              attach_files=True, embed_links=True, reason="Ticket reopened")
            except (discord.Forbidden, discord.HTTPException):
                pass
    category = guild.get_channel(int(settings.get("category_id") or 0))
    if isinstance(category, discord.CategoryChannel) and channel.category_id != category.id:
        try:
            await channel.edit(category=category, sync_permissions=False, reason="Ticket reopened")
        except (discord.Forbidden, discord.HTTPException):
            pass
    return StyledEmbed(title="Ticket Reopened", description=f"Reopened by {actor.mention}.", timestamp=discord.utils.utcnow())


async def ticket_delete(channel, actor):
    record, settings = _ticket_require(channel, actor, staff=True)
    if record.get("status") == "open":
        # Closing was skipped, so archive the transcript before the channel disappears.
        await _ticket_archive(channel, record, actor, "Ticket deleted")
        settings["closed_total"] = int(settings.get("closed_total", 0)) + 1
    try:
        await channel.send(embed=StyledEmbed(title="Ticket Deleted", description="This channel will be deleted in 5 seconds."))
    except discord.HTTPException:
        pass
    await asyncio.sleep(5)
    _ticket_records().pop(str(channel.id), None)
    save_config()
    try:
        await channel.delete(reason=f"Ticket deleted by {actor}")
    except (discord.Forbidden, discord.HTTPException):
        raise TicketError("I could not delete this channel. Check my Manage Channels permission.")


async def ticket_claim(channel, actor, *, target=None, unclaim=False):
    record, settings = _ticket_require(channel, actor, staff=True)
    if not settings.get("claim", True):
        raise TicketError("Claiming is disabled in this server.")
    if record.get("status") != "open":
        raise TicketError("This ticket is closed.")
    if unclaim:
        if not record.get("claimed_by"):
            raise TicketError("This ticket is not claimed.")
        if record["claimed_by"] != actor.id and not has_admin_access(actor):
            raise TicketError("Only the claimer or an administrator can unclaim this ticket.")
        record["claimed_by"] = None
        text = f"{actor.mention} released this ticket."
    else:
        new_owner = target or actor
        if not _ticket_is_staff(new_owner, settings):
            raise TicketError("The new claimer must be ticket staff.")
        if record.get("claimed_by") and record["claimed_by"] != actor.id and target is None and not has_admin_access(actor):
            raise TicketError(f"This ticket is already claimed by <@{record['claimed_by']}>.")
        record["claimed_by"] = new_owner.id
        text = f"{new_owner.mention} is now handling this ticket." if target is None else f"{actor.mention} transferred this ticket to {new_owner.mention}."
    save_config()
    await _ticket_refresh_message(channel, record)
    return StyledEmbed(title="Ticket Claimed" if not unclaim else "Ticket Released", description=text)


async def ticket_set_priority(channel, actor, level):
    record, _ = _ticket_require(channel, actor, staff=True)
    level = level.lower()
    if level not in TICKET_PRIORITIES:
        raise TicketError("Priority must be one of: " + ", ".join(TICKET_PRIORITIES) + ".")
    record["priority"] = level
    save_config()
    await _ticket_refresh_message(channel, record)
    return StyledEmbed(title="Ticket Updated", description=f"Priority set to **{level.title()}**.")


async def ticket_add_user(channel, actor, member):
    record, _ = _ticket_require(channel, actor, staff=True)
    if record.get("status") != "open":
        raise TicketError("This ticket is closed.")
    try:
        await channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True,
                                      attach_files=True, embed_links=True, reason=f"Added to ticket by {actor}")
    except (discord.Forbidden, discord.HTTPException):
        raise TicketError("I could not update permissions for that member.")
    added = record.setdefault("added", [])
    if member.id not in added and member.id != record.get("owner_id"):
        added.append(member.id)
    save_config()
    return StyledEmbed(title="Ticket Updated", description=f"{member.mention} was added to this ticket.")


async def ticket_remove_user(channel, actor, member):
    record, settings = _ticket_require(channel, actor, staff=True)
    if member.id == record.get("owner_id"):
        raise TicketError("You can't remove the ticket opener.")
    if _ticket_is_staff(member, settings):
        raise TicketError("You can't remove ticket staff.")
    try:
        await channel.set_permissions(member, overwrite=None, reason=f"Removed from ticket by {actor}")
    except (discord.Forbidden, discord.HTTPException):
        raise TicketError("I could not update permissions for that member.")
    if member.id in record.get("added", []):
        record["added"].remove(member.id)
    save_config()
    return StyledEmbed(title="Ticket Updated", description=f"{member.mention} was removed from this ticket.")


async def ticket_rename(channel, actor, name):
    _ticket_require(channel, actor, staff=True)
    cleaned = re.sub(r"[^a-z0-9\-_]+", "-", name.lower()).strip("-")[:90]
    if not cleaned:
        raise TicketError("Provide a valid channel name.")
    try:
        await channel.edit(name=cleaned, reason=f"Ticket renamed by {actor}")
    except (discord.Forbidden, discord.HTTPException):
        raise TicketError("I could not rename this channel (it may be rate limited).")
    return StyledEmbed(title="Ticket Updated", description=f"Renamed to `{cleaned}`.")


# ---------- Opening tickets ----------
async def _create_ticket(interaction, ticket_type, subject, details):
    guild = interaction.guild
    member = interaction.user
    settings = _ticket_settings(guild.id)
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    async def fail(text):
        await interaction.followup.send(text, ephemeral=True)

    if member.id in {int(x) for x in settings.get("blacklist", []) if str(x).isdigit()}:
        return await fail("You are blocked from opening tickets in this server.")
    records = _ticket_records()
    open_count = 0
    for cid, rec in list(records.items()):
        if rec.get("guild_id") != guild.id:
            continue
        if guild.get_channel(int(cid)) is None:
            records.pop(cid, None)
            continue
        if rec.get("owner_id") == member.id and rec.get("status") == "open":
            open_count += 1
    max_open = max(1, int(settings.get("max_open") or 1))
    if open_count >= max_open:
        return await fail(f"You already have {open_count} open ticket(s). Close one before opening another.")

    type_label = ticket_type.get("label") if ticket_type else None
    category = None
    for candidate in ((ticket_type or {}).get("category_id"), settings.get("category_id")):
        found = guild.get_channel(int(candidate or 0)) if candidate else None
        if isinstance(found, discord.CategoryChannel):
            category = found
            break

    settings["counter"] = int(settings.get("counter", 0)) + 1
    number = settings["counter"]
    base = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True,
                                       attach_files=True, embed_links=True)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True,
                                              attach_files=True, embed_links=True),
        member: base,
    }
    support_roles = []
    for role_id in settings.get("support_role_ids", []):
        role = guild.get_role(int(role_id))
        if role is not None:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True,
                                                           attach_files=True, embed_links=True, manage_messages=True)
            support_roles.append(role)
    try:
        channel = await guild.create_text_channel(
            name=_ticket_channel_name(settings, member, type_label, number),
            category=category,
            overwrites=overwrites,
            topic=f"Ticket #{number:04d} | Opened by {member} ({member.id})",
            reason=f"Ticket opened by {member}",
        )
    except discord.Forbidden:
        settings["counter"] = number - 1
        return await fail("I need the Manage Channels permission (and access to the ticket category) to create tickets.")
    except discord.HTTPException as exc:
        settings["counter"] = number - 1
        return await fail(f"Discord refused to create the ticket channel: {exc.text[:150]}")

    record = {
        "guild_id": guild.id,
        "owner_id": member.id,
        "number": number,
        "type": type_label,
        "subject": (subject or "").strip()[:100] or None,
        "details": (details or "").strip()[:1000] or None,
        "status": "open",
        "priority": "normal",
        "claimed_by": None,
        "added": [],
        "opened_at": _now_iso(),
    }
    settings["opened_total"] = int(settings.get("opened_total", 0)) + 1
    records[str(channel.id)] = record
    pings = " ".join([member.mention] + [role.mention for role in support_roles])
    try:
        message = await channel.send(
            content=pings,
            embed=_ticket_embed(guild, record, settings),
            view=TicketControlView(),
            allowed_mentions=discord.AllowedMentions(users=[member], roles=support_roles, everyone=False),
        )
        record["message_id"] = message.id
    except discord.HTTPException:
        logger.warning("Could not post the ticket welcome message in guild %s", guild.id)
    save_config()

    log_channel = guild.get_channel(int(settings.get("log_channel_id") or 0))
    if isinstance(log_channel, discord.TextChannel) and log_channel.permissions_for(guild.me).send_messages:
        log_embed = StyledEmbed(title="Ticket Opened", description=f"{member.mention} opened {channel.mention}.", timestamp=discord.utils.utcnow())
        log_embed.add_field(name="Type", value=type_label or "Support", inline=True)
        log_embed.add_field(name="Ticket", value=f"#{number:04d}", inline=True)
        if record["subject"]:
            log_embed.add_field(name="Subject", value=record["subject"], inline=False)
        try:
            await log_channel.send(embed=log_embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass
    await interaction.followup.send(f"Your ticket has been created: {channel.mention}", ephemeral=True)


class TicketOpenModal(discord.ui.Modal):
    def __init__(self, type_label=None):
        super().__init__(title="Open a Ticket")
        self.type_label = type_label
        self.subject = discord.ui.TextInput(label="Subject", max_length=100, placeholder="Briefly describe your issue")
        self.details = discord.ui.TextInput(label="Details", style=discord.TextStyle.paragraph, required=False,
                                            max_length=1000, placeholder="Add any extra information (optional)")
        self.add_item(self.subject)
        self.add_item(self.details)

    async def on_submit(self, interaction: discord.Interaction):
        settings = _ticket_settings(interaction.guild.id)
        await _create_ticket(interaction, _ticket_find_type(settings, self.type_label), self.subject.value, self.details.value)


async def _ticket_begin(interaction, ticket_type):
    if interaction.guild is None:
        return await interaction.response.send_message("Tickets can only be opened in a server.", ephemeral=True)
    settings = _ticket_settings(interaction.guild.id)
    if settings.get("ask_reason", True):
        return await interaction.response.send_modal(TicketOpenModal(ticket_type.get("label") if ticket_type else None))
    await _create_ticket(interaction, ticket_type, None, None)


class TicketOpenButtonView(discord.ui.View):
    def __init__(self, label="Open Ticket"):
        super().__init__(timeout=None)
        self.open_button.label = (label or "Open Ticket")[:80]

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.primary, custom_id="kanade:ticket:open")
    async def open_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _ticket_begin(interaction, None)


class TicketTypeSelect(discord.ui.Select):
    def __init__(self, types=None):
        options = []
        for item in (types or [])[:25]:
            kwargs = {"label": item["label"][:100], "value": item["label"][:100]}
            if item.get("description"):
                kwargs["description"] = item["description"][:100]
            if item.get("emoji"):
                kwargs["emoji"] = item["emoji"]
            options.append(discord.SelectOption(**kwargs))
        if not options:
            options = [discord.SelectOption(label="Open a ticket", value="Open a ticket")]
        super().__init__(placeholder="Select a ticket type", min_values=1, max_values=1,
                         options=options, custom_id="kanade:ticket:type")

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild is None:
            return await interaction.response.send_message("Tickets can only be opened in a server.", ephemeral=True)
        settings = _ticket_settings(interaction.guild.id)
        await _ticket_begin(interaction, _ticket_find_type(settings, self.values[0]))
        # Re-post the idle select so the same option can be picked again.
        try:
            await interaction.message.edit(view=TicketTypeSelectView(settings.get("types", [])))
        except (discord.HTTPException, AttributeError):
            pass


class TicketTypeSelectView(discord.ui.View):
    def __init__(self, types=None):
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect(types))


class TicketCloseModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Close Ticket")
        self.reason = discord.ui.TextInput(label="Reason", style=discord.TextStyle.paragraph, max_length=500,
                                           placeholder="Why is this ticket being closed?", required=False)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            await ticket_close(interaction.channel, interaction.user, self.reason.value)
            await interaction.followup.send("Closing ticket.", ephemeral=True)
        except TicketError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)


class TicketCloseConfirmView(discord.ui.View):
    def __init__(self, actor_id):
        super().__init__(timeout=60)
        self.actor_id = actor_id

    @discord.ui.button(label="Confirm Close", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.actor_id:
            return await interaction.response.send_message("Only the person who pressed Close can confirm.", ephemeral=True)
        await interaction.response.edit_message(content="Closing ticket...", view=None)
        try:
            await ticket_close(interaction.channel, interaction.user)
        except TicketError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled.", view=None)


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="kanade:ticket:close")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            _ticket_require(interaction.channel, interaction.user, owner_ok=True)
        except TicketError as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)
        await interaction.response.send_message("Are you sure you want to close this ticket?",
                                                view=TicketCloseConfirmView(interaction.user.id), ephemeral=True)

    @discord.ui.button(label="Close with Reason", style=discord.ButtonStyle.secondary, custom_id="kanade:ticket:closereason")
    async def close_reason_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            _ticket_require(interaction.channel, interaction.user, owner_ok=True)
        except TicketError as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)
        await interaction.response.send_modal(TicketCloseModal())

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, custom_id="kanade:ticket:claim")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            embed = await ticket_claim(interaction.channel, interaction.user)
        except TicketError as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)
        await interaction.response.send_message(embed=embed)


class TicketClosedView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Reopen", style=discord.ButtonStyle.success, custom_id="kanade:ticket:reopen")
    async def reopen_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            embed = await ticket_reopen(interaction.channel, interaction.user)
        except TicketError as exc:
            return await interaction.followup.send(str(exc), ephemeral=True)
        await interaction.followup.send(embed=embed)

    @discord.ui.button(label="Transcript", style=discord.ButtonStyle.secondary, custom_id="kanade:ticket:transcript")
    async def transcript_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            record, _ = _ticket_require(interaction.channel, interaction.user, staff=True)
        except TicketError as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        data = await _ticket_build_transcript(interaction.channel, record)
        await interaction.followup.send(file=_ticket_transcript_file(data, record), ephemeral=True)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="kanade:ticket:delete")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            _ticket_require(interaction.channel, interaction.user, staff=True)
        except TicketError as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)
        await interaction.response.defer()
        try:
            await ticket_delete(interaction.channel, interaction.user)
        except TicketError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)


class TicketFeedbackView(discord.ui.View):
    """Rating buttons sent with the DM transcript. Not persistent across restarts."""
    def __init__(self, guild_id, number):
        super().__init__(timeout=86400)
        self.guild_id = guild_id
        self.number = number
        for score in range(1, 6):
            button = discord.ui.Button(label=str(score), style=discord.ButtonStyle.secondary)
            button.callback = self._make_callback(score)
            self.add_item(button)

    def _make_callback(self, score):
        async def callback(interaction: discord.Interaction):
            await interaction.response.edit_message(content=f"Thanks for rating your ticket {score}/5.", view=None)
            guild = bot.get_guild(self.guild_id)
            if guild is None:
                return
            settings = _ticket_settings(guild.id)
            settings["rating_total"] = int(settings.get("rating_total", 0)) + score
            settings["rating_count"] = int(settings.get("rating_count", 0)) + 1
            save_config()
            log_channel = guild.get_channel(int(settings.get("log_channel_id") or 0))
            if isinstance(log_channel, discord.TextChannel) and log_channel.permissions_for(guild.me).send_messages:
                try:
                    await log_channel.send(
                        embed=StyledEmbed(title="Ticket Feedback",
                                          description=f"{interaction.user.mention} rated ticket #{self.number:04d}: **{score}/5**"),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    pass
        return callback


@bot.listen("on_guild_channel_delete")
async def kanade_ticket_channel_cleanup(channel):
    records = _ticket_records()
    if str(channel.id) in records:
        records.pop(str(channel.id), None)
        save_config()


@tasks.loop(minutes=15)
async def ticket_autoclose_loop():
    now = discord.utils.utcnow()
    for cid, record in list(_ticket_records().items()):
        if record.get("status") != "open":
            continue
        guild = bot.get_guild(record.get("guild_id") or 0)
        if guild is None:
            continue
        hours = int(_ticket_settings(guild.id).get("autoclose_hours") or 0)
        channel = guild.get_channel(int(cid))
        if hours <= 0 or not isinstance(channel, discord.TextChannel):
            continue
        if channel.last_message_id:
            last = discord.utils.snowflake_time(channel.last_message_id)
        else:
            try:
                last = datetime.fromisoformat(record.get("opened_at"))
            except (TypeError, ValueError):
                continue
        if now - last > timedelta(hours=hours):
            try:
                await ticket_close(channel, guild.me, f"Closed automatically after {hours}h of inactivity.", system=True)
            except Exception:
                logger.exception("Ticket auto-close failed")


@ticket_autoclose_loop.before_loop
async def before_ticket_autoclose_loop():
    await bot.wait_until_ready()


# ---------- Ticket commands ----------
async def _ticket_reply_error(ctx, exc):
    await ctx.reply(str(exc), mention_author=False)


@bot.group(name="ticket", aliases=["tickets"], invoke_without_command=True)
@commands.guild_only()
async def ticket_group(ctx):
    p = ctx.clean_prefix
    description = (
        "**Setup (admins)**\n"
        f"`{p}ticket setup` — Create the ticket categories and log channel\n"
        f"`{p}ticket panel [#channel]` — Post the ticket panel\n"
        f"`{p}ticket config` — View ticket settings\n"
        f"`{p}ticket category <#category>` / `{p}ticket closedcategory <#category>`\n"
        f"`{p}ticket logs <#channel>` — Set the log/transcript channel\n"
        f"`{p}ticket support add|remove|list [@role]` — Manage support roles\n"
        f"`{p}ticket type add|remove|list` — Manage ticket types (dropdown panel)\n"
        f"`{p}ticket max <number>` — Open tickets allowed per user\n"
        f"`{p}ticket format <text>` — Channel name format ({{number}}, {{user}}, {{type}})\n"
        f"`{p}ticket welcome <text>` — Welcome message ({{user}}, {{server}}, {{type}})\n"
        f"`{p}ticket panelset title|description|button <text>` — Customize the panel\n"
        f"`{p}ticket toggle claim|transcripts|dm|feedback|reason` — Toggle features\n"
        f"`{p}ticket autoclose <hours|off>` — Close inactive tickets\n"
        f"`{p}ticket blacklist add|remove|list [@user]` — Block users from tickets\n"
        f"`{p}ticket stats` — Ticket statistics\n\n"
        "**Inside a ticket (staff)**\n"
        f"`{p}close [reason]`, `{p}claim`, `{p}unclaim`, `{p}ticket add|remove @user`, "
        f"`{p}ticket rename <name>`, `{p}ticket priority <low|normal|high|urgent>`, "
        f"`{p}ticket transfer @staff`, `{p}ticket transcript`, `{p}ticket reopen`, `{p}ticket delete`, `{p}ticket info`"
    )
    await ctx.reply(embed=feature_embed("Ticket Commands", description), mention_author=False)


def _ticket_channel_arg_error(ctx):
    return ctx.reply("Run this inside a ticket channel.", mention_author=False)


@ticket_group.command(name="setup")
@commands.guild_only()
async def ticket_setup(ctx):
    if not await manager_check(ctx):
        return
    settings = _ticket_settings(ctx.guild.id)
    guild = ctx.guild
    created = []
    try:
        category = guild.get_channel(int(settings.get("category_id") or 0))
        if not isinstance(category, discord.CategoryChannel):
            category = await guild.create_category("Tickets", reason="Ticket setup")
            settings["category_id"] = category.id
            created.append(f"Category: {category.name}")
        closed = guild.get_channel(int(settings.get("closed_category_id") or 0))
        if not isinstance(closed, discord.CategoryChannel):
            closed = await guild.create_category("Closed Tickets", overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=False)}, reason="Ticket setup")
            settings["closed_category_id"] = closed.id
            created.append(f"Category: {closed.name}")
        log = guild.get_channel(int(settings.get("log_channel_id") or 0))
        if not isinstance(log, discord.TextChannel):
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, embed_links=True),
            }
            for role_id in settings.get("support_role_ids", []):
                role = guild.get_role(int(role_id))
                if role:
                    overwrites[role] = discord.PermissionOverwrite(view_channel=True, read_message_history=True)
            log = await guild.create_text_channel("ticket-logs", overwrites=overwrites, reason="Ticket setup")
            settings["log_channel_id"] = log.id
            created.append(f"Channel: {log.mention}")
    except (discord.Forbidden, discord.HTTPException) as exc:
        return await ctx.reply(f"Setup failed. I need Manage Channels. ({str(exc)[:120]})", mention_author=False)
    save_config()
    text = ("Created:\n" + "\n".join(created) if created else "Everything was already configured.") + \
        f"\n\nNext: add support roles with `{ctx.clean_prefix}ticket support add @role`, then post a panel with `{ctx.clean_prefix}ticket panel`."
    await ctx.reply(embed=feature_embed("Ticket Setup Complete", text), mention_author=False)


@ticket_group.command(name="panel")
@commands.guild_only()
async def ticket_panel(ctx, channel: discord.TextChannel = None):
    if not await manager_check(ctx):
        return
    channel = channel or ctx.channel
    settings = _ticket_settings(ctx.guild.id)
    if not channel.permissions_for(ctx.guild.me).send_messages:
        return await ctx.reply("I can't send messages in that channel.", mention_author=False)
    embed = StyledEmbed(title=settings["panel_title"], description=settings["panel_description"])
    types = settings.get("types", [])
    view = TicketTypeSelectView(types) if len(types) >= 2 else TicketOpenButtonView(settings.get("button_label"))
    try:
        await channel.send(embed=embed, view=view)
    except discord.HTTPException as exc:
        return await ctx.reply(f"Could not post the panel: {str(exc)[:150]}", mention_author=False)
    if channel.id != ctx.channel.id:
        await ctx.reply(f"Ticket panel posted in {channel.mention}.", mention_author=False)


@ticket_group.command(name="config", aliases=["settings"])
@commands.guild_only()
async def ticket_config(ctx):
    if not await manager_check(ctx):
        return
    s = _ticket_settings(ctx.guild.id)
    def chan(value):
        return f"<#{value}>" if value else "Not set"
    roles = " ".join(f"<@&{r}>" for r in s["support_role_ids"]) or "None"
    embed = feature_embed("Ticket Settings", f"Configuration for **{ctx.guild.name}**")
    embed.add_field(name="Category", value=chan(s["category_id"]), inline=True)
    embed.add_field(name="Closed category", value=chan(s["closed_category_id"]), inline=True)
    embed.add_field(name="Log channel", value=chan(s["log_channel_id"]), inline=True)
    embed.add_field(name="Support roles", value=roles[:1000], inline=False)
    embed.add_field(name="Max open per user", value=str(s["max_open"]), inline=True)
    embed.add_field(name="Name format", value=f"`{s['name_format']}`", inline=True)
    embed.add_field(name="Auto-close", value=f"{s['autoclose_hours']}h" if s["autoclose_hours"] else "Off", inline=True)
    embed.add_field(name="Types", value=", ".join(t["label"] for t in s["types"]) or "None (single button panel)", inline=False)
    flags = "\n".join(f"{label}: {'On' if s.get(key, True) else 'Off'}" for key, label in TICKET_TOGGLES.values())
    embed.add_field(name="Features", value=flags, inline=False)
    await ctx.reply(embed=embed, mention_author=False)


async def _ticket_set_category(ctx, key, category):
    if not await manager_check(ctx):
        return
    _ticket_settings(ctx.guild.id)[key] = category.id
    save_config()
    await ctx.reply(f"Saved: **{category.name}**.", mention_author=False)


@ticket_group.command(name="category")
@commands.guild_only()
async def ticket_category(ctx, category: discord.CategoryChannel):
    await _ticket_set_category(ctx, "category_id", category)


@ticket_group.command(name="closedcategory", aliases=["closedcat"])
@commands.guild_only()
async def ticket_closed_category(ctx, category: discord.CategoryChannel):
    await _ticket_set_category(ctx, "closed_category_id", category)


@ticket_group.command(name="logs", aliases=["log"])
@commands.guild_only()
async def ticket_logs(ctx, channel: discord.TextChannel):
    if not await manager_check(ctx):
        return
    _ticket_settings(ctx.guild.id)["log_channel_id"] = channel.id
    save_config()
    await ctx.reply(f"Ticket logs and transcripts will be sent to {channel.mention}.", mention_author=False)


@ticket_group.group(name="support", invoke_without_command=True)
@commands.guild_only()
async def ticket_support(ctx):
    await ctx.reply(f"Usage: `{ctx.clean_prefix}ticket support add @role`, `remove @role`, or `list`.", mention_author=False)


@ticket_support.command(name="add")
@commands.guild_only()
async def ticket_support_add(ctx, role: discord.Role):
    if not await manager_check(ctx):
        return
    if role.is_default():
        return await ctx.reply("The @everyone role cannot be a support role.", mention_author=False)
    ids = _ticket_settings(ctx.guild.id)["support_role_ids"]
    if role.id in ids:
        return await ctx.reply("That role is already a support role.", mention_author=False)
    ids.append(role.id)
    save_config()
    await ctx.reply(f"{role.mention} can now manage tickets.", mention_author=False, allowed_mentions=discord.AllowedMentions.none())


@ticket_support.command(name="remove")
@commands.guild_only()
async def ticket_support_remove(ctx, role: discord.Role):
    if not await manager_check(ctx):
        return
    ids = _ticket_settings(ctx.guild.id)["support_role_ids"]
    if role.id not in ids:
        return await ctx.reply("That role is not a support role.", mention_author=False)
    ids.remove(role.id)
    save_config()
    await ctx.reply(f"{role.mention} removed from support roles.", mention_author=False, allowed_mentions=discord.AllowedMentions.none())


@ticket_support.command(name="list")
@commands.guild_only()
async def ticket_support_list(ctx):
    if not await manager_check(ctx):
        return
    ids = _ticket_settings(ctx.guild.id)["support_role_ids"]
    text = "\n".join(f"<@&{r}>" for r in ids) or "No support roles set. Admins can always manage tickets."
    await ctx.reply(embed=feature_embed("Ticket Support Roles", text), mention_author=False)


@ticket_group.group(name="type", aliases=["types"], invoke_without_command=True)
@commands.guild_only()
async def ticket_type(ctx):
    await ctx.reply(f"Usage: `{ctx.clean_prefix}ticket type add <label> | [description] | [emoji]`, `remove <label>`, or `list`.", mention_author=False)


@ticket_type.command(name="add")
@commands.guild_only()
async def ticket_type_add(ctx, *, spec: str):
    if not await manager_check(ctx):
        return
    parts = [part.strip() for part in spec.split("|")]
    label = parts[0][:100]
    description = parts[1][:100] if len(parts) > 1 and parts[1] else None
    emoji = parts[2] if len(parts) > 2 and parts[2] else None
    if not label:
        return await ctx.reply("Provide a label.", mention_author=False)
    if emoji and not (re.fullmatch(r"<a?:\w+:\d+>", emoji) or (len(emoji) <= 8 and not emoji.isascii())):
        return await ctx.reply("That emoji is not valid. Use a standard emoji or a custom emoji from a server I share.", mention_author=False)
    settings = _ticket_settings(ctx.guild.id)
    if len(settings["types"]) >= 25:
        return await ctx.reply("You can have at most 25 ticket types.", mention_author=False)
    if _ticket_find_type(settings, label):
        return await ctx.reply("A type with that label already exists.", mention_author=False)
    settings["types"].append({"label": label, "description": description, "emoji": emoji})
    save_config()
    note = "" if len(settings["types"]) >= 2 else " Add at least two types for the dropdown panel; with one type the panel uses a button."
    await ctx.reply(f"Added ticket type **{label}**. Re-post the panel with `{ctx.clean_prefix}ticket panel` to apply.{note}", mention_author=False)


@ticket_type.command(name="remove")
@commands.guild_only()
async def ticket_type_remove(ctx, *, label: str):
    if not await manager_check(ctx):
        return
    settings = _ticket_settings(ctx.guild.id)
    item = _ticket_find_type(settings, label)
    if not item:
        return await ctx.reply("No ticket type with that label.", mention_author=False)
    settings["types"].remove(item)
    save_config()
    await ctx.reply(f"Removed ticket type **{item['label']}**.", mention_author=False)


@ticket_type.command(name="list")
@commands.guild_only()
async def ticket_type_list(ctx):
    if not await manager_check(ctx):
        return
    types = _ticket_settings(ctx.guild.id)["types"]
    text = "\n".join(f"`{i}.` {t.get('emoji') or ''} **{t['label']}** {('— ' + t['description']) if t.get('description') else ''}" for i, t in enumerate(types, 1)) or "No types configured."
    await ctx.reply(embed=feature_embed("Ticket Types", text), mention_author=False)


@ticket_group.command(name="max")
@commands.guild_only()
async def ticket_max(ctx, amount: int):
    if not await manager_check(ctx):
        return
    if not 1 <= amount <= 10:
        return await ctx.reply("Choose a number from 1 to 10.", mention_author=False)
    _ticket_settings(ctx.guild.id)["max_open"] = amount
    save_config()
    await ctx.reply(f"Users can now have up to **{amount}** open ticket(s).", mention_author=False)


@ticket_group.command(name="format")
@commands.guild_only()
async def ticket_format(ctx, *, text: str):
    if not await manager_check(ctx):
        return
    if "{number}" not in text:
        return await ctx.reply("Include `{number}` in the format so channel names stay unique.", mention_author=False)
    _ticket_settings(ctx.guild.id)["name_format"] = text[:60]
    save_config()
    await ctx.reply(f"Channel name format set to `{text[:60]}`.", mention_author=False)


@ticket_group.command(name="welcome")
@commands.guild_only()
async def ticket_welcome(ctx, *, text: str):
    if not await manager_check(ctx):
        return
    _ticket_settings(ctx.guild.id)["welcome"] = text[:1500]
    save_config()
    await ctx.reply("Welcome message updated.", mention_author=False)


@ticket_group.command(name="panelset")
@commands.guild_only()
async def ticket_panelset(ctx, field: str, *, text: str):
    if not await manager_check(ctx):
        return
    mapping = {"title": ("panel_title", 200), "description": ("panel_description", 2000), "button": ("button_label", 80)}
    if field.lower() not in mapping:
        return await ctx.reply("Field must be `title`, `description`, or `button`.", mention_author=False)
    key, limit = mapping[field.lower()]
    _ticket_settings(ctx.guild.id)[key] = text[:limit]
    save_config()
    await ctx.reply(f"Panel {field.lower()} updated. Post a new panel to see it.", mention_author=False)


@ticket_group.command(name="toggle")
@commands.guild_only()
async def ticket_toggle(ctx, feature: str):
    if not await manager_check(ctx):
        return
    item = TICKET_TOGGLES.get(feature.lower())
    if not item:
        return await ctx.reply("Choose one of: " + ", ".join(f"`{k}`" for k in TICKET_TOGGLES) + ".", mention_author=False)
    key, label = item
    settings = _ticket_settings(ctx.guild.id)
    settings[key] = not settings.get(key, True)
    save_config()
    await ctx.reply(f"{label}: **{'On' if settings[key] else 'Off'}**.", mention_author=False)


@ticket_group.command(name="autoclose")
@commands.guild_only()
async def ticket_autoclose(ctx, hours: str):
    if not await manager_check(ctx):
        return
    if hours.lower() in {"off", "0", "disable", "none"}:
        value = 0
    elif hours.isdigit() and 1 <= int(hours) <= 720:
        value = int(hours)
    else:
        return await ctx.reply("Use a number of hours from 1 to 720, or `off`.", mention_author=False)
    _ticket_settings(ctx.guild.id)["autoclose_hours"] = value
    save_config()
    await ctx.reply(f"Auto-close {'disabled' if not value else f'set to {value}h of inactivity'}.", mention_author=False)


@ticket_group.group(name="blacklist", invoke_without_command=True)
@commands.guild_only()
async def ticket_blacklist(ctx):
    await ctx.reply(f"Usage: `{ctx.clean_prefix}ticket blacklist add @user`, `remove @user`, or `list`.", mention_author=False)


@ticket_blacklist.command(name="add")
@commands.guild_only()
async def ticket_blacklist_add(ctx, member: discord.Member):
    if not await manager_check(ctx):
        return
    ids = _ticket_settings(ctx.guild.id)["blacklist"]
    if member.id in ids:
        return await ctx.reply("That user is already blocked.", mention_author=False)
    ids.append(member.id)
    save_config()
    await ctx.reply(f"{member.mention} can no longer open tickets.", mention_author=False, allowed_mentions=discord.AllowedMentions.none())


@ticket_blacklist.command(name="remove")
@commands.guild_only()
async def ticket_blacklist_remove(ctx, member: discord.Member):
    if not await manager_check(ctx):
        return
    ids = _ticket_settings(ctx.guild.id)["blacklist"]
    if member.id not in ids:
        return await ctx.reply("That user is not blocked.", mention_author=False)
    ids.remove(member.id)
    save_config()
    await ctx.reply(f"{member.mention} can open tickets again.", mention_author=False, allowed_mentions=discord.AllowedMentions.none())


@ticket_blacklist.command(name="list")
@commands.guild_only()
async def ticket_blacklist_list(ctx):
    if not await manager_check(ctx):
        return
    ids = _ticket_settings(ctx.guild.id)["blacklist"]
    await ctx.reply(embed=feature_embed("Ticket Blacklist", "\n".join(f"<@{i}> (`{i}`)" for i in ids) or "Nobody is blocked."), mention_author=False)


@ticket_group.command(name="stats")
@commands.guild_only()
async def ticket_stats(ctx):
    if not await manager_check(ctx):
        return
    s = _ticket_settings(ctx.guild.id)
    open_now = sum(1 for r in _ticket_records().values() if r.get("guild_id") == ctx.guild.id and r.get("status") == "open")
    rating = f"{s['rating_total'] / s['rating_count']:.2f}/5 ({s['rating_count']} ratings)" if s["rating_count"] else "No ratings yet"
    embed = feature_embed("Ticket Statistics", f"Stats for **{ctx.guild.name}**")
    embed.add_field(name="Opened", value=str(s["opened_total"]), inline=True)
    embed.add_field(name="Closed", value=str(s["closed_total"]), inline=True)
    embed.add_field(name="Open now", value=str(open_now), inline=True)
    embed.add_field(name="Average rating", value=rating, inline=False)
    await ctx.reply(embed=embed, mention_author=False)


# ----- in-ticket commands -----
@bot.command(name="close")
@commands.guild_only()
async def ticket_close_command(ctx, *, reason: str = None):
    try:
        await ticket_close(ctx.channel, ctx.author, reason)
    except TicketError as exc:
        await _ticket_reply_error(ctx, exc)


@ticket_group.command(name="close")
@commands.guild_only()
async def ticket_close_sub(ctx, *, reason: str = None):
    await ticket_close_command.callback(ctx, reason=reason)


@bot.command(name="claim")
@commands.guild_only()
async def ticket_claim_command(ctx):
    try:
        await ctx.reply(embed=await ticket_claim(ctx.channel, ctx.author), mention_author=False)
    except TicketError as exc:
        await _ticket_reply_error(ctx, exc)


@bot.command(name="unclaim")
@commands.guild_only()
async def ticket_unclaim_command(ctx):
    try:
        await ctx.reply(embed=await ticket_claim(ctx.channel, ctx.author, unclaim=True), mention_author=False)
    except TicketError as exc:
        await _ticket_reply_error(ctx, exc)


@ticket_group.command(name="claim")
@commands.guild_only()
async def ticket_claim_sub(ctx):
    await ticket_claim_command.callback(ctx)


@ticket_group.command(name="unclaim")
@commands.guild_only()
async def ticket_unclaim_sub(ctx):
    await ticket_unclaim_command.callback(ctx)


@ticket_group.command(name="transfer")
@commands.guild_only()
async def ticket_transfer(ctx, member: discord.Member):
    try:
        await ctx.reply(embed=await ticket_claim(ctx.channel, ctx.author, target=member), mention_author=False,
                        allowed_mentions=discord.AllowedMentions.none())
    except TicketError as exc:
        await _ticket_reply_error(ctx, exc)


@ticket_group.command(name="add")
@commands.guild_only()
async def ticket_add(ctx, member: discord.Member):
    try:
        await ctx.reply(embed=await ticket_add_user(ctx.channel, ctx.author, member), mention_author=False)
    except TicketError as exc:
        await _ticket_reply_error(ctx, exc)


@ticket_group.command(name="remove")
@commands.guild_only()
async def ticket_remove(ctx, member: discord.Member):
    try:
        await ctx.reply(embed=await ticket_remove_user(ctx.channel, ctx.author, member), mention_author=False)
    except TicketError as exc:
        await _ticket_reply_error(ctx, exc)


@ticket_group.command(name="rename")
@commands.guild_only()
async def ticket_rename_command(ctx, *, name: str):
    try:
        await ctx.reply(embed=await ticket_rename(ctx.channel, ctx.author, name), mention_author=False)
    except TicketError as exc:
        await _ticket_reply_error(ctx, exc)


@ticket_group.command(name="priority")
@commands.guild_only()
async def ticket_priority(ctx, level: str):
    try:
        await ctx.reply(embed=await ticket_set_priority(ctx.channel, ctx.author, level), mention_author=False)
    except TicketError as exc:
        await _ticket_reply_error(ctx, exc)


@ticket_group.command(name="transcript")
@commands.guild_only()
async def ticket_transcript_command(ctx):
    try:
        record, _ = _ticket_require(ctx.channel, ctx.author, staff=True)
    except TicketError as exc:
        return await _ticket_reply_error(ctx, exc)
    async with ctx.typing():
        data = await _ticket_build_transcript(ctx.channel, record)
    await ctx.reply(file=_ticket_transcript_file(data, record), mention_author=False)


@ticket_group.command(name="reopen")
@commands.guild_only()
async def ticket_reopen_command(ctx):
    try:
        await ctx.reply(embed=await ticket_reopen(ctx.channel, ctx.author), mention_author=False)
    except TicketError as exc:
        await _ticket_reply_error(ctx, exc)


@ticket_group.command(name="delete")
@commands.guild_only()
async def ticket_delete_command(ctx):
    try:
        await ticket_delete(ctx.channel, ctx.author)
    except TicketError as exc:
        await _ticket_reply_error(ctx, exc)


@ticket_group.command(name="info")
@commands.guild_only()
async def ticket_info(ctx):
    record = _ticket_record(ctx.channel.id)
    if record is None:
        return await _ticket_channel_arg_error(ctx)
    embed = _ticket_embed(ctx.guild, record)
    embed.add_field(name="Status", value=str(record.get("status", "open")).title(), inline=True)
    try:
        opened = datetime.fromisoformat(record["opened_at"])
        embed.add_field(name="Opened", value=discord.utils.format_dt(opened, "R"), inline=True)
    except (KeyError, TypeError, ValueError):
        pass
    await ctx.reply(embed=embed, mention_author=False)


# ---------------------------------------------------------------------------
# AUTORESPONDER (Mimu-style: match modes, variables, cooldowns, channel limits, reply/embed/delete options)
# ---------------------------------------------------------------------------
AR_MODES = ("exact", "contains", "word", "starts", "ends", "wildcard")
AR_MAX_PER_GUILD = 100
AR_COOLDOWNS = {}


def _ar_entries(guild_id, create=False):
    root = config.setdefault("autoresponders", {})
    if not isinstance(root, dict):
        root = {}
        config["autoresponders"] = root
    if create:
        return root.setdefault(str(guild_id), [])
    return root.get(str(guild_id), [])


def _ar_find(guild_id, ident):
    entries = _ar_entries(guild_id)
    ident = str(ident).strip()
    if ident.isdigit():
        for entry in entries:
            if entry["id"] == int(ident):
                return entry
    lowered = ident.lower()
    for entry in entries:
        if entry["trigger"].lower() == lowered:
            return entry
    return None


def _ar_validate(trigger, response, mode):
    if not trigger.strip():
        return "The trigger cannot be empty."
    if len(trigger) > 200:
        return "The trigger can be at most 200 characters."
    if not response.strip():
        return "The response cannot be empty."
    if len(response) > 1800:
        return "The response can be at most 1,800 characters."
    if mode not in AR_MODES:
        return "Mode must be one of: " + ", ".join(AR_MODES) + "."
    if mode == "wildcard" and trigger.count("*") > 5:
        return "Wildcard triggers can contain at most 5 `*` characters."
    return None


def _ar_add(guild_id, user_id, trigger, response, mode):
    entries = _ar_entries(guild_id, create=True)
    entry = {
        "id": max((e["id"] for e in entries), default=0) + 1,
        "trigger": trigger.strip(),
        "response": response.strip(),
        "mode": mode,
        "enabled": True,
        "ignore_case": True,
        "cooldown": 5,
        "reply": True,
        "embed": False,
        "delete_trigger": False,
        "channels": [],
        "uses": 0,
        "created_by": user_id,
    }
    entries.append(entry)
    save_config()
    return entry


def _ar_matches(entry, content):
    trigger = entry["trigger"]
    text = (content or "").strip()
    if entry.get("ignore_case", True):
        trigger = trigger.lower()
        text = text.lower()
    mode = entry.get("mode", "exact")
    if mode == "exact":
        return text == trigger
    if mode == "contains":
        return trigger in text
    if mode == "word":
        return re.search(r"(?<!\w)" + re.escape(trigger) + r"(?!\w)", text) is not None
    if mode == "starts":
        return text.startswith(trigger)
    if mode == "ends":
        return text.endswith(trigger)
    if mode == "wildcard":
        pattern = ".*".join(re.escape(part) for part in trigger.split("*"))
        return re.fullmatch(pattern, text, re.DOTALL) is not None
    return False


def _ar_render(template, message):
    guild = message.guild
    author = message.author
    values = {
        "{user}": author.display_name,
        "{user.name}": author.name,
        "{user.mention}": author.mention,
        "{user.id}": str(author.id),
        "{server}": guild.name,
        "{server.name}": guild.name,
        "{membercount}": str(guild.member_count or len(guild.members)),
        "{server.members}": str(guild.member_count or len(guild.members)),
        "{channel}": f"#{message.channel.name}" if hasattr(message.channel, "name") else "this channel",
        "{channel.mention}": getattr(message.channel, "mention", "this channel"),
    }
    text = template
    for key, value in values.items():
        text = text.replace(key, value)

    def _random(match):
        low, high = int(match.group(1)), int(match.group(2))
        if low > high:
            low, high = high, low
        return str(random.randint(low, high))

    def _choose(match):
        options = [option.strip() for option in match.group(1).split("|") if option.strip()]
        return random.choice(options) if options else ""

    text = re.sub(r"\{random:(-?\d{1,9})-(-?\d{1,9})\}", _random, text)
    text = re.sub(r"\{choose:([^{}]+)\}", _choose, text)
    return text[:2000]


AR_VARIABLES_HELP = (
    "`{user}` `{user.name}` `{user.mention}` `{user.id}` `{server}` `{membercount}` "
    "`{channel}` `{channel.mention}` `{random:1-100}` `{choose:a|b|c}`"
)


@bot.listen("on_message")
async def kanade_autoresponder_listener(message):
    if message.author.bot or message.guild is None or not message.content:
        return
    entries = _ar_entries(message.guild.id)
    if not entries:
        return
    enabled = [entry for entry in entries if entry.get("enabled", True)]
    if not enabled:
        return
    # Never answer a message that is actually a bot command.
    try:
        ctx = await bot.get_context(message)
    except Exception:
        return
    if ctx.valid:
        return
    me = message.guild.me
    if me is None or not message.channel.permissions_for(me).send_messages:
        return
    now = time.monotonic()
    for entry in enabled:
        channels = entry.get("channels") or []
        if channels and message.channel.id not in channels:
            continue
        if not _ar_matches(entry, message.content):
            continue
        key = (message.guild.id, entry["id"], message.channel.id)
        if now - AR_COOLDOWNS.get(key, -1e9) < float(entry.get("cooldown", 5)):
            return
        AR_COOLDOWNS[key] = now
        if len(AR_COOLDOWNS) > 5000:
            for stale in [k for k, t in AR_COOLDOWNS.items() if now - t > 3600]:
                AR_COOLDOWNS.pop(stale, None)
        text = _ar_render(entry["response"], message)
        entry["uses"] = int(entry.get("uses", 0)) + 1
        allowed = discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False)
        try:
            if entry.get("embed", False):
                embed = StyledEmbed(description=text)
                if entry.get("reply", True):
                    await message.reply(embed=embed, mention_author=False, allowed_mentions=allowed)
                else:
                    await message.channel.send(embed=embed, allowed_mentions=allowed)
            elif entry.get("reply", True):
                await message.reply(text, mention_author=False, allowed_mentions=allowed)
            else:
                await message.channel.send(text, allowed_mentions=allowed)
            if entry.get("delete_trigger", False) and message.channel.permissions_for(me).manage_messages:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
        except discord.HTTPException:
            logger.warning("Autoresponder %s could not send in guild %s", entry["id"], message.guild.id)
        return


def _ar_summary(entry):
    flags = []
    flags.append("on" if entry.get("enabled", True) else "off")
    flags.append(entry.get("mode", "exact"))
    return f"`#{entry['id']}` `{entry['trigger'][:40]}` ({', '.join(flags)}) — {entry['response'][:60].replace(chr(10), ' ')}"


@bot.group(name="autoresponder", aliases=["ar", "autoreply"], invoke_without_command=True)
@commands.guild_only()
async def autoresponder_group(ctx):
    p = ctx.clean_prefix
    description = (
        f"`{p}ar add [mode] <trigger> | <response>` — Create an autoresponder (e.g. `{p}ar add [contains] hello | Hi {{user}}!`)\n"
        f"`{p}ar remove <id|trigger>` — Delete one\n"
        f"`{p}ar edit <id> <new response>` — Change the response\n"
        f"`{p}ar list` / `{p}ar info <id>` — View autoresponders\n"
        f"`{p}ar toggle <id>` — Enable or disable one\n"
        f"`{p}ar mode <id> <{'|'.join(AR_MODES)}>` — Change how the trigger matches\n"
        f"`{p}ar cooldown <id> <seconds>` — Per-channel cooldown (0-3600)\n"
        f"`{p}ar reply <id> on|off` — Reply to the message or send normally\n"
        f"`{p}ar embed <id> on|off` — Send the response as an embed\n"
        f"`{p}ar deletetrigger <id> on|off` — Delete the triggering message\n"
        f"`{p}ar case <id> on|off` — Case-insensitive matching\n"
        f"`{p}ar channels <id> add|remove|clear [#channel]` — Limit to channels\n"
        f"`{p}ar clear` — Remove every autoresponder\n\n"
        f"**Variables:** {AR_VARIABLES_HELP}\n"
        f"**Modes:** {', '.join(f'`{m}`' for m in AR_MODES)}"
    )
    await ctx.reply(embed=feature_embed("Autoresponder", description), mention_author=False)


@autoresponder_group.command(name="add", aliases=["create"])
@commands.guild_only()
async def autoresponder_add(ctx, *, spec: str):
    if not await manager_check(ctx):
        return
    mode = "exact"
    match = re.match(r"^\[(" + "|".join(AR_MODES) + r")\]\s*", spec, re.IGNORECASE)
    if match:
        mode = match.group(1).lower()
        spec = spec[match.end():]
    if "|" not in spec:
        return await ctx.reply(f"Use `{ctx.clean_prefix}ar add [mode] <trigger> | <response>`.", mention_author=False)
    trigger, response = (part.strip() for part in spec.split("|", 1))
    error = _ar_validate(trigger, response, mode)
    if error:
        return await ctx.reply(error, mention_author=False)
    if len(_ar_entries(ctx.guild.id)) >= AR_MAX_PER_GUILD:
        return await ctx.reply(f"This server reached the limit of {AR_MAX_PER_GUILD} autoresponders.", mention_author=False)
    if any(e["trigger"].lower() == trigger.lower() and e.get("mode") == mode for e in _ar_entries(ctx.guild.id)):
        return await ctx.reply("An autoresponder with that trigger and mode already exists.", mention_author=False)
    entry = _ar_add(ctx.guild.id, ctx.author.id, trigger, response, mode)
    await ctx.reply(embed=feature_embed("Autoresponder Created", f"ID `{entry['id']}` — `{mode}` match on `{trigger[:100]}`."), mention_author=False)


@autoresponder_group.command(name="remove", aliases=["delete", "del"])
@commands.guild_only()
async def autoresponder_remove(ctx, *, ident: str):
    if not await manager_check(ctx):
        return
    entry = _ar_find(ctx.guild.id, ident)
    if entry is None:
        return await ctx.reply("No autoresponder matches that ID or trigger.", mention_author=False)
    _ar_entries(ctx.guild.id).remove(entry)
    save_config()
    await ctx.reply(f"Autoresponder `#{entry['id']}` deleted.", mention_author=False)


@autoresponder_group.command(name="edit")
@commands.guild_only()
async def autoresponder_edit(ctx, ident: str, *, response: str):
    if not await manager_check(ctx):
        return
    entry = _ar_find(ctx.guild.id, ident)
    if entry is None:
        return await ctx.reply("No autoresponder matches that ID or trigger.", mention_author=False)
    error = _ar_validate(entry["trigger"], response, entry.get("mode", "exact"))
    if error:
        return await ctx.reply(error, mention_author=False)
    entry["response"] = response.strip()
    save_config()
    await ctx.reply(f"Autoresponder `#{entry['id']}` updated.", mention_author=False)


@autoresponder_group.command(name="list")
@commands.guild_only()
async def autoresponder_list(ctx):
    if not await manager_check(ctx):
        return
    entries = _ar_entries(ctx.guild.id)
    if not entries:
        return await ctx.reply(embed=feature_embed("Autoresponders", "No autoresponders yet. Create one with the `add` subcommand."), mention_author=False)
    text = "\n".join(_ar_summary(e) for e in entries)
    if len(text) > 3900:
        text = text[:3850].rsplit("\n", 1)[0] + "\n\nList shortened due to Discord's embed limit."
    embed = feature_embed("Autoresponders", text)
    embed.set_footer(text=f"{len(entries)} / {AR_MAX_PER_GUILD} used")
    await ctx.reply(embed=embed, mention_author=False)


@autoresponder_group.command(name="info", aliases=["view"])
@commands.guild_only()
async def autoresponder_info(ctx, *, ident: str):
    if not await manager_check(ctx):
        return
    entry = _ar_find(ctx.guild.id, ident)
    if entry is None:
        return await ctx.reply("No autoresponder matches that ID or trigger.", mention_author=False)
    embed = feature_embed(f"Autoresponder #{entry['id']}", "")
    embed.add_field(name="Trigger", value=f"`{entry['trigger'][:200]}`", inline=False)
    embed.add_field(name="Response", value=entry["response"][:1000], inline=False)
    embed.add_field(name="Mode", value=entry.get("mode", "exact"), inline=True)
    embed.add_field(name="Enabled", value="Yes" if entry.get("enabled", True) else "No", inline=True)
    embed.add_field(name="Cooldown", value=f"{entry.get('cooldown', 5)}s", inline=True)
    embed.add_field(name="Reply", value="On" if entry.get("reply", True) else "Off", inline=True)
    embed.add_field(name="Embed", value="On" if entry.get("embed") else "Off", inline=True)
    embed.add_field(name="Delete trigger", value="On" if entry.get("delete_trigger") else "Off", inline=True)
    embed.add_field(name="Ignore case", value="On" if entry.get("ignore_case", True) else "Off", inline=True)
    embed.add_field(name="Channels", value=" ".join(f"<#{c}>" for c in entry.get("channels", [])) or "All channels", inline=False)
    embed.add_field(name="Uses", value=str(entry.get("uses", 0)), inline=True)
    await ctx.reply(embed=embed, mention_author=False)


@autoresponder_group.command(name="toggle")
@commands.guild_only()
async def autoresponder_toggle(ctx, *, ident: str):
    if not await manager_check(ctx):
        return
    entry = _ar_find(ctx.guild.id, ident)
    if entry is None:
        return await ctx.reply("No autoresponder matches that ID or trigger.", mention_author=False)
    entry["enabled"] = not entry.get("enabled", True)
    save_config()
    await ctx.reply(f"Autoresponder `#{entry['id']}` is now **{'enabled' if entry['enabled'] else 'disabled'}**.", mention_author=False)


@autoresponder_group.command(name="mode")
@commands.guild_only()
async def autoresponder_mode(ctx, ident: str, mode: str):
    if not await manager_check(ctx):
        return
    entry = _ar_find(ctx.guild.id, ident)
    if entry is None:
        return await ctx.reply("No autoresponder matches that ID or trigger.", mention_author=False)
    mode = mode.lower()
    error = _ar_validate(entry["trigger"], entry["response"], mode)
    if error:
        return await ctx.reply(error, mention_author=False)
    entry["mode"] = mode
    save_config()
    await ctx.reply(f"Autoresponder `#{entry['id']}` now matches using `{mode}`.", mention_author=False)


@autoresponder_group.command(name="cooldown")
@commands.guild_only()
async def autoresponder_cooldown(ctx, ident: str, seconds: int):
    if not await manager_check(ctx):
        return
    entry = _ar_find(ctx.guild.id, ident)
    if entry is None:
        return await ctx.reply("No autoresponder matches that ID or trigger.", mention_author=False)
    if not 0 <= seconds <= 3600:
        return await ctx.reply("Cooldown must be between 0 and 3600 seconds.", mention_author=False)
    entry["cooldown"] = seconds
    save_config()
    await ctx.reply(f"Cooldown for `#{entry['id']}` set to **{seconds}s**.", mention_author=False)


async def _ar_set_flag(ctx, ident, key, value, label):
    if not await manager_check(ctx):
        return
    entry = _ar_find(ctx.guild.id, ident)
    if entry is None:
        return await ctx.reply("No autoresponder matches that ID or trigger.", mention_author=False)
    state = value.lower() in {"on", "true", "yes", "enable", "enabled", "1"}
    if value.lower() not in {"on", "off", "true", "false", "yes", "no", "enable", "enabled", "disable", "disabled", "1", "0"}:
        return await ctx.reply("Use `on` or `off`.", mention_author=False)
    entry[key] = state
    save_config()
    await ctx.reply(f"{label} for `#{entry['id']}`: **{'On' if state else 'Off'}**.", mention_author=False)


@autoresponder_group.command(name="reply")
@commands.guild_only()
async def autoresponder_reply(ctx, ident: str, value: str):
    await _ar_set_flag(ctx, ident, "reply", value, "Reply mode")


@autoresponder_group.command(name="embed")
@commands.guild_only()
async def autoresponder_embed(ctx, ident: str, value: str):
    await _ar_set_flag(ctx, ident, "embed", value, "Embed mode")


@autoresponder_group.command(name="deletetrigger", aliases=["deletemsg"])
@commands.guild_only()
async def autoresponder_deletetrigger(ctx, ident: str, value: str):
    await _ar_set_flag(ctx, ident, "delete_trigger", value, "Delete trigger")


@autoresponder_group.command(name="case")
@commands.guild_only()
async def autoresponder_case(ctx, ident: str, value: str):
    # "on" means case-INsensitive matching.
    await _ar_set_flag(ctx, ident, "ignore_case", value, "Ignore case")


@autoresponder_group.command(name="channels", aliases=["channel"])
@commands.guild_only()
async def autoresponder_channels(ctx, ident: str, action: str, channel: discord.TextChannel = None):
    if not await manager_check(ctx):
        return
    entry = _ar_find(ctx.guild.id, ident)
    if entry is None:
        return await ctx.reply("No autoresponder matches that ID or trigger.", mention_author=False)
    channels = entry.setdefault("channels", [])
    action = action.lower()
    if action == "clear":
        channels.clear()
    elif action in {"add", "remove"} and channel is not None:
        if action == "add" and channel.id not in channels:
            channels.append(channel.id)
        elif action == "remove" and channel.id in channels:
            channels.remove(channel.id)
    else:
        return await ctx.reply("Use `add #channel`, `remove #channel`, or `clear`.", mention_author=False)
    save_config()
    shown = " ".join(f"<#{c}>" for c in channels) or "all channels"
    await ctx.reply(f"Autoresponder `#{entry['id']}` runs in: {shown}.", mention_author=False)


@autoresponder_group.command(name="clear", aliases=["reset"])
@commands.guild_only()
async def autoresponder_clear(ctx):
    if not await manager_check(ctx):
        return
    entries = _ar_entries(ctx.guild.id)
    count = len(entries)
    if not count:
        return await ctx.reply("There are no autoresponders to clear.", mention_author=False)
    config.get("autoresponders", {}).pop(str(ctx.guild.id), None)
    save_config()
    await ctx.reply(f"Removed **{count}** autoresponder(s).", mention_author=False)


# ----- slash commands (Mimu-style /autoresponder) -----
autoresponder_slash = app_commands.Group(name="autoresponder", description="Manage autoresponders", guild_only=True)


def _slash_ar_denied(interaction):
    return interaction.guild is None or not has_admin_access(interaction.user)


@autoresponder_slash.command(name="add", description="Create an autoresponder")
@app_commands.describe(trigger="Text that triggers the response", response="What Kanade should say", mode="How the trigger is matched")
@app_commands.choices(mode=[app_commands.Choice(name=m, value=m) for m in AR_MODES])
async def slash_ar_add(interaction: discord.Interaction, trigger: str, response: str, mode: app_commands.Choice[str] = None):
    if _slash_ar_denied(interaction):
        return await interaction.response.send_message("Administrator access required.", ephemeral=True)
    mode_value = mode.value if mode else "exact"
    error = _ar_validate(trigger, response, mode_value)
    if error:
        return await interaction.response.send_message(error, ephemeral=True)
    if len(_ar_entries(interaction.guild.id)) >= AR_MAX_PER_GUILD:
        return await interaction.response.send_message(f"This server reached the limit of {AR_MAX_PER_GUILD} autoresponders.", ephemeral=True)
    entry = _ar_add(interaction.guild.id, interaction.user.id, trigger, response, mode_value)
    await interaction.response.send_message(f"Autoresponder `#{entry['id']}` created ({mode_value} match).", ephemeral=True)


@autoresponder_slash.command(name="remove", description="Delete an autoresponder by ID or trigger")
async def slash_ar_remove(interaction: discord.Interaction, ident: str):
    if _slash_ar_denied(interaction):
        return await interaction.response.send_message("Administrator access required.", ephemeral=True)
    entry = _ar_find(interaction.guild.id, ident)
    if entry is None:
        return await interaction.response.send_message("No autoresponder matches that ID or trigger.", ephemeral=True)
    _ar_entries(interaction.guild.id).remove(entry)
    save_config()
    await interaction.response.send_message(f"Autoresponder `#{entry['id']}` deleted.", ephemeral=True)


@autoresponder_slash.command(name="list", description="List this server's autoresponders")
async def slash_ar_list(interaction: discord.Interaction):
    if _slash_ar_denied(interaction):
        return await interaction.response.send_message("Administrator access required.", ephemeral=True)
    entries = _ar_entries(interaction.guild.id)
    text = "\n".join(_ar_summary(e) for e in entries) or "No autoresponders yet."
    if len(text) > 3900:
        text = text[:3850].rsplit("\n", 1)[0] + "\n\nList shortened due to Discord's embed limit."
    await interaction.response.send_message(embed=feature_embed("Autoresponders", text), ephemeral=True)


@autoresponder_slash.command(name="toggle", description="Enable or disable an autoresponder")
async def slash_ar_toggle(interaction: discord.Interaction, ident: str):
    if _slash_ar_denied(interaction):
        return await interaction.response.send_message("Administrator access required.", ephemeral=True)
    entry = _ar_find(interaction.guild.id, ident)
    if entry is None:
        return await interaction.response.send_message("No autoresponder matches that ID or trigger.", ephemeral=True)
    entry["enabled"] = not entry.get("enabled", True)
    save_config()
    await interaction.response.send_message(f"Autoresponder `#{entry['id']}` is now {'enabled' if entry['enabled'] else 'disabled'}.", ephemeral=True)


bot.tree.add_command(autoresponder_slash)


# ---------------------------------------------------------------------------
# MUSIC (Jockie-style: play/search, queue, loop, shuffle, seek, filters, vote-skip, DJ role, autoplay, 24/7, controls)
# Requires: ffmpeg on the host, yt-dlp, PyNaCl (install discord.py[voice]).
# ---------------------------------------------------------------------------
def _find_ffmpeg():
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


FFMPEG_EXE = _find_ffmpeg()
MUSIC_PLAYERS = {}
MUSIC_IDLE_TIMEOUT = 180       # seconds the queue may sit empty before leaving
MUSIC_ALONE_TIMEOUT = 60       # seconds alone in a channel before leaving
MUSIC_MAX_QUEUE = 500
MUSIC_PLAYLIST_LIMIT = 100
MUSIC_FILTERS = {
    "off": None,
    "bassboost": "bass=g=10,dynaudnorm=f=200",
    "bassboost_low": "bass=g=5,dynaudnorm=f=200",
    "bassboost_high": "bass=g=16,dynaudnorm=f=200",
    "nightcore": "aresample=48000,asetrate=60000,aresample=48000",
    "vaporwave": "aresample=48000,asetrate=38400,aresample=48000",
    "8d": "apulsator=hz=0.08",
    "treble": "treble=g=8",
    "tremolo": "tremolo=f=5.0:d=0.7",
    "vibrato": "vibrato=f=6.0:d=0.5",
}
MUSIC_FILTER_ALIASES = {
    "bass": "bassboost", "bb": "bassboost", "nc": "nightcore", "slowed": "vaporwave",
    "reset": "off", "none": "off", "clear": "off", "disable": "off", "8": "8d",
}


class MusicError(Exception):
    """User-facing music problem."""


def _music_settings(guild_id):
    root = config.setdefault("music_settings", {})
    if not isinstance(root, dict):
        root = {}
        config["music_settings"] = root
    return root.setdefault(str(guild_id), {})


def _fmt_duration(seconds):
    if seconds is None:
        return "Live/Unknown"
    seconds = int(max(0, seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _parse_timestamp(text):
    text = (text or "").strip().lower()
    if re.fullmatch(r"\d+", text):
        return int(text)
    if ":" in text:
        parts = text.split(":")
        if len(parts) in (2, 3) and all(part.isdigit() for part in parts):
            total = 0
            for part in parts:
                total = total * 60 + int(part)
            return total
        return None
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", text)
    if match and any(match.groups()):
        hours, minutes, secs = (int(g) if g else 0 for g in match.groups())
        return hours * 3600 + minutes * 60 + secs
    return None


def _md_safe(text, limit=60):
    text = (text or "Unknown").replace("[", "(").replace("]", ")").replace("`", "'").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def _music_error(ctx, text):
    await ctx.reply(embed=feature_embed(f"{ERROR_EMOJI} Music Error", text), mention_author=False)


class MusicTrack:
    def __init__(self, *, title, webpage_url, duration=None, thumbnail=None, uploader=None,
                 requester_id=0, requester_name="Unknown", stream_url=None, search_query=None):
        self.title = title or "Unknown title"
        self.webpage_url = webpage_url      # link shown to users (YouTube, SoundCloud or Spotify)
        self.duration = int(duration) if duration else None
        self.thumbnail = thumbnail
        self.uploader = uploader
        self.requester_id = requester_id
        self.requester_name = requester_name
        self.stream_url = stream_url
        self.search_query = search_query    # set for Spotify tracks: audio is found on YouTube when played
        self._yt_url = None
        self.resolved_at = time.monotonic() if stream_url else 0.0

    @staticmethod
    def _first_entry(data):
        if data and data.get("entries"):
            return next((e for e in data["entries"] if e), None)
        return data

    async def ensure_stream(self):
        # Stream URLs expire, so refresh anything older than 20 minutes.
        if self.stream_url and time.monotonic() - self.resolved_at < 1200:
            return
        info = None
        try:
            if self.search_query and not self._yt_url:
                data = await _ytdl_run(_ytdl_extract_sync, f"ytsearch1:{self.search_query}", flat=False, noplaylist=True)
                info = self._first_entry(data)
                if info is None:
                    raise MusicError("No matching audio was found on YouTube.")
                self._yt_url = info.get("webpage_url") or info.get("original_url")
            else:
                data = await _ytdl_run(_ytdl_resolve_sync, self._yt_url or self.webpage_url)
                info = self._first_entry(data)
                if info is None:
                    raise MusicError("Nothing playable was found for that track.")
        except MusicError as first_error:
            # YouTube may block the host; try SoundCloud for the same song before giving up.
            query = self.search_query or f"{self.title} {self.uploader or ''}".strip()
            try:
                data = await _ytdl_run(_ytdl_extract_sync, f"scsearch1:{query}", flat=False, noplaylist=True)
                info = self._first_entry(data)
            except MusicError:
                info = None
            if info is None:
                raise first_error
        url = _stream_url_from_info(info)
        if not url:
            raise MusicError("YouTube returned no playable audio for this track. This is usually the bot check: "
                             "add YouTube cookies (`YTDLP_COOKIES_B64`), keep yt-dlp updated, and make sure deno or node is installed.")
        self.stream_url = url
        self.resolved_at = time.monotonic()
        self.duration = self.duration or (int(info["duration"]) if info.get("duration") else None)
        self.thumbnail = self.thumbnail or info.get("thumbnail")


def _stream_url_from_info(info):
    """Find a playable audio URL in a yt-dlp info dict."""
    if info.get("url"):
        return info["url"]
    for fmt in info.get("requested_formats") or []:
        if fmt.get("url") and fmt.get("acodec") not in (None, "none"):
            return fmt["url"]
    audio = [f for f in (info.get("formats") or []) if f.get("url") and f.get("acodec") not in (None, "none")
             and f.get("protocol", "") not in ("mhtml",)]
    if audio:
        audio.sort(key=lambda f: (f.get("vcodec") in (None, "none"), f.get("abr") or 0), reverse=True)
        return audio[0]["url"]
    return None


def _ytdl_cookie_file():
    """Cookie file for YouTube. Use YTDLP_COOKIES (a path) or YTDLP_COOKIES_B64 (base64 of cookies.txt, handy on Railway)."""
    path = os.getenv("YTDLP_COOKIES", "").strip()
    if path and os.path.isfile(path):
        return path
    encoded = os.getenv("YTDLP_COOKIES_B64", "").strip()
    if encoded:
        target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yt_cookies.txt")
        try:
            import base64
            with open(target, "wb") as handle:
                handle.write(base64.b64decode(encoded))
            return target
        except Exception:
            logger.warning("YTDLP_COOKIES_B64 is set but could not be decoded.")
    return None


YTDL_COOKIE_FILE = _ytdl_cookie_file()
# YouTube player-client sets tried in order when YouTube refuses to give a stream.
YT_CLIENT_SETS = [None, ["tv", "android_vr"], ["web_safari", "mweb"]]
_YT_RETRY_PATTERN = re.compile(r"sign in|confirm you|not a bot|format is not available|player response|HTTP Error 403|unavailable on this app", re.IGNORECASE)


def _ytdl_base_options():
    options = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 15,
        "retries": 3,
        "source_address": "0.0.0.0",
        "ignoreerrors": False,
    }
    if YTDL_COOKIE_FILE:
        options["cookiefile"] = YTDL_COOKIE_FILE
    return options


def _ytdl_extract_sync(query, *, flat, noplaylist, limit=None):
    is_youtube = "youtu" in query or query.startswith("ytsearch")
    attempts = [None]
    if is_youtube and not flat:
        custom = [c.strip() for c in os.getenv("YTDLP_YT_CLIENTS", "").split(",") if c.strip()]
        attempts = ([custom] if custom else []) + YT_CLIENT_SETS
    last_error = None
    for clients in attempts:
        options = _ytdl_base_options()
        options["noplaylist"] = noplaylist
        if flat:
            options["extract_flat"] = "in_playlist"
        if limit:
            options["playlistend"] = limit
        if clients:
            options["extractor_args"] = {"youtube": {"player_client": clients}}
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                return ydl.extract_info(query, download=False)
        except Exception as exc:
            last_error = exc
            if not _YT_RETRY_PATTERN.search(str(exc)):
                raise
    raise last_error


def _ytdl_resolve_sync(url):
    return _ytdl_extract_sync(url, flat=False, noplaylist=True)


async def _ytdl_run(function, *args, **kwargs):
    if yt_dlp is None:
        raise MusicError("Music is unavailable because `yt-dlp` is not installed on the host.")
    try:
        return await asyncio.wait_for(asyncio.to_thread(function, *args, **kwargs), timeout=60)
    except asyncio.TimeoutError:
        raise MusicError("Timed out while contacting the media source. Try again.")
    except MusicError:
        raise
    except Exception as exc:
        raw = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).strip()
        if re.search(r"sign in to confirm|not a bot", raw, re.IGNORECASE):
            raise MusicError("YouTube is blocking this host with a bot check. Add exported YouTube cookies "
                             "(`YTDLP_COOKIES_B64` or `YTDLP_COOKIES`) and restart, and keep yt-dlp updated.")
        if re.search(r"video unavailable|private video|has been removed", raw, re.IGNORECASE):
            raise MusicError("That video is unavailable or private.")
        lines = raw.splitlines()
        raise MusicError((lines[-1] if lines else "Could not load that media.")[:220])


def _music_track_from_info(info, requester, *, resolved=False):
    if not info:
        return None
    url = info.get("webpage_url") or info.get("original_url") or info.get("url")
    extractor = (info.get("ie_key") or info.get("extractor_key") or info.get("extractor") or "").lower()
    video_id = info.get("id")
    if (not url or not str(url).startswith("http")) and video_id and "youtube" in extractor:
        url = f"https://www.youtube.com/watch?v={video_id}"
    if not url or not str(url).startswith("http"):
        return None
    thumbnail = info.get("thumbnail")
    if not thumbnail and info.get("thumbnails"):
        thumbnail = info["thumbnails"][-1].get("url")
    return MusicTrack(
        title=info.get("title"),
        webpage_url=url,
        duration=info.get("duration"),
        thumbnail=thumbnail,
        uploader=info.get("uploader") or info.get("channel"),
        requester_id=getattr(requester, "id", 0),
        requester_name=getattr(requester, "display_name", str(requester)),
        stream_url=info.get("url") if resolved and info.get("url") != url else None,
    )


SPOTIFY_RE = re.compile(r"(?:open\.spotify\.com/(?:intl-[a-z]{2}/)?|spotify:)(track|album|playlist)[/:]([A-Za-z0-9]{22})", re.IGNORECASE)
_SPOTIFY_TOKEN = {"value": None, "expires": 0.0}


async def _spotify_token(session):
    if _SPOTIFY_TOKEN["value"] and time.monotonic() < _SPOTIFY_TOKEN["expires"] - 30:
        return _SPOTIFY_TOKEN["value"]
    client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise SpotifyAccessError("Spotify links need `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` set in the bot's environment variables.")
    try:
        async with session.post("https://accounts.spotify.com/api/token", data={"grant_type": "client_credentials"},
                                auth=aiohttp.BasicAuth(client_id, client_secret)) as response:
            if response.status != 200:
                raise SpotifyAccessError("Spotify rejected the configured client ID/secret.")
            payload = await response.json()
    except aiohttp.ClientError:
        raise MusicError("Could not reach Spotify. Try again shortly.")
    _SPOTIFY_TOKEN["value"] = payload["access_token"]
    _SPOTIFY_TOKEN["expires"] = time.monotonic() + int(payload.get("expires_in", 3600))
    return _SPOTIFY_TOKEN["value"]


class SpotifyAccessError(MusicError):
    """Spotify's API refused the request (403/401)."""


async def _spotify_scrape_track(spotify_id, requester):
    """Credential-free fallback for single tracks: read title/artist from the public track page."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
               "Accept-Language": "en-US,en;q=0.9"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15), headers=headers) as session:
            async with session.get(f"https://open.spotify.com/track/{spotify_id}") as response:
                if response.status != 200:
                    return None
                page = await response.text()
    except aiohttp.ClientError:
        return None

    def meta(name):
        match = re.search(r'<meta[^>]+(?:property|name)="' + name + r'"[^>]+content="([^"]*)"', page)
        return html.unescape(match.group(1)).strip() if match else None

    title = artist = None
    heading = re.search(r"<title>(.+?)</title>", page, re.S)
    if heading:
        parsed = re.match(r"(.+?) - (?:song|single)(?: and lyrics)? by (.+?) \| Spotify", html.unescape(heading.group(1)).strip())
        if parsed:
            title, artist = parsed.group(1).strip(), parsed.group(2).strip()
    if not title:
        title = meta("og:title")
        description = meta("og:description") or ""
        artist = description.split("·")[0].strip() if "·" in description else None
    if not title:
        return None
    return MusicTrack(
        title=f"{title} - {artist}" if artist else title,
        webpage_url=f"https://open.spotify.com/track/{spotify_id}",
        thumbnail=meta("og:image"),
        uploader=artist,
        requester_id=getattr(requester, "id", 0),
        requester_name=getattr(requester, "display_name", str(requester)),
        search_query=f"{artist or ''} {title} audio".strip(),
    )


async def _spotify_get(session, url, params=None):
    token = await _spotify_token(session)
    try:
        async with session.get(url, params=params, headers={"Authorization": f"Bearer {token}"}) as response:
            if response.status == 429:
                raise MusicError("Spotify is rate limiting requests. Try again in a moment.")
            if response.status in (401, 403):
                _SPOTIFY_TOKEN["value"] = None
                logger.warning("Spotify API refused %s (%s): %s", url, response.status, (await response.text())[:300])
                raise SpotifyAccessError("Spotify's API refused this request. If this happens for every link, your Spotify "
                                         "developer app is restricted (check the app's status/owner account in the Spotify dashboard).")
            if response.status == 404:
                raise MusicError("That Spotify link was not found or is private.")
            if response.status != 200:
                raise MusicError(f"Spotify returned an error ({response.status}).")
            return await response.json()
    except aiohttp.ClientError:
        raise MusicError("Could not reach Spotify. Try again shortly.")


def _spotify_track(item, requester, fallback_image=None):
    if not item or item.get("is_local") or not item.get("name"):
        return None
    artists = [a["name"] for a in item.get("artists", []) if a.get("name")]
    images = (item.get("album") or {}).get("images") or []
    return MusicTrack(
        title=item["name"] + (f" - {', '.join(artists[:2])}" if artists else ""),
        webpage_url=(item.get("external_urls") or {}).get("spotify") or "https://open.spotify.com",
        duration=(item.get("duration_ms") or 0) // 1000 or None,
        thumbnail=(images[0]["url"] if images else fallback_image),
        uploader=artists[0] if artists else None,
        requester_id=getattr(requester, "id", 0),
        requester_name=getattr(requester, "display_name", str(requester)),
        search_query=f"{artists[0] if artists else ''} {item['name']} audio".strip(),
    )


async def _spotify_from_embed(kind, spotify_id, requester):
    """No-credentials fallback: read the public embed page's data (works for tracks, albums, playlists)."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15), headers=headers) as session:
            async with session.get(f"https://open.spotify.com/embed/{kind}/{spotify_id}") as response:
                if response.status != 200:
                    return None
                page = await response.text()
        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', page, re.S)
        if not match:
            return None
        entity = json.loads(match.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    except (aiohttp.ClientError, KeyError, TypeError, ValueError):
        return None

    def make(title, artist, duration_ms, url):
        if not title:
            return None
        return MusicTrack(
            title=f"{title} - {artist}" if artist else title,
            webpage_url=url,
            duration=(int(duration_ms) // 1000) if duration_ms else None,
            uploader=artist,
            requester_id=getattr(requester, "id", 0),
            requester_name=getattr(requester, "display_name", str(requester)),
            search_query=f"{artist or ''} {title} audio".strip(),
        )

    if kind == "track":
        artists = ", ".join(a.get("name", "") for a in entity.get("artists", [])[:2] if a.get("name"))
        track = make(entity.get("name") or entity.get("title"), artists or None, entity.get("duration"),
                     f"https://open.spotify.com/track/{spotify_id}")
        return ([track], None) if track else None
    tracks = []
    for item in (entity.get("trackList") or [])[:MUSIC_PLAYLIST_LIMIT]:
        uri = str(item.get("uri", ""))
        link = f"https://open.spotify.com/track/{uri.split(':')[-1]}" if uri.startswith("spotify:track:") else f"https://open.spotify.com/{kind}/{spotify_id}"
        track = make(item.get("title"), item.get("subtitle"), item.get("duration"), link)
        if track:
            tracks.append(track)
    return (tracks, entity.get("name") or entity.get("title")) if tracks else None


async def spotify_resolve(kind, spotify_id, requester):
    """Spotify API first; if it refuses (or no credentials), fall back to the public embed/track pages."""
    try:
        return await _spotify_via_api(kind, spotify_id, requester)
    except SpotifyAccessError as exc:
        result = await _spotify_from_embed(kind, spotify_id, requester)
        if result:
            return result
        if kind == "track":
            track = await _spotify_scrape_track(spotify_id, requester)
            if track:
                return [track], None
        raise exc


async def _spotify_via_api(kind, spotify_id, requester):
    """Turn a Spotify track/album/playlist into queue entries; audio is matched on YouTube at play time."""
    base = "https://api.spotify.com/v1"
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if kind == "track":
            item = await _spotify_get(session, f"{base}/tracks/{spotify_id}")
            track = _spotify_track(item, requester)
            if track is None:
                raise MusicError("That Spotify track can't be played.")
            return [track], None
        tracks = []
        if kind == "album":
            album = await _spotify_get(session, f"{base}/albums/{spotify_id}")
            cover = (album.get("images") or [{}])[0].get("url")
            page = album.get("tracks") or {}
            while page:
                for item in page.get("items", []):
                    track = _spotify_track(item, requester, cover)
                    if track:
                        tracks.append(track)
                if len(tracks) >= MUSIC_PLAYLIST_LIMIT or not page.get("next"):
                    break
                page = await _spotify_get(session, page["next"])
            title = album.get("name")
        else:
            meta = await _spotify_get(session, f"{base}/playlists/{spotify_id}", {"fields": "name"})
            title = meta.get("name")
            page = await _spotify_get(session, f"{base}/playlists/{spotify_id}/tracks", {
                "limit": 100,
                "fields": "next,items(track(name,is_local,duration_ms,external_urls,artists(name),album(images)))",
            })
            while page:
                for entry in page.get("items", []):
                    track = _spotify_track(entry.get("track"), requester)
                    if track:
                        tracks.append(track)
                if len(tracks) >= MUSIC_PLAYLIST_LIMIT or not page.get("next"):
                    break
                page = await _spotify_get(session, page["next"])
        if not tracks:
            raise MusicError("No playable tracks were found in that Spotify link.")
        return tracks[:MUSIC_PLAYLIST_LIMIT], title


async def music_resolve(query, requester):
    """Return (tracks, playlist_title) for a URL, playlist URL or search phrase."""
    query = (query or "").strip().strip("<>")
    if not query:
        raise MusicError("Provide a song name or link.")
    spotify = SPOTIFY_RE.search(query)
    if spotify:
        return await spotify_resolve(spotify.group(1).lower(), spotify.group(2), requester)
    if re.match(r"^https?://", query, re.IGNORECASE):
        parsed = urlparse(query)
        host = parsed.netloc.lower()
        if "spotify.com" in host or host == "spotify.link":
            raise MusicError("I can read Spotify track, album and playlist links (open.spotify.com/...). "
                             "Short or artist links aren't supported; copy the full track/album/playlist link.")
        path = parsed.path.lower()
        is_playlist = any(marker in path for marker in ("/playlist", "/sets/", "/album", "/albums/"))
        if is_playlist:
            info = await _ytdl_run(_ytdl_extract_sync, query, flat=True, noplaylist=False, limit=MUSIC_PLAYLIST_LIMIT)
            entries = [e for e in (info.get("entries") or []) if e]
            tracks = [t for t in (_music_track_from_info(e, requester) for e in entries) if t]
            if not tracks:
                raise MusicError("That playlist is empty or private.")
            return tracks, info.get("title")
        info = await _ytdl_run(_ytdl_resolve_sync, query)
        if info.get("entries"):
            info = next((e for e in info["entries"] if e), None)
        track = _music_track_from_info(info, requester, resolved=True)
        if track is None:
            raise MusicError("Could not read that link.")
        return [track], None
    tracks = await music_search(query, requester, 1)
    return tracks[:1], None


async def music_search(query, requester, count=5):
    last_error = None
    for prefix in ("ytsearch", "scsearch"):
        try:
            info = await _ytdl_run(_ytdl_extract_sync, f"{prefix}{count}:{query}", flat=True, noplaylist=True)
        except MusicError as exc:
            last_error = exc
            continue
        tracks = [t for t in (_music_track_from_info(e, requester) for e in (info.get("entries") or []) if e) if t]
        if tracks:
            return tracks
    if last_error:
        raise last_error
    raise MusicError("No results found.")


class MusicPlayer:
    def __init__(self, guild_id):
        self.guild_id = guild_id
        settings = _music_settings(guild_id)
        self.queue = deque()
        self.history = deque(maxlen=50)
        self.current = None
        self.loop_mode = "off"          # off | track | queue
        self.autoplay = False
        self.filter_name = "off"
        self.volume = int(settings.get("volume", 100))
        self.stay_247 = bool(settings.get("stay_247", False))
        self.text_channel_id = None
        self.queue_event = asyncio.Event()
        self.task = None
        self.alone_task = None
        self.skip_votes = set()
        self.now_message = None
        self._skip_requested = False
        self._seek_to = None
        self._started = 0.0
        self._offset = 0.0
        self._paused_at = None
        self._paused_total = 0.0

    # ----- helpers -----
    @property
    def guild(self):
        return bot.get_guild(self.guild_id)

    @property
    def voice(self):
        guild = self.guild
        return guild.voice_client if guild else None

    @property
    def position(self):
        if self.current is None:
            return 0.0
        reference = self._paused_at or time.monotonic()
        position = (reference - self._started) - self._paused_total + self._offset
        if self.current.duration:
            position = min(position, self.current.duration)
        return max(position, 0.0)

    def ensure_task(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run())

    async def _announce(self, embed=None, view=None):
        channel = bot.get_channel(self.text_channel_id) if self.text_channel_id else None
        if channel is None or not hasattr(channel, "send"):
            return None
        guild = self.guild
        try:
            if guild and not channel.permissions_for(guild.me).send_messages:
                return None
            return await channel.send(embed=embed, view=view)
        except discord.HTTPException:
            return None

    # ----- control -----
    def skip(self):
        voice = self.voice
        if voice and (voice.is_playing() or voice.is_paused()):
            self._skip_requested = True
            voice.stop()
            return True
        return False

    def pause(self):
        voice = self.voice
        if voice and voice.is_playing():
            voice.pause()
            self._paused_at = time.monotonic()
            return True
        return False

    def resume(self):
        voice = self.voice
        if voice and voice.is_paused():
            voice.resume()
            if self._paused_at:
                self._paused_total += time.monotonic() - self._paused_at
            self._paused_at = None
            return True
        return False

    def seek(self, seconds):
        voice = self.voice
        if self.current is None or voice is None:
            return False
        if voice.is_paused():
            self.resume()
        self._seek_to = max(0.0, float(seconds))
        voice.stop()
        return True

    def set_filter(self, name):
        self.filter_name = name
        if self.current is not None:
            return self.seek(self.position)
        return True

    def set_volume(self, percent):
        self.volume = max(0, min(200, int(percent)))
        voice = self.voice
        if voice and isinstance(voice.source, discord.PCMVolumeTransformer):
            voice.source.volume = self.volume / 100
        settings = _music_settings(self.guild_id)
        settings["volume"] = self.volume
        save_config()

    def request_skip(self, member):
        voice = self.voice
        track = self.current
        if voice is None or track is None:
            return False, "Nothing is playing."
        listeners = [m for m in voice.channel.members if not m.bot]
        if member.id == track.requester_id or _music_has_dj_power(member) or len(listeners) <= 1:
            self.skip()
            return True, "Skipped."
        self.skip_votes.add(member.id)
        needed = len(listeners) // 2 + 1
        votes = len(self.skip_votes & {m.id for m in listeners})
        if votes >= needed:
            self.skip()
            return True, "Vote passed. Skipped."
        return False, f"Vote to skip registered ({votes}/{needed})."

    # ----- playback loop -----
    def _make_source(self, track, start):
        before = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
        if start > 0:
            before += f" -ss {start:.2f}"
        options = "-vn"
        chain = MUSIC_FILTERS.get(self.filter_name)
        if chain:
            options += f' -af "{chain}"'
        self._ffmpeg_log = tempfile.TemporaryFile()
        source = discord.FFmpegPCMAudio(track.stream_url, executable=FFMPEG_EXE or "ffmpeg", before_options=before, options=options, stderr=self._ffmpeg_log)
        return discord.PCMVolumeTransformer(source, volume=self.volume / 100)

    def _ffmpeg_tail(self):
        try:
            self._ffmpeg_log.seek(0)
            lines = self._ffmpeg_log.read().decode(errors="replace").strip().splitlines()
            return lines[-1][:300] if lines else ""
        except Exception:
            return ""

    async def _autoplay_fill(self):
        if not self.history:
            return 0
        seed = self.history[-1]
        known = {t.webpage_url for t in self.history}
        tracks = []
        try:
            match = re.search(r"[?&]v=([\w-]{11})", seed.webpage_url or "")
            if match:
                vid = match.group(1)
                info = await _ytdl_run(_ytdl_extract_sync, f"https://www.youtube.com/watch?v={vid}&list=RD{vid}",
                                       flat=True, noplaylist=False, limit=12)
                tracks = [t for t in (_music_track_from_info(e, None) for e in (info.get("entries") or []) if e) if t]
            if not tracks:
                tracks = await music_search(f"{seed.title} {seed.uploader or ''}".strip(), None, 6)
        except MusicError:
            return 0
        added = 0
        for track in tracks:
            if track.webpage_url in known:
                continue
            track.requester_id = 0
            track.requester_name = "Autoplay"
            self.queue.append(track)
            added += 1
            if added >= 3:
                break
        return added

    async def _run(self):
        try:
            while True:
                self.queue_event.clear()
                track = self.queue.popleft() if self.queue else None
                if track is None:
                    if self.autoplay and await self._autoplay_fill():
                        continue
                    self.current = None
                    try:
                        await asyncio.wait_for(self.queue_event.wait(), timeout=None if self.stay_247 else MUSIC_IDLE_TIMEOUT)
                        continue
                    except asyncio.TimeoutError:
                        await self._announce(feature_embed("Queue Finished", "The queue ended, so I left the voice channel."))
                        await music_destroy(self.guild_id)
                        return
                if not await self._play_track(track):
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Music player loop crashed in guild %s", self.guild_id)
            await music_destroy(self.guild_id)

    async def _play_track(self, track):
        voice = self.voice
        if voice is None or not voice.is_connected():
            await music_destroy(self.guild_id)
            return False
        self.current = track
        self.skip_votes.clear()
        try:
            await track.ensure_stream()
        except MusicError as exc:
            await self._announce(feature_embed(f"{ERROR_EMOJI} Playback Error", f"Could not play **{_md_safe(track.title)}**: {exc}"))
            self.current = None
            return True

        start = 0.0
        announce = True
        loop = asyncio.get_running_loop()
        while True:
            self._seek_to = None
            try:
                source = self._make_source(track, start)
                finished = asyncio.Event()

                def after(error, finished=finished):
                    if error:
                        logger.warning("Music playback error: %s", error)
                    loop.call_soon_threadsafe(finished.set)

                voice.play(source, after=after)
            except (discord.ClientException, OSError) as exc:
                await self._announce(feature_embed(f"{ERROR_EMOJI} Playback Error", f"FFmpeg could not start. Make sure `ffmpeg` is installed. ({str(exc)[:120]})"))
                self.current = None
                return True
            self._started = time.monotonic()
            self._offset = start
            self._paused_at = None
            self._paused_total = 0.0
            if announce:
                announce = False
                await self._send_now_playing()
            await finished.wait()
            if (self._seek_to is None and not self._skip_requested and voice.is_connected()
                    and time.monotonic() - self._started < 3 and (track.duration or 0) > 10):
                detail = self._ffmpeg_tail()
                logger.warning("Track ended immediately in guild %s. ffmpeg: %s", self.guild_id, detail)
                await self._announce(feature_embed(f"{ERROR_EMOJI} Playback Error",
                                                   "FFmpeg stopped right away, so no audio was sent." + (f"\n`{detail}`" if detail else "")))
            if self._seek_to is not None and voice.is_connected():
                start = self._seek_to
                continue
            break

        finished_track = self.current
        self.current = None
        if finished_track is not None:
            if self.loop_mode == "track" and not self._skip_requested:
                self.queue.appendleft(finished_track)
            else:
                self.history.append(finished_track)
                if self.loop_mode == "queue":
                    self.queue.append(finished_track)
        self._skip_requested = False
        return True

    async def _send_now_playing(self):
        if self.now_message is not None:
            try:
                await self.now_message.edit(view=None)
            except discord.HTTPException:
                pass
        self.now_message = await self._announce(_music_now_embed(self), MusicControlView(self.guild_id))


def _music_progress_bar(position, duration, width=18):
    if not duration:
        return "Live stream"
    ratio = min(max(position / duration, 0), 1)
    filled = int(ratio * width)
    return f"`{_fmt_duration(position)}` " + "━" * filled + "●" + "─" * (width - filled) + f" `{_fmt_duration(duration)}`"


def _music_now_embed(player):
    track = player.current
    if track is None:
        return feature_embed("Now Playing", "Nothing is playing right now.")
    embed = feature_embed("Now Playing", f"[{_md_safe(track.title, 100)}]({track.webpage_url})\n{_music_progress_bar(player.position, track.duration)}")
    embed.add_field(name="Requested by", value=track.requester_name, inline=True)
    embed.add_field(name="Duration", value=_fmt_duration(track.duration), inline=True)
    embed.add_field(name="Volume", value=f"{player.volume}%", inline=True)
    embed.add_field(name="Loop", value=player.loop_mode.title(), inline=True)
    embed.add_field(name="Filter", value="None" if player.filter_name == "off" else player.filter_name, inline=True)
    embed.add_field(name="Autoplay", value="On" if player.autoplay else "Off", inline=True)
    if player.queue:
        embed.add_field(name="Up next", value=_md_safe(player.queue[0].title, 80), inline=False)
    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)
    return embed


async def music_destroy(guild_id):
    player = MUSIC_PLAYERS.pop(guild_id, None)
    guild = bot.get_guild(guild_id)
    if player is not None:
        player.queue.clear()
        for task in (player.alone_task, player.task):
            if task is not None and not task.done() and task is not asyncio.current_task():
                task.cancel()
        if player.now_message is not None:
            try:
                await player.now_message.edit(view=None)
            except discord.HTTPException:
                pass
    voice = guild.voice_client if guild else None
    if voice is not None:
        try:
            voice.stop()
            await voice.disconnect(force=True)
        except Exception:
            pass


def _music_startup_report():
    if yt_dlp is not None:
        logger.info("yt-dlp %s loaded; cookies %s", getattr(getattr(yt_dlp, "version", None), "__version__", "?"),
                    "configured" if YTDL_COOKIE_FILE else "not configured")
        if shutil.which("deno") is None and shutil.which("node") is None:
            logger.warning("No JavaScript runtime (deno/node) found. Recent yt-dlp releases need one to read YouTube.")
    if FFMPEG_EXE is None:
        logger.warning("ffmpeg was not found; add `imageio-ffmpeg` to requirements or install ffmpeg on the host.")
    import importlib.util
    for module, label in (("nacl", "PyNaCl"), ("davey", "davey")):
        if importlib.util.find_spec(module) is None:
            logger.warning("%s is not installed; voice may connect but stay silent.", label)
    if not (os.getenv("SPOTIFY_CLIENT_ID") and os.getenv("SPOTIFY_CLIENT_SECRET")):
        logger.info("Spotify credentials not set; Spotify links will ask for SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET.")


_music_startup_report()


@bot.command(name="voicecheck", aliases=["vcheck"])
@commands.guild_only()
async def music_voicecheck_command(ctx):
    """Admins: report the voice/audio dependencies and the live voice connection state."""
    if not await manager_check(ctx):
        return
    import importlib.util
    def has(module):
        try:
            return "installed" if importlib.util.find_spec(module) else "MISSING"
        except Exception:
            return "MISSING"
    try:
        opus_loaded = discord.opus.is_loaded()
    except Exception:
        opus_loaded = False
    voice = ctx.guild.voice_client
    player = MUSIC_PLAYERS.get(ctx.guild.id)
    lines = [
        f"discord.py: `{discord.__version__}`",
        f"PyNaCl: `{has('nacl')}`",
        f"davey (voice encryption): `{has('davey')}`",
        f"Opus library: `{'loaded' if opus_loaded else 'NOT LOADED'}`",
        f"ffmpeg: `{FFMPEG_EXE or 'MISSING'}`",
        f"yt-dlp: `{getattr(getattr(yt_dlp, 'version', None), '__version__', 'MISSING') if yt_dlp else 'MISSING'}`",
        "",
    ]
    if voice is None or not voice.is_connected():
        lines.append("Voice: not connected")
    else:
        lines += [
            f"Voice: connected to {voice.channel.mention}",
            f"Playing: `{voice.is_playing()}` | Paused: `{voice.is_paused()}`",
            f"Source: `{type(voice.source).__name__ if voice.source else 'None'}`",
            f"Latency: `{voice.latency * 1000:.0f} ms`",
            f"Encryption mode: `{getattr(voice, 'mode', 'unknown')}`",
        ]
    if player is not None:
        lines.append(f"Queue: `{len(player.queue)}` | Volume: `{player.volume}%` | Position: `{_fmt_duration(player.position)}`")
    await ctx.reply(embed=feature_embed("Voice Check", "\n".join(lines)), mention_author=False)


@bot.command(name="ytupdate", aliases=["updateytdlp"])
async def music_ytupdate_command(ctx):
    """Bot staff: upgrade yt-dlp in the running environment (restart the bot afterwards)."""
    if not await global_staff_check(ctx):
        return
    async with ctx.typing():
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "-U", "--break-system-packages", "yt-dlp",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=180)
        except asyncio.TimeoutError:
            process.kill()
            return await ctx.reply("The update timed out.", mention_author=False)
    tail = output.decode(errors="replace").strip().splitlines()[-1:] or ["Done."]
    await ctx.reply(embed=feature_embed("yt-dlp Update", f"{tail[0][:300]}\nRestart the bot to load the new version. "
                                        "For a permanent fix, keep `yt-dlp` unpinned in requirements so each deploy pulls the latest."), mention_author=False)


# ----- permissions -----
def _music_has_dj_power(member):
    if has_admin_access(member) or member.guild_permissions.manage_guild:
        return True
    role_id = int(_music_settings(member.guild.id).get("dj_role_id") or 0)
    return bool(role_id and any(role.id == role_id for role in member.roles))


def _music_dj_ok(member):
    role_id = int(_music_settings(member.guild.id).get("dj_role_id") or 0)
    return True if not role_id else _music_has_dj_power(member)


async def _music_precheck(ctx, *, join=False, dj=False, playing=False, strict_dj=False):
    """Validate voice state and return the guild's MusicPlayer (creating/joining when join=True)."""
    voice_state = getattr(ctx.author, "voice", None)
    if not voice_state or not voice_state.channel:
        await _music_error(ctx, "Join a voice channel first.")
        return None
    channel = voice_state.channel
    voice = ctx.guild.voice_client
    player = MUSIC_PLAYERS.get(ctx.guild.id)
    connected = voice is not None and voice.is_connected()
    if connected and voice.channel.id != channel.id:
        busy = player is not None and (player.current is not None or player.queue)
        humans = [m for m in voice.channel.members if not m.bot]
        if busy and humans:
            await _music_error(ctx, f"I'm already playing in {voice.channel.mention}. Join that channel to control me.")
            return None
        if not join:
            await _music_error(ctx, f"I'm connected to {voice.channel.mention}. Join that channel first.")
            return None
    if not connected and not join:
        await _music_error(ctx, "I'm not connected to a voice channel.")
        return None
    if strict_dj and not _music_has_dj_power(ctx.author):
        await _music_error(ctx, "You need the DJ role or Manage Server permission for that.")
        return None
    if dj and not _music_dj_ok(ctx.author):
        await _music_error(ctx, "A DJ role is set in this server, and you don't have it.")
        return None
    if join:
        permissions = channel.permissions_for(ctx.guild.me)
        if not (permissions.view_channel and permissions.connect and permissions.speak):
            await _music_error(ctx, f"I need View Channel, Connect and Speak permissions in {channel.mention}.")
            return None
        try:
            if connected:
                if voice.channel.id != channel.id:
                    await voice.move_to(channel)
            else:
                await channel.connect(timeout=20, reconnect=True, self_deaf=True)
        except (discord.ClientException, discord.Forbidden, discord.HTTPException, asyncio.TimeoutError) as exc:
            await _music_error(ctx, f"Could not join the voice channel. Check my permissions and that voice dependencies are installed. ({str(exc)[:100]})")
            return None
        if player is None:
            player = MusicPlayer(ctx.guild.id)
            MUSIC_PLAYERS[ctx.guild.id] = player
        player.text_channel_id = ctx.channel.id
        player.ensure_task()
        return player
    if player is None:
        await _music_error(ctx, "Nothing is playing.")
        return None
    player.text_channel_id = ctx.channel.id
    if playing and player.current is None:
        await _music_error(ctx, "Nothing is playing.")
        return None
    return player


def _music_queue_embed(player, page):
    per_page = 10
    total_pages = max(1, math.ceil(len(player.queue) / per_page))
    page = max(1, min(page, total_pages))
    lines = []
    if player.current:
        lines.append(f"**Now playing:** [{_md_safe(player.current.title, 70)}]({player.current.webpage_url}) `{_fmt_duration(player.current.duration)}`\n")
    items = list(player.queue)[(page - 1) * per_page: page * per_page]
    for offset, track in enumerate(items, start=(page - 1) * per_page + 1):
        lines.append(f"`{offset}.` [{_md_safe(track.title, 55)}]({track.webpage_url}) `{_fmt_duration(track.duration)}` — {track.requester_name}")
    if not items:
        lines.append("The queue is empty.")
    total_seconds = sum(t.duration or 0 for t in player.queue)
    embed = feature_embed("Music Queue", "\n".join(lines))
    embed.set_footer(text=f"Page {page}/{total_pages} • {len(player.queue)} queued • {_fmt_duration(total_seconds)} • Loop: {player.loop_mode}")
    return embed, page, total_pages


class MusicQueueView(discord.ui.View):
    def __init__(self, guild_id, author_id, page, total_pages):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.author_id = author_id
        self.page = page
        self.total_pages = total_pages
        self._sync()

    def _sync(self):
        self.previous_button.disabled = self.page <= 1
        self.next_button.disabled = self.page >= self.total_pages

    async def _show(self, interaction):
        player = MUSIC_PLAYERS.get(self.guild_id)
        if player is None:
            return await interaction.response.edit_message(content="The queue no longer exists.", embed=None, view=None)
        embed, self.page, self.total_pages = _music_queue_embed(player, self.page)
        self._sync()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("Only the person who opened this queue can page it.", ephemeral=True)
        self.page -= 1
        await self._show(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("Only the person who opened this queue can page it.", ephemeral=True)
        self.page += 1
        await self._show(interaction)


def _music_interaction_player(interaction, *, dj=False):
    """Validate a button click. Returns (player, error_text)."""
    player = MUSIC_PLAYERS.get(interaction.guild_id)
    voice = interaction.guild.voice_client if interaction.guild else None
    if player is None or voice is None:
        return None, "The player is no longer active."
    member = interaction.user
    if not getattr(member, "voice", None) or member.voice.channel is None or member.voice.channel.id != voice.channel.id:
        return None, "Join my voice channel to use the controls."
    if dj and not _music_dj_ok(member):
        return None, "A DJ role is set in this server, and you don't have it."
    return player, None


class MusicControlView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="Pause/Resume", style=discord.ButtonStyle.primary)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        player, error = _music_interaction_player(interaction, dj=True)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)
        text = "Paused." if player.pause() else ("Resumed." if player.resume() else "Nothing to pause.")
        await interaction.response.send_message(text, ephemeral=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        player, error = _music_interaction_player(interaction)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)
        _, text = player.request_skip(interaction.user)
        await interaction.response.send_message(text, ephemeral=True)

    @discord.ui.button(label="Loop", style=discord.ButtonStyle.secondary)
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        player, error = _music_interaction_player(interaction, dj=True)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)
        player.loop_mode = {"off": "track", "track": "queue", "queue": "off"}[player.loop_mode]
        await interaction.response.send_message(f"Loop mode: **{player.loop_mode}**.", ephemeral=True)

    @discord.ui.button(label="Shuffle", style=discord.ButtonStyle.secondary)
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        player, error = _music_interaction_player(interaction, dj=True)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)
        items = list(player.queue)
        random.shuffle(items)
        player.queue = deque(items)
        await interaction.response.send_message("Queue shuffled.", ephemeral=True)

    @discord.ui.button(label="Queue", style=discord.ButtonStyle.secondary)
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = MUSIC_PLAYERS.get(interaction.guild_id)
        if player is None:
            return await interaction.response.send_message("The player is no longer active.", ephemeral=True)
        embed, _, _ = _music_queue_embed(player, 1)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        player, error = _music_interaction_player(interaction, dj=True)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)
        await interaction.response.send_message("Stopped playback and cleared the queue.", ephemeral=True)
        await _music_stop(player)


async def _music_stop(player):
    if player.stay_247:
        player.queue.clear()
        player.loop_mode = "off"
        player.autoplay = False
        player.skip()
    else:
        await music_destroy(player.guild_id)


# ----- queueing -----
async def _music_enqueue(ctx, player, tracks, playlist_title, mode):
    room = MUSIC_MAX_QUEUE - len(player.queue)
    if room <= 0:
        return await _music_error(ctx, f"The queue is full ({MUSIC_MAX_QUEUE} tracks).")
    tracks = tracks[:room]
    was_idle = player.current is None and not player.queue
    if mode in {"next", "now"}:
        for track in reversed(tracks):
            player.queue.appendleft(track)
    else:
        player.queue.extend(tracks)
    player.queue_event.set()
    if mode == "now" and player.current is not None:
        player.skip()
    if was_idle and not playlist_title:
        return
    if playlist_title:
        embed = feature_embed("Playlist Queued", f"Added **{len(tracks)}** tracks from **{_md_safe(playlist_title, 80)}**.")
    else:
        track = tracks[0]
        position = 1 if mode in {"next", "now"} else len(player.queue)
        embed = feature_embed("Track Queued", f"[{_md_safe(track.title, 90)}]({track.webpage_url})")
        embed.add_field(name="Position", value=str(position), inline=True)
        embed.add_field(name="Duration", value=_fmt_duration(track.duration), inline=True)
        if track.thumbnail:
            embed.set_thumbnail(url=track.thumbnail)
    await ctx.reply(embed=embed, mention_author=False)


async def _music_play(ctx, query, mode):
    if yt_dlp is None:
        return await _music_error(ctx, "Music is unavailable because `yt-dlp` is not installed on the host.")
    if FFMPEG_EXE is None:
        return await _music_error(ctx, "Music is unavailable because `ffmpeg` was not found. Add `imageio-ffmpeg` to requirements (or install ffmpeg on the host).")
    if not query and ctx.message.attachments:
        query = ctx.message.attachments[0].url
    if not query:
        player = MUSIC_PLAYERS.get(ctx.guild.id)
        if player and player.resume():
            return await ctx.reply(embed=feature_embed("Music", "Resumed playback."), mention_author=False)
        return await _music_error(ctx, f"Usage: `{ctx.clean_prefix}play <song name or link>`")
    player = await _music_precheck(ctx, join=True)
    if player is None:
        return
    async with ctx.typing():
        try:
            tracks, playlist_title = await music_resolve(query, ctx.author)
        except MusicError as exc:
            return await _music_error(ctx, str(exc))
    if not tracks:
        return await _music_error(ctx, "No results found.")
    await _music_enqueue(ctx, player, tracks, playlist_title, mode)


@bot.command(name="play", aliases=["p"])
@commands.guild_only()
async def music_play_command(ctx, *, query: str = None):
    await _music_play(ctx, query, "queue")


@bot.command(name="playnext", aliases=["pn", "playtop", "pt"])
@commands.guild_only()
async def music_playnext_command(ctx, *, query: str = None):
    if not query and not ctx.message.attachments:
        return await _music_error(ctx, f"Usage: `{ctx.clean_prefix}playnext <song name or link>`")
    await _music_play(ctx, query, "next")


@bot.command(name="playskip", aliases=["ps", "forceplay", "fp"])
@commands.guild_only()
async def music_playskip_command(ctx, *, query: str = None):
    if not query and not ctx.message.attachments:
        return await _music_error(ctx, f"Usage: `{ctx.clean_prefix}playskip <song name or link>`")
    if not _music_dj_ok(ctx.author):
        return await _music_error(ctx, "A DJ role is set in this server, and you don't have it.")
    await _music_play(ctx, query, "now")


class MusicSearchSelect(discord.ui.Select):
    def __init__(self, ctx, tracks):
        self.ctx = ctx
        self.tracks = tracks
        options = [
            discord.SelectOption(label=_md_safe(t.title, 100), value=str(i),
                                 description=f"{_md_safe(t.uploader or 'Unknown', 60)} • {_fmt_duration(t.duration)}"[:100])
            for i, t in enumerate(tracks)
        ]
        super().__init__(placeholder="Pick a track to queue", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("Only the person who searched can choose.", ephemeral=True)
        track = self.tracks[int(self.values[0])]
        await interaction.response.edit_message(content=f"Selected **{_md_safe(track.title, 80)}**.", embed=None, view=None)
        player = await _music_precheck(self.ctx, join=True)
        if player is None:
            return
        await _music_enqueue(self.ctx, player, [track], None, "queue")


@bot.command(name="search", aliases=["find"])
@commands.guild_only()
async def music_search_command(ctx, *, query: str):
    if yt_dlp is None or FFMPEG_EXE is None:
        return await _music_error(ctx, "Music is unavailable because `yt-dlp` or `ffmpeg` is not installed on the host.")
    if not getattr(ctx.author, "voice", None) or not ctx.author.voice.channel:
        return await _music_error(ctx, "Join a voice channel first.")
    async with ctx.typing():
        try:
            tracks = await music_search(query, ctx.author, 5)
        except MusicError as exc:
            return await _music_error(ctx, str(exc))
    lines = [f"`{i}.` {_md_safe(t.title, 70)} `{_fmt_duration(t.duration)}`" for i, t in enumerate(tracks, 1)]
    view = discord.ui.View(timeout=60)
    view.add_item(MusicSearchSelect(ctx, tracks))
    await ctx.reply(embed=feature_embed("Search Results", "\n".join(lines)), view=view, mention_author=False)


@bot.command(name="join", aliases=["connect", "summon"])
@commands.guild_only()
async def music_join_command(ctx):
    player = await _music_precheck(ctx, join=True)
    if player is not None:
        await ctx.reply(embed=feature_embed("Voice Connected", f"Joined {ctx.author.voice.channel.mention}."), mention_author=False)


@bot.command(name="leave", aliases=["disconnect", "dc"])
@commands.guild_only()
async def music_leave_command(ctx):
    voice = ctx.guild.voice_client
    if voice is None or not voice.is_connected():
        return await _music_error(ctx, "I'm not connected to a voice channel.")
    if MUSIC_PLAYERS.get(ctx.guild.id):
        if await _music_precheck(ctx, dj=True) is None:
            return
    elif not has_admin_access(ctx.author):
        return await _music_error(ctx, "Only administrators can disconnect me when nothing is playing.")
    await music_destroy(ctx.guild.id)
    await ctx.reply(embed=feature_embed("Voice Disconnected", "Left the voice channel."), mention_author=False)


@bot.command(name="stop")
@commands.guild_only()
async def music_stop_command(ctx):
    player = await _music_precheck(ctx, dj=True)
    if player is None:
        return
    await _music_stop(player)
    await ctx.reply(embed=feature_embed("Playback Stopped", "Stopped the music and cleared the queue."), mention_author=False)


@bot.command(name="pause")
@commands.guild_only()
async def music_pause_command(ctx):
    player = await _music_precheck(ctx, dj=True, playing=True)
    if player is None:
        return
    if player.pause():
        await ctx.reply(embed=feature_embed("Music", "Paused."), mention_author=False)
    else:
        await _music_error(ctx, "Playback is already paused.")


@bot.command(name="resume", aliases=["unpause"])
@commands.guild_only()
async def music_resume_command(ctx):
    player = await _music_precheck(ctx, dj=True, playing=True)
    if player is None:
        return
    if player.resume():
        await ctx.reply(embed=feature_embed("Music", "Resumed."), mention_author=False)
    else:
        await _music_error(ctx, "Playback is not paused.")


@bot.command(name="skip", aliases=["s", "next", "voteskip"])
@commands.guild_only()
async def music_skip_command(ctx):
    player = await _music_precheck(ctx, playing=True)
    if player is None:
        return
    _, text = player.request_skip(ctx.author)
    await ctx.reply(embed=feature_embed("Skip", text), mention_author=False)


@bot.command(name="forceskip", aliases=["fs", "fskip"])
@commands.guild_only()
async def music_forceskip_command(ctx):
    player = await _music_precheck(ctx, playing=True, strict_dj=True)
    if player is None:
        return
    player.skip()
    await ctx.reply(embed=feature_embed("Skip", "Force skipped."), mention_author=False)


@bot.command(name="skipto", aliases=["jump", "st"])
@commands.guild_only()
async def music_skipto_command(ctx, position: int):
    player = await _music_precheck(ctx, dj=True)
    if player is None:
        return
    if not 1 <= position <= len(player.queue):
        return await _music_error(ctx, f"Choose a position from 1 to {len(player.queue)}.")
    for _ in range(position - 1):
        player.history.append(player.queue.popleft())
    if player.current is not None:
        player.skip()
    else:
        player.queue_event.set()
    await ctx.reply(embed=feature_embed("Skip", f"Skipped to position {position}."), mention_author=False)


@bot.command(name="previous", aliases=["prev", "back"])
@commands.guild_only()
async def music_previous_command(ctx):
    player = await _music_precheck(ctx, dj=True)
    if player is None:
        return
    if not player.history:
        return await _music_error(ctx, "There is no previous track.")
    player.queue.appendleft(player.history.pop())
    if player.current is not None:
        player.skip()
    else:
        player.queue_event.set()
    await ctx.reply(embed=feature_embed("Music", "Going back to the previous track."), mention_author=False)


@bot.command(name="queue", aliases=["q", "list"])
@commands.guild_only()
async def music_queue_command(ctx, page: int = 1):
    player = MUSIC_PLAYERS.get(ctx.guild.id)
    if player is None or (player.current is None and not player.queue):
        return await _music_error(ctx, "The queue is empty.")
    embed, page, total = _music_queue_embed(player, page)
    view = MusicQueueView(ctx.guild.id, ctx.author.id, page, total) if total > 1 else None
    await ctx.reply(embed=embed, view=view, mention_author=False)


@bot.command(name="nowplaying", aliases=["np", "now", "current", "playing"])
@commands.guild_only()
async def music_nowplaying_command(ctx):
    player = MUSIC_PLAYERS.get(ctx.guild.id)
    if player is None or player.current is None:
        return await _music_error(ctx, "Nothing is playing.")
    await ctx.reply(embed=_music_now_embed(player), mention_author=False)


@bot.command(name="volume", aliases=["vol", "v"])
@commands.guild_only()
async def music_volume_command(ctx, level: int = None):
    player = await _music_precheck(ctx, dj=level is not None)
    if player is None:
        return
    if level is None:
        return await ctx.reply(embed=feature_embed("Volume", f"Current volume is **{player.volume}%**."), mention_author=False)
    if not 0 <= level <= 200:
        return await _music_error(ctx, "Volume must be between 0 and 200.")
    player.set_volume(level)
    note = " Values above 100 may distort." if level > 100 else ""
    await ctx.reply(embed=feature_embed("Volume", f"Volume set to **{level}%**.{note}"), mention_author=False)


@bot.command(name="loop", aliases=["repeat", "l"])
@commands.guild_only()
async def music_loop_command(ctx, mode: str = None):
    player = await _music_precheck(ctx, dj=True)
    if player is None:
        return
    aliases = {"off": "off", "none": "off", "disable": "off", "track": "track", "song": "track", "one": "track",
               "single": "track", "current": "track", "queue": "queue", "all": "queue", "playlist": "queue"}
    if mode is None:
        new_mode = {"off": "track", "track": "queue", "queue": "off"}[player.loop_mode]
    elif mode.lower() in aliases:
        new_mode = aliases[mode.lower()]
    else:
        return await _music_error(ctx, "Use `off`, `track`, or `queue` (or run the command with no argument to cycle).")
    player.loop_mode = new_mode
    await ctx.reply(embed=feature_embed("Loop", f"Loop mode: **{new_mode}**."), mention_author=False)


@bot.command(name="shuffle", aliases=["mix"])
@commands.guild_only()
async def music_shuffle_command(ctx):
    player = await _music_precheck(ctx, dj=True)
    if player is None:
        return
    if len(player.queue) < 2:
        return await _music_error(ctx, "Add at least two tracks to shuffle.")
    items = list(player.queue)
    random.shuffle(items)
    player.queue = deque(items)
    await ctx.reply(embed=feature_embed("Shuffle", f"Shuffled **{len(items)}** tracks."), mention_author=False)


@bot.command(name="clear", aliases=["cq", "clearqueue", "empty"])
@commands.guild_only()
async def music_clear_command(ctx):
    player = await _music_precheck(ctx, dj=True)
    if player is None:
        return
    count = len(player.queue)
    player.queue.clear()
    await ctx.reply(embed=feature_embed("Queue Cleared", f"Removed **{count}** queued track(s)."), mention_author=False)


@bot.command(name="remove", aliases=["rm"])
@commands.guild_only()
async def music_remove_command(ctx, position: str):
    player = await _music_precheck(ctx, dj=True)
    if player is None:
        return
    match = re.fullmatch(r"(\d+)(?:-(\d+))?", position.strip())
    if not match:
        return await _music_error(ctx, "Use a position like `3` or a range like `2-5`.")
    start = int(match.group(1))
    end = int(match.group(2) or start)
    if start > end or start < 1 or end > len(player.queue):
        return await _music_error(ctx, f"Choose positions between 1 and {len(player.queue)}.")
    items = list(player.queue)
    removed = items[start - 1:end]
    del items[start - 1:end]
    player.queue = deque(items)
    label = _md_safe(removed[0].title, 60) if len(removed) == 1 else f"{len(removed)} tracks"
    await ctx.reply(embed=feature_embed("Removed", f"Removed **{label}** from the queue."), mention_author=False)


@bot.command(name="move", aliases=["mv"])
@commands.guild_only()
async def music_move_command(ctx, source: int, destination: int):
    player = await _music_precheck(ctx, dj=True)
    if player is None:
        return
    size = len(player.queue)
    if not (1 <= source <= size and 1 <= destination <= size):
        return await _music_error(ctx, f"Positions must be between 1 and {size}.")
    items = list(player.queue)
    track = items.pop(source - 1)
    items.insert(destination - 1, track)
    player.queue = deque(items)
    await ctx.reply(embed=feature_embed("Moved", f"Moved **{_md_safe(track.title, 60)}** to position {destination}."), mention_author=False)


async def _music_seek_to(ctx, seconds):
    player = await _music_precheck(ctx, dj=True, playing=True)
    if player is None:
        return
    track = player.current
    if not track.duration:
        return await _music_error(ctx, "You can't seek in a live stream.")
    seconds = max(0, min(seconds, track.duration - 1))
    player.seek(seconds)
    await ctx.reply(embed=feature_embed("Seek", f"Moved to **{_fmt_duration(seconds)}**."), mention_author=False)


@bot.command(name="seek", aliases=["goto"])
@commands.guild_only()
async def music_seek_command(ctx, *, timestamp: str):
    seconds = _parse_timestamp(timestamp)
    if seconds is None:
        return await _music_error(ctx, "Use a time like `90`, `1:30`, or `1m30s`.")
    await _music_seek_to(ctx, seconds)


@bot.command(name="forward", aliases=["fwd", "ff"])
@commands.guild_only()
async def music_forward_command(ctx, seconds: int = 10):
    player = MUSIC_PLAYERS.get(ctx.guild.id)
    if player is None or player.current is None:
        return await _music_error(ctx, "Nothing is playing.")
    await _music_seek_to(ctx, int(player.position) + max(1, seconds))


@bot.command(name="rewind", aliases=["rew", "rwd"])
@commands.guild_only()
async def music_rewind_command(ctx, seconds: int = 10):
    player = MUSIC_PLAYERS.get(ctx.guild.id)
    if player is None or player.current is None:
        return await _music_error(ctx, "Nothing is playing.")
    await _music_seek_to(ctx, int(player.position) - max(1, seconds))


@bot.command(name="replay", aliases=["restart"])
@commands.guild_only()
async def music_replay_command(ctx):
    await _music_seek_to(ctx, 0)


async def _music_apply_filter(ctx, name):
    player = await _music_precheck(ctx, dj=True)
    if player is None:
        return
    name = MUSIC_FILTER_ALIASES.get(name.lower(), name.lower())
    if name not in MUSIC_FILTERS:
        return await _music_error(ctx, "Unknown filter. Available: " + ", ".join(f"`{n}`" for n in MUSIC_FILTERS) + ".")
    player.set_filter(name)
    text = "All filters removed." if name == "off" else f"Filter **{name}** applied. It takes effect within a moment."
    await ctx.reply(embed=feature_embed("Audio Filter", text), mention_author=False)


@bot.command(name="filter", aliases=["filters", "fx", "effect"])
@commands.guild_only()
async def music_filter_command(ctx, name: str = None):
    if name is None:
        player = MUSIC_PLAYERS.get(ctx.guild.id)
        active = player.filter_name if player else "off"
        return await ctx.reply(embed=feature_embed("Audio Filters", f"Active: **{active}**\nAvailable: " + ", ".join(f"`{n}`" for n in MUSIC_FILTERS)), mention_author=False)
    await _music_apply_filter(ctx, name)


@bot.command(name="bassboost", aliases=["bb", "bass"])
@commands.guild_only()
async def music_bassboost_command(ctx, level: str = "medium"):
    level = level.lower()
    mapping = {"low": "bassboost_low", "medium": "bassboost", "med": "bassboost", "high": "bassboost_high", "off": "off"}
    if level not in mapping:
        return await _music_error(ctx, "Use `low`, `medium`, `high`, or `off`.")
    await _music_apply_filter(ctx, mapping[level])


@bot.command(name="nightcore", aliases=["nc"])
@commands.guild_only()
async def music_nightcore_command(ctx):
    player = MUSIC_PLAYERS.get(ctx.guild.id)
    await _music_apply_filter(ctx, "off" if player and player.filter_name == "nightcore" else "nightcore")


@bot.command(name="vaporwave", aliases=["slowed"])
@commands.guild_only()
async def music_vaporwave_command(ctx):
    player = MUSIC_PLAYERS.get(ctx.guild.id)
    await _music_apply_filter(ctx, "off" if player and player.filter_name == "vaporwave" else "vaporwave")


@bot.command(name="8d")
@commands.guild_only()
async def music_8d_command(ctx):
    player = MUSIC_PLAYERS.get(ctx.guild.id)
    await _music_apply_filter(ctx, "off" if player and player.filter_name == "8d" else "8d")


@bot.command(name="autoplay", aliases=["ap"])
@commands.guild_only()
async def music_autoplay_command(ctx):
    player = await _music_precheck(ctx, dj=True)
    if player is None:
        return
    player.autoplay = not player.autoplay
    player.queue_event.set()
    await ctx.reply(embed=feature_embed("Autoplay", f"Autoplay is now **{'on' if player.autoplay else 'off'}**. I'll queue related tracks when the queue runs out."), mention_author=False)


@bot.command(name="247", aliases=["24/7", "stay"])
@commands.guild_only()
async def music_247_command(ctx):
    if not await manager_check(ctx):
        return
    player = await _music_precheck(ctx, join=True)
    if player is None:
        return
    player.stay_247 = not player.stay_247
    settings = _music_settings(ctx.guild.id)
    settings["stay_247"] = player.stay_247
    save_config()
    player.queue_event.set()
    await ctx.reply(embed=feature_embed("24/7 Mode", f"24/7 mode is now **{'on' if player.stay_247 else 'off'}**. "
                                        + ("I'll stay in the voice channel until it's turned off or the bot restarts." if player.stay_247 else "I'll leave when idle or alone.")), mention_author=False)


@bot.command(name="djrole", aliases=["dj"])
@commands.guild_only()
async def music_djrole_command(ctx, role: discord.Role = None):
    if not await manager_check(ctx):
        return
    settings = _music_settings(ctx.guild.id)
    if role is None:
        current = int(settings.get("dj_role_id") or 0)
        text = f"Current DJ role: <@&{current}>." if current else f"No DJ role is set, so everyone can control music. Use `{ctx.clean_prefix}djrole @role` to set one, or `{ctx.clean_prefix}djrole off`."
        return await ctx.reply(embed=feature_embed("DJ Role", text), mention_author=False, allowed_mentions=discord.AllowedMentions.none())
    settings["dj_role_id"] = role.id
    save_config()
    await ctx.reply(embed=feature_embed("DJ Role", f"DJ role set to {role.mention}. Members with it (and Manage Server) control the player."), mention_author=False, allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="djoff")
@commands.guild_only()
async def music_djoff_command(ctx):
    if not await manager_check(ctx):
        return
    _music_settings(ctx.guild.id).pop("dj_role_id", None)
    save_config()
    await ctx.reply(embed=feature_embed("DJ Role", "DJ role removed. Everyone in the voice channel can control music."), mention_author=False)


@bot.command(name="history", aliases=["recent"])
@commands.guild_only()
async def music_history_command(ctx):
    player = MUSIC_PLAYERS.get(ctx.guild.id)
    if player is None or not player.history:
        return await _music_error(ctx, "No tracks have been played yet.")
    recent = list(player.history)[-10:][::-1]
    lines = [f"`{i}.` [{_md_safe(t.title, 70)}]({t.webpage_url}) `{_fmt_duration(t.duration)}`" for i, t in enumerate(recent, 1)]
    await ctx.reply(embed=feature_embed("Recently Played", "\n".join(lines)), mention_author=False)


@bot.command(name="grab", aliases=["save", "yoink"])
@commands.guild_only()
async def music_grab_command(ctx):
    player = MUSIC_PLAYERS.get(ctx.guild.id)
    if player is None or player.current is None:
        return await _music_error(ctx, "Nothing is playing.")
    track = player.current
    embed = feature_embed("Saved Track", f"[{_md_safe(track.title, 100)}]({track.webpage_url})\nFrom **{ctx.guild.name}**")
    embed.add_field(name="Duration", value=_fmt_duration(track.duration), inline=True)
    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)
    try:
        await ctx.author.send(embed=embed)
        await ctx.reply(embed=feature_embed("Saved Track", "Sent the current track to your DMs."), mention_author=False)
    except (discord.Forbidden, discord.HTTPException):
        await _music_error(ctx, "I couldn't DM you. Enable direct messages from server members.")


@bot.listen("on_voice_state_update")
async def kanade_music_voice_listener(member, before, after):
    guild = member.guild
    player = MUSIC_PLAYERS.get(guild.id)
    if player is None:
        return
    if bot.user and member.id == bot.user.id:
        if after.channel is None:
            # Disconnected (kicked, channel deleted, or manual disconnect).
            await music_destroy(guild.id)
        return
    voice = guild.voice_client
    if voice is None or voice.channel is None:
        return
    humans = [m for m in voice.channel.members if not m.bot]
    if humans or player.stay_247:
        if player.alone_task is not None and not player.alone_task.done():
            player.alone_task.cancel()
        player.alone_task = None
        return
    if player.alone_task is None or player.alone_task.done():
        async def leave_when_alone():
            try:
                await asyncio.sleep(MUSIC_ALONE_TIMEOUT)
                current = MUSIC_PLAYERS.get(guild.id)
                vc = guild.voice_client
                if current is player and vc and vc.channel and not [m for m in vc.channel.members if not m.bot]:
                    await player._announce(feature_embed("Voice Disconnected", "Everyone left, so I left the voice channel."))
                    await music_destroy(guild.id)
            except asyncio.CancelledError:
                pass
        player.alone_task = asyncio.create_task(leave_when_alone())


@bot.event
async def on_message(message):
    """Keep command processing separate from the independent moderation listeners."""
    if message.author.bot:
        return

    # Handle normal prefixed commands first; The anti-spam and anti-link listeners run independently.
    ctx = await bot.get_context(message)
    if ctx.command is not None:
        await bot.process_commands(message)
        return

    # Preserve no-prefix command access without making AutoMod depend on this event.
    if has_no_prefix_access(message) and message.guild is not None:
        raw_content = (message.content or "").strip()
        if raw_content:
            active_prefix = config.get("guild_prefixes", {}).get(str(message.guild.id), PREFIX)
            if not isinstance(active_prefix, str) or not active_prefix:
                active_prefix = PREFIX
            synthetic_message = copy.copy(message)
            synthetic_message.content = f"{active_prefix}{raw_content}"
            no_prefix_ctx = await bot.get_context(synthetic_message)
            if no_prefix_ctx.command is not None:
                await bot.invoke(no_prefix_ctx)
                return

    await bot.process_commands(message)

# ==================== ERRORS / STARTUP ====================
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        # Avoid cluttering chat for every unknown command, but explain the requested command.
        return
    if isinstance(error, commands.MissingPermissions):
        return await ctx.reply(
            "You do not have permission to use this command.",
            mention_author=False
        )
    if isinstance(error, commands.MissingRequiredArgument):
        command_name = ctx.command.qualified_name if ctx.command else "command"
        missing = getattr(error, "param", None)
        missing_name = getattr(missing, "name", "argument")
        embed = StyledEmbed(
            title=f"{ERROR_EMOJI} Error",
            description=f"Missing argument: `{missing_name}`. Use `{ctx.prefix}help {command_name}` for usage.",
        )
        return await ctx.reply(embed=embed, mention_author=False)
    if isinstance(error, commands.MemberNotFound):
        return await ctx.reply(
            "Member not found. Mention them or use their ID.",
            mention_author=False
        )
    if isinstance(error, commands.BadArgument):
        return await ctx.reply(
            "Invalid argument. Check the command syntax.",
            mention_author=False
        )
    logger.error(
        "Command error",
        exc_info=(type(error), error, error.__traceback__)
    )
@bot.tree.error
async def slash_error(interaction, error):
    logger.error("Slash command failed: %r", error)
    try:
        message = (
            "Command failed. Check bot permissions "
            "and role hierarchy."
        )
        if interaction.response.is_done():
            await interaction.followup.send(
                message,
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                message,
                ephemeral=True
            )
    except discord.HTTPException:
        pass
async def main():
    if not TOKEN:
        raise SystemExit(
            "Bot token is missing. In Railway > antinuker > Variables, add DISCORD_TOKEN "
            "with the raw bot token copied from Discord Developer Portal > Bot. "
            "The token must not be stored in config.json."
        )

    retry_delay = 5
    async with bot:
        while True:
            try:
                await bot.start(TOKEN)
                # start() normally runs until the bot closes; return if it exits cleanly.
                break
            except discord.LoginFailure as error:
                # 401 Unauthorized means the supplied token is invalid, revoked, or wrong.
                # It is not a transient network failure, so do not retry in a restart loop.
                logger.critical(
                    "Discord rejected the bot token (401 Unauthorized). Update the secret in "
                    "Railway Variables using the current token from Developer Portal > Bot. "
                    "This version reads only DISCORD_TOKEN; config.json is not used for credentials. "
                    "The token is not printed."
                )
                raise SystemExit(1) from error
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as error:
                logger.warning(
                    "Temporary Discord connection failure: %s. Retrying in %s seconds.",
                    error, retry_delay
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped.")
