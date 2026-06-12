import discord

async def send_message(channel: discord.TextChannel, content: str):
    await channel.send(content)

async def reply_message(message: discord.Message, content: str):
    await message.reply(content)