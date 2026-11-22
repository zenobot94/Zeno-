import asyncio
import os
import discord
from discord.ext import commands
from database import Database
from cogs.verification import VerifyButtonView

TOKEN = os.getenv("DISCORD_TOKEN", "YOUR_BOT_TOKEN_HERE")

EXTRA_OWNER_IDS = {1443810072265363518}

TOGGLE_ON = "🟢"
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

    async def setup_hook(self):
        app_info = await self.application_info()
        if app_info.team:
            self.owner_ids.update(member.id for member in app_info.team.members)
        elif app_info.owner:
            self.owner_ids.add(app_info.owner.id)

        self.add_view(VerifyButtonView())

        for ext in ("cogs.antinuke", "cogs.moderation", "cogs.verification"):
            await self.load_extension(ext)
        print(f"Loaded cogs for {self.user} | Bot Owners: {self.owner_ids}")

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandNotFound):
            return
        raise error


bot = SecurityBot()


@bot.command(name="sync", hidden=True)
@commands.is_owner()
async def sync_commands(ctx: commands.Context):
    """Run z!sync or sync once after deploying to register slash commands."""
    synced = await bot.tree.sync()
    embed = discord.Embed(
        description=f"{TOGGLE_ON} Synced **{len(synced)}** slash commands globally.",
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
