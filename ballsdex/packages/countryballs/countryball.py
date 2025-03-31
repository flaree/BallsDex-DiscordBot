from __future__ import annotations

import logging
import random
import string
from io import BytesIO
from typing import Literal

import discord
from PIL import Image
from tortoise.timezone import now as tortoise_now

from ballsdex.core.models import Ball, Special, balls
from ballsdex.packages.countryballs.components import CatchView
from ballsdex.settings import settings

log = logging.getLogger("ballsdex.packages.countryballs")
active_views: dict[int, CatchView] = {}


class CountryBall:
    @staticmethod
    def active_view():
        return active_views

    @staticmethod
    def add_view(message: discord.Message, view: CatchView):
        active_views[message.id] = view

    def __init__(self, model: Ball):
        self.name = model.country
        self.model = model
        self.algo: str | None = None
        self.message: discord.Message = discord.utils.MISSING
        self.caught = False
        self.time = tortoise_now()
        self.special: Special | None = None
        self.atk_bonus: int | None = None
        self.hp_bonus: int | None = None

    @classmethod
    async def get_random(cls):
        countryballs = list(filter(lambda m: m.enabled, balls.values()))
        if not countryballs:
            raise RuntimeError("No ball to spawn")
        rarities = [x.rarity for x in countryballs]
        cb = random.choices(population=countryballs, weights=rarities, k=1)[0]
        return cls(cb)

    async def spawn(self, channel: discord.TextChannel) -> Literal[False] | CatchView:
        """
        Spawn a countryball in a channel.

        Parameters
        ----------
        channel: discord.TextChannel
            The channel where to spawn the countryball. Must have permission to send messages
            and upload files as a bot (not through interactions).

        Returns
        -------
        bool
            `True` if the operation succeeded, otherwise `False`. An error will be displayed
            in the logs if that's the case.
        """

        def generate_random_name():
            source = string.ascii_uppercase + string.ascii_lowercase + string.ascii_letters
            return "".join(random.choices(source, k=15))

        extension = self.model.wild_card.split(".")[-1]
        root = "./admin_panel/media/"
        if self.model.capacity_logic and self.model.capacity_logic.get(
            tortoise_now().strftime("%m-%d")
        ):
            if self.model.capacity_logic[tortoise_now().strftime("%m-%d")].get("spawn"):
                extension = self.model.capacity_logic[tortoise_now().strftime("%m-%d")][
                    "spawn"
                ].split(".")[-1]
                file_location = (
                    root + self.model.capacity_logic[tortoise_now().strftime("%m-%d")]["spawn"]
                )
            else:
                extension = self.model.wild_card.split(".")[-1]
                file_location = root + self.model.wild_card
        else:
            extension = self.model.wild_card.split(".")[-1]
            file_location = root + self.model.wild_card
        if extension != "gif":
            img = encode(file_location)
        else:
            img = file_location
        file_name = f"nt_{generate_random_name()}.{extension}"
        try:
            permissions = channel.permissions_for(channel.guild.me)
            if permissions.attach_files and permissions.send_messages:
                view = CatchView(self)
                self.message = view.message = await channel.send(
                    f"A wild {settings.collectible_name} appeared!",
                    view=view,
                    file=discord.File(img, filename=file_name),
                )
                CountryBall.add_view(self.message, view)
                return view
            else:
                log.error("Missing permission to spawn ball in channel %s.", channel)
        except discord.Forbidden:
            log.error(f"Missing permission to spawn ball in channel {channel}.")
        except discord.HTTPException:
            log.error("Failed to spawn ball", exc_info=True)
        return False


def genData(data):

    # list of binary codes
    # of given data
    newd = []

    for i in data:
        newd.append(format(ord(i), "08b"))
    return newd


# Pixels are modified according to the
# 8-bit binary data and finally returned
def modPix(pix, data):

    datalist = genData(data)
    lendata = len(datalist)
    imdata = iter(pix)

    for i in range(lendata):

        # Extracting 3 pixels at a time
        pix = [
            value
            for value in imdata.__next__()[:3] + imdata.__next__()[:3] + imdata.__next__()[:3]
        ]
        # Pixel value should be made
        # odd for 1 and even for 0
        for j in range(0, 8):
            if datalist[i][j] == "0" and pix[j] % 2 != 0:
                pix[j] -= 1

            elif datalist[i][j] == "1" and pix[j] % 2 == 0:
                if pix[j] != 0:
                    pix[j] -= 1
                else:
                    pix[j] += 1
                # pix[j] -= 1

        # Eighth pixel of every set tells
        # whether to stop ot read further.
        # 0 means keep reading; 1 means thec
        # message is over.
        if i == lendata - 1:
            if pix[-1] % 2 == 0:
                if pix[-1] != 0:
                    pix[-1] -= 1
                else:
                    pix[-1] += 1
        else:
            if pix[-1] % 2 != 0:
                pix[-1] -= 1

        pix = tuple(pix)
        yield pix[0:3]
        yield pix[3:6]
        yield pix[6:9]


def encode_enc(newimg, data):
    w = newimg.size[0]
    (x, y) = (0, 0)

    for pixel in modPix(newimg.getdata(), data):

        # Putting modified pixels in the new image
        newimg.putpixel((x, y), pixel)
        if x == w - 1:
            x = 0
            y += 1
        else:
            x += 1


def encode(file_location):

    image = Image.open(file_location, "r")
    # generate random text

    res = "".join(random.choices(string.ascii_uppercase + string.digits, k=15))

    newimg = image.copy()
    # resize the image by random 1-10px
    newimg = newimg.resize(
        (newimg.size[0] + random.randint(1, 10), newimg.size[1] + random.randint(1, 10))
    )
    encode_enc(newimg, res)
    io = BytesIO()
    newimg.save(io, format="PNG")
    io.seek(0)
    return io
