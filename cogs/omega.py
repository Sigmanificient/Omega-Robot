"""Contains cog related to omega."""

import asyncio
from datetime import datetime
import re
from typing import AsyncGenerator, Optional

import aiohttp
import discord
from discord.ext import commands

from src.utils import user_only


async def get_github_issues(message: discord.Message) -> AsyncGenerator[dict, None]:
    """Asynchronous generator that returns information on each issue,
    identified with a specific format, in the message.

    If a request error occurs, it sends a message and stops.
    """
    matches = re.findall("(?=((^| )#[0-9]+([eul])?($| )))", message.content)

    async with aiohttp.ClientSession() as session:
        for i in matches:
            issue = i[0].strip("#e ")
            issue = issue.strip("#u ")
            issue = issue.strip("#l ")

            if "e" in i[0]:
                repo = "numworks/epsilon"
            elif "u" in i[0]:
                repo = "UpsilonNumworks/Upsilon"
            elif "l" in i[0]:
                repo = "Lambda-Numworks/Lambda"
            else:
                repo = "omega-numworks/omega"

            async with session.get(
                f"https://api.github.com/repos/{repo}/issues/{issue}"
            ) as response:
                if response.status != 200:
                    await message.channel.send(
                        f"Erreur lors de la requête ({response.status})"
                    )
                    return
                yield await response.json()


async def make_embed(data: dict) -> discord.Embed:
    """Return a formatted ``discord.Embed`` from given data."""
    embed = discord.Embed(
        title=data["title"], url=data["html_url"], description=data["body"]
    )

    # Truncate the description if it's above the maximum size.
    if len(embed.description) > 2048:
        embed.description = f"{embed.description[:2043]}[...]"

    author = data["user"]
    embed.set_author(
        name=author["login"], url=author["html_url"], icon_url=author["avatar_url"]
    )

    additional_infos = []

    if data.get("locked"):
        additional_infos.append(":lock: locked")

    if pull_request := data.get("pull_request"):
        additional_infos.append(":arrows_clockwise: Pull request")

        async with aiohttp.ClientSession() as session:
            async with session.get(pull_request["url"] + "/commits") as response:
                commits_data = await response.json()

        # Format all commits data into strings.
        formatted = [
            (
                f"[`{commit['sha'][:7]}`]({commit['html_url']})"
                f" {commit['commit']['message']} - {commit['committer']['login']}"
            ) for commit in commits_data
        ]

        result = "\n".join(formatted)

        # If the result is over the field's value's max size,
        # it truncates the result.
        if len(result) > 1024:
            diff = len(result) - 1024 + 4

            while diff > 0:
                line = formatted.pop(len(formatted) // 2)
                diff -= len(line) + 1

            formatted.insert(len(formatted) // 2 + 1, "...")
            result = "\n".join(formatted)

        embed.add_field(name="Commits", value=result)

    if data["comments"]:
        additional_infos.append(f":speech_balloon: Comments : {data['comments']}")

    if data["state"] == "closed":
        closed_at = datetime.strptime(data["closed_at"], "%Y-%m-%dT%H:%M:%SZ").strftime(
            "%b. %d %H:%M %Y"
        )

        additional_infos.append(
            f":x: Closed by {data['closed_by']['login']} on {closed_at}"
        )

    elif data["state"] == "open":
        additional_infos.append(":white_check_mark: Open")

    if data["labels"]:
        labels = "` `".join(i["name"] for i in data["labels"])
        additional_infos.append(f":label: Labels: `{labels}`")

    embed.add_field(name="Additional informations", value="\n".join(additional_infos))

    return embed


async def make_color_embed(
    hex_code: int, message: discord.Message
) -> Optional[discord.Embed]:
    """Return a ``discord.Embed`` contains some informations about color
    of given ``hex_code``.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://www.thecolorapi.com/id?hex={hex_code}"
        ) as response:
            if response.status == 200:
                data = await response.json()
            else:
                await message.channel.send(
                    f"Erreur lors de la requête ({response.status})"
                )
                return

    title = f"{data['name']['value']} color"
    description = f"**Hex:** #{hex_code}\n"
    description += "\n".join(
        (
            f"**{color_format.capitalize()}:**"
            f"{', '.join(data[color_format][letter] for letter in tuple(color_format))}"
        )
        for color_format in ("rgb", "hsl", "hsv")
    )

    return discord.Embed(
        title=title, description=description, color=int(hex_code, base=16)
    )


class Omega(commands.Cog):
    """Contains commands related to omega."""

    def __init__(self, bot, config):
        self.bot = bot
        self.config = config
        self.issue_embeds = {}

    @commands.Cog.listener()
    @user_only()
    async def on_message(self, message):
        """Check if the message has an issue identifier in it and
        process message if this is the case.
        """
        # Checks if the message is an hex code
        if re.match("^#([A-Fa-f0-9]{6})$", message.content):
            hex_code = message.content.lstrip("#")
            color_embed = await make_color_embed(hex_code, message)

            await message.channel.send(embed=color_embed)

        # Check if the message has an issue identifier in it
        if re.search("(^| )#[0-9]+(e|u|l)?($| )", message.content):
            async for i in get_github_issues(message):
                # Create an embed with the data from the issue
                embed = await make_embed(i)

                # Send the embed in a message, react to it and temporally store
                # this message's id.
                issue_embed = await message.channel.send(embed=embed)
                await issue_embed.add_reaction("🗑️")
                self.issue_embeds[issue_embed.id] = 1

                # After 60 seconds, it deletes it from the storage dictionary.
                await asyncio.sleep(60)
                await issue_embed.remove_reaction("🗑️", self.bot.user)
                self.issue_embeds.pop(issue_embed.id)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, reaction):
        """Delete issue embed if a user reacts with trash emote."""
        # Ignore bots.
        reaction_user = await self.bot.fetch_user(reaction.user_id)
        if reaction_user.bot:
            return

        # If the reaction is "🗑️" and on a message stored in issue_embeds,
        # it deletes it on discord and in the storage dictionary.
        if reaction.emoji.name == "🗑️" and self.issue_embeds.pop(
            reaction.message_id, None
        ):
            channel = self.bot.get_channel(reaction.channel_id)
            message = await channel.fetch_message(reaction.message_id)
            await message.delete()
