"""Google Chat direct-message helper.

Wraps the ``findDirectMessage`` → ``messages().create()`` pattern so any
module can send a DM to a user by email address without duplicating the
lookup/creation logic.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def send_dm(service, user_email: str, text: str) -> dict:
    """Send a Google Chat direct message to *user_email*.

    1. Find (or set up) the DM space between the authenticated bot and the
       target user.
    2. Post *text* as a plain-text message in that space.

    Returns the API response dict from ``messages().create()``.
    """
    dm_space = await _find_or_create_dm_space(service, user_email)
    space_name = dm_space["name"]

    result = await asyncio.to_thread(
        service.spaces()
        .messages()
        .create(parent=space_name, body={"text": text})
        .execute
    )
    logger.info("DM sent to %s in space %s", user_email, space_name)
    return result


async def _find_or_create_dm_space(service, user_email: str) -> dict:
    """Return an existing DM space or create one via ``spaces().setup()``."""
    try:
        dm_space = await asyncio.to_thread(
            service.spaces()
            .findDirectMessage(name=f"users/{user_email}")
            .execute
        )
        return dm_space
    except Exception:
        logger.debug("No existing DM space for %s — creating one", user_email)

    dm_space = await asyncio.to_thread(
        service.spaces()
        .setup(
            body={
                "space": {"spaceType": "DIRECT_MESSAGE"},
                "memberships": [
                    {"member": {"name": f"users/{user_email}", "type": "HUMAN"}}
                ],
            }
        )
        .execute
    )
    return dm_space
