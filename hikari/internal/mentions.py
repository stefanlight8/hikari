# Copyright (c) 2020 Nekokatt
# Copyright (c) 2021-present davfsa
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""Utility functions used for managing mentions on Discord."""

from __future__ import annotations

__all__: typing.Sequence[str] = ("generate_allowed_mentions",)

import typing

if typing.TYPE_CHECKING:
    from hikari import guilds
    from hikari import snowflakes
    from hikari import undefined
    from hikari import users
    from hikari.internal import data_binding


def generate_allowed_mentions(
    mentions_everyone: undefined.UndefinedOr[bool],
    mentions_reply: undefined.UndefinedOr[bool],
    user_mentions: undefined.UndefinedOr[snowflakes.SnowflakeishSequence[users.PartialUser] | bool],
    role_mentions: undefined.UndefinedOr[snowflakes.SnowflakeishSequence[guilds.PartialRole] | bool],
) -> data_binding.JSONObject:
    """Generate an allowed mentions JSON object.

    Parameters
    ----------
    mentions_everyone
        Whether @everyone and @here mentions are enabled. If
        [`hikari.undefined.UNDEFINED`][] or [`False`][] then this will be disabled.
    mentions_reply
        Whether the reply mention should be enabled. If [`hikari.undefined.UNDEFINED`][]
        or [`False`][] then this will be disabled.
    user_mentions
        Either a sequence of objects/IDs of the users to enabled mentions for,
        [`True`][] to allow all mentions or [`False`][]/[`hikari.undefined.UNDEFINED`][]
        to disable all user mentions.
    role_mentions
        Either a sequence of objects/IDs of the roles to enabled mentions for,
        [`True`][] to allow all mentions or [`False`][]/[`hikari.undefined.UNDEFINED`][]
        to disable all user mentions.

    Returns
    -------
    hikari.internal.data_binding.JSONObject
        The allowed mentions JSON Object.
    """
    parsed_mentions: list[str] = []
    allowed_mentions: dict[str, typing.Any] = {"parse": parsed_mentions}

    if mentions_everyone is True:
        parsed_mentions.append("everyone")

    if mentions_reply is True:
        allowed_mentions["replied_user"] = True

    if user_mentions is True:
        parsed_mentions.append("users")
    elif isinstance(user_mentions, typing.Collection):
        # Duplicates will cause Discord to error.
        ids = {str(int(u)) for u in user_mentions}
        allowed_mentions["users"] = list(ids)

    if role_mentions is True:
        parsed_mentions.append("roles")
    elif isinstance(role_mentions, typing.Collection):
        # Duplicates will cause Discord to error.
        ids = {str(int(r)) for r in role_mentions}
        allowed_mentions["roles"] = list(ids)

    return allowed_mentions
