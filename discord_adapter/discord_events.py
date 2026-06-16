import discord
from discord import DiscordException

async def send_message(channel: discord.TextChannel, content: str):
    try:
        await channel.send(content)

    except DiscordException:
        pass

async def reply_message(message: discord.Message, content: str):
    try:
        await message.reply(content)

    except DiscordException:
        pass

async def add_reaction(message: discord.Message, emoji: str):
    try:
        await message.add_reaction(emoji)

    except DiscordException:
        pass

async def remove_reaction(message: discord.Message, emoji: str, member: discord.Member):
    try:
        await message.remove_reaction(emoji, member)

    except DiscordException:
        pass