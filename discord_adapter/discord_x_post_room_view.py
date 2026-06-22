import discord

class CreateRoomView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="missile_39",
        emoji="🚀",
        style=discord.ButtonStyle.gray,
        custom_id="missile_39_role",
        row=0
    )

    async def missile_39_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.role_process(interaction, button, 1518630870435631155)

    @discord.ui.button(
        label="足立レイ",
        emoji="🧡",
        style=discord.ButtonStyle.gray,
        custom_id="adachi_rei_role",
        row=1
    )

    async def adachi_rei_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.role_process(interaction, button, 1518626034768023714)

    @discord.ui.button(
        label="九十九シオン",
        emoji="💜",
        style=discord.ButtonStyle.gray,
        custom_id="tsukumoshion_role",
        row=2
    )

    async def tsukumoshion_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.role_process(interaction, button, 1518631739868840096)

    async def role_process(self, interaction: discord.Interaction, button: discord.ui.Button, role_id: int):
        await interaction.response.defer(ephemeral=True)

        user = interaction.user
        role = interaction.guild.get_role(role_id)

        if role in user.roles:
            await user.remove_roles(role)
            await interaction.followup.send(f"已移除身份組：{role.name}", ephemeral=True)

        else:
            await user.add_roles(role)
            await interaction.followup.send(f"已新增身份組：{role.name}", ephemeral=True)
