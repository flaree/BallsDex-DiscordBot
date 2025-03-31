import importlib
import logging
import math
import random
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, cast

import discord
from discord.ext import commands
from google.cloud.speech_v2 import (
    ExplicitDecodingConfig,
    RecognitionConfig,
    RecognitionFeatures,
    RecognizeRequest,
    SpeechAdaptation,
    SpeechAsyncClient,
)
from tortoise.exceptions import DoesNotExist
from tortoise.timezone import get_default_timezone
from tortoise.timezone import now as datetime_now

from ballsdex.core.metrics import caught_balls
from ballsdex.core.models import BallInstance, GuildConfig, Player, specials
from ballsdex.packages.countryballs.components import CatchView
from ballsdex.packages.countryballs.countryball import CountryBall
from ballsdex.packages.countryballs.spawn import BaseSpawnManager
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot
    from ballsdex.packages.players.cog import Player as PlayerCog

log = logging.getLogger("ballsdex.packages.countryballs")


PROJECT_ID = ""
PHRASE_SET = "countryballs"
speech_client = SpeechAsyncClient()
speech_config = RecognitionConfig(
    # Encoding details: https://discord.com/developers/docs/resources/message#voice-messages
    explicit_decoding_config=ExplicitDecodingConfig(
        encoding=ExplicitDecodingConfig.AudioEncoding.OGG_OPUS,
        sample_rate_hertz=48000,
        audio_channel_count=1,
    ),
    language_codes=["en-US"],
    model="short",
    features=RecognitionFeatures(profanity_filter=True, max_alternatives=1),
    adaptation=SpeechAdaptation(
        phrase_sets=(
            SpeechAdaptation.AdaptationPhraseSet(
                phrase_set=f"projects/{PROJECT_ID}/locations/global/phraseSets/{PHRASE_SET}"
            ),
        )
    ),
)


