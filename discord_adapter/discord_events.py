import discord

async def send_message(channel: discord.TextChannel, content: str):
    await channel.send(content)

async def reply_message(message: discord.Message, content: str):
    await message.reply(content)

async def add_reaction(message: discord.Message, emoji: str):
    await message.add_reaction(emoji)

async def remove_reaction(message: discord.Message, emoji: str, member: discord.Member):
    await message.remove_reaction(emoji, member)