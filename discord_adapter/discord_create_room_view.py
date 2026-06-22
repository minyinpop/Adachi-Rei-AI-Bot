import data.discord_repository as adachi_rei_db
import discord_events
import discord
import json

from pathlib import Path

class CreateRoomView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="創建私人聊天室",
        emoji="🧋",
        style=discord.ButtonStyle.green,
        custom_id="create_room",
        row=0
    )

    async def create_room(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        user = interaction.user

        with open(Path(__file__).parent/"settings/discord_configs.json", "r", encoding="utf-8") as f:
            discord_configs = json.load(f)

        for content in discord_configs["private_ollama_chat_channel_id"]:
            if user.id == content["user_id"]:
                await interaction.followup.send("您已經擁有私人聊天頻道了！", ephemeral=True)
                return

        category = discord.utils.get(
            interaction.guild.categories,
            id=1511963644844576788
        )

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False
            ),
            interaction.guild.me: discord.PermissionOverwrite(
                view_channel=True,
                manage_channels=True
            ),
            user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True
            )
        }

        channel = await interaction.guild.create_text_channel(
            name=f"【與レイ聊地】{interaction.user.name}",
            category=category,
            overwrites=overwrites
        )

        discord_configs["private_ollama_chat_channel_id"].append(
            {
                "user_id": user.id,
                "channel_id": channel.id
            }
        )

        with open(Path(__file__).parent/"settings/discord_configs.json", "w", encoding="utf-8") as f:
            json.dump(
                discord_configs,
                f,
                ensure_ascii=False,
                indent=4
            )

        with open(Path(__file__).parent.parent / "ai_adapter/ollama_adapter/ollama_configs.json", "r",
                  encoding="utf-8") as f:
            ollama_configs = json.load(f)

        await discord_events.send_message(
            channel=channel,
            content=f"【系統訊息】足立レイ - 啟動\n【回應模型】{ollama_configs['response_model']}",
        )

        await interaction.followup.send("頻道創建完畢！", ephemeral=True)

    @discord.ui.button(
        label="刪除私人聊天室",
        emoji="☕",
        style=discord.ButtonStyle.red,
        custom_id="delete_room",
        row=1
    )

    async def delete_room(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        user = interaction.user

        with open(Path(__file__).parent/"settings/discord_configs.json", "r", encoding="utf-8") as f:
            discord_configs = json.load(f)

        for content in discord_configs["private_ollama_chat_channel_id"]:
            if user.id != content["user_id"]:
                continue

            channel = guild.get_channel(content["channel_id"])

            if channel:
                try:
                    await channel.delete()

                except discord.DiscordException as e:
                    print(f"【❗】嘗試刪除 Discord 頻道 {guild.get_channel(content['channel_id'])} 時發生錯誤：{e}")

                finally:
                    # === 刪除記憶 ===
                    adachi_rei_db.delete_short_memory(channel.id)
                    # ===

                    # 刪除該頻道的資料
                    discord_configs["private_ollama_chat_channel_id"].remove(content)

                    with open(Path(__file__).parent/"settings/discord_configs.json", "w", encoding="utf-8") as f:
                        json.dump(
                            discord_configs,
                            f,
                            ensure_ascii=False,
                            indent=4
                        )
                    # ===

                    await interaction.followup.send("頻道刪除完畢！", ephemeral=True)

                return

        await interaction.followup.send("您還沒有創建私人聊天頻道！", ephemeral=True)