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
import time
BOT_STARTED_AT = time.monotonic()
import asyncio
import logging
import re
import secrets
import string
from pathlib import Path
import aiohttp
from datetime import timedelta
from collections import defaultdict, deque
import discord
from discord.ext import commands, tasks
from discord import app_commands

# ==================== EMBED STYLE ====================
_DiscordEmbed = discord.Embed
_EMBED_PURPLE = discord.Color.from_rgb(35, 0, 55)

def _clean_embed_text(value):
    """Normalize whitespace without changing the meaning of command output."""
    if not isinstance(value, str):
        return value
    value = value.replace("\r\n", "\n").replace("\r", "\n")
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
    # Discord controls the embed background. Leaving color unset uses the
    # client's native dark background without a colored accent/sidebar.
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
    """Consistent Kanade branding with readable spacing and safe defaults."""
    def __init__(self, *args, **kwargs):
        # Madoka-style clean embeds: omit the colored side accent entirely.
        kwargs["color"] = None
        # Keep command embeds minimal: no automatic title decoration.
        title = kwargs.get("title")
        if isinstance(title, str):
            # Retain the bot-name header only on the main help/info pages.
            normalized_title = title.lower()
            keep_brand = any(token in normalized_title for token in (
                "help", "about", "information", " bot info", "server info", "sinfo"
            ))
            if not keep_brand and "$pyx security" in normalized_title:
                title = re.sub(r"(?i)\Kanade\s*\|?\s*", "", title).strip(" |—-")
                title = re.sub(r"^[^\w$]+\s*", "", title).strip()
                kwargs["title"] = title or "Details"
        if isinstance(kwargs.get("description"), str):
            description = _clean_embed_text(kwargs["description"])
            title_text = str(kwargs.get("title", "")).lower()
            footer_text = str(getattr(kwargs.get("footer"), "text", "") or "").lower()
            # Error details should stay direct; structured security-log layouts
            # have their own formatting and must not be prefixed line-by-line.
            is_error = "error" in title_text or "failed" in title_text or "denied" in title_text
            is_security_log = "security logs" in footer_text
            if is_error and isinstance(kwargs.get("title"), str):
                kwargs["title"] = re.sub(r"^(?:˃|➔|➜|→|▸|›|➤)\s*", "", kwargs["title"]).strip()
                if "error" in kwargs["title"].lower() and not kwargs["title"].startswith("⚒"):
                    kwargs["title"] = "⚒ " + kwargs["title"]
            # Do not decorate every response line. Command-list views call
            # _arrow_lines explicitly; ordinary command replies stay minimal.
            kwargs["description"] = description
        super().__init__(*args, **kwargs)
        bot_obj = globals().get("bot")
        user = getattr(bot_obj, "user", None) if bot_obj else None
        # Ordinary command embeds stay minimal: no repeated bot-name/developer footer.
        # Help and information views add their own branding explicitly where needed.

    def add_field(self, *, name, value, inline=True):
        name = _clean_embed_text(name)
        value = _clean_embed_text(value)
        if not name:
            name = "Details"
        if not value:
            value = "—"
        return super().add_field(name=name, value=value, inline=inline)

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




