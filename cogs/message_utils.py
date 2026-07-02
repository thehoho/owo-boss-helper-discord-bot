"""Shared Discord message-response helpers.

Prefix commands normally reply to the invoking message. Some Discord channel or
thread permission combinations allow ordinary sends but reject message replies.
The helper below preserves replies where possible and falls back to a normal send
instead of silently losing the command response.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

logger = logging.getLogger(__name__)


async def safe_reply(
    message: discord.Message,
    content: str | None = None,
    **kwargs: Any,
) -> discord.Message:
    """Reply to ``message`` and fall back to a normal channel send on failure."""
    try:
        return await message.reply(content, **kwargs)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
        fallback_kwargs = dict(kwargs)
        fallback_kwargs.pop("mention_author", None)
        fallback_kwargs.pop("fail_if_not_exists", None)
        logger.warning(
            "Reply failed in guild %s channel %s (%s); falling back to channel.send: %s",
            getattr(message.guild, "id", None),
            message.channel.id,
            type(message.channel).__name__,
            exc,
        )
        return await message.channel.send(content, **fallback_kwargs)
