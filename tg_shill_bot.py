# stdlib
import asyncio
import functools
import math
import random
import sys
from datetime import datetime
from pathlib import Path

# custom
import asyncstdlib
import jsonschema
import yaml
from telethon import TelegramClient, functions
from telethon.errors.rpcerrorlist import FloodWaitError, SlowModeWaitError


def log(message):
    now = datetime.now()
    print("[" + now.strftime("%H:%M:%S.%f")[:-3] + "] " + message)


def random_thank_you():
    thank_yous = [
        "Cheers",
        "Thank you",
        "Thank you so much",
        "Thanks",
        "Thanks a bunch",
        "Thanks a million",
        "Ta",
        "Tak",
        "Dank u",
        "Kiitos",
        "Merci",
        "Merci beaucoup",
        "Danke",
        "Danke schön",
        "Danke vielmals",
        "Mahalo",
        "Grazie",
        "Arigato",
        "Obrigado",
        "Gracias",
        "Xie xie",
        "Shukran",
        "Hvala",
        "Efharisto",
        "Dhanyavaad",
        "Spasiba",
        "Salamat",
        "Khob khun",
    ]
    return thank_yous[random.randrange(len(thank_yous))]


def channels_to_raid():
    settings = load_settings()
    return settings["raid"].keys()


@functools.lru_cache()
def recommended_splay():
    # all of this assumes TG rate limit is 20 API calls per 1 minute
    segment_time = 72  # 120% of 60 seconds
    max_channels_per_segment = 20  # max calls per segment
    channels = len(channels_to_raid())
    segments = math.ceil(channels / max_channels_per_segment)
    total_segment_time = segments * segment_time
    default_splay = math.ceil(segment_time / max_channels_per_segment)
    calculated_splay = math.ceil(total_segment_time / channels)
    return default_splay if calculated_splay > default_splay else calculated_splay


@functools.lru_cache()
def splay_map():
    count = 1
    result = {}
    for channel in channels_to_raid():
        result[channel] = count * recommended_splay()
        count += 1
    return result


@functools.lru_cache()
def splay(channel):
    channel_splay = splay_map()
    return channel_splay[channel]


@asyncstdlib.lru_cache()
async def get_entity(channel):
    return await CLIENT.get_entity(channel)


def channel_to_raid(channel):
    settings = load_settings()
    return settings["raid"][channel]


def channel_message(channel):
    settings = load_settings()
    messages = settings["messages"]
    message_type = channel_to_raid(channel)["message_type"]
    return messages[message_type]


def channel_wait_interval(channel):
    return channel_to_raid(channel).get("wait_interval", None)


def channel_increase_wait_interval(channel):
    return channel_to_raid(channel).get("increase_wait_interval", None)


def channel_image(channel):
    return channel_to_raid(channel).get("image", None)


def channel_map(channel):
    return {
        "name": channel,
        "splay": splay(channel),
        "wait_interval": channel_wait_interval(channel),
        "increase_wait_interval": channel_increase_wait_interval(channel),
        "message": channel_message(channel),
        "image": channel_image(channel),
        "count": 0,
    }


def increment_count(channel):
    channel["count"] += 1
    return channel


async def handle_floodwaiterror(error, channel):
    log(
        "FloodWaitError invoked while sending a message;"
        + f" Forcing {error.seconds} second wait interval for {channel['name']}"
    )
    await asyncio.sleep(error.seconds)


def handle_slowmodewaiterror(error, channel):
    log(
        "SlowModeWaitError invoked while sending a message;"
        + f" Dynamically updating {channel['name']}'s calculated wait interval"
    )
    channel["calculated_wait_interval"] = error.seconds + 10
    return channel


def handle_unknownerror(error, channel):
    message = (
        "Unknown error invoked while sending a message; "
        + f" Abandoning sending messages to {channel['name']}"
    )
    if hasattr(error, "message"):
        message = message + f"\n{error.message}"
    log(message)
    channel["loop"] = False
    return channel


def image_exists(channel):
    result = False
    if channel["image"]:
        path = Path(channel["image"])
        if path.is_file():
            result = True
        else:
            log(
                f">> Unable to locate {channel['name']}'s configured image {channel['image']};"
                + " Sending message without image"
            )
    return result


async def dispatch_message(channel):
    new_message = channel["message"] + "\n" + random_thank_you() + "!"
    entity = await get_entity(channel["name"])
    if image_exists(channel):
        await CLIENT.send_message(entity, new_message, file=channel["image"])
    else:
        await CLIENT.send_message(entity, new_message)


async def send_message(channel):
    channel = increment_count(channel)
    log(f"Sending message to {channel['name']} (#{channel['count']})")
    try:
        await dispatch_message(channel)
    except FloodWaitError as fwe:
        await handle_floodwaiterror(fwe, channel)
    except SlowModeWaitError as smwe:
        channel = handle_slowmodewaiterror(smwe, channel)
    except Exception as e:
        channel = handle_unknownerror(e, channel)
    return channel


