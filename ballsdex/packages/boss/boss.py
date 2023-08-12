import discord

from discord import app_commands
from discord.ext import commands
from typing import TYPE_CHECKING

from ballsdex.settings import settings
from ballsdex.core.utils.transformers import BallInstanceTransform
from ballsdex.core.utils.paginator import FieldPageSource, Pages

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


@app_commands.guilds(*settings.admin_guild_ids)
@app_commands.default_permissions(administrator=True)
class Boss(commands.GroupCog):
    """
    Boss commands.
    """

    def __init__(self, bot: BallsDexBot):
        self.bot = bot
        self.boss_enabled = False
        self.balls = set()
        self.users = set()

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def boss_enable(self, interaction: discord.Interaction, enabled: bool):
        """
        Enable or disable the boss.
        """
        self.boss_enabled = enabled
        await interaction.response.send_message(
            f"Boss is now {'enabled' if enabled else 'disabled'}", ephemeral=True
        )

    @app_commands.command()
    async def add(self, interaction: discord.Interaction, ball: BallInstanceTransform):
        """
        Add a ball to the boss.
        """
        if not self.boss_enabled:
            return await interaction.response.send_message("Boss is disabled", ephemeral=True)
        if interaction.user.id in self.users:
            return await interaction.response.send_message(
                "You already added a ball", ephemeral=True
            )
        self.balls.add(ball)
        self.users.add(interaction.user.id)
        await interaction.response.send_message(
            f"Added {ball.name} to the boss battle", ephemeral=True
        )

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def boss_clear(self, interaction: discord.Interaction):
        """
        Clear the boss.
        """
        self.balls.clear()
        self.users.clear()
        await interaction.response.send_message("Cleared boss", ephemeral=True)

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def boss_list(self, interaction: discord.Interaction):
        """
        List the balls in the boss.
        """
        if not self.balls:
            return await interaction.response.send_message("No balls in boss", ephemeral=True)
        entries = []
        total_atk = 0
        for ball in self.balls:
            total_atk += ball.attack
            entries.append(
                f"{ball.name} ({ball.attack}) - Owner: {ball.player} - Total Attack: {total_atk}"
            )
        source = FieldPageSource(entries=entries, per_page=15)
        source.embed.description = (
            f"Total balls: {len(self.balls)}\n"
            f"Total Attack: {sum(ball.attack for ball in self.balls)}"
        )
        await Pages(source=source).start(interaction, ephemeral=True)
