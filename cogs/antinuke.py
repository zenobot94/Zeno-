import asyncio
import re
import time
from collections import defaultdict, deque
from typing import Literal, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

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

WHITELIST_ACTION_CHOICES = [
    app_commands.Choice(name="Add", value="add"),
    app_commands.Choice(name="Remove", value="remove"),
    app_commands.Choice(name="View", value="view"),
]

ROLE_CAT_LABELS = {
    "all": "Roles: All",
    "ban": "Roles: Ban",
    "kick": "Roles: Kick",
    "bot_add": "Roles: Bot-Add",
    "dangerous_perms": "Roles: Role-Update",
    "admin_role": "Roles: Role-Add",
    "channel_create": "Roles: Channel-Create",
    "channel_delete": "Roles: Channel-Delete",
    "role_create": "Roles: Role-Create",
    "role_delete": "Roles: Role-Delete",
    "invite_links": "Roles: Invite-Link",
    "prune": "Roles: Prune",
}

USER_CAT_LABELS = {
    "all": "Users: All",
    "ban": "Users: Ban",
    "kick": "Users: Kick",
    "bot_add": "Users: Bot-Add",
    "dangerous_perms": "Users: Role-Update",
    "admin_role": "Users: Role-Add",
    "channel_create": "Users: Channel-Create",
    "channel_delete": "Users: Channel-Delete",
    "role_create": "Users: Role-Create",
    "role_delete": "Users: Role-Delete",
    "invite_links": "Users: Invite-Link",
    "prune": "Users: Prune",
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

    @property
    def ok(self) -> str:
        return getattr(self.bot, "ok_prefix", "")

    @property
    def no(self) -> str:
        return getattr(self.bot, "no_prefix", "")

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

    def is_owner_or_trusted(self, ctx: commands.Context) -> bool:
        if ctx.author.id == ctx.guild.owner_id:
            return True
        perms = self.db.get_user_permissions(ctx.guild.id, ctx.author.id)
        return "admin" in perms or "antinuke" in perms

    async def _can_edit_whitelist(self, ctx: commands.Context) -> bool:
        return ctx.author.id == ctx.guild.owner_id or await self.bot.is_owner(ctx.author)

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
                embed=sec_embed(f"{self.no}Only the **Server Owner** or permitted admins can configure Anti-Nuke."),
                ephemeral=True,
            )

        resolved = self.resolve_action(action)
        if not resolved:
            valid_keys = ", ".join(f"`{k}`" for k in ACTION_LABELS)
            return await ctx.send(
                embed=sec_embed(f"{self.no}Invalid action. Valid options: {valid_keys}"),
                ephemeral=True,
            )

        key, label = resolved
        data = self.db.get_guild(ctx.guild.id)
        data["actions"][key]["enabled"] = True
        self.db.update_field(ctx.guild.id, "actions_json", data["actions"])

        await ctx.send(
            embed=sec_embed(f"{self.ok}**{label}** more than the allowed amount will result in a punishment.")
        )

    @commands.hybrid_command(name="disable", description="Disable an anti nuke action")
    @app_commands.describe(action="Select an action")
    @app_commands.choices(action=ACTION_CHOICES)
    async def disable_action(self, ctx: commands.Context, *, action: str):
        if not self.is_owner_or_trusted(ctx):
            return await ctx.send(
                embed=sec_embed(f"{self.no}Only the **Server Owner** or permitted admins can configure Anti-Nuke."),
                ephemeral=True,
            )

        resolved = self.resolve_action(action)
        if not resolved:
            valid_keys = ", ".join(f"`{k}`" for k in ACTION_LABELS)
            return await ctx.send(
                embed=sec_embed(f"{self.no}Invalid action. Valid options: {valid_keys}"),
                ephemeral=True,
            )

        key, label = resolved
        data = self.db.get_guild(ctx.guild.id)
        data["actions"][key]["enabled"] = False
        self.db.update_field(ctx.guild.id, "actions_json", data["actions"])

        await ctx.send(
            embed=sec_embed(f"{self.no}**{label}** will no longer result in a punishment.")
        )

    @commands.hybrid_command(name="setlimit", description="Change the limit of an action")
    @app_commands.describe(action="Select an action", limit="Maximum allowed actions within 15 seconds")
    @app_commands.choices(action=ACTION_CHOICES)
    async def set_limit(self, ctx: commands.Context, action: str, limit: app_commands.Range[int, 1, 20]):
        if not self.is_owner_or_trusted(ctx):
            return await ctx.send(
                embed=sec_embed(f"{self.no}Only the **Server Owner** or permitted admins can configure Anti-Nuke."),
                ephemeral=True,
            )

        resolved = self.resolve_action(action)
        if not resolved:
            return await ctx.send(embed=sec_embed(f"{self.no}Invalid action specified."), ephemeral=True)

        key, label = resolved
        data = self.db.get_guild(ctx.guild.id)
        data["actions"][key]["limit"] = limit
        self.db.update_field(ctx.guild.id, "actions_json", data["actions"])

        await ctx.send(
            embed=sec_embed(f"{self.ok}**{label}** limit has been set to **{limit}** actions per {self.window_seconds}s.")
        )

    @commands.hybrid_command(name="setpunishment", description="Change the punishment type of a specific action")
    @app_commands.describe(action="Select an action", punishment="Punishment to execute when limit is exceeded")
    @app_commands.choices(action=ACTION_CHOICES)
    async def set_punishment(self, ctx: commands.Context, action: str, punishment: PUNISHMENT_CHOICES):
        if not self.is_owner_or_trusted(ctx):
            return await ctx.send(
                embed=sec_embed(f"{self.no}Only the **Server Owner** or permitted admins can configure Anti-Nuke."),
                ephemeral=True,
            )

        resolved = self.resolve_action(action)
        if not resolved:
            return await ctx.send(embed=sec_embed(f"{self.no}Invalid action specified."), ephemeral=True)

        key, label = resolved
        data = self.db.get_guild(ctx.guild.id)
        data["actions"][key]["punishment"] = punishment
        self.db.update_field(ctx.guild.id, "actions_json", data["actions"])

        await ctx.send(
            embed=sec_embed(f"{self.ok}**{label}** punishment has been set to **{punishment}**.")
        )

    @commands.hybrid_command(name="settings", description="Get current settings of the server")
    async def settings_cmd(self, ctx: commands.Context):
        data = self.db.get_guild(ctx.guild.id)
        lines = []
        for act_key, label in ACTION_LABELS.items():
            cfg = data["actions"][act_key]
            status = "ON" if cfg["enabled"] else "OFF"
            prefix = self.ok if cfg["enabled"] else self.no
            lines.append(f"{prefix}**{label}** — `{status}` | Limit: `{cfg['limit']}` | Punishment: `{cfg['punishment']}`")

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

        bullet = self.no if self.no else "• "
        embed = discord.Embed(
            title="Server Security Audit Scan",
            description="\n".join(f"{bullet}{w}" for w in warnings)
            if warnings
            else f"{self.ok}No major security vulnerabilities detected.",
            color=discord.Color.orange() if warnings else BOT_BLUE,
        )
        await ctx.send(embed=embed)

    # --- Whitelist Internal Handlers ---
    def _apply_user_whitelist(self, guild_id: int, user_id: int, cat: str, add: bool):
        data = self.db.get_guild(guild_id)
        uid = str(user_id)
        if add:
            cats = data["whitelist_users"].setdefault(uid, [])
            if cat == "all":
                data["whitelist_users"][uid] = ["all"]
            elif "all" not in cats and cat not in cats:
                cats.append(cat)
        else:
            if uid in data["whitelist_users"]:
                if cat == "all":
                    del data["whitelist_users"][uid]
                else:
                    cats = data["whitelist_users"][uid]
                    if cat in cats:
                        cats.remove(cat)
                    if not cats:
                        del data["whitelist_users"][uid]
        self.db.update_field(guild_id, "whitelist_users", data["whitelist_users"])

    def _apply_role_whitelist(self, guild_id: int, role_id: int, cat: str, add: bool):
        data = self.db.get_guild(guild_id)
        rid = str(role_id)
        if add:
            cats = data["whitelist_roles"].setdefault(rid, [])
            if cat == "all":
                data["whitelist_roles"][rid] = ["all"]
            elif "all" not in cats and cat not in cats:
                cats.append(cat)
        else:
            if rid in data["whitelist_roles"]:
                if cat == "all":
                    del data["whitelist_roles"][rid]
                else:
                    cats = data["whitelist_roles"][rid]
                    if cat in cats:
                        cats.remove(cat)
                    if not cats:
                        del data["whitelist_roles"][rid]
        self.db.update_field(guild_id, "whitelist_roles", data["whitelist_roles"])

    async def _handle_role_whitelist_cmd(
        self,
        ctx: commands.Context,
        cat: str,
        action: str,
        role: Optional[discord.Role],
    ):
        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.defer()

        act = action.strip().lower()
        label = ROLE_CAT_LABELS[cat]

        if act == "view":
            data = self.db.get_guild(ctx.guild.id)
            lines = [
                f"<@&{rid}> (`{rid}`) — {', '.join(f'`{ROLE_CAT_LABELS.get(c, c)}`' for c in cats)}"
                for rid, cats in data["whitelist_roles"].items()
                if cat == "all" or "all" in cats or cat in cats
            ]
            return await ctx.send(
                embed=discord.Embed(title=f"Whitelisted Roles ({label})", description="\n".join(lines) or "None", color=BOT_BLUE)
            )

        if not await self._can_edit_whitelist(ctx):
            return await ctx.send(embed=sec_embed(f"{self.no}Only the **Server Owner** can edit the whitelist."))
        if not role:
            return await ctx.send(embed=sec_embed(f"{self.no}Please select a **role**."))

        if act == "add":
            self._apply_role_whitelist(ctx.guild.id, role.id, cat, add=True)
            await ctx.send(embed=sec_embed(f"{self.ok}Added {role.mention} to the whitelist under **{label}**."))
        elif act == "remove":
            self._apply_role_whitelist(ctx.guild.id, role.id, cat, add=False)
            await ctx.send(embed=sec_embed(f"{self.no}Removed {role.mention} from **{label}**."))
        else:
            await ctx.send(embed=sec_embed(f"{self.no}Invalid action. Choose `Add`, `Remove`, or `View`."))

    async def _handle_user_whitelist_cmd(
        self,
        ctx: commands.Context,
        cat: str,
        action: str,
        user: Optional[discord.Member],
    ):
        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.defer()

        act = action.strip().lower()
        label = USER_CAT_LABELS[cat]

        if act == "view":
            data = self.db.get_guild(ctx.guild.id)
            lines = [
                f"<@{uid}> (`{uid}`) — {', '.join(f'`{USER_CAT_LABELS.get(c, c)}`' for c in cats)}"
                for uid, cats in data["whitelist_users"].items()
                if cat == "all" or "all" in cats or cat in cats
            ]
            return await ctx.send(
                embed=discord.Embed(title=f"Whitelisted Users ({label})", description="\n".join(lines) or "None", color=BOT_BLUE)
            )

        if not await self._can_edit_whitelist(ctx):
            return await ctx.send(embed=sec_embed(f"{self.no}Only the **Server Owner** can edit the whitelist."))
        if not user:
            return await ctx.send(embed=sec_embed(f"{self.no}Please select a **user**."))

        if act == "add":
            self._apply_user_whitelist(ctx.guild.id, user.id, cat, add=True)
            await ctx.send(embed=sec_embed(f"{self.ok}Added {user.mention} to the whitelist under **{label}**."))
        elif act == "remove":
            self._apply_user_whitelist(ctx.guild.id, user.id, cat, add=False)
            await ctx.send(embed=sec_embed(f"{self.no}Removed {user.mention} from **{label}**."))
        else:
            await ctx.send(embed=sec_embed(f"{self.no}Invalid action. Choose `Add`, `Remove`, or `View`."))

    # --- Whitelist Command Group ---
    @commands.hybrid_group(name="whitelist", description="Manage anti-nuke whitelists")
    async def whitelist(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send(
                embed=sec_embed(
                    "Use `/whitelist role <category>`, `/whitelist user <category>`, or `/whitelist channel <add|remove|view>`.\n"
                    "Categories: `all`, `ban`, `kick`, `bot_add`, `role_update`, `role_add`, `channel_create`, `channel_delete`, `role_create`, `role_delete`, `invite_link`, `prune`."
                )
            )

    # --- Separate Role Whitelist Commands for Each Anti-Nuke Category ---
    @whitelist.group(name="role", description="Whitelist roles for each anti-nuke category")
    async def wl_role(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send(
                embed=sec_embed(
                    "Categories: `all`, `ban`, `kick`, `bot_add`, `role_update`, `role_add`, `channel_create`, `channel_delete`, `role_create`, `role_delete`, `invite_link`, `prune`."
                )
            )

    @wl_role.command(name="all", description="Whitelist role for all anti-nuke categories")
    @app_commands.describe(action="Select action (Add, Remove, View)", role="Role to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_role_all(self, ctx: commands.Context, action: str, role: Optional[discord.Role] = None):
        await self._handle_role_whitelist_cmd(ctx, "all", action, role)

    @wl_role.command(name="ban", description="Whitelist role for Banning Members")
    @app_commands.describe(action="Select action (Add, Remove, View)", role="Role to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_role_ban(self, ctx: commands.Context, action: str, role: Optional[discord.Role] = None):
        await self._handle_role_whitelist_cmd(ctx, "ban", action, role)

    @wl_role.command(name="kick", description="Whitelist role for Kicking Members")
    @app_commands.describe(action="Select action (Add, Remove, View)", role="Role to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_role_kick(self, ctx: commands.Context, action: str, role: Optional[discord.Role] = None):
        await self._handle_role_whitelist_cmd(ctx, "kick", action, role)

    @wl_role.command(name="bot_add", description="Whitelist role for Adding Bots")
    @app_commands.describe(action="Select action (Add, Remove, View)", role="Role to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_role_bot_add(self, ctx: commands.Context, action: str, role: Optional[discord.Role] = None):
        await self._handle_role_whitelist_cmd(ctx, "bot_add", action, role)

    @wl_role.command(name="role_update", description="Whitelist role for Giving Dangerous Permissions")
    @app_commands.describe(action="Select action (Add, Remove, View)", role="Role to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_role_update(self, ctx: commands.Context, action: str, role: Optional[discord.Role] = None):
        await self._handle_role_whitelist_cmd(ctx, "dangerous_perms", action, role)

    @wl_role.command(name="role_add", description="Whitelist role for Giving Administration Roles")
    @app_commands.describe(action="Select action (Add, Remove, View)", role="Role to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_role_add(self, ctx: commands.Context, action: str, role: Optional[discord.Role] = None):
        await self._handle_role_whitelist_cmd(ctx, "admin_role", action, role)

    @wl_role.command(name="channel_create", description="Whitelist role for Creating Channels")
    @app_commands.describe(action="Select action (Add, Remove, View)", role="Role to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_role_ch_create(self, ctx: commands.Context, action: str, role: Optional[discord.Role] = None):
        await self._handle_role_whitelist_cmd(ctx, "channel_create", action, role)

    @wl_role.command(name="channel_delete", description="Whitelist role for Deleting Channels")
    @app_commands.describe(action="Select action (Add, Remove, View)", role="Role to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_role_ch_delete(self, ctx: commands.Context, action: str, role: Optional[discord.Role] = None):
        await self._handle_role_whitelist_cmd(ctx, "channel_delete", action, role)

    @wl_role.command(name="role_create", description="Whitelist role for Creating Roles")
    @app_commands.describe(action="Select action (Add, Remove, View)", role="Role to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_role_create(self, ctx: commands.Context, action: str, role: Optional[discord.Role] = None):
        await self._handle_role_whitelist_cmd(ctx, "role_create", action, role)

    @wl_role.command(name="role_delete", description="Whitelist role for Deleting Roles")
    @app_commands.describe(action="Select action (Add, Remove, View)", role="Role to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_role_delete(self, ctx: commands.Context, action: str, role: Optional[discord.Role] = None):
        await self._handle_role_whitelist_cmd(ctx, "role_delete", action, role)

    @wl_role.command(name="invite_link", description="Whitelist role for Sending Invite Links")
    @app_commands.describe(action="Select action (Add, Remove, View)", role="Role to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_role_invite_link(self, ctx: commands.Context, action: str, role: Optional[discord.Role] = None):
        await self._handle_role_whitelist_cmd(ctx, "invite_links", action, role)

    @wl_role.command(name="prune", description="Whitelist role for Pruning Members")
    @app_commands.describe(action="Select action (Add, Remove, View)", role="Role to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_role_prune(self, ctx: commands.Context, action: str, role: Optional[discord.Role] = None):
        await self._handle_role_whitelist_cmd(ctx, "prune", action, role)

    # --- Separate User Whitelist Commands for Each Anti-Nuke Category ---
    @whitelist.group(name="user", description="Whitelist users for each anti-nuke category")
    async def wl_user(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send(
                embed=sec_embed(
                    "Categories: `all`, `ban`, `kick`, `bot_add`, `role_update`, `role_add`, `channel_create`, `channel_delete`, `role_create`, `role_delete`, `invite_link`, `prune`."
                )
            )

    @wl_user.command(name="all", description="Whitelist user for all anti-nuke categories")
    @app_commands.describe(action="Select action (Add, Remove, View)", user="User to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_user_all(self, ctx: commands.Context, action: str, user: Optional[discord.Member] = None):
        await self._handle_user_whitelist_cmd(ctx, "all", action, user)

    @wl_user.command(name="ban", description="Whitelist user for Banning Members")
    @app_commands.describe(action="Select action (Add, Remove, View)", user="User to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_user_ban(self, ctx: commands.Context, action: str, user: Optional[discord.Member] = None):
        await self._handle_user_whitelist_cmd(ctx, "ban", action, user)

    @wl_user.command(name="kick", description="Whitelist user for Kicking Members")
    @app_commands.describe(action="Select action (Add, Remove, View)", user="User to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_user_kick(self, ctx: commands.Context, action: str, user: Optional[discord.Member] = None):
        await self._handle_user_whitelist_cmd(ctx, "kick", action, user)

    @wl_user.command(name="bot_add", description="Whitelist user for Adding Bots")
    @app_commands.describe(action="Select action (Add, Remove, View)", user="User to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_user_bot_add(self, ctx: commands.Context, action: str, user: Optional[discord.Member] = None):
        await self._handle_user_whitelist_cmd(ctx, "bot_add", action, user)

    @wl_user.command(name="role_update", description="Whitelist user for Giving Dangerous Permissions")
    @app_commands.describe(action="Select action (Add, Remove, View)", user="User to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_user_update(self, ctx: commands.Context, action: str, user: Optional[discord.Member] = None):
        await self._handle_user_whitelist_cmd(ctx, "dangerous_perms", action, user)

    @wl_user.command(name="role_add", description="Whitelist user for Giving Administration Roles")
    @app_commands.describe(action="Select action (Add, Remove, View)", user="User to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_user_add(self, ctx: commands.Context, action: str, user: Optional[discord.Member] = None):
        await self._handle_user_whitelist_cmd(ctx, "admin_role", action, user)

    @wl_user.command(name="channel_create", description="Whitelist user for Creating Channels")
    @app_commands.describe(action="Select action (Add, Remove, View)", user="User to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_user_ch_create(self, ctx: commands.Context, action: str, user: Optional[discord.Member] = None):
        await self._handle_user_whitelist_cmd(ctx, "channel_create", action, user)

    @wl_user.command(name="channel_delete", description="Whitelist user for Deleting Channels")
    @app_commands.describe(action="Select action (Add, Remove, View)", user="User to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_user_ch_delete(self, ctx: commands.Context, action: str, user: Optional[discord.Member] = None):
        await self._handle_user_whitelist_cmd(ctx, "channel_delete", action, user)

    @wl_user.command(name="role_create", description="Whitelist user for Creating Roles")
    @app_commands.describe(action="Select action (Add, Remove, View)", user="User to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_user_create(self, ctx: commands.Context, action: str, user: Optional[discord.Member] = None):
        await self._handle_user_whitelist_cmd(ctx, "role_create", action, user)

    @wl_user.command(name="role_delete", description="Whitelist user for Deleting Roles")
    @app_commands.describe(action="Select action (Add, Remove, View)", user="User to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_user_delete(self, ctx: commands.Context, action: str, user: Optional[discord.Member] = None):
        await self._handle_user_whitelist_cmd(ctx, "role_delete", action, user)

    @wl_user.command(name="invite_link", description="Whitelist user for Sending Invite Links")
    @app_commands.describe(action="Select action (Add, Remove, View)", user="User to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_user_invite_link(self, ctx: commands.Context, action: str, user: Optional[discord.Member] = None):
        await self._handle_user_whitelist_cmd(ctx, "invite_links", action, user)

    @wl_user.command(name="prune", description="Whitelist user for Pruning Members")
    @app_commands.describe(action="Select action (Add, Remove, View)", user="User to whitelist or remove")
    @app_commands.choices(action=WHITELIST_ACTION_CHOICES)
    async def wl_user_prune(self, ctx: commands.Context, action: str, user: Optional[discord.Member] = None):
        await self._handle_user_whitelist_cmd(ctx, "prune", action, user)

    # --- Channel Whitelist Sub-Group ---
    @whitelist.group(name="channel", description="Manage whitelisted channels")
    async def wl_channel(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send(embed=sec_embed("Subcommands: `add`, `remove`, `view`."))

    @wl_channel.command(name="add", description="Add channel to whitelist")
    async def wl_add_channel(self, ctx: commands.Context, channel: discord.abc.GuildChannel):
        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.defer()
        if not await self._can_edit_whitelist(ctx):
            return await ctx.send(embed=sec_embed(f"{self.no}Only the **Server Owner** can edit the whitelist."))
        data = self.db.get_guild(ctx.guild.id)
        if channel.id not in data["whitelist_channels"]:
            data["whitelist_channels"].append(channel.id)
            self.db.update_field(ctx.guild.id, "whitelist_channels", data["whitelist_channels"])
        await ctx.send(embed=sec_embed(f"{self.ok}Added {channel.mention} to the channel whitelist."))

    @wl_channel.command(name="remove", description="Remove a channel from whitelist")
    async def wl_remove_channel(self, ctx: commands.Context, channel: discord.abc.GuildChannel):
        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.defer()
        if not await self._can_edit_whitelist(ctx):
            return await ctx.send(embed=sec_embed(f"{self.no}Only the **Server Owner** can edit the whitelist."))
        data = self.db.get_guild(ctx.guild.id)
        if channel.id in data["whitelist_channels"]:
            data["whitelist_channels"].remove(channel.id)
            self.db.update_field(ctx.guild.id, "whitelist_channels", data["whitelist_channels"])
        await ctx.send(embed=sec_embed(f"{self.no}Removed {channel.mention} from the channel whitelist."))

    @wl_channel.command(name="view", description="Shows current whitelisted channels")
    async def wl_view_channels(self, ctx: commands.Context):
        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.defer()
        data = self.db.get_guild(ctx.guild.id)
        channels = [f"<#{cid}> (`{cid}`)" for cid in data["whitelist_channels"]]
        await ctx.send(
            embed=discord.Embed(title="Whitelisted Channels", description="\n".join(channels) or "None", color=BOT_BLUE)
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiNuke(bot))
