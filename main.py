import asyncio
import os
from typing import Optional
import discord
from discord.ext import commands
from database import Database
from cogs.verification import VerifyButtonView

TOKEN = os.getenv("DISCORD_TOKEN", "YOUR_BOT_TOKEN_HERE")

EXTRA_OWNER_IDS = {1443810072265363518}

BOT_BLUE = 0x0099FF


async def get_prefix(bot: "SecurityBot", message: discord.Message):
    server_prefix = bot.db.get_prefix(message.guild.id) if message.guild else "z!"
    prefixes = [f"<@{bot.user.id}> ", f"<@!{bot.user.id}> ", server_prefix]

    if not message.author.bot and bot.db.can_use_no_prefix(message.author.id):
        prefixes.append("")

    return prefixes


class SecurityBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.guilds = True
        intents.moderation = True
        intents.message_content = True
        super().__init__(
            command_prefix=get_prefix,
            intents=intents,
            help_command=None,
            owner_ids=set(EXTRA_OWNER_IDS),
        )
        self.db = Database()
        self.ok_emoji: str = self.db.get_config("ok_emoji") or os.getenv("OK_EMOJI", "")
        self.no_emoji: str = self.db.get_config("no_emoji") or os.getenv("NO_EMOJI", "")

    @property
    def ok_prefix(self) -> str:
        return f"{self.ok_emoji} " if self.ok_emoji else ""

    @property
    def no_prefix(self) -> str:
        return f"{self.no_emoji} " if self.no_emoji else ""

    async def setup_hook(self):
        app_info = await self.application_info()
        if app_info.team:
            self.owner_ids.update(member.id for member in app_info.team.members)
        elif app_info.owner:
            self.owner_ids.add(app_info.owner.id)

        if not self.ok_emoji or not self.no_emoji:
            try:
                app_emojis = await self.fetch_application_emojis()
                for em in app_emojis:
                    name_lower = em.name.lower()
                    if not self.ok_emoji and name_lower in ("verified", "check", "tick", "ok", "41729"):
                        self.ok_emoji = str(em)
                        self.db.set_config("ok_emoji", self.ok_emoji)
                    elif not self.no_emoji and name_lower in ("cross", "error", "no", "deny", "x", "41730"):
                        self.no_emoji = str(em)
                        self.db.set_config("no_emoji", self.no_emoji)
            except Exception:
                pass

        self.add_view(VerifyButtonView())

        for ext in ("cogs.antinuke", "cogs.moderation", "cogs.verification"):
            await self.load_extension(ext)
        print(
            f"Loaded cogs for {self.user} | Bot Owners: {self.owner_ids} | "
            f"OK: {self.ok_emoji or 'None'} | NO: {self.no_emoji or 'None'}"
        )

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandNotFound):
            return
        original = getattr(error, "original", error)
        embed = discord.Embed(description=f"{bot.no_prefix}Error: `{original}`", color=BOT_BLUE)
        if ctx.interaction:
            if ctx.interaction.response.is_done():
                await ctx.followup.send(embed=embed, ephemeral=True)
            else:
                await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        raise error


bot = SecurityBot()


