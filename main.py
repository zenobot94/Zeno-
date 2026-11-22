import os
import discord
from discord.ext import commands
from database import Database

TOKEN = os.getenv("DISCORD_TOKEN", "YOUR_BOT_TOKEN_HERE")

EXTRA_OWNER_IDS = {1443810072265363518}


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

        for ext in ("cogs.antinuke", "cogs.moderation", "cogs.verification"):
            await self.load_extension(ext)
        await self.tree.sync()
        print(f"Synced slash commands for {self.user} | Bot Owners: {self.owner_ids}")

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandNotFound):
            return
        raise error


bot = SecurityBot()

if __name__ == "__main__":
    bot.run(TOKEN)
  