class CountryBallsSpawner(commands.Cog):
    spawn_manager: BaseSpawnManager

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
        self.cache: dict[int, int] = {}
        self.countryball_cls = CountryBall

        module_path, class_name = settings.spawn_manager.rsplit(".", 1)
        module = importlib.import_module(module_path)
        # force a reload, otherwise cog reloads won't reflect to this class
        importlib.reload(module)
        spawn_manager = getattr(module, class_name)
        self.spawn_manager = spawn_manager(bot)

    async def load_cache(self):
        i = 0
        async for config in GuildConfig.filter(enabled=True, spawn_channel__isnull=False).only(
            "guild_id", "spawn_channel"
        ):
            self.cache[config.guild_id] = config.spawn_channel
            i += 1
        grammar = "" if i == 1 else "s"
        log.info(f"Loaded {i} guild{grammar} in cache.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.webhook_id is not None:
            return
        guild = message.guild
        if not guild:
            return
        if guild.id not in self.cache:
            return
        if guild.id in self.bot.blacklist_guild:
            return

        self.bot.loop.create_task(self.check_voice_message(message))

        result = await self.spawn_manager.handle_message(message)
        if result is False:
            return

        if isinstance(result, tuple):
            result, algo = result
        else:
            algo = settings.spawn_manager

        channel = guild.get_channel(self.cache[guild.id])
        if not channel:
            log.warning(f"Lost channel {self.cache[guild.id]} for guild {guild.name}.")
            del self.cache[guild.id]
            return
        ball = await CountryBall.get_random()
        ball.algo = algo
        await ball.spawn(cast(discord.TextChannel, channel))

    @commands.Cog.listener()
    async def on_ballsdex_settings_change(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel | None = None,
        enabled: bool | None = None,
    ):
        if guild.id not in self.cache:
            if enabled is False:
                return  # do nothing
            if channel:
                self.cache[guild.id] = channel.id
            else:
                try:
                    config = await GuildConfig.get(guild_id=guild.id)
                except DoesNotExist:
                    return
                else:
                    self.cache[guild.id] = config.spawn_channel
        else:
            if enabled is False:
                del self.cache[guild.id]
            elif channel:
                self.cache[guild.id] = channel.id

    async def catch_ball(
        self, user: discord.Member, ball: CountryBall
    ) -> tuple[BallInstance, bool]:
        player, created = await Player.get_or_create(discord_id=user.id)

        # stat may vary by +/- 20% of base stat
        bonus_attack = (
            ball.atk_bonus
            if ball.atk_bonus is not None
            else random.randint(-settings.max_attack_bonus, settings.max_attack_bonus)
        )
        bonus_health = (
            ball.hp_bonus
            if ball.hp_bonus is not None
            else random.randint(-settings.max_health_bonus, settings.max_health_bonus)
        )

        # check if we can spawn cards with a special background
        special = ball.special
        population = [
            x
            for x in specials.values()
            # handle null start/end dates with infinity times
            if (x.start_date or datetime.min.replace(tzinfo=get_default_timezone()))
            <= datetime_now()
            <= (x.end_date or datetime.max.replace(tzinfo=get_default_timezone()))
        ]
        if not special and population:
            # Here we try to determine what should be the chance of having a common card
            # since the rarity field is a value between 0 and 1, 1 being no common
            # and 0 only common, we get the remaining value by doing (1-rarity)
            # We then sum each value for each current event, and we should get an algorithm
            # that kinda makes sense.
            common_weight = sum(1 - x.rarity for x in population)

            weights = [x.rarity for x in population] + [common_weight]
            # None is added representing the common countryball
            special = random.choices(population=population + [None], weights=weights, k=1)[0]

        is_new = not await BallInstance.filter(player=player, ball=ball.model).exists()
        ballinst = await BallInstance.create(
            ball=ball.model,
            player=player,
            special=special,
            attack_bonus=bonus_attack,
            health_bonus=bonus_health,
            server_id=user.guild.id,
            spawned_time=ball.time,
        )
        if user.id in self.bot.catch_log:
            log.info(
                f"{user} caught {settings.collectible_name}" f" {ball.model}, {special=}",
            )
        else:
            log.debug(
                f"{user} caught {settings.collectible_name}" f" {ball.model}, {special=}",
            )
        if user.guild.member_count:
            caught_balls.labels(
                country=ball.model.country,
                special=special,
                # observe the size of the server, rounded to the nearest power of 10
                guild_size=10 ** math.ceil(math.log(max(user.guild.member_count - 1, 1), 10)),
                spawn_algo=ball.algo,
            ).inc()
        return ballinst, is_new

    async def try_catch_ball(
        self, message: discord.Message, view: CatchView, msg: str, confidence: float
    ):
        player, _ = await Player.get_or_create(discord_id=message.author.id)
        if view.ball.caught:
            await message.reply(
                "I was caught already!",
                allowed_mentions=discord.AllowedMentions(users=player.can_be_mentioned),
            )
            return
        if view.ball.model.catch_names:
            possible_names = (view.ball.name.lower(), *view.ball.model.catch_names.split(";"))
        else:
            possible_names = (view.ball.name.lower(),)
        if view.ball.model.translations:
            possible_names += tuple(x.lower() for x in view.ball.model.translations.split(";"))
        cname = msg.lower().strip()
        # Remove fancy unicode characters like ’ to replace to '
        cname = cname.replace("\u2019", "'")
        cname = cname.replace("\u2018", "'")
        cname = cname.replace("\u201c", '"')
        cname = cname.replace("\u201d", '"')
        # There are other "fancy" quotes as well but these are most common
        if cname in possible_names:
            view.ball.caught = True
            ball, has_caught_before = await self.catch_ball(
                cast(discord.Member, message.author), view.ball
            )

            special = ""
            if ball.specialcard and ball.specialcard.catch_phrase:
                special += f"*{ball.specialcard.catch_phrase}*\n"
            if has_caught_before:
                special += (
                    f"This is a **new {settings.collectible_name}** "
                    "that has been added to your completion!"
                )
            await message.reply(
                f"You caught **{view.ball.name}!** "
                f"`(#{ball.pk:0X}, {ball.attack_bonus:+}%/{ball.health_bonus:+}%)`\n\n"
                f"{special}",
                allowed_mentions=discord.AllowedMentions(users=player.can_be_mentioned),
            )
            view.catch_button.disabled = True
            await view.message.edit(view=view)
        else:
            await message.reply(
                f"Wrong name! You tried: {msg}",
                allowed_mentions=discord.AllowedMentions(
                    replied_user=player.can_be_mentioned, everyone=False, roles=False
                ),
            )

    async def _check_voice_message(self, message: discord.Message):
        if not message.attachments or not message.guild:
            return
        if message.author.id in self.bot.blacklist:
            return
        # if message.channel.id != self.cache.get(message.guild.id):
        #    # not a message in the spawn channel
        #    return
        attachment = message.attachments[0]
        if message.flags.voice is False or not attachment.duration:
            # not a voice message
            return
        view = (
            CountryBall.active_view().get(message.reference.message_id or 0)
            if message.reference
            else None
        )
        if not view:
            await message.reply(
                "Were you trying to catch a ball? You need to reply to the spawned message."
            )
            return
        if view.ball.caught:
            await message.reply("This ball was already caught")
            return
        if attachment.duration > 4.5:
            await message.reply("This message is too long for me.")
            return
        player_cog = cast("PlayerCog | None", self.bot.get_cog("Player"))
        consent_command = (
            player_cog.consent.extras.get("mention", None) if player_cog else None
        ) or "`/player consent`"
        player, _ = await Player.get_or_create(discord_id=message.author.id)
        consent = player.extra_data.get("google-api-consent")
        if consent is None:
            await message.reply(
                "You have not consented to the new privacy policy to use this feature. "
                f"Please use {consent_command} first."
            )
            return
        if consent is False:
            await message.reply(
                "You have revoked your consent to the usage of your audio messages. Please use "
                f"{consent_command} if you want to revert this."
            )
            return

        request = RecognizeRequest(
            recognizer=f"projects/{PROJECT_ID}/locations/global/recognizers/_",
            config=speech_config,
            content=await attachment.read(),
        )
        response = await speech_client.recognize(request=request)
        log.info(f"Received response: {response}")
        await self.bot.redis.incrbyfloat(
            "gcp-stt-seconds",
            cast(timedelta, response.metadata.total_billed_duration).total_seconds(),
        )
        if not response.results:
            await message.reply("Can't hear anything you :(")
            log.info("Couldn't detect anything")
            return
        result = response.results[0].alternatives[0]
        await self.try_catch_ball(message, view, result.transcript, result.confidence)

    async def check_voice_message(self, message: discord.Message):
        try:
            await self._check_voice_message(message)
        except Exception:
            log.error(
                f"Error while performing speech recognition ({message.id=} {message.author=})",
                exc_info=True,
            )
            await message.reply(
                "An error occured while performing speech recognition. Contact support if "
                "this persists."
            )
