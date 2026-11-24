import re
import time as time_mod
from typing import Literal, Optional, Tuple, Union

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

BOT_BLUE = 0x0099FF


def sec_embed(text: str, color: int = BOT_BLUE) -> discord.Embed:
    return discord.Embed(description=text, color=color)


PERM_CHOICES = Literal["admin", "antinuke", "moderation", "verification"]

TIME_REGEX = re.compile(r"^(\d+)([smhdw])$", re.IGNORECASE)
TIME_MULTIPLIERS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
}
URL_REGEX = re.compile(r"https?://\S+", re.IGNORECASE)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def parse_duration(duration_str: str) -> Optional[int]:
    match = TIME_REGEX.match(duration_str.strip())
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2).lower()
    return amount * TIME_MULTIPLIERS[unit]


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        self.check_expired_punishments.start()

    def cog_unload(self):
        self.check_expired_punishments.cancel()

    @property
    def ok(self) -> str:
        return getattr(self.bot, "ok_prefix", "")

    @property
    def no(self) -> str:
        return getattr(self.bot, "no_prefix", "")

    def has_bot_perm(self, ctx: commands.Context, required: str) -> bool:
        if ctx.author.id == ctx.guild.owner_id:
            return True
        if ctx.author.guild_permissions.administrator:
            return True
        perms = self.db.get_user_permissions(ctx.guild.id, ctx.author.id)
        return "admin" in perms or required in perms

    async def can_edit_bot_profile(self, ctx: commands.Context) -> bool:
        return ctx.author.id == ctx.guild.owner_id or await self.bot.is_owner(ctx.author)

    async def _get_image_payload(
        self,
        ctx: commands.Context,
        image: Optional[discord.Attachment],
        url: Optional[str],
    ) -> Tuple[Optional[bytes], bool, Optional[str]]:
        if url and url.strip().lower() in ("reset", "remove", "clear", "none"):
            return None, True, None

        attachment = image or (ctx.message.attachments[0] if ctx.message and ctx.message.attachments else None)
        if attachment:
            if attachment.size > 10 * 1024 * 1024:
                return None, False, "Image file size must be **10 MB or less**."
            return await attachment.read(), False, None

        if url:
            cleaned_url = url.strip()
            if not cleaned_url.startswith(("http://", "https://")):
                return None, False, "Provide a valid `http://` or `https://` image URL, attach an image, or type `reset`."
            try:
                timeout = aiohttp.ClientTimeout(total=15)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(cleaned_url) as resp:
                        if resp.status != 200:
                            return None, False, f"Failed to download image (HTTP `{resp.status}`)."
                        data = await resp.read()
                        if len(data) > 10 * 1024 * 1024:
                            return None, False, "Image file size must be **10 MB or less**."
                        return data, False, None
            except Exception:
                return None, False, "Could not fetch an image from that URL."

        return None, False, "Attach an image, provide an image URL, or pass `reset`."

    async def log_mod(self, guild: discord.Guild, action: str, moderator: discord.Member, target: str, reason: str):
        data = self.db.get_guild(guild.id)
        log_id = data.get("mod_log_id")
        if log_id:
            channel = guild.get_channel(log_id)
            if channel:
                embed = discord.Embed(
                    title=f"Moderation: {action}",
                    color=BOT_BLUE,
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="Target", value=target, inline=True)
                embed.add_field(name="Moderator", value=moderator.mention, inline=True)
                embed.add_field(name="Reason", value=reason, inline=False)
                await channel.send(embed=embed)

    @tasks.loop(seconds=30)
    async def check_expired_punishments(self):
        now = time_mod.time()
        for row in self.db.get_expired_bans(now):
            guild_id, user_id = row["guild_id"], row["user_id"]
            guild = self.bot.get_guild(guild_id)
            if guild:
                try:
                    user = await self.bot.fetch_user(user_id)
                    await guild.unban(user, reason="Temporary ban duration expired")
                except (discord.NotFound, discord.Forbidden):
                    pass
            self.db.remove_temp_ban(guild_id, user_id)

        for row in self.db.get_expired_mutes(now):
            guild_id, user_id = row["guild_id"], row["user_id"]
            guild = self.bot.get_guild(guild_id)
            if guild:
                data = self.db.get_guild(guild_id)
                role = guild.get_role(data["mute_role_id"]) if data["mute_role_id"] else None
                member = guild.get_member(user_id)
                if role and member and role in member.roles:
                    try:
                        await member.remove_roles(role, reason="Temporary mute duration expired")
                    except discord.Forbidden:
                        pass
            self.db.remove_temp_mute(guild_id, user_id)

    @check_expired_punishments.before_loop
    async def before_check_expired(self):
        await self.bot.wait_until_ready()

    # --- Server Bot PFP & Banner Commands ---
    @commands.hybrid_command(name="setpfp", description="Change or reset the bot's server avatar (PFP)")
    @app_commands.describe(
        image="Upload an image (PNG, JPG, WEBP, GIF)",
        url="Direct image URL, or type 'reset' to restore default PFP",
    )
    async def set_server_pfp(
        self,
        ctx: commands.Context,
        image: Optional[discord.Attachment] = None,
        url: Optional[str] = None,
    ):
        if not await self.can_edit_bot_profile(ctx):
            return await ctx.send(
                embed=sec_embed(f"{self.no}Only the **Server Owner** or **Bot Owner** can change the bot's server PFP."),
                ephemeral=True,
            )

        if ctx.interaction:
            await ctx.defer()

        img_bytes, is_reset, error = await self._get_image_payload(ctx, image, url)
        if error:
            return await ctx.send(embed=sec_embed(f"{self.no}{error}"), ephemeral=True)

        try:
            await ctx.guild.me.edit(avatar=None if is_reset else img_bytes)
        except TypeError:
            return await ctx.send(
                embed=sec_embed(f"{self.no}Your `discord.py` version is outdated. Update `requirements.txt` to `discord.py>=2.5.0` and redeploy."),
                ephemeral=True,
            )
        except discord.Forbidden:
            return await ctx.send(
                embed=sec_embed(f"{self.no}Missing permissions to edit the bot's server profile."),
                ephemeral=True,
            )
        except discord.HTTPException as e:
            return await ctx.send(
                embed=sec_embed(f"{self.no}Discord rejected the avatar: `{e.text}`"),
                ephemeral=True,
            )

        if is_reset:
            await ctx.send(embed=sec_embed(f"{self.no}Reset the bot's **Server PFP** back to default in **{ctx.guild.name}**."))
        else:
            embed = sec_embed(f"{self.ok}Updated the bot's **Server PFP** in **{ctx.guild.name}**.")
            embed.set_thumbnail(url=ctx.guild.me.display_avatar.url)
            await ctx.send(embed=embed)

    @commands.hybrid_command(name="setbanner", description="Change or reset the bot's server banner")
    @app_commands.describe(
        image="Upload a banner image (PNG, JPG, WEBP, GIF)",
        url="Direct image URL, or type 'reset' to remove the server banner",
    )
    async def set_server_banner(
        self,
        ctx: commands.Context,
        image: Optional[discord.Attachment] = None,
        url: Optional[str] = None,
    ):
        if not await self.can_edit_bot_profile(ctx):
            return await ctx.send(
                embed=sec_embed(f"{self.no}Only the **Server Owner** or **Bot Owner** can change the bot's server banner."),
                ephemeral=True,
            )

        if ctx.interaction:
            await ctx.defer()

        img_bytes, is_reset, error = await self._get_image_payload(ctx, image, url)
        if error:
            return await ctx.send(embed=sec_embed(f"{self.no}{error}"), ephemeral=True)

        try:
            await ctx.guild.me.edit(banner=None if is_reset else img_bytes)
        except TypeError:
            return await ctx.send(
                embed=sec_embed(f"{self.no}Your `discord.py` version is outdated. Update `requirements.txt` to `discord.py>=2.5.0` and redeploy."),
                ephemeral=True,
            )
        except discord.Forbidden:
            return await ctx.send(
                embed=sec_embed(f"{self.no}Missing permissions to edit the bot's server profile."),
                ephemeral=True,
            )
        except discord.HTTPException as e:
            return await ctx.send(
                embed=sec_embed(f"{self.no}Discord rejected the banner: `{e.text}`"),
                ephemeral=True,
            )

        if is_reset:
            await ctx.send(embed=sec_embed(f"{self.no}Removed the bot's **Server Banner** in **{ctx.guild.name}**."))
        else:
            embed = sec_embed(f"{self.ok}Updated the bot's **Server Banner** in **{ctx.guild.name}**.")
            if ctx.guild.me.guild_banner:
                embed.set_image(url=ctx.guild.me.guild_banner.url)
            await ctx.send(embed=embed)

    # --- Prefix Command ---
    @commands.hybrid_command(name="prefix", description="View or change the bot prefix for this server")
    @app_commands.describe(new_prefix="New prefix (max 5 characters). Leave empty to view current prefix")
    async def prefix_cmd(self, ctx: commands.Context, new_prefix: Optional[str] = None):
        current_prefix = self.db.get_prefix(ctx.guild.id)
        if new_prefix is None:
            return await ctx.send(
                embed=sec_embed(f"{self.ok}Current prefix for **{ctx.guild.name}** is `{current_prefix}`")
            )

        if not self.has_bot_perm(ctx, "admin"):
            return await ctx.send(
                embed=sec_embed(f"{self.no}Only the **Server Owner** or permitted admins can change the prefix."),
                ephemeral=True,
            )

        cleaned = new_prefix.strip()
        if not cleaned or len(cleaned) > 5 or " " in cleaned:
            return await ctx.send(
                embed=sec_embed(f"{self.no}Prefix must be **1 to 5 characters** with no spaces."),
                ephemeral=True,
            )

        self.db.set_prefix(ctx.guild.id, cleaned)
        await self.log_mod(ctx.guild, "Prefix Updated", ctx.author, f"`{current_prefix}` ➔ `{cleaned}`", "Prefix changed")
        await ctx.send(
            embed=sec_embed(f"{self.ok}Server prefix changed from `{current_prefix}` to **`{cleaned}`**.")
        )

    # --- Premium & No-Prefix Commands ---
    @commands.hybrid_command(name="premium", description="Information about the premium version")
    async def premium_cmd(self, ctx: commands.Context):
        guild_active, guild_expires = self.db.get_premium_info(ctx.guild.id)
        user_active, user_expires, np_enabled = self.db.get_user_premium(ctx.author.id)

        guild_status = (
            "**Active** (`Lifetime`)"
            if guild_active and guild_expires is None
            else (f"**Active** (Expires <t:{guild_expires}:R>)" if guild_active else "**Inactive** (`Free Plan`)")
        )

        if user_active:
            exp_text = "`Lifetime`" if user_expires is None else f"Expires <t:{user_expires}:R>"
            user_status = f"**Active** ({exp_text}) | No-Prefix: `{'ON' if np_enabled else 'OFF'}`"
        else:
            user_status = "**Inactive** | No-Prefix: `Locked`"

        embed = discord.Embed(
            title="Security Premium",
            description="Unlock No-Prefix command execution and advanced server protection.",
            color=discord.Color.gold() if (guild_active or user_active) else BOT_BLUE,
        )
        embed.add_field(name="Your User Premium", value=user_status, inline=False)
        embed.add_field(name="Server Premium", value=guild_status, inline=False)
        embed.add_field(
            name="Premium Perks",
            value=(
                "• **No-Prefix Commands** (`ban`, `clear`, `lock`, `help`, etc. without `z!`)\n"
                "• **Unlimited** Whitelisted Users, Roles & Channels\n"
                "• **Priority Anti-Nuke Execution**"
            ),
            inline=False,
        )
        embed.set_footer(text=f"User ID: {ctx.author.id} • Server ID: {ctx.guild.id}")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="noprefix", description="Toggle No-Prefix mode on or off (Premium users only)")
    @app_commands.describe(mode="Enable or disable No-Prefix mode")
    async def noprefix_toggle(self, ctx: commands.Context, mode: Optional[Literal["on", "off"]] = None):
        user_active, _, np_enabled = self.db.get_user_premium(ctx.author.id)
        if not user_active:
            return await ctx.send(
                embed=sec_embed(f"{self.no}You are not whitelisted for **Premium** No-Prefix access."),
                ephemeral=True,
            )

        new_state = (not np_enabled) if mode is None else (mode == "on")
        self.db.set_user_no_prefix(ctx.author.id, new_state)
        prefix = self.ok if new_state else self.no
        await ctx.send(
            embed=sec_embed(f"{prefix}No-Prefix mode is now **`{'ENABLED' if new_state else 'DISABLED'}`** for {ctx.author.mention}."),
            ephemeral=True,
        )

    @commands.command(name="addpremium", hidden=True)
    @commands.is_owner()
    async def add_premium_cmd(
        self,
        ctx: commands.Context,
        target: Optional[Union[discord.User, int]] = None,
        days: Optional[int] = None,
    ):
        duration_label = f"`{days}` day(s)" if days else "`Lifetime`"

        if isinstance(target, discord.User):
            self.db.add_user_premium(target.id, days)
            return await ctx.send(
                embed=sec_embed(f"{self.ok}Whitelisted {target.mention} (`{target.id}`) for **User Premium + No-Prefix** ({duration_label}).")
            )

        target_id = target if isinstance(target, int) else ctx.guild.id
        if self.bot.get_guild(target_id):
            self.db.set_premium(target_id, days)
            return await ctx.send(
                embed=sec_embed(f"{self.ok}Activated **Server Premium** ({duration_label}) for server `{target_id}`.")
            )

        try:
            user = await self.bot.fetch_user(target_id)
            self.db.add_user_premium(user.id, days)
            return await ctx.send(
                embed=sec_embed(f"{self.ok}Whitelisted {user.mention} (`{user.id}`) for **User Premium + No-Prefix** ({duration_label}).")
            )
        except discord.NotFound:
            return await ctx.send(embed=sec_embed(f"{self.no}Could not find a user or server with that ID."))

    @commands.command(name="removepremium", hidden=True)
    @commands.is_owner()
    async def remove_premium_cmd(self, ctx: commands.Context, target: Optional[Union[discord.User, int]] = None):
        if isinstance(target, discord.User):
            self.db.remove_user_premium(target.id)
            return await ctx.send(
                embed=sec_embed(f"{self.no}Removed **User Premium & No-Prefix** from {target.mention} (`{target.id}`).")
            )

        target_id = target if isinstance(target, int) else ctx.guild.id
        if self.bot.get_guild(target_id):
            self.db.remove_premium(target_id)
            return await ctx.send(embed=sec_embed(f"{self.no}Removed **Server Premium** from server `{target_id}`."))

        self.db.remove_user_premium(target_id)
        await ctx.send(embed=sec_embed(f"{self.no}Removed **User Premium & No-Prefix** from ID `{target_id}`."))

    @commands.command(name="premiumlist", hidden=True)
    @commands.is_owner()
    async def premium_list_cmd(self, ctx: commands.Context):
        rows = self.db.get_all_premium_users()
        if not rows:
            return await ctx.send(embed=sec_embed(f"{self.no}No users are currently whitelisted for Premium."))

        lines = []
        for r in rows:
            exp = "Lifetime" if r["premium_until"] == -1.0 else f"<t:{int(r['premium_until'])}:R>"
            np = "ON" if r["no_prefix"] else "OFF"
            lines.append(f"{self.ok}<@{r['user_id']}> (`{r['user_id']}`) — `{exp}` | No-Prefix: `{np}`")

        embed = discord.Embed(title="Whitelisted Premium Users", description="\n".join(lines), color=BOT_BLUE)
        await ctx.send(embed=embed)

    # --- Info Commands ---
    @commands.hybrid_command(name="about", description="Information about the bot")
    async def about(self, ctx: commands.Context):
        p = self.db.get_prefix(ctx.guild.id)
        embed = discord.Embed(
            title="Security Anti-Nuke Bot",
            description=f"Real-time server protection, button verification, and moderation.\nPrefix: `{p}` | Slash: `/`",
            color=BOT_BLUE,
        )
        embed.add_field(name="Latency", value=f"`{round(self.bot.latency * 1000)}ms`")
        embed.add_field(name="Servers", value=f"`{len(self.bot.guilds)}`")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="help", description="Get some help")
    async def help_cmd(self, ctx: commands.Context):
        p = self.db.get_prefix(ctx.guild.id)
        embed = discord.Embed(title=f"Security Commands (Prefix: {p})", color=BOT_BLUE)
        embed.add_field(name="Anti-Nuke", value=f"`{p}enable`, `{p}disable`, `{p}setlimit`, `{p}setpunishment`, `{p}scan`, `{p}settings`", inline=False)
        embed.add_field(name="Whitelist", value=f"`{p}whitelist role <cat>`, `{p}whitelist user <cat>`, `{p}whitelist channel <add|remove|view>`", inline=False)
        embed.add_field(name="Permissions", value=f"`{p}permission add|remove|reset|reset_id|view_permission|view_user`", inline=False)
        embed.add_field(name="Moderation", value=f"`{p}warn`, `{p}warnings`, `{p}delwarn`, `{p}clearwarns`, `{p}ban`, `{p}softban`, `{p}unban`, `{p}kick`, `{p}mute`, `{p}unmute`, `{p}setmuterole`, `{p}clear`, `{p}lock`, `{p}unlock`, `{p}lockall`, `{p}unlockall`", inline=False)
        embed.add_field(name="Verification & Utility", value=f"`{p}verification setup|panel`, `{p}setpfp`, `{p}setbanner`, `{p}premium`, `{p}noprefix`, `{p}prefix`, `{p}logs`, `{p}server`, `{p}user`, `{p}about`", inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="invite", description="Invite link of the bot")
    async def invite_cmd(self, ctx: commands.Context):
        url = discord.utils.oauth_url(self.bot.user.id, permissions=discord.Permissions(administrator=True))
        await ctx.send(embed=sec_embed(f"{self.ok}**[Click here to invite Security Bot to your server]({url})**"), ephemeral=True)

    @commands.hybrid_command(name="server", description="Information about the server")
    async def server_info(self, ctx: commands.Context):
        g = ctx.guild
        embed = discord.Embed(title=g.name, color=BOT_BLUE)
        embed.add_field(name="Owner", value=f"<@{g.owner_id}>")
        embed.add_field(name="Members", value=f"`{g.member_count}`")
        embed.add_field(name="Roles", value=f"`{len(g.roles)}`")
        embed.add_field(name="Channels", value=f"`{len(g.channels)}`")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="user", description="Information about you or the selected user")
    async def user_info(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        target = user or ctx.author
        embed = discord.Embed(title=str(target), color=BOT_BLUE)
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="ID", value=f"`{target.id}`")
        embed.add_field(name="Joined Server", value=discord.utils.format_dt(target.joined_at, style="R"))
        embed.add_field(name="Account Created", value=discord.utils.format_dt(target.created_at, style="R"))
        await ctx.send(embed=embed)

    # --- Logs Group ---
    @commands.hybrid_group(name="logs", description="Configure logging channels")
    async def logs(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send(embed=sec_embed("Use `logs mod #channel` or `logs security #channel`."))

    @logs.command(name="mod", description="Log channel for moderation commands")
    async def logs_mod(self, ctx: commands.Context, channel: discord.TextChannel):
        if not self.has_bot_perm(ctx, "admin"):
            return await ctx.send(embed=sec_embed(f"{self.no}Missing admin permission."), ephemeral=True)
        self.db.update_field(ctx.guild.id, "mod_log_id", channel.id)
        await ctx.send(embed=sec_embed(f"{self.ok}Moderation logs channel has been set to {channel.mention}."))

    @logs.command(name="security", description="Log channel for security actions")
    async def logs_security(self, ctx: commands.Context, channel: discord.TextChannel):
        if not self.has_bot_perm(ctx, "admin"):
            return await ctx.send(embed=sec_embed(f"{self.no}Missing admin permission."), ephemeral=True)
        self.db.update_field(ctx.guild.id, "sec_log_id", channel.id)
        await ctx.send(embed=sec_embed(f"{self.ok}Security logs channel has been set to {channel.mention}."))

    # --- Permissions Group ---
    @commands.hybrid_group(name="permission", description="Manage bot-level permissions")
    async def permission(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send(embed=sec_embed("Subcommands: `add`, `remove`, `reset`, `reset_id`, `view_permission`, `view_user`."))

    @permission.command(name="add", description="Add some permission to a user")
    async def perm_add(self, ctx: commands.Context, user: discord.Member, perm: PERM_CHOICES):
        if ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=sec_embed(f"{self.no}Only the **Server Owner** can manage bot permissions."), ephemeral=True)
        self.db.add_permission(ctx.guild.id, user.id, perm)
        await ctx.send(embed=sec_embed(f"{self.ok}Granted **{perm}** permission to {user.mention}."))

    @permission.command(name="remove", description="Remove some permission from a user")
    async def perm_remove(self, ctx: commands.Context, user: discord.Member, perm: PERM_CHOICES):
        if ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=sec_embed(f"{self.no}Only the **Server Owner** can manage bot permissions."), ephemeral=True)
        self.db.remove_permission(ctx.guild.id, user.id, perm)
        await ctx.send(embed=sec_embed(f"{self.no}Removed **{perm}** permission from {user.mention}."))

    @permission.command(name="reset", description="Remove all the permissions from a user")
    async def perm_reset(self, ctx: commands.Context, user: discord.Member):
        if ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=sec_embed(f"{self.no}Only the **Server Owner** can manage bot permissions."), ephemeral=True)
        self.db.reset_user_permissions(ctx.guild.id, user.id)
        await ctx.send(embed=sec_embed(f"{self.no}Reset all bot permissions for {user.mention}."))

    @permission.command(name="reset_id", description="Remove all the permissions from a user using their id")
    async def perm_reset_id(self, ctx: commands.Context, user_id: str):
        if ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=sec_embed(f"{self.no}Only the **Server Owner** can manage bot permissions."), ephemeral=True)
        cleaned_id = int(user_id.strip("<@!>"))
        self.db.reset_user_permissions(ctx.guild.id, cleaned_id)
        await ctx.send(embed=sec_embed(f"{self.no}Reset all bot permissions for ID `{cleaned_id}`."))

    @permission.command(name="view_permission", description="Get users with the selected permission")
    async def perm_view_perm(self, ctx: commands.Context, perm: PERM_CHOICES):
        users = self.db.get_users_with_permission(ctx.guild.id, perm)
        text = "\n".join(f"<@{u}> (`{u}`)" for u in users) or "None"
        await ctx.send(embed=discord.Embed(title=f"Users with `{perm}`", description=text, color=BOT_BLUE))

    @permission.command(name="view_user", description="Get granted permissions of a user")
    async def perm_view_user(self, ctx: commands.Context, user: discord.Member):
        perms = self.db.get_user_permissions(ctx.guild.id, user.id)
        await ctx.send(embed=sec_embed(f"{self.ok}{user.mention}'s bot permissions: `{', '.join(perms) or 'None'}`"))

    # --- Warning Commands ---
    @commands.hybrid_command(name="warn", description="Warn a user")
    @app_commands.describe(user="User to warn", reason="Reason for warning this user")
    async def warn_cmd(self, ctx: commands.Context, user: discord.Member, *, reason: Optional[str] = None):
        if not self.has_bot_perm(ctx, "moderation"):
            return await ctx.send(embed=sec_embed(f"{self.no}Missing moderation permission."), ephemeral=True)
        if user.bot:
            return await ctx.send(embed=sec_embed(f"{self.no}You cannot warn a bot."), ephemeral=True)
        if user.id in (ctx.guild.owner_id, ctx.author.id):
            return await ctx.send(embed=sec_embed(f"{self.no}You cannot warn this user."), ephemeral=True)
        if ctx.author.id != ctx.guild.owner_id and user.top_role >= ctx.author.top_role:
            return await ctx.send(embed=sec_embed(f"{self.no}You cannot warn someone with an equal or higher role."), ephemeral=True)

        warn_reason = reason or "No reason provided"
        warn_id = self.db.add_warning(ctx.guild.id, user.id, ctx.author.id, warn_reason)
        total_warns = len(self.db.get_warnings(ctx.guild.id, user.id))

        try:
            await user.send(
                embed=sec_embed(
                    f"{self.no}You have been warned in **{ctx.guild.name}**.\n"
                    f"**Reason:** `{warn_reason}`\n"
                    f"**Total Warnings:** `{total_warns}`"
                )
            )
        except discord.Forbidden:
            pass

        await self.log_mod(
            ctx.guild,
            f"Warn (#{warn_id})",
            ctx.author,
            f"{user} (`{user.id}`)",
            f"{warn_reason} | Total Warnings: `{total_warns}`",
        )

        await ctx.send(
            embed=sec_embed(
                f"{self.ok}{user.mention} has been warned (Warning `#{warn_id}` | Total: `{total_warns}`). | Reason: `{warn_reason}`"
            )
        )

    @commands.hybrid_command(name="warnings", description="View warnings of a user")
    @app_commands.describe(user="User to view warnings for")
    async def warnings_cmd(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        target = user or ctx.author
        rows = self.db.get_warnings(ctx.guild.id, target.id)
        if not rows:
            return await ctx.send(embed=sec_embed(f"{self.ok}{target.mention} has **0** warnings."))

        lines = [
            f"`#{r['id']}` — **{r['reason']}** (by <@{r['moderator_id']}>, <t:{int(r['created_at'])}:R>)"
            for r in rows[-15:]
        ]
        embed = discord.Embed(
            title=f"Warnings for {target} ({len(rows)} total)",
            description="\n".join(lines),
            color=BOT_BLUE,
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="delwarn", description="Delete a specific warning by its ID")
    @app_commands.describe(warn_id="The warning ID number to remove")
    async def delwarn_cmd(self, ctx: commands.Context, warn_id: int):
        if not self.has_bot_perm(ctx, "moderation"):
            return await ctx.send(embed=sec_embed(f"{self.no}Missing moderation permission."), ephemeral=True)
        removed = self.db.remove_warning(ctx.guild.id, warn_id)
        if not removed:
            return await ctx.send(embed=sec_embed(f"{self.no}Warning `#{warn_id}` was not found in this server."), ephemeral=True)
        await self.log_mod(ctx.guild, "Warning Deleted", ctx.author, f"Warning `#{warn_id}`", "Warning removed")
        await ctx.send(embed=sec_embed(f"{self.ok}Removed warning `#{warn_id}`."))

    @commands.hybrid_command(name="clearwarns", description="Clear all warnings of a user")
    @app_commands.describe(user="User whose warnings should be cleared")
    async def clearwarns_cmd(self, ctx: commands.Context, user: discord.Member):
        if not self.has_bot_perm(ctx, "moderation"):
            return await ctx.send(embed=sec_embed(f"{self.no}Missing moderation permission."), ephemeral=True)
        count = self.db.clear_warnings(ctx.guild.id, user.id)
        await self.log_mod(ctx.guild, "Warnings Cleared", ctx.author, f"{user} (`{user.id}`)", f"Cleared {count} warning(s)")
        await ctx.send(embed=sec_embed(f"{self.ok}Cleared **{count}** warning(s) for {user.mention}."))

    # --- Moderation Commands ---
    @commands.hybrid_command(name="ban", description="Ban a user")
    @app_commands.describe(
        user="User to ban",
        time="Time until the user gets unbanned",
        reason="Reason of banning this user",
        delete_days="Number of days the messages should be deleted, 7 is the limit",
    )
    async def ban_cmd(
        self,
        ctx: commands.Context,
        user: discord.Member,
        time: Optional[str] = None,
        reason: Optional[str] = None,
        delete_days: Optional[app_commands.Range[int, 0, 7]] = None,
    ):
        if not self.has_bot_perm(ctx, "moderation"):
            return await ctx.send(embed=sec_embed(f"{self.no}Missing moderation permission."), ephemeral=True)
        if user.id in (ctx.guild.owner_id, ctx.author.id):
            return await ctx.send(embed=sec_embed(f"{self.no}You cannot ban this user."), ephemeral=True)
        if ctx.author.id != ctx.guild.owner_id and user.top_role >= ctx.author.top_role:
            return await ctx.send(embed=sec_embed(f"{self.no}You cannot ban someone with an equal or higher role."), ephemeral=True)
        if user.top_role >= ctx.guild.me.top_role:
            return await ctx.send(embed=sec_embed(f"{self.no}I cannot ban this user due to role hierarchy."), ephemeral=True)

        duration_seconds = None
        if time:
            duration_seconds = parse_duration(time)
            if not duration_seconds:
                return await ctx.send(embed=sec_embed(f"{self.no}Invalid `time` format. Use `30m`, `12h`, `7d`, or `1w`."), ephemeral=True)

        ban_reason = reason or "No reason provided"
        days = delete_days if delete_days is not None else 0

        await user.ban(reason=ban_reason, delete_message_seconds=days * 86400)
        if duration_seconds:
            self.db.add_temp_ban(ctx.guild.id, user.id, time_mod.time() + duration_seconds)

        await self.log_mod(
            ctx.guild,
            "Temp-Ban" if time else "Ban",
            ctx.author,
            f"{user} (`{user.id}`)",
            f"{ban_reason} | Duration: `{time or 'Permanent'}` | Deleted Messages: `{days}d`",
        )

        duration_label = f" for **{time}**" if time else ""
        await ctx.send(
            embed=sec_embed(f"{self.ok}**{user}** has been banned{duration_label}. | Reason: `{ban_reason}`")
        )

    @commands.hybrid_command(name="softban", description="Softban a user")
    async def softban_cmd(self, ctx: commands.Context, user: discord.Member, *, reason: str = "Softban"):
        if not self.has_bot_perm(ctx, "moderation"):
            return await ctx.send(embed=sec_embed(f"{self.no}Missing moderation permission."), ephemeral=True)
        await user.ban(reason=reason, delete_message_seconds=86400)
        await ctx.guild.unban(user, reason="Softban unban")
        await self.log_mod(ctx.guild, "Softban", ctx.author, f"{user} (`{user.id}`)", reason)
        await ctx.send(embed=sec_embed(f"{self.ok}**{user}** has been softbanned (cleared 24h messages)."))

    @commands.hybrid_command(name="unban", description="Unban a user")
    async def unban_cmd(self, ctx: commands.Context, user_id: str, *, reason: str = "Unbanned"):
        if not self.has_bot_perm(ctx, "moderation"):
            return await ctx.send(embed=sec_embed(f"{self.no}Missing moderation permission."), ephemeral=True)
        cleaned_id = int(user_id.strip("<@!>"))
        user = await self.bot.fetch_user(cleaned_id)
        await ctx.guild.unban(user, reason=reason)
        self.db.remove_temp_ban(ctx.guild.id, user.id)
        await self.log_mod(ctx.guild, "Unban", ctx.author, f"{user} (`{user.id}`)", reason)
        await ctx.send(embed=sec_embed(f"{self.ok}**{user}** has been unbanned."))

    @commands.hybrid_command(name="kick", description="Kick a user")
    @app_commands.describe(user="User to kick", reason="Reason of kicking this user")
    async def kick_cmd(self, ctx: commands.Context, user: discord.Member, *, reason: Optional[str] = None):
        if not self.has_bot_perm(ctx, "moderation"):
            return await ctx.send(embed=sec_embed(f"{self.no}Missing moderation permission."), ephemeral=True)
        if user.id in (ctx.guild.owner_id, ctx.author.id):
            return await ctx.send(embed=sec_embed(f"{self.no}You cannot kick this user."), ephemeral=True)
        if ctx.author.id != ctx.guild.owner_id and user.top_role >= ctx.author.top_role:
            return await ctx.send(embed=sec_embed(f"{self.no}You cannot kick someone with an equal or higher role."), ephemeral=True)
        if user.top_role >= ctx.guild.me.top_role:
            return await ctx.send(embed=sec_embed(f"{self.no}I cannot kick this user due to role hierarchy."), ephemeral=True)

        kick_reason = reason or "No reason provided"
        await user.kick(reason=kick_reason)
        await self.log_mod(ctx.guild, "Kick", ctx.author, f"{user} (`{user.id}`)", kick_reason)
        await ctx.send(embed=sec_embed(f"{self.ok}**{user}** has been kicked. | Reason: `{kick_reason}`"))

    @commands.hybrid_command(name="setmuterole", description="Create a new mute role or set an existing one")
    async def set_mute_role(self, ctx: commands.Context, role: Optional[discord.Role] = None):
        if not self.has_bot_perm(ctx, "admin"):
            return await ctx.send(embed=sec_embed(f"{self.no}Missing admin permission."), ephemeral=True)
        if not role:
            role = await ctx.guild.create_role(name="Muted", reason="Security Bot Mute Role")
            for ch in ctx.guild.channels:
                await ch.set_permissions(role, send_messages=False, speak=False, add_reactions=False)
        self.db.update_field(ctx.guild.id, "mute_role_id", role.id)
        await ctx.send(embed=sec_embed(f"{self.ok}Mute role has been configured to {role.mention}."))

    @commands.hybrid_command(name="mute", description="Mute a user")
    @app_commands.describe(user="User to mute", time="Time until the user gets unmuted", reason="Reason of muting this user")
    async def mute_cmd(
        self,
        ctx: commands.Context,
        user: discord.Member,
        time: Optional[str] = None,
        *,
        reason: Optional[str] = None,
    ):
        if not self.has_bot_perm(ctx, "moderation"):
            return await ctx.send(embed=sec_embed(f"{self.no}Missing moderation permission."), ephemeral=True)
        if user.id in (ctx.guild.owner_id, ctx.author.id):
            return await ctx.send(embed=sec_embed(f"{self.no}You cannot mute this user."), ephemeral=True)

        data = self.db.get_guild(ctx.guild.id)
        role = ctx.guild.get_role(data["mute_role_id"]) if data["mute_role_id"] else None
        if not role:
            return await ctx.send(embed=sec_embed(f"{self.no}Mute role not set. Run `/setmuterole` first."), ephemeral=True)

        duration_seconds = None
        if time:
            duration_seconds = parse_duration(time)
            if not duration_seconds:
                return await ctx.send(embed=sec_embed(f"{self.no}Invalid `time` format. Use `10m`, `2h`, `1d`, or `1w`."), ephemeral=True)

        mute_reason = reason or "No reason provided"
        await user.add_roles(role, reason=mute_reason)

        if duration_seconds:
            self.db.add_temp_mute(ctx.guild.id, user.id, time_mod.time() + duration_seconds)
        else:
            self.db.remove_temp_mute(ctx.guild.id, user.id)

        await self.log_mod(
            ctx.guild,
            "Temp-Mute" if time else "Mute",
            ctx.author,
            f"{user} (`{user.id}`)",
            f"{mute_reason} | Duration: `{time or 'Permanent'}`",
        )

        duration_label = f" for **{time}**" if time else ""
        await ctx.send(embed=sec_embed(f"{self.ok}{user.mention} has been muted{duration_label}. | Reason: `{mute_reason}`"))

    @commands.hybrid_command(name="unmute", description="Unmute a user")
    @app_commands.describe(user="User to unmute", reason="Reason of unmuting this user")
    async def unmute_cmd(self, ctx: commands.Context, user: discord.Member, *, reason: Optional[str] = None):
        if not self.has_bot_perm(ctx, "moderation"):
            return await ctx.send(embed=sec_embed(f"{self.no}Missing moderation permission."), ephemeral=True)
        data = self.db.get_guild(ctx.guild.id)
        role = ctx.guild.get_role(data["mute_role_id"]) if data["mute_role_id"] else None
        if role and role in user.roles:
            await user.remove_roles(role, reason=reason or "Manual unmute")
        self.db.remove_temp_mute(ctx.guild.id, user.id)
        await self.log_mod(ctx.guild, "Unmute", ctx.author, f"{user} (`{user.id}`)", reason or "No reason provided")
        await ctx.send(embed=sec_embed(f"{self.ok}{user.mention} has been unmuted."))

    @commands.hybrid_command(name="clear", description="Clear tons of messages")
    @app_commands.describe(
        amount="Amount of messages to check and delete",
        user="Check if message is sent by selected user",
        bots="Check if message is sent by bot, default: false",
        embeds="Check if message contains an embed, default: false",
        links="Check if message contains a link, default: false",
        images="Check if message contains an image, default: false",
    )
    async def clear_cmd(
        self,
        ctx: commands.Context,
        amount: app_commands.Range[int, 1, 200],
        user: Optional[discord.User] = None,
        bots: Optional[bool] = False,
        embeds: Optional[bool] = False,
        links: Optional[bool] = False,
        images: Optional[bool] = False,
    ):
        if not self.has_bot_perm(ctx, "moderation"):
            return await ctx.send(embed=sec_embed(f"{self.no}Missing moderation permission."), ephemeral=True)

        if ctx.interaction:
            await ctx.defer(ephemeral=True)
        elif ctx.message:
            try:
                await ctx.message.delete()
            except discord.Forbidden:
                pass

        def message_check(msg: discord.Message) -> bool:
            if user is not None and msg.author.id != user.id:
                return False
            if bots and not msg.author.bot:
                return False
            if embeds and not msg.embeds:
                return False
            if links and not URL_REGEX.search(msg.content or ""):
                return False
            if images:
                has_img_attachment = any(
                    (att.content_type and att.content_type.startswith("image/"))
                    or att.filename.lower().endswith(IMAGE_EXTENSIONS)
                    for att in msg.attachments
                )
                has_img_embed = any(e.image.url or e.thumbnail.url for e in msg.embeds)
                if not (has_img_attachment or has_img_embed):
                    return False
            return True

        deleted = await ctx.channel.purge(limit=amount, check=message_check)
        msg = await ctx.send(
            embed=sec_embed(f"{self.ok}Deleted **{len(deleted)}** message(s) out of `{amount}` checked."),
            ephemeral=True,
        )
        if not ctx.interaction:
            await msg.delete(delay=4)

    @commands.hybrid_command(name="lock", description="Lock current or selected text channel")
    async def lock_cmd(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        if not self.has_bot_perm(ctx, "moderation"):
            return await ctx.send(embed=sec_embed(f"{self.no}Missing moderation permission."), ephemeral=True)
        ch = channel or ctx.channel
        await ch.set_permissions(ctx.guild.default_role, send_messages=False)
        await ctx.send(embed=sec_embed(f"{self.ok}{ch.mention} has been locked."))

    @commands.hybrid_command(name="unlock", description="Unlock current or selected text channel")
    async def unlock_cmd(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        if not self.has_bot_perm(ctx, "moderation"):
            return await ctx.send(embed=sec_embed(f"{self.no}Missing moderation permission."), ephemeral=True)
        ch = channel or ctx.channel
        await ch.set_permissions(ctx.guild.default_role, send_messages=None)
        await ctx.send(embed=sec_embed(f"{self.no}{ch.mention} has been unlocked."))

    @commands.hybrid_command(name="lockall", description="Lock all the text channels")
    async def lockall_cmd(self, ctx: commands.Context):
        if not self.has_bot_perm(ctx, "admin"):
            return await ctx.send(embed=sec_embed(f"{self.no}Missing admin permission."), ephemeral=True)
        await ctx.defer()
        for ch in ctx.guild.text_channels:
            await ch.set_permissions(ctx.guild.default_role, send_messages=False)
        await ctx.send(embed=sec_embed(f"{self.ok}All text channels have been locked."))

    @commands.hybrid_command(name="unlockall", description="Unlock all the locked text channels")
    async def unlockall_cmd(self, ctx: commands.Context):
        if not self.has_bot_perm(ctx, "admin"):
            return await ctx.send(embed=sec_embed(f"{self.no}Missing admin permission."), ephemeral=True)
        await ctx.defer()
        for ch in ctx.guild.text_channels:
            await ch.set_permissions(ctx.guild.default_role, send_messages=None)
        await ctx.send(embed=sec_embed(f"{self.no}All text channels have been unlocked."))


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
