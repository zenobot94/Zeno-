from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

TOGGLE_ON = "🟢"
TOGGLE_OFF = "🔴"
BOT_BLUE = 0x0099FF


def sec_embed(text: str, color: int = BOT_BLUE) -> discord.Embed:
    return discord.Embed(description=text, color=color)


def build_verify_panel_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="Server Verification",
        description=f"{TOGGLE_ON} To verify, click on the button below.",
        color=BOT_BLUE,
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text=f"{guild.name} • Security Verification")
    return embed


class VerifyButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Verify",
        style=discord.ButtonStyle.success,
        emoji="🪪",
        custom_id="security_bot:verify_button",
    )
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return

        cog: Optional[Verification] = interaction.client.get_cog("Verification")
        if not cog:
            return await interaction.response.send_message(
                embed=sec_embed(f"{TOGGLE_OFF} Verification module is currently unavailable."),
                ephemeral=True,
            )

        data = cog.db.get_guild(interaction.guild.id)
        if not data.get("verification_enabled"):
            return await interaction.response.send_message(
                embed=sec_embed(f"{TOGGLE_OFF} Verification is currently disabled on this server."),
                ephemeral=True,
            )

        unv_role = interaction.guild.get_role(data["unverified_role_id"]) if data.get("unverified_role_id") else None
        ver_role = interaction.guild.get_role(data["verified_role_id"]) if data.get("verified_role_id") else None

        already_verified = (
            (not unv_role or unv_role not in interaction.user.roles)
            and (ver_role is not None and ver_role in interaction.user.roles)
        )
        if already_verified:
            return await interaction.response.send_message(
                embed=sec_embed(f"{TOGGLE_ON} You are already verified!"),
                ephemeral=True,
            )

        try:
            await cog.complete_verification(interaction.user)
            await interaction.response.send_message(
                embed=sec_embed(f"{TOGGLE_ON} You have been verified! Welcome to **{interaction.guild.name}**."),
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=sec_embed(f"{TOGGLE_OFF} I am missing role permissions to verify you. Please move my role above the Verified/Unverified roles."),
                ephemeral=True,
            )


