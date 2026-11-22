import asyncio
import re
import time
from collections import defaultdict, deque
from typing import Literal, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

TOGGLE_ON = "🟢"
TOGGLE_OFF = "🔴"
BOT_BLUE = 0x0099FF


def sec_embed(text: str, color: int = BOT_BLUE) -> discord.Embed:
    return discord.Embed(description=text, color=color)


ACTION_CHOICES = [
    app_commands.Choice(name="Banning Members", value="ban"),
    app_commands.Choice(name="Kicking Members", value="kick"),
    app_commands.Choice(name="Deleting Roles", value="role_delete"),
    app_commands.Choice(name="Creating Roles", value="role_create"),
    app_commands.Choice(name="Deleting Channels", value="channel_delete"),
    app_commands.Choice(name="Creating Channels", value="channel_create"),
    app_commands.Choice(name="Adding Bots", value="bot_add"),
    app_commands.Choice(name="Giving Dangerous Permissions", value="dangerous_perms"),
    app_commands.Choice(name="Giving Administration Roles", value="admin_role"),
    app_commands.Choice(name="Pruning Members", value="prune"),
    app_commands.Choice(name="Sending invite links", value="invite_links"),
]

ROLE_WHITELIST_CHOICES = [
    app_commands.Choice(name="Roles: All", value="all"),
    app_commands.Choice(name="Roles: Ban", value="ban"),
    app_commands.Choice(name="Roles: Kick", value="kick"),
    app_commands.Choice(name="Roles: Bot-Add", value="bot_add"),
    app_commands.Choice(name="Roles: Role-Update", value="dangerous_perms"),
    app_commands.Choice(name="Roles: Role-Add", value="admin_role"),
    app_commands.Choice(name="Roles: Channel-Create", value="channel_create"),
    app_commands.Choice(name="Roles: Channel-Delete", value="channel_delete"),
    app_commands.Choice(name="Roles: Role-Create", value="role_create"),
    app_commands.Choice(name="Roles: Role-Delete", value="role_delete"),
    app_commands.Choice(name="Roles: Invite-Link", value="invite_links"),
    app_commands.Choice(name="Roles: Prune", value="prune"),
]

USER_WHITELIST_CHOICES = [
    app_commands.Choice(name="Users: All", value="all"),
    app_commands.Choice(name="Users: Ban", value="ban"),
    app_commands.Choice(name="Users: Kick", value="kick"),
    app_commands.Choice(name="Users: Bot-Add", value="bot_add"),
    app_commands.Choice(name="Users: Role-Update", value="dangerous_perms"),
    app_commands.Choice(name="Users: Role-Add", value="admin_role"),
    app_commands.Choice(name="Users: Channel-Create", value="channel_create"),
    app_commands.Choice(name="Users: Channel-Delete", value="channel_delete"),
    app_commands.Choice(name="Users: Role-Create", value="role_create"),
    app_commands.Choice(name="Users: Role-Delete", value="role_delete"),
    app_commands.Choice(name="Users: Invite-Link", value="invite_links"),
    app_commands.Choice(name="Users: Prune", value="prune"),
]

ROLE_CAT_LABELS = {c.value: c.name for c in ROLE_WHITELIST_CHOICES}
USER_CAT_LABELS = {c.value: c.name for c in USER_WHITELIST_CHOICES}

CATEGORY_ALIASES = {
    "all": "all",
    "roles: all": "all",
    "users: all": "all",
    "ban": "ban",
    "roles: ban": "ban",
    "users: ban": "ban",
    "kick": "kick",
    "roles: kick": "kick",
    "users: kick": "kick",
    "bot_add": "bot_add",
    "bot-add": "bot_add",
    "roles: bot-add": "bot_add",
    "users: bot-add": "bot_add",
    "dangerous_perms": "dangerous_perms",
    "role_update": "dangerous_perms",
    "role-update": "dangerous_perms",
    "roles: role-update": "dangerous_perms",
    "users: role-update": "dangerous_perms",
    "admin_role": "admin_role",
    "role_add": "admin_role",
    "role-add": "admin_role",
    "roles: role-add": "admin_role",
    "users: role-add": "admin_role",
    "channel_create": "channel_create",
    "channel-create": "channel_create",
    "roles: channel-create": "channel_create",
    "users: channel-create": "channel_create",
    "channel_delete": "channel_delete",
    "channel-delete": "channel_delete",
    "roles: channel-delete": "channel_delete",
    "users: channel-delete": "channel_delete",
    "role_create": "role_create",
    "role-create": "role_create",
    "roles: role-create": "role_create",
    "users: role-create": "role_create",
    "role_delete": "role_delete",
    "role-delete": "role_delete",
    "roles: role-delete": "role_delete",
    "users: role-delete": "role_delete",
    "invite_links": "invite_links",
    "invite-link": "invite_links",
    "roles: invite-link": "invite_links",
    "users: invite-link": "invite_links",
    "prune": "prune",
    "roles: prune": "prune",
    "users: prune": "prune",
}