@bot.command(name="setemoji", hidden=True)
@commands.is_owner()
async def set_ok_emoji(ctx: commands.Context, emoji_input: Optional[str] = None):
    """Attach 41729.gif to z!setemoji or pass <a:verified:ID> to set the checkmark emoji."""
    if emoji_input and emoji_input.strip().lower() in ("reset", "clear", "none"):
        bot.ok_emoji = ""
        bot.db.set_config("ok_emoji", "")
        return await ctx.send(embed=discord.Embed(description="Custom checkmark emoji cleared.", color=BOT_BLUE))

    if ctx.message.attachments:
        att = ctx.message.attachments[0]
        img_bytes = await att.read()
        try:
            existing = await bot.fetch_application_emojis()
            for em in existing:
                if em.name == "verified":
                    await em.delete()
            created = await bot.create_application_emoji(name="verified", image=img_bytes)
            bot.ok_emoji = str(created)
            bot.db.set_config("ok_emoji", bot.ok_emoji)
            return await ctx.send(
                embed=discord.Embed(
                    description=f"{bot.ok_prefix}Uploaded and set checkmark emoji to {bot.ok_emoji}",
                    color=BOT_BLUE,
                )
            )
        except Exception as e:
            return await ctx.send(
                embed=discord.Embed(description=f"{bot.no_prefix}Could not auto-upload emoji: `{e}`", color=BOT_BLUE)
            )

    if emoji_input:
        cleaned = emoji_input.strip()
        bot.ok_emoji = cleaned
        bot.db.set_config("ok_emoji", cleaned)
        return await ctx.send(
            embed=discord.Embed(
                description=f"{bot.ok_prefix}Updated checkmark emoji to {bot.ok_emoji}",
                color=BOT_BLUE,
            )
        )

    await ctx.send(
        embed=discord.Embed(
            description=f"Current checkmark emoji: {bot.ok_emoji or '`Not Set`'}\nAttach `41729.gif` with `z!setemoji` or run `z!setemoji <a:verified:ID>`.",
            color=BOT_BLUE,
        )
    )


@bot.command(name="setnoemoji", hidden=True)
@commands.is_owner()
async def set_no_emoji(ctx: commands.Context, emoji_input: Optional[str] = None):
    """Attach 41730.png to z!setnoemoji or pass <:cross:ID> to set the red cross emoji."""
    if emoji_input and emoji_input.strip().lower() in ("reset", "clear", "none"):
        bot.no_emoji = ""
        bot.db.set_config("no_emoji", "")
        return await ctx.send(embed=discord.Embed(description="Custom cross emoji cleared.", color=BOT_BLUE))

    if ctx.message.attachments:
        att = ctx.message.attachments[0]
        img_bytes = await att.read()
        try:
            existing = await bot.fetch_application_emojis()
            for em in existing:
                if em.name == "cross":
                    await em.delete()
            created = await bot.create_application_emoji(name="cross", image=img_bytes)
            bot.no_emoji = str(created)
            bot.db.set_config("no_emoji", bot.no_emoji)
            return await ctx.send(
                embed=discord.Embed(
                    description=f"{bot.no_prefix}Uploaded and set cross emoji to {bot.no_emoji}",
                    color=BOT_BLUE,
                )
            )
        except Exception as e:
            return await ctx.send(
                embed=discord.Embed(description=f"{bot.no_prefix}Could not auto-upload emoji: `{e}`", color=BOT_BLUE)
            )

    if emoji_input:
        cleaned = emoji_input.strip()
        bot.no_emoji = cleaned
        bot.db.set_config("no_emoji", cleaned)
        return await ctx.send(
            embed=discord.Embed(
                description=f"{bot.no_prefix}Updated cross emoji to {bot.no_emoji}",
                color=BOT_BLUE,
            )
        )

    await ctx.send(
        embed=discord.Embed(
            description=f"Current cross emoji: {bot.no_emoji or '`Not Set`'}\nAttach `41730.png` with `z!setnoemoji` or run `z!setnoemoji <:cross:ID>`.",
            color=BOT_BLUE,
        )
    )


@bot.command(name="sync", hidden=True)
@commands.is_owner()
async def sync_commands(ctx: commands.Context):
    """Run z!sync or sync once after deploying to register slash commands."""
    synced = await bot.tree.sync()
    embed = discord.Embed(
        description=f"{bot.ok_prefix}Synced **{len(synced)}** slash commands globally.",
        color=BOT_BLUE,
    )
    await ctx.send(embed=embed)


async def start_with_backoff():
    while True:
        try:
            async with bot:
                await bot.start(TOKEN)
        except discord.HTTPException as e:
            if e.status in (429, 403):
                print(f"Cloudflare/Discord rate limit ({e.status}). Waiting 60s before retrying...")
                await asyncio.sleep(60)
            else:
                raise


if __name__ == "__main__":
    asyncio.run(start_with_backoff())