class Verification(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db

    def can_manage_verification(self, ctx: commands.Context) -> bool:
        if ctx.author.id == ctx.guild.owner_id or ctx.author.guild_permissions.administrator:
            return True
        perms = self.db.get_user_permissions(ctx.guild.id, ctx.author.id)
        return "admin" in perms or "verification" in perms

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        data = self.db.get_guild(member.guild.id)
        if data.get("verification_enabled") and data.get("unverified_role_id"):
            role = member.guild.get_role(data["unverified_role_id"])
            if role:
                try:
                    await member.add_roles(role, reason="Auto-assigned Unverified role")
                except discord.Forbidden:
                    pass

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        data = self.db.get_guild(channel.guild.id)
        if data.get("verification_enabled") and data.get("unverified_role_id"):
            role = channel.guild.get_role(data["unverified_role_id"])
            if role:
                try:
                    is_verify_ch = channel.id == data.get("verification_channel_id")
                    await channel.set_permissions(role, view_channel=is_verify_ch, send_messages=False)
                except discord.Forbidden:
                    pass

    async def complete_verification(self, member: discord.Member):
        data = self.db.get_guild(member.guild.id)
        if data.get("unverified_role_id"):
            unv = member.guild.get_role(data["unverified_role_id"])
            if unv and unv in member.roles:
                await member.remove_roles(unv, reason="User verified via button")
        if data.get("verified_role_id"):
            ver = member.guild.get_role(data["verified_role_id"])
            if ver and ver not in member.roles:
                await member.add_roles(ver, reason="User verified via button")

    # --- /verification Group ---
    @commands.hybrid_group(name="verification", description="Configure button verification on your server")
    async def verification(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send(
                embed=sec_embed(
                    "Subcommands: `setup`, `panel`, `enable`, `disable`, `reset`, `setup_channel`, `setup_role`, `set_verified`, `update_role`, `verify`."
                )
            )

    @verification.command(name="setup", description="One-step setup: creates verify channel, roles, locks channels, and posts the Verify button")
    @app_commands.describe(
        verified_role="Existing role to give verified members (leave empty to auto-create @Verified)",
        channel="Existing channel for verification (leave empty to auto-create #verify)",
    )
    async def ver_setup(
        self,
        ctx: commands.Context,
        verified_role: Optional[discord.Role] = None,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not self.can_manage_verification(ctx):
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Missing verification permission."), ephemeral=True)

        await ctx.defer()
        data = self.db.get_guild(ctx.guild.id)

        # 1. Unverified role
        unv_role = ctx.guild.get_role(data["unverified_role_id"]) if data.get("unverified_role_id") else None
        if not unv_role:
            unv_role = discord.utils.get(ctx.guild.roles, name="Unverified") or await ctx.guild.create_role(
                name="Unverified", reason="Security Verification Setup"
            )

        # 2. Verified role
        ver_role = verified_role or (ctx.guild.get_role(data["verified_role_id"]) if data.get("verified_role_id") else None)
        if not ver_role:
            ver_role = discord.utils.get(ctx.guild.roles, name="Verified") or await ctx.guild.create_role(
                name="Verified", reason="Security Verification Setup"
            )

        # 3. Verification channel
        ver_channel = channel or (ctx.guild.get_channel(data["verification_channel_id"]) if data.get("verification_channel_id") else None)
        if not ver_channel:
            ver_channel = await ctx.guild.create_text_channel("verify", reason="Security Verification Setup")

        # 4. Lock all channels for Unverified except #verify
        for ch in ctx.guild.channels:
            try:
                if ch.id == ver_channel.id:
                    await ch.set_permissions(unv_role, view_channel=True, send_messages=False, add_reactions=False)
                else:
                    await ch.set_permissions(unv_role, view_channel=False)
            except discord.Forbidden:
                pass

        # 5. Save settings & enable
        self.db.update_field(ctx.guild.id, "unverified_role_id", unv_role.id)
        self.db.update_field(ctx.guild.id, "verified_role_id", ver_role.id)
        self.db.update_field(ctx.guild.id, "verification_channel_id", ver_channel.id)
        self.db.update_field(ctx.guild.id, "verification_enabled", 1)

        # 6. Post the verification embed + Verify button
        await ver_channel.send(embed=build_verify_panel_embed(ctx.guild), view=VerifyButtonView())

        await ctx.send(
            embed=sec_embed(
                f"{TOGGLE_ON} Verification system started in {ver_channel.mention}!\n"
                f"• **Unverified Role:** {unv_role.mention}\n"
                f"• **Verified Role:** {ver_role.mention}"
            )
        )

    @verification.command(name="panel", description="Send the verification embed and Verify button to a channel")
    @app_commands.describe(channel="Channel to post the verification button in")
    async def ver_panel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        if not self.can_manage_verification(ctx):
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Missing verification permission."), ephemeral=True)

        data = self.db.get_guild(ctx.guild.id)
        target_ch = channel or (ctx.guild.get_channel(data["verification_channel_id"]) if data.get("verification_channel_id") else ctx.channel)
        self.db.update_field(ctx.guild.id, "verification_channel_id", target_ch.id)
        self.db.update_field(ctx.guild.id, "verification_enabled", 1)

        await target_ch.send(embed=build_verify_panel_embed(ctx.guild), view=VerifyButtonView())
        if target_ch.id != ctx.channel.id:
            await ctx.send(embed=sec_embed(f"{TOGGLE_ON} Verification panel posted in {target_ch.mention}."), ephemeral=True)
        elif ctx.interaction:
            await ctx.send(embed=sec_embed(f"{TOGGLE_ON} Verification panel posted."), ephemeral=True)

    @verification.command(name="enable", description="Enable verification on your server")
    async def ver_enable(self, ctx: commands.Context):
        if not self.can_manage_verification(ctx):
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Missing verification permission."), ephemeral=True)
        self.db.update_field(ctx.guild.id, "verification_enabled", 1)
        await ctx.send(embed=sec_embed(f"{TOGGLE_ON} Server verification has been **enabled**."))

    @verification.command(name="disable", description="Disable verification on your server")
    async def ver_disable(self, ctx: commands.Context):
        if not self.can_manage_verification(ctx):
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Missing verification permission."), ephemeral=True)
        self.db.update_field(ctx.guild.id, "verification_enabled", 0)
        await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Server verification has been **disabled**."))

    @verification.command(name="reset", description="Reset verification settings on your server")
    async def ver_reset(self, ctx: commands.Context):
        if not self.can_manage_verification(ctx):
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Missing verification permission."), ephemeral=True)
        self.db.update_field(ctx.guild.id, "verification_enabled", 0)
        self.db.update_field(ctx.guild.id, "verification_channel_id", None)
        self.db.update_field(ctx.guild.id, "unverified_role_id", None)
        self.db.update_field(ctx.guild.id, "verified_role_id", None)
        await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Verification configuration has been reset."))

    @verification.command(name="setup_channel", description="Create the #verify channel and post the Verify button")
    async def ver_setup_channel(self, ctx: commands.Context):
        if not self.can_manage_verification(ctx):
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Missing verification permission."), ephemeral=True)
        channel = await ctx.guild.create_text_channel("verify")
        self.db.update_field(ctx.guild.id, "verification_channel_id", channel.id)
        await channel.send(embed=build_verify_panel_embed(ctx.guild), view=VerifyButtonView())
        await ctx.send(embed=sec_embed(f"{TOGGLE_ON} Created verification channel {channel.mention} and posted the Verify button."))

    @verification.command(name="setup_role", description="Create the Unverified role on your server")
    async def ver_setup_role(self, ctx: commands.Context):
        if not self.can_manage_verification(ctx):
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Missing verification permission."), ephemeral=True)
        role = await ctx.guild.create_role(name="Unverified")
        self.db.update_field(ctx.guild.id, "unverified_role_id", role.id)
        await ctx.send(
            embed=sec_embed(f"{TOGGLE_ON} Created unverified role {role.mention}. Run `/verification update_role` to hide channels.")
        )

    @verification.command(name="set_verified", description="Set the role given to members when they click Verify")
    async def ver_set_verified(self, ctx: commands.Context, role: discord.Role):
        if not self.can_manage_verification(ctx):
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Missing verification permission."), ephemeral=True)
        self.db.update_field(ctx.guild.id, "verified_role_id", role.id)
        await ctx.send(embed=sec_embed(f"{TOGGLE_ON} Verified role set to {role.mention}."))

    @verification.command(name="update_role", description="Hide all channels except #verify from the Unverified role")
    async def ver_update_role(self, ctx: commands.Context):
        if not self.can_manage_verification(ctx):
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Missing verification permission."), ephemeral=True)
        data = self.db.get_guild(ctx.guild.id)
        unv_id = data.get("unverified_role_id")
        ver_ch_id = data.get("verification_channel_id")
        if not unv_id:
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Run `/verification setup_role` first."), ephemeral=True)
        role = ctx.guild.get_role(unv_id)
        await ctx.defer()
        for ch in ctx.guild.channels:
            is_verify_ch = ch.id == ver_ch_id
            await ch.set_permissions(role, view_channel=is_verify_ch, send_messages=False)
        await ctx.send(embed=sec_embed(f"{TOGGLE_ON} Updated channel permissions for the Unverified role."))

    @verification.command(name="verify", description="Manually verify a member")
    async def ver_manual(self, ctx: commands.Context, user: discord.Member):
        if not self.can_manage_verification(ctx):
            return await ctx.send(embed=sec_embed(f"{TOGGLE_OFF} Missing verification permission."), ephemeral=True)
        await self.complete_verification(user)
        await ctx.send(embed=sec_embed(f"{TOGGLE_ON} Manually verified {user.mention}."))


async def setup(bot: commands.Bot):
    await bot.add_cog(Verification(bot))
      