def save_config():
    temporary = CONFIG_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(config, file, indent=4)
    os.replace(temporary, CONFIG_FILE)


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
    return user_id in user_ids
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
async def send_log(guild, title, description, color=None):
    """Send logs only to a channel explicitly configured for this guild."""
    if guild is None:
        return
    if color is None:
        color = discord.Color.blurple()
    per_guild = config.get("log_channels", {})
    channel_id = int(per_guild.get(str(guild.id), 0) or 0)
    # Compatibility: honor the legacy global channel only if it belongs to this guild.
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
    embed = StyledEmbed(
        title=title,
        description=formatted_description,
        color=color,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="Kanade • Security Logs")
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        logger.warning("Could not send opt-in log in guild %s", guild.id)
async def notify_owners(guild, message):
    """Send anti-nuke alert embeds to the developer and configured owner IDs."""
    if not config.get("notify_owner", True):
        logger.info("Owner anti-nuke DM skipped: notify_owner is disabled.")
        return

    # Include the configured developer ID as well as any extra owners. Previously
    # this loop used only config["owner_ids"], which defaults to [], so a fresh
    # config silently sent alerts to nobody.
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

    embed = StyledEmbed(
        title="Kanade | Anti-Nuke Alert",
        description=(message or "An anti-nuke event was detected.")[:4000],
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Server", value=f"{guild.name[:200]}\n`{guild.id}`", inline=False)
    embed.set_footer(text="Kanade • Owner Notification")

    for owner_id in owner_ids:
        try:
            user = bot.get_user(owner_id)
            if user is None:
                user = await bot.fetch_user(owner_id)
            await user.send(embed=embed)
            logger.info("Sent owner anti-nuke DM to user ID %s for guild %s.", owner_id, guild.id)
        except discord.Forbidden:
            logger.warning(
                "Could not DM owner ID %s: Discord blocked the DM (privacy settings, "
                "blocked bot, or no available DM channel).", owner_id
            )
        except (discord.HTTPException, TypeError, ValueError):
            logger.exception("Could not send owner anti-nuke DM to user ID %s.", owner_id)
# Keep a short per-member message history and prevent repeated punishment.



# ==================== SERVER SNAPSHOTS ====================
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
    global STARTUP_SNAPSHOTS_DONE, SLASH_COMMANDS_SYNCED
    logger.info("Logged in as %s (%s)", bot.user, bot.user.id)

    # Snapshot once per process. Re-running this for every gateway reconnect
    # can add avoidable CPU/memory work on small hosting plans.
    if not STARTUP_SNAPSHOTS_DONE:
        for guild in bot.guilds:
            try:
                await snapshot_guild(guild)
            except Exception:
                logger.exception("Could not snapshot guild %s", guild.id)
        STARTUP_SNAPSHOTS_DONE = True

    # Global command sync is an API operation; don't repeat it on reconnect.
    if not SLASH_COMMANDS_SYNCED:
        try:
            synced = await bot.tree.sync()
            logger.info("Synced %s slash commands", len(synced))
            SLASH_COMMANDS_SYNCED = True
        except Exception:
            logger.exception("Slash command sync failed; it can retry on next reconnect")
    await update_presence()
    if not presence_refresh_loop.is_running():
        presence_refresh_loop.start()

async def update_presence():
    try:
        # Sum member counts across all connected guilds. Members shared by
        # multiple guilds are counted once per guild (combined membership).
        total_users = sum(
            guild.member_count if guild.member_count is not None else len(guild.members)
            for guild in bot.guilds
        )
        # Discord requires a valid Twitch/YouTube URL for Streaming presence.
        # Replace this URL with your own channel if you want the activity to link there.
        await bot.change_presence(
            status=discord.Status.online,
            activity=discord.Streaming(
                name=f"Watching over {total_users:,} users",
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
    if member.bot or before.channel == after.channel:
        return
    if before.channel is None and after.channel is not None:
        title = "🔊 Member Joined VC"
        detail = f"**User:** {member.mention} (`{member.id}`)\n**Voice Channel:** {after.channel.mention}"
        color = discord.Color.green()
    elif before.channel is not None and after.channel is None:
        title = "🔇 Member Left VC"
        detail = f"**User:** {member.mention} (`{member.id}`)\n**Voice Channel:** {before.channel.mention}"
        color = discord.Color.orange()
    else:
        title = "🔁 Member Switched VC"
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
                     f"Prefix: `{p}`\nDeveloper: SPYWXRE\n\n"
                     "Choose a category to view commands. Bot staff commands are in `admhelp`."),
            "moderation": ("Moderation",
                           f"`{p}kick @member [reason]` — Kick a member\n"
                           f"`{p}ban @member|<user ID> [reason]` — Ban a member\n"
                           f"`{p}timeout @member|<user ID> <1min|1d|1w> [reason]` — Timeout a member\n"
                           f"`{p}unban <user ID>` — Unban a user by ID\n"
                           f"`{p}purge <amount>` — Delete recent messages\n"
                           f"`{p}purge all` — Clear accessible channel history\n"
                           f"`{p}purge @user <amount>` — Purge one member's messages\n"
                           f"`{p}lock` — Lock the current channel\n"
                           f"`{p}unlock` — Restore saved channel permissions\n"
                           f"`{p}lockdown` / `{p}unlockdown` — Lock or restore server channels"),
            "roles": ("Roles",
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
                           "`/lockdown` / `/unlockdown` — Slash-command lockdown controls"),
            "backups": ("Backups & Logs",
                        f"`{p}backup create` — Create a server backup (Premium)\n"
                        f"`{p}backup load` — Restore a server backup (Premium)\n"
                        f"`{p}setuplogs #channel` — Set up security logs\n"
                        f"`{p}disablelogs` — Disable security logs\n"
                        f"`{p}snipe` — View the latest deleted message"),
            "utilities": ("Information",
                          f"`{p}afk [message]` — Set your AFK status\n"
                          f"`{p}server` — View server information\n"
                          f"`{p}info` — Explore Kanade information\n"
                          f"`{p}av [@user]` — View a user's avatar (Premium)\n"
                          f"`{p}banner [@user|user ID]` — View a user's Discord banner (Premium)\n"
                          f"`{p}ping` — Show bot gateway latency\n"
                          f"`{p}joinvc` — Join your current voice channel"),
            "premium": ("Premium",
                        f"`{p}cb <image URL>` — Change the bot's banner (Premium)\n"
                        f"`{p}cp <image URL>` — Change the bot's profile picture (Premium)\n"
                        f"`{p}cn <new name>` — Change the bot's nickname in this server (Premium)\n"
                        f"`{p}cn reset` — Reset the bot's server nickname (Premium)\n"
                        f"`{p}steal <custom emoji> [name]` — Add a custom emoji (Premium)\n\n"
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
            self.embed.set_footer(text="Kanade • Developed by SPYWXRE", icon_url=bot.user.display_avatar.url)
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
                       f"`{p}np <user ID|@user>` — Allow no-prefix commands in this server\n"
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
        title=f"🖼️ Avatar: {discord.utils.escape_markdown(target.name)}",
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
            title=f"🖼️ Banner: {discord.utils.escape_markdown(target.name)}",
            description=f"**User:** {target.mention if isinstance(target, discord.Member) else target.name}\n**User ID:** `{target.id}`\nThis user does not have a profile banner set.",
            color=discord.Color.from_rgb(128, 0, 255),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Kanade • Premium Feature", icon_url=ctx.bot.user.display_avatar.url if ctx.bot.user else None)
        await ctx.reply(embed=embed, mention_author=False)
        return

    banner_url = banner_asset.url
    embed = StyledEmbed(
        title=f"🖼️ Banner: {discord.utils.escape_markdown(target.name)}",
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
            "home": ("Overview", f"📶 **Latency:** `{latency_text}`\n⏱️ **Uptime:** `{uptime_text}`\n👥 **Users:** `{total_users:,}`\n🌐 **Servers:** `{len(bot.guilds):,}`\n\nA security and moderation bot built to help protect Discord communities.\n\nDeveloped by **SPYWXRE**\n\nSelect a category below to explore features."),
            "security": ("Security", "• Anti-nuke monitoring and configurable thresholds\n• Anti-raid join detection and optional lockdown\n• Whitelist management and security status checks\n• Security logs and owner notifications\n• Channel/role restore support where configured"),
            "moderation": ("Moderation", "• Kick, ban, timeout, and remove timeouts\n• Assign or remove roles\n• Purge messages and view deleted messages with `!snipe`\n• Lock/unlock channels and server lockdown\n• AFK status and voice-channel join support"),
            "backups": ("Backups & Logs", "• Create and restore server backups (Premium)\n• Configure security log channels\n• Avatar and banner lookup commands\n• Configure server-specific bot profile (Premium)\n• Configure server command prefix (Premium)\n• Prefix and slash commands"),
            "stats": ("Statistics", f"📶 **Latency:** `{latency_text}`\n⏱️ **Uptime:** `{uptime_text}`\n• **Status:** `{'Online' if bot.is_ready() else 'Connecting'}`\n👥 **Users:** `{total_users:,}` *(approximate; users across servers may overlap)*\n🌐 **Servers:** `{len(bot.guilds):,}`\n• **Prefix commands:** `{len(bot.commands):,}`\n• **Slash commands:** `{len(bot.tree.get_commands()):,}`\n• **Bot ID:** `{bot_user.id if bot_user else 'Unavailable'}`"),
        }
        title, description = labels.get(category, labels["home"])
        description = _arrow_lines(description)
        self.embed = StyledEmbed(title=title, description=description, color=discord.Color.from_rgb(35, 0, 55), timestamp=discord.utils.utcnow())
        if bot_user:
            self.embed.set_author(name="Kanade", icon_url=bot_user.display_avatar.url)
            self.embed.set_thumbnail(url=bot_user.display_avatar.url)
            self.embed.set_footer(text="🛡️ Kanade • Developed by SPYWXRE", icon_url=bot_user.display_avatar.url)
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
async def ping_command(ctx):
    """Show the bot's current Discord gateway latency in a branded embed."""
    latency_ms = round(bot.latency * 1000)
    connection_status = "Online" if bot.is_ready() else "Connecting"
    latency_note = "Connection looks healthy." if latency_ms < 200 else (
        "Connection is a little slow." if latency_ms < 400 else
        "Connection is currently experiencing high latency."
    )

    embed = StyledEmbed(
        title="📶 Connection Status",
        description=(
            f"📶 Latency: `{latency_ms} ms`\n"
            f"🟢 Status: {connection_status}\n"
            f"ℹ️ {latency_note}"
        ),
        color=discord.Color.from_rgb(35, 0, 55),
        timestamp=discord.utils.utcnow(),
    )
    if bot.user:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    await ctx.reply(embed=embed, mention_author=False)

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
        embed.set_footer(text="Kanade • Developed by spywxre", icon_url=bot.user.display_avatar.url if bot.user else None)
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
    embed.set_footer(text="Kanade • Developed by spywxre")
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
    embed.set_footer(text="Kanade • Developed by spywxre")
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
            title=f"Kanade | {title}",
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
async def unban_command(ctx, user_id: str):
    """Unban by numeric Discord user ID (the user need not be in the server)."""
    raw_id = user_id.strip().strip("<@!>")
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
    embed.set_footer(text="Kanade")
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
        embed.set_footer(text="Kanade • Developed by spywxre")
        return await ctx.reply(embed=embed, mention_author=False)

    if not has_admin_access(ctx.author) and not is_owner_id(ctx.author.id):
        embed = StyledEmbed(
            title="Kanade | Permission Denied",
            description="Administrator permission required.",
            color=purple
        )
        embed.set_footer(text="Kanade • Developed by spywxre")
        return await ctx.reply(embed=embed, mention_author=False)

    if new_prefix is None:
        embed = StyledEmbed(
            title="Kanade | Server Prefix",
            description=(
                f"Prefix: `{current_prefix}`\nUsage: `{current_prefix}prefix <new_prefix>`"
            ),
            color=purple
        )
        embed.set_footer(text="Kanade • Developed by spywxre")
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
        embed.set_footer(text="Kanade • Developed by spywxre")
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
    embed.set_footer(text="Kanade • Developed by spywxre")
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
        embed.set_footer(text="Kanade • Developed by spywxre")
        return await ctx.reply(embed=embed, mention_author=False)

    enabled = mode == "on"
    set_guild_setting(ctx.guild, "antinuke_enabled", enabled)

    embed = StyledEmbed(
        title=f"Kanade | Anti-Nuke {'Enabled' if enabled else 'Disabled'}",
        description=(
            f"🛡️ Anti-nuke protection has been **{'ENABLED' if enabled else 'DISABLED'}** "
            f"for **{ctx.guild.name}**."
        ),
        color=purple
    )
    embed.add_field(
        name="Status",
        value="🟢 Protection is active." if enabled else "🔴 Protection is inactive.",
        inline=False
    )
    embed.add_field(
        name="Changed by",
        value=ctx.author.mention,
        inline=True
    )
    embed.set_footer(text="Kanade • Developed by spywxre")
    await ctx.reply(embed=embed, mention_author=False)
# ==================== PER-SERVER PREFIX WHITELIST ====================
@bot.group(name="whitelist", invoke_without_command=True)
@commands.guild_only()
async def whitelist_command(ctx):
    await ctx.reply(
        "Usage: `!whitelist add @member`, "
        "`!whitelist remove @member`, or `!whitelist list`",
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
PURPLE = discord.Color.from_rgb(128, 0, 255)
BACKUP_DIR = "backups"


def feature_embed(title, description, *, color=PURPLE):
    embed = StyledEmbed(title=f"🛡️ Kanade | {title}", description=description, color=color, timestamp=discord.utils.utcnow())
    embed.set_footer(text="Kanade")
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


async def restore_backup(guild, code=None, *, replace_channels=True):
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
    if not guild.me.guild_permissions.manage_channels:
        raise discord.Forbidden(response=None, message="The bot needs Manage Channels permission to replace channels.")
    if data.get("roles") and not guild.me.guild_permissions.manage_roles:
        raise discord.Forbidden(response=None, message="The bot needs Manage Roles permission to restore roles.")

    # Match the source @everyone role to the destination server's @everyone role.
    role_map = {role.id: role for role in guild.roles}
    role_map[int(data.get("everyone_role_id", guild.default_role.id))] = guild.default_role
    role_map[guild.default_role.id] = guild.default_role
    for item in sorted(data.get("roles", []), key=lambda x: x.get("position", 0)):
        existing = guild.get_role(int(item.get("id", 0)))
        if existing is None:
            existing = discord.utils.get(guild.roles, name=item["name"])
        if existing is None:
            try:
                existing = await guild.create_role(
                    name=item["name"], permissions=discord.Permissions(int(item.get("permissions", 0))),
                    colour=discord.Colour(int(item.get("color", 0))), hoist=bool(item.get("hoist", False)),
                    mentionable=bool(item.get("mentionable", False)), reason="Kanade Premium backup restore"
                )
            except (discord.Forbidden, discord.HTTPException) as exc:
                continue
        role_map[int(item.get("id", 0))] = existing

    channel_items = data.get("channels", [])
    if replace_channels:
        # Delete ordinary channels first, then categories, so no category retains children.
        # This intentionally replaces the destination's channel layout with the backup.
        current_channels = list(guild.channels)
        current_channels.sort(key=lambda channel: isinstance(channel, discord.CategoryChannel))
        for channel in current_channels:
            try:
                await channel.delete(reason="Kanade backup restore: replacing channels with selected backup")
            except (discord.Forbidden, discord.HTTPException) as exc:
                raise RuntimeError(
                    f"Could not delete existing channel/category `{channel.name}`. "
                    "No further channels were deleted; check the bot's Manage Channels permission and role position."
                ) from exc
        channel_map = {}
    else:
        channel_map = {channel.id: channel for channel in guild.channels}

    # Restore categories before their children. Existing channels are replaced by default.
    ordered = sorted(channel_items, key=lambda x: (0 if x.get("type") == "category" else 1, x.get("position", 0)))
    for item in ordered:
        old_id = int(item.get("id", 0))
        existing = guild.get_channel(old_id)
        if existing is None:
            existing = discord.utils.get(guild.channels, name=item.get("name"), type={"text": discord.ChannelType.text, "voice": discord.ChannelType.voice, "category": discord.ChannelType.category, "stage_voice": discord.ChannelType.stage_voice}.get(item.get("type")))
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
        try:
            if existing is None:
                ctype = item.get("type")
                common = {"name": item["name"], "category": category, "overwrites": overwrites, "reason": "Kanade Premium backup restore"}
                if ctype == "category":
                    existing = await guild.create_category(item["name"], overwrites=overwrites, reason=common["reason"])
                elif ctype == "voice":
                    existing = await guild.create_voice_channel(**common, bitrate=int(item.get("bitrate", 64000)), user_limit=int(item.get("user_limit", 0)))
                elif ctype == "stage_voice":
                    existing = await guild.create_stage_channel(**common)
                elif ctype == "forum":
                    existing = await guild.create_forum(**common)
                else:
                    existing = await guild.create_text_channel(**common, topic=item.get("topic"), nsfw=bool(item.get("nsfw", False)), slowmode_delay=int(item.get("slowmode_delay", 0)))
                channel_map[old_id] = existing
            else:
                # Apply saved overwrites to listed targets without deleting unrelated extra overwrites.
                for target, overwrite in overwrites.items():
                    await existing.set_permissions(target, overwrite=overwrite, reason="Kanade Premium backup restore")
                channel_map[old_id] = existing
        except (discord.Forbidden, discord.HTTPException, TypeError, ValueError) as exc:
            logger.warning("Backup restore could not create channel %s in %s: %s", item.get("name"), guild.id, exc)
    return len(role_map), len(channel_map)


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
    # Explicit confirmation is required because this removes every current channel/category.
    prompt = await ctx.reply(
        embed=feature_embed(
            "Confirm Channel Replacement",
            "This will **delete every existing channel and category** in this server, then recreate the backup layout.\n"
            "Roles are not deleted. React with ✅ within 30 seconds to continue, or ❌ to cancel.",
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
        roles_count, channels_count = await restore_backup(ctx.guild, code=code, replace_channels=True)
        report = feature_embed("Backup Restored", f"Roles available: **{roles_count}**\nChannels recreated: **{channels_count}**\nExisting channels/categories were replaced.", color=discord.Color.green())
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


@bot.command(name="setuplogs")
async def setuplogs_command(ctx, channel: discord.TextChannel = None):
    if not await manager_check(ctx):
        return
    channel = channel or ctx.channel
    config.setdefault("log_channels", {})[str(ctx.guild.id)] = channel.id
    # Retain the old setting as a compatibility field without making it the global destination.
    save_config()
    await ctx.reply(embed=feature_embed("Security Logs Enabled", f"Logs will be sent to {channel.mention}."), mention_author=False)

@bot.command(name="disablelogs")
async def disablelogs_command(ctx):
    if not await manager_check(ctx):
        return
    config.setdefault("log_channels", {}).pop(str(ctx.guild.id), None)
    save_config()
    await ctx.reply(embed=feature_embed("Security Logs Disabled", "Security logs disabled."), mention_author=False)


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
        "🗑️ Message Deleted",
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

@bot.command(name="np")
async def no_prefix_guild_command(ctx, user: discord.User = None):
    if not is_bot_admin_id(ctx.author.id):
        return await ctx.reply(embed=feature_embed("Access Denied", "Only the bot owner and authorized bot admins can use this command.", color=discord.Color.red()), mention_author=False)
    if ctx.guild is None or user is None:
        return await ctx.reply(embed=feature_embed("Usage", "Usage: `!np @user`"), mention_author=False)
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
    confirm="Set to True to confirm deleting existing channels/categories",
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
                "This restore will **delete every existing channel and category** and recreate those in the backup. Roles will not be deleted.\n"
                "Run `/backup load` again with the same code and set **confirm** to **True** to proceed.",
                color=discord.Color.orange(),
            ), ephemeral=True
        )
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        roles_count, channels_count = await restore_backup(guild, code=code, replace_channels=True)
        await interaction.followup.send(embed=feature_embed("Backup Restored", f"Roles available: **{roles_count}**\nChannels recreated: **{channels_count}**\nExisting channels/categories were replaced."), ephemeral=True)
    except FileNotFoundError:
        await interaction.followup.send(embed=feature_embed("No Backup Found", "Check the code or create a backup first.", color=discord.Color.orange()), ephemeral=True)
    except (ValueError, OSError, RuntimeError, json.JSONDecodeError, discord.HTTPException) as exc:
        await interaction.followup.send(embed=feature_embed("Restore Failed", str(exc)[:3000], color=discord.Color.red()), ephemeral=True)

bot.tree.add_command(backup_slash_group)

@bot.tree.command(name="setuplogs", description="Enable security logs in a selected channel")
@app_commands.describe(channel="Channel where security logs should be sent")
async def slash_setuplogs(interaction: discord.Interaction, channel: discord.TextChannel):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(embed=feature_embed("Server Only", "Use this command in a server.", color=discord.Color.red()), ephemeral=True)
    if not has_admin_access(interaction.user):
        return await interaction.response.send_message(embed=feature_embed("Access Denied", "Administrator permission is required.", color=discord.Color.red()), ephemeral=True)
    config.setdefault("log_channels", {})[str(guild.id)] = channel.id
    save_config()
    await interaction.response.send_message(embed=feature_embed("Security Logs Enabled", f"Logs for **{guild.name}** will be sent to {channel.mention}."), ephemeral=True)

@bot.tree.command(name="disablelogs", description="Disable automatic security logs for this server")
async def slash_disablelogs(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(embed=feature_embed("Server Only", "Use this command in a server.", color=discord.Color.red()), ephemeral=True)
    if not has_admin_access(interaction.user):
        return await interaction.response.send_message(embed=feature_embed("Access Denied", "Administrator permission is required.", color=discord.Color.red()), ephemeral=True)
    config.setdefault("log_channels", {}).pop(str(guild.id), None)
    save_config()
    await interaction.response.send_message(embed=feature_embed("Security Logs Disabled", "Automatic security logs are now disabled for this server."), ephemeral=True)

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
# ==================== ADDITIONAL SLASH COMMAND COVERAGE ====================
@bot.tree.command(name="commands", description="List the available Kanade slash commands")
async def slash_commands(interaction: discord.Interaction):
    description = (
        "**Protection:** `/antinuke status`, `/antinuke on`, `/antinuke off`\n\n"
        "**Moderation:** `/kick`, `/ban`, `/unban`, `/purge`, `/role`, `/unrole`, `/lock`, `/unlock`, `/lockdown`, `/unlockdown`\n\n"
        "**Utilities:** `/afk`, `/snipe`, `/security`, `/status`, `/info`, `/serverinfo`, `/avatar`, `/prefix`, `/joinvc`, `/setuplogs`, `/disablelogs`, `/backup create`, `/backup load`, `/roleall`, `/unroleall`, `/whitelist`, `/adminlist`, `/admlist`, `/changebanner`, `/changepicture`, `/steal`, `/botnick`, `/help`\n\n"
        "**Bot staff:** `/premium`, `/np`, `/npremove`, `/gnp`, `/gnpremove`, `/generatelink`, `/serverlists`\n\n"
        "Prefix commands remain available. Use `/help` if it is registered by your client, or `!help` / your server prefix plus `help` for the full guide."
    )
    await interaction.response.send_message(embed=feature_embed("Kanade | Slash Commands", description), ephemeral=True)

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
    embed.set_footer(text="Kanade • Developed by spywxre", icon_url=user.display_avatar.url if user else None)
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
            title="⚒ Error",
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