ACTION_LABELS = {choice.value: choice.name for choice in ACTION_CHOICES}
NAME_TO_ACTION = {choice.name.lower(): choice.value for choice in ACTION_CHOICES}

PUNISHMENT_CHOICES = Literal["ban", "kick", "strip"]

DANGEROUS_PERMS = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "ban_members",
    "kick_members",
    "manage_webhooks",
    "mention_everyone",
)

INVITE_REGEX = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord\.(?:gg|io|me|li)|discordapp\.com/invite|discord\.com/invite)/[a-zA-Z0-9-]+",
    re.IGNORECASE,
)


class AntiNuke(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        self.tracker = defaultdict(lambda: deque(maxlen=50))
        self.window_seconds = 15

    def resolve_action(self, raw: str) -> Optional[Tuple[str, str]]:
        cleaned = raw.strip().lower()
        if cleaned in ACTION_LABELS:
            return cleaned, ACTION_LABELS[cleaned]
        underscored = cleaned.replace(" ", "_")
        if underscored in ACTION_LABELS:
            return underscored, ACTION_LABELS[underscored]
        if cleaned in NAME_TO_ACTION:
            key = NAME_TO_ACTION[cleaned]
            return key, ACTION_LABELS[key]
        return None

    def resolve_category(self, raw: Optional[str]) -> Optional[str]:
        if not raw:
            return "all"
        return CATEGORY_ALIASES.get(raw.strip().lower())

    def is_owner_or_trusted(self, ctx: commands.Context) -> bool:
        if ctx.author.id == ctx.guild.owner_id:
            return True
        perms = self.db.get_user_permissions(ctx.guild.id, ctx.author.id)
        return "admin" in perms or "antinuke" in perms

    def is_whitelisted(
        self,
        guild: discord.Guild,
        member: Optional[discord.Member],
        channel_id: Optional[int] = None,
        action: Optional[str] = None,
    ) -> bool:
        data = self.db.get_guild(guild.id)
        if channel_id and channel_id in data["whitelist_channels"]:
            return True
        if not member:
            return False
        if member.id in (guild.owner_id, self.bot.user.id):
            return True

        user_cats = data["whitelist_users"].get(str(member.id), [])
        if "all" in user_cats or (action and action in user_cats):
            return True

        for role in member.roles:
            role_cats = data["whitelist_roles"].get(str(role.id), [])
            if "all" in role_cats or (action and action in role_cats):
                return True

        return False

    async def log_security(self, guild: discord.Guild, title: str, description: str, color=discord.Color.red()):
        data = self.db.get_guild(guild.id)
        log_id = data.get("sec_log_id")
        if log_id:
            channel = guild.get_channel(log_id)
            if channel:
                embed = discord.Embed(
                    title=title,
                    description=description,
                    color=color,
                    timestamp=discord.utils.utcnow(),
                )
                await channel.send(embed=embed)

    async def handle_violation(
        self,
        guild: discord.Guild,
        executor: discord.Member,
        action: str,
        channel_id: Optional[int] = None,
    ):
        if self.is_whitelisted(guild, executor, channel_id, action):
            return

        data = self.db.get_guild(guild.id)
        cfg = data["actions"].get(action)
        if not cfg or not cfg["enabled"]:
            return

        now = time.monotonic()
        key = (guild.id, executor.id, action)
        bucket = self.tracker[key]
        bucket.append(now)

        while bucket and now - bucket[0] > self.window_seconds:
            bucket.popleft()

        if len(bucket) >= cfg["limit"]:
            bucket.clear()
            punishment = cfg["punishment"]
            label = ACTION_LABELS.get(action, action)
            reason = f"Anti-Nuke triggered: {label} limit ({cfg['limit']}/{self.window_seconds}s) exceeded"
            try:
                if punishment == "ban":
                    await guild.ban(executor, reason=reason, delete_message_seconds=0)
                elif punishment == "kick":
                    await guild.kick(executor, reason=reason)
                elif punishment == "strip":
                    removable_roles = [
                        r for r in executor.roles
                        if r != guild.default_role and r < guild.me.top_role and not r.managed
                    ]
                    await executor.remove_roles(*removable_roles, reason=reason)

                await self.log_security(
                    guild,
                    "Anti-Nuke Action Triggered",
                    f"**User:** {executor.mention} (`{executor.id}`)\n"
                    f"**Trigger:** `{label}`\n"
                    f"**Punishment Executed:** `{punishment.upper()}`",
                )
            except discord.Forbidden:
                await self.log_security(
                    guild,
                    "Anti-Nuke Punishment Failed",
                    f"Missing role hierarchy permissions to `{punishment}` {executor.mention} (`{executor.id}`).",
                    color=discord.Color.orange(),
                )

    async def get_audit_executor(self, guild: discord.Guild, audit_action: discord.AuditLogAction) -> Optional[discord.Member]:
        await asyncio.sleep(0.4)
        try:
            async for entry in guild.audit_logs(limit=1, action=audit_action):
                if entry.user:
                    return guild.get_member(entry.user.id)
        except discord.Forbidden:
            return None
        return None

    # --- Real-Time Anti-Nuke Listeners ---
    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        executor = await self.get_audit_executor(guild, discord.AuditLogAction.ban)
        if executor:
            await self.handle_violation(guild, executor, "ban")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        await asyncio.sleep(0.4)
        try:
            async for entry in guild.audit_logs(limit=1):
                if entry.action == discord.AuditLogAction.kick and entry.target and entry.target.id == member.id:
                    executor = guild.get_member(entry.user.id) if entry.user else None
                    if executor:
                        await self.handle_violation(guild, executor, "kick")
                    break
                elif entry.action == discord.AuditLogAction.member_prune:
                    executor = guild.get_member(entry.user.id) if entry.user else None
                    if executor:
                        await self.handle_violation(guild, executor, "prune")
                    break
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        executor = await self.get_audit_executor(role.guild, discord.AuditLogAction.role_delete)
        if executor:
            await self.handle_violation(role.guild, executor, "role_delete")

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        executor = await self.get_audit_executor(role.guild, discord.AuditLogAction.role_create)
        if executor:
            await self.handle_violation(role.guild, executor, "role_create")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        executor = await self.get_audit_executor(channel.guild, discord.AuditLogAction.channel_delete)
        if executor:
            await self.handle_violation(channel.guild, executor, "channel_delete", channel.id)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        executor = await self.get_audit_executor(channel.guild, discord.AuditLogAction.channel_create)
        if executor:
            await self.handle_violation(channel.guild, executor, "channel_create", channel.id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            executor = await self.get_audit_executor(member.guild, discord.AuditLogAction.bot_add)
            if executor and not self.is_whitelisted(member.guild, executor, action="bot_add"):
                data = self.db.get_guild(member.guild.id)
                if data["actions"]["bot_add"]["enabled"]:
                    try:
                        await member.kick(reason="Anti-Nuke: Unauthorized bot addition")
                    except discord.Forbidden:
                        pass
                    await self.handle_violation(member.guild, executor, "bot_add")

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        added_dangerous = [
            perm for perm in DANGEROUS_PERMS
            if not getattr(before.permissions, perm) and getattr(after.permissions, perm)
        ]
        if not added_dangerous:
            return

        executor = await self.get_audit_executor(after.guild, discord.AuditLogAction.role_update)
        if executor and not self.is_whitelisted(after.guild, executor, action="dangerous_perms"):
            data = self.db.get_guild(after.guild.id)
            if data["actions"]["dangerous_perms"]["enabled"]:
                try:
                    await after.edit(permissions=before.permissions, reason="Anti-Nuke: Reverted dangerous permissions")
                except discord.Forbidden:
                    pass
                await self.handle_violation(after.guild, executor, "dangerous_perms")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        added_roles = [r for r in after.roles if r not in before.roles]
        if not added_roles:
            return

        admin_roles_added = [r for r in added_roles if r.permissions.administrator]
        if not admin_roles_added:
            return

        executor = await self.get_audit_executor(after.guild, discord.AuditLogAction.member_role_update)
        if executor and not self.is_whitelisted(after.guild, executor, action="admin_role"):
            data = self.db.get_guild(after.guild.id)
            if data["actions"]["admin_role"]["enabled"]:
                try:
                    await after.remove_roles(*admin_roles_added, reason="Anti-Nuke: Unauthorized Administrator role grant")
                except discord.Forbidden:
                    pass
                await self.handle_violation(after.guild, executor, "admin_role")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return
        if not INVITE_REGEX.search(message.content or ""):
            return

        data = self.db.get_guild(message.guild.id)
        if not data["actions"]["invite_links"]["enabled"]:
            return

        if self.is_whitelisted(message.guild, message.author, message.channel.id, action="invite_links"):
            return

        try:
            await message.delete()
        except discord.Forbidden:
            pass
        await self.handle_violation(message.guild, message.author, "invite_links", message.channel.id)

    # --- Anti-Nuke Configuration Commands ---
    @commands.hybrid_command(name="enable", description="Enable an anti nuke action")
    @app_commands.describe(action="Select an action")
    @app_commands.choices(action=ACTION_CHOICES)
    async def enable_action(self, ctx: commands.Context, *, action: str):
        if not self.is_owner_or_trusted(ctx):
            return await ctx.send(
                embed=sec_embed(f"{TOGGLE_OFF} Only the **Server Owner** or permitted admins can configure Anti-Nuke."),
                ephemeral=True,
            )

        resolved = self.resolve_action(action)
        if not resolved:
            valid_keys = ", ".join(f"`{k}`" for k in ACTION_LABELS)
            return await ctx.send(
                embed=sec_embed(f"{TOGGLE_OFF} Invalid action. Valid options: {valid_keys}"),
                ephemeral=True,
            )

        key, label = resolved
        data = self.db.get_guild(ctx.guild.id)
        data["actions"][key]["enabled"] = True
        self.db.update_field(ctx.guild.id, "actions_json", data["actions"])

        await ctx.send(
            embed=sec_embed(f"{TOGGLE_ON} **{label}** more than the allowed amount will result in a punishment.")
        )

    @commands.hybrid_command(name="disable", description="Disable an anti nuke action")
    @app_commands.describe(action="Select an action")
    @app_commands.choices(action=ACTION_CHOICES)
    async def disable_action(self, ctx: commands.Context, *, action: str):
        if not self.is_owner_or_trusted(ctx):
            return await ctx.send(
                embed=sec_embed(f"{TOGGLE_OFF} Only the **Server Owner** or permitted admins can configure Anti-Nuke."),
                ephemeral=True,
            )

        resolved = self.resolve_action(action)
        if not resolved:
            valid_keys = ", ".join(f"`{k}`" for k in ACTION_LABELS)
            return await ctx.send(
                embed=sec_embed(f"{TOGGLE_OFF} Invalid action. Valid options: {valid_keys}"),
                ephemeral=True,
            )

        key, label = resolved
        data = self.db.get_guild(ctx.guild.id)
        data["actions"][key]["enabled"] = False
        self.db.update_field(ctx.guild.id, "actions_json", data["actions"])

        await ctx.send(
            embed=sec_embed(f"{TOGGLE_OFF} **{label}** will no longer result in a punishment.")
        )

    @commands.hybrid_command(name="setlimit", description="Change the limit of an action")
    @app_commands.describe(action="Select an action", limit="Maximum allowed actions within 15 seconds")
    @app_commands.choices(action=ACTION_CHOICES)
    async def set_limit(self, ctx: commands.Context, action: str, limit: app_commands.Range[int, 1, 20]):
        if not self.is_owner_or_trusted(ctx):
            return await ctx.send(
                embed=sec_embed(f"{TOGGLE_OFF} Only the **Server Owner** or permitted admins can configure Anti-Nuke."),
                ephemeral=True,
            )

        resolved = self.resolve_action(action)
        if not resolved:
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Invalid action specified."), ephemeral=True)

        key, label = resolved
        data = self.db.get_guild(ctx.guild.id)
        data["actions"][key]["limit"] = limit
        self.db.update_field(ctx.guild.id, "actions_json", data["actions"])

        await ctx.send(
            embed=sec_embed(f"{TOGGLE_ON} **{label}** limit has been set to **{limit}** actions per {self.window_seconds}s.")
        )

    @commands.hybrid_command(name="setpunishment", description="Change the punishment type of a specific action")
    @app_commands.describe(action="Select an action", punishment="Punishment to execute when limit is exceeded")
    @app_commands.choices(action=ACTION_CHOICES)
    async def set_punishment(self, ctx: commands.Context, action: str, punishment: PUNISHMENT_CHOICES):
        if not self.is_owner_or_trusted(ctx):
            return await ctx.send(
                embed=sec_embed(f"{TOGGLE_OFF} Only the **Server Owner** or permitted admins can configure Anti-Nuke."),
                ephemeral=True,
            )

        resolved = self.resolve_action(action)
        if not resolved:
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Invalid action specified."), ephemeral=True)

        key, label = resolved
        data = self.db.get_guild(ctx.guild.id)
        data["actions"][key]["punishment"] = punishment
        self.db.update_field(ctx.guild.id, "actions_json", data["actions"])

        await ctx.send(
            embed=sec_embed(f"{TOGGLE_ON} **{label}** punishment has been set to **{punishment}**.")
        )

    @commands.hybrid_command(name="settings", description="Get current settings of the server")
    async def settings_cmd(self, ctx: commands.Context):
        data = self.db.get_guild(ctx.guild.id)
        lines = []
        for act_key, label in ACTION_LABELS.items():
            cfg = data["actions"][act_key]
            icon = TOGGLE_ON if cfg["enabled"] else TOGGLE_OFF
            lines.append(f"{icon} **{label}** — Limit: `{cfg['limit']}` | Punishment: `{cfg['punishment']}`")

        mod_log = f"<#{data['mod_log_id']}>" if data["mod_log_id"] else "`Not Set`"
        sec_log = f"<#{data['sec_log_id']}>" if data["sec_log_id"] else "`Not Set`"
        ver_state = "Enabled" if data.get("verification_enabled") else "Disabled"

        embed = discord.Embed(title=f"Security Settings — {ctx.guild.name}", color=BOT_BLUE)
        embed.add_field(name="Anti-Nuke Modules", value="\n".join(lines), inline=False)
        embed.add_field(
            name="Logs & Verification",
            value=(
                f"Mod Logs: {mod_log}\n"
                f"Security Logs: {sec_log}\n"
                f"Verification: `{ver_state}`"
            ),
            inline=False,
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="scan", description="Let Security take a deep look into your server and detect vulnerabilities")
    async def scan_server(self, ctx: commands.Context):
        guild = ctx.guild
        data = self.db.get_guild(guild.id)
        warnings = []

        everyone_perms = guild.default_role.permissions
        risky_everyone = [
            p for p in ("administrator", "manage_guild", "manage_roles", "manage_channels", "mention_everyone")
            if getattr(everyone_perms, p)
        ]
        if risky_everyone:
            warnings.append(f"`@everyone` has dangerous permissions: `{', '.join(risky_everyone)}`")

        admin_roles = [
            r.name for r in guild.roles
            if r.permissions.administrator and not r.managed and r != guild.default_role
        ]
        if len(admin_roles) > 3:
            warnings.append(f"High number of roles with **Administrator** ({len(admin_roles)}): `{', '.join(admin_roles[:5])}`")

        if guild.me.top_role.position < len(guild.roles) - 2:
            warnings.append("Bot role is not near the top of the role hierarchy; punishments on high-ranking staff may fail.")

        disabled = [ACTION_LABELS[k] for k, v in data["actions"].items() if not v["enabled"] and k in ACTION_LABELS]
        if disabled:
            warnings.append(f"Disabled Anti-Nuke protections: `{', '.join(disabled)}`")
        if not data["sec_log_id"]:
            warnings.append("Security log channel is not configured (`/logs security`).")

        embed = discord.Embed(
            title="Server Security Audit Scan",
            description="\n".join(f"{TOGGLE_OFF} {w}" for w in warnings)
            if warnings
            else f"{TOGGLE_ON} No major security vulnerabilities detected.",
            color=discord.Color.orange() if warnings else BOT_BLUE,
        )
        await ctx.send(embed=embed)

    # --- Whitelist Command Group with Category Selector ---
    @commands.hybrid_group(name="whitelist", description="Manage anti-nuke whitelists")
    async def whitelist(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send(
                embed=sec_embed("Subcommands: `add_user`, `remove_user`, `view_users`, `add_role`, `remove_role`, `view_roles`, `add_channel`, `remove_channel`, `view_channels`.")
            )

    @whitelist.command(name="add_user", description="Add user to whitelist")
    @app_commands.describe(user="User to whitelist", category="The whitelist category")
    @app_commands.choices(category=USER_WHITELIST_CHOICES)
    async def wl_add_user(self, ctx: commands.Context, user: discord.Member, *, category: Optional[str] = None):
        if ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Only the **Server Owner** can edit the whitelist."), ephemeral=True)

        cat = self.resolve_category(category)
        if not cat:
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Invalid whitelist category."), ephemeral=True)

        data = self.db.get_guild(ctx.guild.id)
        uid = str(user.id)
        cats = data["whitelist_users"].setdefault(uid, [])
        if cat == "all":
            data["whitelist_users"][uid] = ["all"]
        elif "all" not in cats and cat not in cats:
            cats.append(cat)

        self.db.update_field(ctx.guild.id, "whitelist_users", data["whitelist_users"])
        label = USER_CAT_LABELS[cat]
        await ctx.send(embed=sec_embed(f"{TOGGLE_ON} Added {user.mention} to the whitelist under **{label}**."))

    @whitelist.command(name="remove_user", description="Remove a user from whitelist")
    @app_commands.describe(user="User to remove from whitelist", category="The whitelist category")
    @app_commands.choices(category=USER_WHITELIST_CHOICES)
    async def wl_remove_user(self, ctx: commands.Context, user: discord.Member, *, category: Optional[str] = None):
        if ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Only the **Server Owner** can edit the whitelist."), ephemeral=True)

        cat = self.resolve_category(category)
        if not cat:
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Invalid whitelist category."), ephemeral=True)

        data = self.db.get_guild(ctx.guild.id)
        uid = str(user.id)
        if uid in data["whitelist_users"]:
            if cat == "all":
                del data["whitelist_users"][uid]
            else:
                cats = data["whitelist_users"][uid]
                if cat in cats:
                    cats.remove(cat)
                if not cats:
                    del data["whitelist_users"][uid]
            self.db.update_field(ctx.guild.id, "whitelist_users", data["whitelist_users"])

        label = USER_CAT_LABELS[cat]
        await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Removed {user.mention} from **{label}**."))

    @whitelist.command(name="view_users", description="Shows current whitelisted users")
    @app_commands.describe(category="The whitelist category")
    @app_commands.choices(category=USER_WHITELIST_CHOICES)
    async def wl_view_users(self, ctx: commands.Context, *, category: Optional[str] = None):
        cat = self.resolve_category(category) or "all"
        data = self.db.get_guild(ctx.guild.id)
        lines = []
        for uid, cats in data["whitelist_users"].items():
            if cat == "all" or "all" in cats or cat in cats:
                cat_names = ", ".join(f"`{USER_CAT_LABELS.get(c, c)}`" for c in cats)
                lines.append(f"<@{uid}> (`{uid}`) — {cat_names}")

        title = f"Whitelisted Users ({USER_CAT_LABELS[cat]})"
        await ctx.send(
            embed=discord.Embed(title=title, description="\n".join(lines) or "None", color=BOT_BLUE)
        )

    @whitelist.command(name="add_role", description="Add role to whitelist")
    @app_commands.describe(role="Role to whitelist", category="The whitelist category")
    @app_commands.choices(category=ROLE_WHITELIST_CHOICES)
    async def wl_add_role(self, ctx: commands.Context, role: discord.Role, *, category: Optional[str] = None):
        if ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Only the **Server Owner** can edit the whitelist."), ephemeral=True)

        cat = self.resolve_category(category)
        if not cat:
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Invalid whitelist category."), ephemeral=True)

        data = self.db.get_guild(ctx.guild.id)
        rid = str(role.id)
        cats = data["whitelist_roles"].setdefault(rid, [])
        if cat == "all":
            data["whitelist_roles"][rid] = ["all"]
        elif "all" not in cats and cat not in cats:
            cats.append(cat)

        self.db.update_field(ctx.guild.id, "whitelist_roles", data["whitelist_roles"])
        label = ROLE_CAT_LABELS[cat]
        await ctx.send(embed=sec_embed(f"{TOGGLE_ON} Added {role.mention} to the whitelist under **{label}**."))

    @whitelist.command(name="remove_role", description="Remove a role from whitelist")
    @app_commands.describe(role="Role to remove from whitelist", category="The whitelist category")
    @app_commands.choices(category=ROLE_WHITELIST_CHOICES)
    async def wl_remove_role(self, ctx: commands.Context, role: discord.Role, *, category: Optional[str] = None):
        if ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Only the **Server Owner** can edit the whitelist."), ephemeral=True)

        cat = self.resolve_category(category)
        if not cat:
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Invalid whitelist category."), ephemeral=True)

        data = self.db.get_guild(ctx.guild.id)
        rid = str(role.id)
        if rid in data["whitelist_roles"]:
            if cat == "all":
                del data["whitelist_roles"][rid]
            else:
                cats = data["whitelist_roles"][rid]
                if cat in cats:
                    cats.remove(cat)
                if not cats:
                    del data["whitelist_roles"][rid]
            self.db.update_field(ctx.guild.id, "whitelist_roles", data["whitelist_roles"])

        label = ROLE_CAT_LABELS[cat]
        await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Removed {role.mention} from **{label}**."))

    @whitelist.command(name="view_roles", description="Shows current whitelisted roles")
    @app_commands.describe(category="The whitelist category")
    @app_commands.choices(category=ROLE_WHITELIST_CHOICES)
    async def wl_view_roles(self, ctx: commands.Context, *, category: Optional[str] = None):
        cat = self.resolve_category(category) or "all"
        data = self.db.get_guild(ctx.guild.id)
        lines = []
        for rid, cats in data["whitelist_roles"].items():
            if cat == "all" or "all" in cats or cat in cats:
                cat_names = ", ".join(f"`{ROLE_CAT_LABELS.get(c, c)}`" for c in cats)
                lines.append(f"<@&{rid}> (`{rid}`) — {cat_names}")

        title = f"Whitelisted Roles ({ROLE_CAT_LABELS[cat]})"
        await ctx.send(
            embed=discord.Embed(title=title, description="\n".join(lines) or "None", color=BOT_BLUE)
        )

    @whitelist.command(name="add_channel", description="Add channel to whitelist")
    async def wl_add_channel(self, ctx: commands.Context, channel: discord.abc.GuildChannel):
        if ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Only the **Server Owner** can edit the whitelist."), ephemeral=True)
        data = self.db.get_guild(ctx.guild.id)
        if channel.id not in data["whitelist_channels"]:
            data["whitelist_channels"].append(channel.id)
            self.db.update_field(ctx.guild.id, "whitelist_channels", data["whitelist_channels"])
        await ctx.send(embed=sec_embed(f"{TOGGLE_ON} Added {channel.mention} to the channel whitelist."))

    @whitelist.command(name="remove_channel", description="Remove a channel from whitelist")
    async def wl_remove_channel(self, ctx: commands.Context, channel: discord.abc.GuildChannel):
        if ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Only the **Server Owner** can edit the whitelist."), ephemeral=True)
        data = self.db.get_guild(ctx.guild.id)
        if channel.id in data["whitelist_channels"]:
            data["whitelist_channels"].remove(channel.id)
            self.db.update_field(ctx.guild.id, "whitelist_channels", data["whitelist_channels"])
        await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Removed {channel.mention} from the channel whitelist."))

    @whitelist.command(name="view_channels", description="Shows current whitelisted channels")
    async def wl_view_channels(self, ctx: commands.Context):
        data = self.db.get_guild(ctx.guild.id)
        channels = [f"<#{cid}> (`{cid}`)" for cid in data["whitelist_channels"]]
        await ctx.send(
            embed=discord.Embed(title="Whitelisted Channels", description="\n".join(channels) or "None", color=BOT_BLUE)
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiNuke(bot))
