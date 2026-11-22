import io
import random
import string
import time
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont


def generate_captcha_image(code: str, hex_color: str, line_count: int) -> io.BytesIO:
    width, height = 280, 100
    img = Image.new("RGB", (width, height), color=(30, 33, 36))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("arial.ttf", 40)
    except IOError:
        try:
            font = ImageFont.load_default(size=34)
        except TypeError:
            font = ImageFont.load_default()

    for _ in range(line_count):
        x1, y1 = random.randint(0, width), random.randint(0, height)
        x2, y2 = random.randint(0, width), random.randint(0, height)
        draw.line([(x1, y1), (x2, y2)], fill=hex_color, width=2)

    spacing = width // (len(code) + 1)
    for idx, char in enumerate(code):
        x = spacing * (idx + 1) - 12
        y = random.randint(22, 46)
        draw.text((x, y), char, fill=hex_color, font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


class Verification(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        self.active_challenges = {}

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
        if data["verification_enabled"] and data["unverified_role_id"]:
            role = member.guild.get_role(data["unverified_role_id"])
            if role:
                try:
                    await member.add_roles(role, reason="Auto-assigned Unverified role")
                except discord.Forbidden:
                    pass

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        data = self.db.get_guild(channel.guild.id)
        if data["verification_enabled"] and data["unverified_role_id"]:
            role = channel.guild.get_role(data["unverified_role_id"])
            if role:
                try:
                    await channel.set_permissions(role, view_channel=False)
                except discord.Forbidden:
                    pass

    async def complete_verification(self, member: discord.Member):
        data = self.db.get_guild(member.guild.id)
        if data["unverified_role_id"]:
            unv = member.guild.get_role(data["unverified_role_id"])
            if unv and unv in member.roles:
                await member.remove_roles(unv, reason="User verified")
        if data["verified_role_id"]:
            ver = member.guild.get_role(data["verified_role_id"])
            if ver:
                await member.add_roles(ver, reason="User verified")

    # --- /verify Command ---
    @commands.hybrid_command(name="verify", description="Verify yourself")
    @app_commands.describe(code="Enter the CAPTCHA code shown in the image")
    async def verify_self(self, ctx: commands.Context, code: Optional[str] = None):
        if not isinstance(ctx.author, discord.Member):
            return

        data = self.db.get_guild(ctx.guild.id)
        if not data["verification_enabled"]:
            return await ctx.send("Verification is not enabled on this server.", ephemeral=True)

        if data["verification_channel_id"] and ctx.channel.id != data["verification_channel_id"]:
            return await ctx.send(
                f"Please verify in <#{data['verification_channel_id']}>.", ephemeral=True
            )

        if data["verification_type"] == "button":
            await self.complete_verification(ctx.author)
            return await ctx.send("You have been verified!", ephemeral=True)

        key = (ctx.guild.id, ctx.author.id)
        if code is None:
            chars = string.ascii_letters if data["captcha_sensitive"] else string.ascii_uppercase
            if data["captcha_numbers"]:
                chars += string.digits
            generated = "".join(random.choices(chars, k=data["captcha_length"]))
            expires = time.monotonic() + data["captcha_timeout"]
            self.active_challenges[key] = (generated, expires)

            img_buf = generate_captcha_image(generated, data["captcha_color"], data["captcha_lines"])
            file = discord.File(img_buf, filename="captcha.png")
            await ctx.send(
                content=f"Type `/verify code:<text>` or `z!verify <text>` within `{data['captcha_timeout']}s`:",
                file=file,
                ephemeral=True,
            )
        else:
            challenge = self.active_challenges.get(key)
            if not challenge:
                return await ctx.send("Run `/verify` (or `z!verify`) without a code first to generate your CAPTCHA.", ephemeral=True)
            expected, expires = challenge
            if time.monotonic() > expires:
                del self.active_challenges[key]
                return await ctx.send("Your CAPTCHA timed out. Run `/verify` again.", ephemeral=True)

            valid = (code == expected) if data["captcha_sensitive"] else (code.upper() == expected.upper())
            if not valid:
                return await ctx.send("Incorrect CAPTCHA code. Try again.", ephemeral=True)

            del self.active_challenges[key]
            await self.complete_verification(ctx.author)
            await ctx.send("Verification successful! Access granted.", ephemeral=True)

    # --- /verification Group ---
    @commands.hybrid_group(name="verification", description="Configure server verification")
    async def verification(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send("Subcommands: `enable`, `disable`, `reset`, `type`, `setup_channel`, `setup_role`, `set_verified`, `update_role`, `verify`, `captcha`.")

    @verification.command(name="enable", description="Enable verification on your server")
    async def ver_enable(self, ctx: commands.Context):
        if not self.can_manage_verification(ctx):
            return await ctx.send("Missing verification permission.", ephemeral=True)
        self.db.update_field(ctx.guild.id, "verification_enabled", 1)
        await ctx.send("Server verification **enabled**.")

    @verification.command(name="disable", description="Disable verification on your server without deleting unverified role")
    async def ver_disable(self, ctx: commands.Context):
        if not self.can_manage_verification(ctx):
            return await ctx.send("Missing verification permission.", ephemeral=True)
        self.db.update_field(ctx.guild.id, "verification_enabled", 0)
        await ctx.send("Server verification **disabled**.")

    @verification.command(name="reset", description="Reset verification on your server")
    async def ver_reset(self, ctx: commands.Context):
        if not self.can_manage_verification(ctx):
            return await ctx.send("Missing verification permission.", ephemeral=True)
        self.db.update_field(ctx.guild.id, "verification_enabled", 0)
        self.db.update_field(ctx.guild.id, "verification_channel_id", None)
        self.db.update_field(ctx.guild.id, "unverified_role_id", None)
        self.db.update_field(ctx.guild.id, "verified_role_id", None)
        await ctx.send("Verification configuration has been reset.")

    @verification.command(name="type", description="Set the verification type")
    async def ver_type(self, ctx: commands.Context, mode: Literal["captcha", "button"]):
        if not self.can_manage_verification(ctx):
            return await ctx.send("Missing verification permission.", ephemeral=True)
        self.db.update_field(ctx.guild.id, "verification_type", mode)
        await ctx.send(f"Verification type set to **`{mode}`**.")

    @verification.command(name="setup_channel", description="Setup verification channel on your server")
    async def ver_setup_channel(self, ctx: commands.Context):
        if not self.can_manage_verification(ctx):
            return await ctx.send("Missing verification permission.", ephemeral=True)
        channel = await ctx.guild.create_text_channel("verify")
        self.db.update_field(ctx.guild.id, "verification_channel_id", channel.id)
        await ctx.send(f"Created verification channel {channel.mention}.")

    @verification.command(name="setup_role", description="Setup verification role on your server")
    async def ver_setup_role(self, ctx: commands.Context):
        if not self.can_manage_verification(ctx):
            return await ctx.send("Missing verification permission.", ephemeral=True)
        role = await ctx.guild.create_role(name="Unverified")
        self.db.update_field(ctx.guild.id, "unverified_role_id", role.id)
        await ctx.send(f"Created unverified role {role.mention}. Run `verification update_role` to lock channels.")

    @verification.command(name="set_verified", description="Set a role to give your users when they get verified")
    async def ver_set_verified(self, ctx: commands.Context, role: discord.Role):
        if not self.can_manage_verification(ctx):
            return await ctx.send("Missing verification permission.", ephemeral=True)
        self.db.update_field(ctx.guild.id, "verified_role_id", role.id)
        await ctx.send(f"Verified role set to {role.mention}.")

    @verification.command(name="update_role", description="Update unverified role permissions in text channels")
    async def ver_update_role(self, ctx: commands.Context):
        if not self.can_manage_verification(ctx):
            return await ctx.send("Missing verification permission.", ephemeral=True)
        data = self.db.get_guild(ctx.guild.id)
        unv_id = data["unverified_role_id"]
        ver_ch_id = data["verification_channel_id"]
        if not unv_id:
            return await ctx.send("Run `verification setup_role` first.", ephemeral=True)
        role = ctx.guild.get_role(unv_id)
        await ctx.defer()
        for ch in ctx.guild.channels:
            is_verify_ch = ch.id == ver_ch_id
            await ch.set_permissions(role, view_channel=is_verify_ch, send_messages=is_verify_ch)
        await ctx.send("Updated channel permissions for the Unverified role.")

    @verification.command(name="verify", description="Manually verify someone")
    async def ver_manual(self, ctx: commands.Context, user: discord.Member):
        if not self.can_manage_verification(ctx):
            return await ctx.send("Missing verification permission.", ephemeral=True)
        await self.complete_verification(user)
        await ctx.send(f"Manually verified {user.mention}.")

    # --- /verification captcha Sub-Group ---
    @verification.group(name="captcha", description="Customize verification captcha")
    async def captcha(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send("Subcommands: `color`, `length`, `lines`, `numbers`, `sensitive`, `timeout`.")

    @captcha.command(name="color", description="Changes verification captcha color")
    async def cap_color(self, ctx: commands.Context, hex_color: str):
        if not self.can_manage_verification(ctx):
            return await ctx.send("Missing verification permission.", ephemeral=True)
        if not hex_color.startswith("#") or len(hex_color) != 7:
            return await ctx.send("Provide a valid hex color (e.g. `#5865F2`).", ephemeral=True)
        self.db.update_field(ctx.guild.id, "captcha_color", hex_color)
        await ctx.send(f"CAPTCHA color set to `{hex_color}`.")

    @captcha.command(name="length", description="Changes verification captcha code length")
    async def cap_length(self, ctx: commands.Context, length: app_commands.Range[int, 4, 10]):
        if not self.can_manage_verification(ctx):
            return await ctx.send("Missing verification permission.", ephemeral=True)
        self.db.update_field(ctx.guild.id, "captcha_length", length)
        await ctx.send(f"CAPTCHA length set to `{length}`.")

    @captcha.command(name="lines", description="Changes verification captcha code lines")
    async def cap_lines(self, ctx: commands.Context, lines: app_commands.Range[int, 0, 15]):
        if not self.can_manage_verification(ctx):
            return await ctx.send("Missing verification permission.", ephemeral=True)
        self.db.update_field(ctx.guild.id, "captcha_lines", lines)
        await ctx.send(f"CAPTCHA noise lines set to `{lines}`.")

    @captcha.command(name="numbers", description="Toggle if captcha should contain numbers or not")
    async def cap_numbers(self, ctx: commands.Context, enabled: bool):
        if not self.can_manage_verification(ctx):
            return await ctx.send("Missing verification permission.", ephemeral=True)
        self.db.update_field(ctx.guild.id, "captcha_numbers", int(enabled))
        await ctx.send(f"CAPTCHA numbers set to `{enabled}`.")

    @captcha.command(name="sensitive", description="Toggle if captcha should be case sensitive or not")
    async def cap_sensitive(self, ctx: commands.Context, enabled: bool):
        if not self.can_manage_verification(ctx):
            return await ctx.send("Missing verification permission.", ephemeral=True)
        self.db.update_field(ctx.guild.id, "captcha_sensitive", int(enabled))
        await ctx.send(f"CAPTCHA case-sensitivity set to `{enabled}`.")

    @captcha.command(name="timeout", description="Set captcha timeout in seconds")
    async def cap_timeout(self, ctx: commands.Context, seconds: app_commands.Range[int, 30, 600]):
        if not self.can_manage_verification(ctx):
            return await ctx.send("Missing verification permission.", ephemeral=True)
        self.db.update_field(ctx.guild.id, "captcha_timeout", seconds)
        await ctx.send(f"CAPTCHA timeout set to `{seconds}s`.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Verification(bot))