async def send_single_message(channel):
    log(f"Raiding {channel['name']} once")
    await send_message(channel)


def calculate_wait_interval(channel):
    calculated_wait_interval = channel["wait_interval"] + channel["splay"]
    channel["calculated_wait_interval"] = calculated_wait_interval
    return channel


def recalculate_wait_interval(channel):
    if channel["increase_wait_interval"]:
        channel["calculated_wait_interval"] += channel["increase_wait_interval"]
        log(
            f">> Recalculated {channel['name']} wait interval to"
            + f" {channel['calculated_wait_interval']} seconds"
        )
    return channel


async def send_looped_message(channel):
    channel = calculate_wait_interval(channel)
    channel["loop"] = True
    log(
        f"Raiding {channel['name']} every {channel['calculated_wait_interval']} seconds"
    )
    while channel["loop"]:
        channel = await send_message(channel)
        channel = recalculate_wait_interval(channel)
        await asyncio.sleep(channel["calculated_wait_interval"])


def message_once(channel):
    return not bool(channel["wait_interval"])


async def raid(channel):
    await asyncio.sleep(channel["splay"])

    if message_once(channel):
        await send_single_message(channel)
    else:
        await send_looped_message(channel)


def handle_connectionerror(error, channel):
    message = (
        "Unknown error invoked while connecting to a channel;"
        + f" Abandoning sending messages to {channel['name']}"
    )
    if hasattr(error, "message"):
        message = message + f"\n{error.message}"
    log(message)


async def connect(channel):
    is_connected = False
    try:
        await asyncio.sleep(channel["splay"])
        log(f"Connecting to {channel['name']}")
        await CLIENT(functions.channels.JoinChannelRequest(channel=channel["name"]))
        is_connected = True
    except Exception as e:
        handle_connectionerror(e, channel)
    channel["is_connected"] = is_connected
    return channel


async def do_raid(channels):
    tasks = [raid(channel) for channel in channels]
    await asyncio.gather(*tasks)


async def do_connect():
    tasks = [connect(channel_map(channel)) for channel in channels_to_raid()]
    channels = await asyncio.gather(*tasks)
    connected_channels = filter(lambda channel: channel["is_connected"], channels)
    return connected_channels


async def close():
    await CLIENT.log_out()


async def start():
    await CLIENT.start()
    await asyncio.sleep(10)

    log(f"Calculated splay: {recommended_splay()} seconds")
    log(
        "Splay will be added to connection and user defined wait intervals"
        + " to avoid Telegram rate limiting"
    )
    channels = await do_connect()
    await do_raid(channels)


def validate_account_settings(settings):
    schema = {
        "type": "object",
        "properties": {
            "api_id": {"type": "number"},
            "api_hash": {"type": "string"},
            "app_short_name": {"type": "string"},
        },
        "required": [
            "api_id",
            "api_hash",
            "app_short_name",
        ],
    }
    jsonschema.validate(settings, schema)


@functools.lru_cache()
def load_settings(path="settings.yml"):
    with open(path, "r", encoding="utf8") as settings_file:
        try:
            settings = yaml.safe_load(settings_file)
        except Exception as e:
            print(
                """
!@#!@#!@#!@#!@#!@#!@#!@#!@#!@#!@#!@#!@#!@#!@#!@#!@#!@#
!@#                                                !@#
!@#   THE `settings.yml` FILE IS NOT VALID YAML    !@#
!@#                                                !@#
!@#!@#!@#!@#!@#!@#!@#!@#!@#!@#!@#!@#!@#!@#!@#!@#!@#!@#

BEFORE ASKING QUESTIONS IN THE SURFRANCH CHANNEL, PLEASE GIVE A BEST EFFORT
TO FIX THE YAML ERRORS YOURSELF USING THIS LINTER

>>>   http://www.yamllint.com/   <<<


IF YOU KNOW NOTHING ABOUT THE YAML SYNTAX, WE RECOMMEND READING THIS TUTORIAL

>>>   https://gettaurus.org/docs/YAMLTutorial/   <<<
"""
            )
            raise e

        validate_account_settings(settings)
    return settings


def api_id():
    settings = load_settings()
    return settings["api_id"]


def api_hash():
    settings = load_settings()
    return settings["api_hash"]


def app_short_name():
    settings = load_settings()
    return settings["app_short_name"]


if __name__ == "__main__":
    CLIENT = TelegramClient(app_short_name(), api_id(), api_hash())
    LOOP = asyncio.get_event_loop()
    try:
        LOOP.run_until_complete(start())
        LOOP.run_until_complete(close())
    except KeyboardInterrupt:
        LOOP.run_until_complete(close())
        sys.exit(0)
