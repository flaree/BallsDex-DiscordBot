import logging
from collections import defaultdict, namedtuple
from typing import TYPE_CHECKING, cast

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button
from tortoise.exceptions import DoesNotExist

from ballsdex.core.models import Ball, GuildConfig
from ballsdex.core.utils.logging import log_action
from ballsdex.core.utils.paginator import FieldPageSource, Pages, TextPageSource
from ballsdex.settings import settings

from .balls import Balls as BallsGroup
from .balls import save_file
from .blacklist import Blacklist as BlacklistGroup
from .blacklist import BlacklistGuild as BlacklistGuildGroup
from .history import History as HistoryGroup
from .info import Info as InfoGroup
from .logs import Logs as LogsGroup

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot
    from ballsdex.packages.countryballs.cog import CountryBallsSpawner

log = logging.getLogger(__name__)


@app_commands.guilds(*settings.admin_guild_ids)
@app_commands.default_permissions(administrator=True)
class Admin(commands.GroupCog):
    """
    Bot admin commands.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

        assert self.__cog_app_commands_group__
        self.__cog_app_commands_group__.add_command(
            BallsGroup(name=settings.players_group_cog_name)
        )
        self.__cog_app_commands_group__.add_command(BlacklistGroup())
        self.__cog_app_commands_group__.add_command(BlacklistGuildGroup())
        self.__cog_app_commands_group__.add_command(HistoryGroup())
        self.__cog_app_commands_group__.add_command(LogsGroup())
        self.__cog_app_commands_group__.add_command(InfoGroup())
        self.ctx_menu = app_commands.ContextMenu(name="Update Art", callback=self.update_art)
        self.bot.tree.add_command(self.ctx_menu)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.ctx_menu.name)

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    async def status(
        self,
        interaction: discord.Interaction,
        status: discord.Status | None = None,
        name: str | None = None,
        state: str | None = None,
        activity_type: discord.ActivityType | None = None,
    ):
        """
        Change the status of the bot. Provide at least status or text.

        Parameters
        ----------
        status: discord.Status
            The status you want to set
        name: str
            Title of the activity, if not custom
        state: str
            Custom status or subtitle of the activity
        activity_type: discord.ActivityType
            The type of activity
        """
        if not status and not name and not state:
            await interaction.response.send_message(
                "You must provide at least `status`, `name` or `state`.", ephemeral=True
            )
            return

        activity: discord.Activity | None = None
        status = status or discord.Status.online
        activity_type = activity_type or discord.ActivityType.custom

        if activity_type == discord.ActivityType.custom and name and not state:
            await interaction.response.send_message(
                "You must provide `state` for custom activities. `name` is unused.", ephemeral=True
            )
            return
        if activity_type != discord.ActivityType.custom and not name:
            await interaction.response.send_message(
                "You must provide `name` for pre-defined activities.", ephemeral=True
            )
            return
        if name or state:
            activity = discord.Activity(name=name or state, state=state, type=activity_type)
        await self.bot.change_presence(status=status, activity=activity)
        await interaction.response.send_message("Status updated.", ephemeral=True)

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    async def rarity(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        chunked: bool = True,
        include_disabled: bool = False,
    ):
        """
        Generate a list of countryballs ranked by rarity.

        Parameters
        ----------
        chunked: bool
            Group together countryballs with the same rarity.
        include_disabled: bool
            Include the countryballs that are disabled or with a rarity of 0.
        """
        text = ""
        balls_queryset = Ball.all().order_by("rarity")
        if not include_disabled:
            balls_queryset = balls_queryset.filter(rarity__gt=0, enabled=True)
        sorted_balls = await balls_queryset

        if chunked:
            indexes: dict[float, list[Ball]] = defaultdict(list)
            for ball in sorted_balls:
                indexes[ball.rarity].append(ball)
            i = 1
            for chunk in indexes.values():
                for ball in chunk:
                    text += f"{i}. {ball.country}\n"
                i += len(chunk)
        else:
            for i, ball in enumerate(sorted_balls, start=1):
                text += f"{i}. {ball.country}\n"

        source = TextPageSource(text, prefix="```md\n", suffix="```")
        pages = Pages(source=source, interaction=interaction, compact=True)
        pages.remove_item(pages.stop_pages)
        await pages.start(ephemeral=True)

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    async def cooldown(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        guild_id: str | None = None,
    ):
        """
        Show the details of the spawn cooldown system for the given server

        Parameters
        ----------
        guild_id: int | None
            ID of the server you want to inspect. If not given, inspect the current server.
        """
        if guild_id:
            try:
                if cog := self.bot.get_cog("IPC"):
                    guild = await cog.handler("get_guild", 1, {"guild_id": int(guild_id)})
                    Guild = namedtuple("Guild", "id name member_count")
                    if not guild:
                        await interaction.response.send_message(
                            "The given guild could not be found.", ephemeral=True
                        )
                        return
                    guild = Guild(*guild[0])
                else:
                    guild = self.bot.get_guild(int(guild_id))
            except ValueError:
                await interaction.response.send_message(
                    "Invalid guild ID. Please make sure it's a number.", ephemeral=True
                )
                return
        else:
            guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "The given guild could not be found.", ephemeral=True
            )
            return

        spawn_manager = cast(
            "CountryBallsSpawner", self.bot.get_cog("CountryBallsSpawner")
        ).spawn_manager
        await spawn_manager.admin_explain(interaction, guild)

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def guilds(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        user: discord.User,
    ):
        """
        Shows the guilds shared with the specified user. Provide either user or user_id.

        Parameters
        ----------
        user: discord.User
            The user you want to check, if available in the current server.
        """
        if cog := self.bot.get_cog("IPC"):
            guild_results = await cog.handler(
                "guilds", self.bot.cluster_count, {"user_id": user.id}
            )
            # guild_rests is a list of lists, join them into one list
            guilds = []
            Guild = namedtuple("Guild", "id name member_count")
            for result in guild_results:
                guilds.extend([Guild(*x) for x in result])
        else:
            if self.bot.intents.members:
                guilds = user.mutual_guilds
            else:
                guilds = [x for x in self.bot.guilds if x.owner_id == user.id]

        if not guilds:
            if self.bot.intents.members:
                await interaction.response.send_message(
                    f"The user does not own any server with {settings.bot_name}.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"The user does not own any server with {settings.bot_name}.\n"
                    ":warning: *The bot cannot be aware of the member's presence in servers, "
                    "it is only aware of server ownerships.*",
                    ephemeral=True,
                )
            return

        entries: list[tuple[str, str]] = []
        for guild in guilds:
            if config := await GuildConfig.get_or_none(guild_id=guild.id):
                spawn_enabled = config.enabled and config.guild_id
            else:
                spawn_enabled = False

            field_name = f"`{guild.id}`"
            field_value = ""

            # highlight suspicious server names
            if any(x in guild.name.lower() for x in ("farm", "grind", "spam")):
                field_value += f"- :warning: **{guild.name}**\n"
            else:
                field_value += f"- {guild.name}\n"

            # highlight low member count
            if guild.member_count <= 3:  # type: ignore
                field_value += f"- :warning: **{guild.member_count} members**\n"
            else:
                field_value += f"- {guild.member_count} members\n"

            # highlight if spawning is enabled
            if spawn_enabled:
                field_value += "- :warning: **Spawn is enabled**"
            else:
                field_value += "- Spawn is disabled"

            entries.append((field_name, field_value))

        source = FieldPageSource(entries, per_page=25, inline=True)
        source.embed.set_author(name=f"{user} ({user.id})", icon_url=user.display_avatar.url)

        if len(guilds) > 1:
            source.embed.title = f"{len(guilds)} servers shared"
        else:
            source.embed.title = "1 server shared"

        if not self.bot.intents.members:
            source.embed.set_footer(
                text="\N{WARNING SIGN} The bot cannot be aware of the member's "
                "presence in servers, it is only aware of server ownerships."
            )

        pages = Pages(source=source, interaction=interaction, compact=True)
        pages.add_item(
            Button(
                style=discord.ButtonStyle.link,
                label="View profile",
                url=f"discord://-/users/{user.id}",
                emoji="\N{LEFT-POINTING MAGNIFYING GLASS}",
            )
        )
        await pages.start(ephemeral=True)

    @app_commands.guilds(*settings.admin_guild_ids)
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    async def update_art(self, interaction: discord.Interaction, message: discord.Message):
        """
        Update the artwork of a countryball.
        """
        await interaction.response.defer(ephemeral=True, thinking=True)
        if message.channel.parent_id == 1280116466431299664:
            submission_type = "card"
        elif message.channel.parent_id == 1280151904910966895:
            submission_type = "spawn"
        else:
            await interaction.followup.send(
                "This command must be invoked in a spawn or card submission channel.",
                ephemeral=True,
            )
            return
        try:
            # get message thread then thrad title
            thread = await interaction.guild.fetch_channel(message.channel.id)
            ball_name = thread.name

            ball = await Ball.get(country=ball_name)
        except DoesNotExist:
            await interaction.followup.send(
                "The message you replied to is not a countryball.", ephemeral=True
            )
            return
        if not message.attachments:
            await interaction.followup.send(
                "You must attach an image to the message.", ephemeral=True
            )
            return
        try:
            path = await save_file(message.attachments[0])
        except Exception as e:
            log.exception("Failed saving file when updating countryball artwork", exc_info=True)
            await interaction.followup.send(
                f"Failed saving the attached file: {message.attachments[0].url}.\n"
                f"Partial error: {', '.join(str(x) for x in e.args)}\n"
                "The full error is in the bot logs."
            )
            return
        _credits = ball.credits.split(",")
        if len(_credits) != 2:
            await interaction.followup.send(
                "The credits for this countryball are not formatted correctly. Please fix it on the panel first",
                ephemeral=True,
            )
            return
        if submission_type == "card":
            ball.collection_card = "/" + str(path)
            ball.credits = f"{_credits[0]}, {message.author.name} (card)"
        else:
            ball.wild_card = "/" + str(path)
            ball.credits = f"{message.author.name} (spawn), {_credits[1]}"
        await ball.save()
        cog = self.bot.get_cog("IPC")
        if cog:
            await cog.handler("reload_cache", 0, {})
        if thread.starter_message is None:
            thread_starter = await thread.fetch_message(thread.id)
        else:
            thread_starter = thread.starter_message

        file = discord.File(path)
        await thread_starter.edit(
            attachments=[file],
        )
        await message.add_reaction("✅")
        role = interaction.guild.get_role(1060136184384335903)
        member = await interaction.guild.fetch_member(message.author.id)
        if role and member:
            await member.add_roles(role)
        await interaction.followup.send(f"Artwork for {ball} updated.", ephemeral=True)
        try:
            await member.send(
                f"Your artwork for {ball_name} {submission_type} has been accepted, thank you for your submission!"
            )
        except discord.Forbidden:
            pass
        await log_action(
            f"{interaction.user} updated artwork for {ball} ({ball.pk}) {submission_type}.",
            self.bot,
        )
