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
"""Basic implementation of a cache for general bots and gateway apps."""

from __future__ import annotations

__all__: typing.Sequence[str] = ("CacheImpl",)

import copy
import logging
import typing

from hikari import channels as channels_
from hikari import emojis
from hikari import messages
from hikari import snowflakes
from hikari import undefined
from hikari.api import cache
from hikari.api import config as config_api
from hikari.internal import cache as cache_utility
from hikari.internal import collections
from hikari.internal import typing_extensions

if typing.TYPE_CHECKING:
    from hikari import guilds
    from hikari import invites
    from hikari import presences
    from hikari import stickers
    from hikari import traits
    from hikari import users
    from hikari import voices
    from hikari.impl import config as config_impl

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("hikari.cache")


# TODO: do we want to hide entities that are marked as "deleted" and being kept alive by references?
class CacheImpl(cache.MutableCache):
    """In-memory cache implementation.

    Parameters
    ----------
    app
        The object of the REST aware app this is bound to.
    settings
        The cache settings to use.
    """

    __slots__: typing.Sequence[str] = (
        "_app",
        "_dm_channel_entries",
        "_emoji_entries",
        "_guild_channel_entries",
        "_guild_entries",
        "_guild_thread_entries",
        "_intents",
        "_invite_entries",
        "_me",
        "_message_entries",
        "_referenced_messages",
        "_role_entries",
        "_settings",
        "_sticker_entries",
        "_unknown_custom_emoji_entries",
        "_user_entries",
    )

    # For the sake of keeping things clean, the annotations are being kept separate from the assignment here.
    _me: users.OwnUser | None
    _emoji_entries: collections.ExtendedMutableMapping[snowflakes.Snowflake, cache_utility.KnownCustomEmojiData]
    _dm_channel_entries: collections.ExtendedMutableMapping[snowflakes.Snowflake, snowflakes.Snowflake]
    _guild_channel_entries: collections.ExtendedMutableMapping[snowflakes.Snowflake, channels_.PermissibleGuildChannel]
    _guild_thread_entries: collections.ExtendedMutableMapping[snowflakes.Snowflake, channels_.GuildThreadChannel]
    _guild_entries: collections.ExtendedMutableMapping[snowflakes.Snowflake, cache_utility.GuildRecord]
    _invite_entries: collections.ExtendedMutableMapping[str, cache_utility.InviteData]
    _role_entries: collections.ExtendedMutableMapping[snowflakes.Snowflake, guilds.Role]
    _sticker_entries: collections.ExtendedMutableMapping[snowflakes.Snowflake, cache_utility.GuildStickerData]
    _unknown_custom_emoji_entries: collections.ExtendedMutableMapping[
        snowflakes.Snowflake, cache_utility.RefCell[emojis.CustomEmoji]
    ]
    _user_entries: collections.ExtendedMutableMapping[snowflakes.Snowflake, cache_utility.RefCell[users.User]]
    _message_entries: collections.ExtendedMutableMapping[
        snowflakes.Snowflake, cache_utility.RefCell[cache_utility.MessageData]
    ]
    _referenced_messages: collections.ExtendedMutableMapping[
        snowflakes.Snowflake, cache_utility.RefCell[cache_utility.MessageData]
    ]

    def __init__(self, app: traits.RESTAware, settings: config_impl.CacheSettings) -> None:
        self._app = app
        self._settings = settings
        self._create_cache()

    @property
    @typing_extensions.override
    def settings(self) -> config_impl.CacheSettings:
        return self._settings

    def _create_cache(self) -> None:
        self._me = None
        self._dm_channel_entries = collections.LimitedCapacityCacheMap(limit=self._settings.max_dm_channel_ids)
        self._emoji_entries = collections.FreezableDict()
        self._guild_channel_entries = collections.FreezableDict()
        self._guild_thread_entries = collections.FreezableDict()
        self._guild_entries = collections.FreezableDict()
        self._invite_entries = collections.FreezableDict()
        self._role_entries = collections.FreezableDict()
        self._sticker_entries = collections.FreezableDict()
        # This is a purely internal cache used for handling the caching and de-duplicating of the unknown custom emojis
        # found attached to cached presence activities.
        self._unknown_custom_emoji_entries = collections.FreezableDict()
        self._user_entries = collections.FreezableDict()
        self._message_entries = collections.LimitedCapacityCacheMap(
            limit=self._settings.max_messages, on_expire=self._on_message_expire
        )
        self._referenced_messages = collections.FreezableDict()

    def _is_cache_enabled_for(self, required_flag: config_api.CacheComponents) -> bool:
        return (self._settings.components & required_flag) == required_flag

    @staticmethod
    def _increment_ref_count(obj: cache_utility.RefCell[typing.Any], increment: int = 1) -> None:
        obj.ref_count += increment

    @typing_extensions.override
    def clear(self) -> None:
        if self._settings.components == config_api.CacheComponents.NONE:
            return

        self._create_cache()

    @typing_extensions.override
    def clear_dm_channel_ids(self) -> cache.CacheView[snowflakes.Snowflake, snowflakes.Snowflake]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.DM_CHANNEL_IDS):
            return cache_utility.EmptyCacheView()

        result = self._dm_channel_entries
        self._dm_channel_entries = collections.LimitedCapacityCacheMap(limit=self._settings.max_dm_channel_ids)
        return cache_utility.CacheMappingView(result)

    @typing_extensions.override
    def delete_dm_channel_id(
        self, user: snowflakes.SnowflakeishOr[users.PartialUser], /
    ) -> snowflakes.Snowflake | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.DM_CHANNEL_IDS):
            return None

        return self._dm_channel_entries.pop(snowflakes.Snowflake(user), None)

    @typing_extensions.override
    def get_dm_channel_id(self, user: snowflakes.SnowflakeishOr[users.PartialUser], /) -> snowflakes.Snowflake | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.DM_CHANNEL_IDS):
            return None

        return self._dm_channel_entries.get(snowflakes.Snowflake(user))

    @typing_extensions.override
    def get_dm_channel_ids_view(self) -> cache.CacheView[snowflakes.Snowflake, snowflakes.Snowflake]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.DM_CHANNEL_IDS):
            return cache_utility.EmptyCacheView()

        return cache_utility.CacheMappingView(self._dm_channel_entries.freeze())

    @typing_extensions.override
    def set_dm_channel_id(
        self,
        user: snowflakes.SnowflakeishOr[users.PartialUser],
        channel: snowflakes.SnowflakeishOr[channels_.PartialChannel],
        /,
    ) -> None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.DM_CHANNEL_IDS):
            return

        self._dm_channel_entries[snowflakes.Snowflake(user)] = snowflakes.Snowflake(channel)

    def _build_emoji(self, emoji_data: cache_utility.KnownCustomEmojiData) -> emojis.KnownCustomEmoji:
        return emoji_data.build_entity(self._app)

    @typing_extensions.override
    def clear_emojis(self) -> cache.CacheView[snowflakes.Snowflake, emojis.KnownCustomEmoji]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.EMOJIS):
            return cache_utility.EmptyCacheView()

        cached_emojis = self._emoji_entries
        self._emoji_entries = collections.FreezableDict()

        for emoji_data in cached_emojis.values():
            if emoji_data.user:
                self._garbage_collect_user(emoji_data.user, decrement=1)

        for guild_id, guild_record in self._guild_entries.freeze().items():
            guild_record.emojis = None
            self._remove_guild_record_if_empty(guild_id, guild_record)

        return cache_utility.CacheMappingView(cached_emojis, builder=self._build_emoji)

    @typing_extensions.override
    def clear_emojis_for_guild(
        self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /
    ) -> cache.CacheView[snowflakes.Snowflake, emojis.KnownCustomEmoji]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.EMOJIS):
            return cache_utility.EmptyCacheView()

        guild_id = snowflakes.Snowflake(guild)
        guild_record = self._guild_entries.get(guild_id)
        if not guild_record or not guild_record.emojis:
            return cache_utility.EmptyCacheView()

        cached_emojis = {emoji_id: self._emoji_entries.pop(emoji_id) for emoji_id in guild_record.emojis}
        guild_record.emojis = None
        self._remove_guild_record_if_empty(guild_id, guild_record)

        for emoji_data in cached_emojis.values():
            if emoji_data.user:
                self._garbage_collect_user(emoji_data.user, decrement=1)

        return cache_utility.CacheMappingView(cached_emojis, builder=self._build_emoji)

    @typing_extensions.override
    def delete_emoji(self, emoji: snowflakes.SnowflakeishOr[emojis.CustomEmoji], /) -> emojis.KnownCustomEmoji | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.EMOJIS):
            return None

        emoji_id = snowflakes.Snowflake(emoji)
        emoji_data = self._emoji_entries.pop(emoji_id, None)
        if not emoji_data:
            return None

        if emoji_data.user:
            self._garbage_collect_user(emoji_data.user, decrement=1)

        guild_record = self._guild_entries.get(emoji_data.guild_id)
        if guild_record and guild_record.emojis:
            guild_record.emojis.remove(emoji_id)

            if not guild_record.emojis:
                guild_record.emojis = None
                self._remove_guild_record_if_empty(emoji_data.guild_id, guild_record)

        return self._build_emoji(emoji_data)

    @typing_extensions.override
    def get_emoji(self, emoji: snowflakes.SnowflakeishOr[emojis.CustomEmoji], /) -> emojis.KnownCustomEmoji | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.EMOJIS):
            return None

        emoji_data = self._emoji_entries.get(snowflakes.Snowflake(emoji))
        return self._build_emoji(emoji_data) if emoji_data else None

    @typing_extensions.override
    def get_emojis_view(self) -> cache.CacheView[snowflakes.Snowflake, emojis.KnownCustomEmoji]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.EMOJIS):
            return cache_utility.EmptyCacheView()

        return cache_utility.CacheMappingView(self._emoji_entries.freeze(), builder=self._build_emoji)

    @typing_extensions.override
    def get_emojis_view_for_guild(
        self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /
    ) -> cache.CacheView[snowflakes.Snowflake, emojis.KnownCustomEmoji]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.EMOJIS):
            return cache_utility.EmptyCacheView()

        guild_record = self._guild_entries.get(snowflakes.Snowflake(guild))
        if not guild_record or not guild_record.emojis:
            return cache_utility.EmptyCacheView()

        cached_emojis = {emoji_id: self._emoji_entries[emoji_id] for emoji_id in guild_record.emojis}
        return cache_utility.CacheMappingView(cached_emojis, builder=self._build_emoji)

    @typing_extensions.override
    def set_emoji(self, emoji: emojis.KnownCustomEmoji, /) -> None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.EMOJIS):
            return

        if not emoji.guild_id:
            msg = "Cannot cache an emoji without a guild ID."
            raise ValueError(msg)

        user: cache_utility.RefCell[users.User] | None = None
        if emoji.user:
            user = self._set_user(emoji.user)
            if emoji.id not in self._emoji_entries:
                self._increment_ref_count(user)

        emoji_data = cache_utility.KnownCustomEmojiData.build_from_entity(emoji, user=user)
        self._emoji_entries[emoji.id] = emoji_data
        guild_record = self._get_or_create_guild_record(emoji.guild_id)

        if guild_record.emojis is None:  # TODO: add test cases when it is not None?
            guild_record.emojis = collections.SnowflakeSet()

        guild_record.emojis.add(emoji.id)

    @typing_extensions.override
    def update_emoji(
        self, emoji: emojis.KnownCustomEmoji, /
    ) -> tuple[emojis.KnownCustomEmoji | None, emojis.KnownCustomEmoji | None]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.EMOJIS):
            return None, None

        if not emoji.guild_id:
            msg = "Emoji must have a guild ID to be cached."
            raise ValueError(msg)

        cached_emoji = self.get_emoji(emoji.id)
        self.set_emoji(emoji)
        return cached_emoji, self.get_emoji(emoji.id)

    def _build_sticker(self, sticker_data: cache_utility.GuildStickerData) -> stickers.GuildSticker:
        return sticker_data.build_entity(self._app)

    @typing_extensions.override
    def clear_stickers(self) -> cache.CacheView[snowflakes.Snowflake, stickers.GuildSticker]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_STICKERS):
            return cache_utility.EmptyCacheView()

        cached_stickers = self._sticker_entries
        self._sticker_entries = collections.FreezableDict()

        for sticker_data in cached_stickers.values():
            if sticker_data.user:
                self._garbage_collect_user(sticker_data.user, decrement=1)

        for guild_id, guild_record in self._guild_entries.freeze().items():
            guild_record.stickers = None
            self._remove_guild_record_if_empty(guild_id, guild_record)

        return cache_utility.CacheMappingView(cached_stickers, builder=self._build_sticker)

    @typing_extensions.override
    def clear_stickers_for_guild(
        self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /
    ) -> cache.CacheView[snowflakes.Snowflake, stickers.GuildSticker]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_STICKERS):
            return cache_utility.EmptyCacheView()

        guild_id = snowflakes.Snowflake(guild)
        guild_record = self._guild_entries.get(guild_id)
        if not guild_record or not guild_record.stickers:
            return cache_utility.EmptyCacheView()

        cached_stickers = {sticker_id: self._sticker_entries.pop(sticker_id) for sticker_id in guild_record.stickers}
        guild_record.stickers = None
        self._remove_guild_record_if_empty(guild_id, guild_record)

        for sticker_data in cached_stickers.values():
            if sticker_data.user:
                self._garbage_collect_user(sticker_data.user, decrement=1)

        return cache_utility.CacheMappingView(cached_stickers, builder=self._build_sticker)

    @typing_extensions.override
    def get_sticker(self, sticker: snowflakes.SnowflakeishOr[stickers.GuildSticker], /) -> stickers.GuildSticker | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_STICKERS):
            return None

        sticker_data = self._sticker_entries.get(snowflakes.Snowflake(sticker))
        return self._build_sticker(sticker_data) if sticker_data else None

    @typing_extensions.override
    def get_stickers_view(self) -> cache.CacheView[snowflakes.Snowflake, stickers.GuildSticker]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_STICKERS):
            return cache_utility.EmptyCacheView()

        return cache_utility.CacheMappingView(self._sticker_entries.freeze(), builder=self._build_sticker)

    @typing_extensions.override
    def get_stickers_view_for_guild(
        self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /
    ) -> cache.CacheView[snowflakes.Snowflake, stickers.GuildSticker]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_STICKERS):
            return cache_utility.EmptyCacheView()

        guild_record = self._guild_entries.get(snowflakes.Snowflake(guild))
        if not guild_record or not guild_record.stickers:
            return cache_utility.EmptyCacheView()

        cached_stickers = {sticker_id: self._sticker_entries[sticker_id] for sticker_id in guild_record.stickers}
        return cache_utility.CacheMappingView(cached_stickers, builder=self._build_sticker)

    @typing_extensions.override
    def delete_sticker(
        self, sticker: snowflakes.SnowflakeishOr[stickers.GuildSticker], /
    ) -> stickers.GuildSticker | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_STICKERS):
            return None

        sticker_id = snowflakes.Snowflake(sticker)
        sticker_data = self._sticker_entries.pop(sticker_id, None)
        if not sticker_data:
            return None

        if sticker_data.user:
            self._garbage_collect_user(sticker_data.user, decrement=1)

        guild_record = self._guild_entries.get(sticker_data.guild_id)
        if guild_record and guild_record.stickers:
            guild_record.stickers.remove(sticker_id)

            if not guild_record.stickers:
                guild_record.stickers = None

        return self._build_sticker(sticker_data)

    @typing_extensions.override
    def set_sticker(self, sticker: stickers.GuildSticker, /) -> None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_STICKERS):
            return

        user: cache_utility.RefCell[users.User] | None = None
        if sticker.user:
            user = self._set_user(sticker.user)
            if sticker.id not in self._sticker_entries:
                self._increment_ref_count(user)

        sticker_data = cache_utility.GuildStickerData.build_from_entity(sticker, user=user)
        self._sticker_entries[sticker.id] = sticker_data
        guild_record = self._get_or_create_guild_record(sticker.guild_id)

        if guild_record.stickers is None:  # TODO: add test cases when it is not None?
            guild_record.stickers = collections.SnowflakeSet()

        guild_record.stickers.add(sticker.id)

    def _remove_guild_record_if_empty(
        self, guild_id: snowflakes.Snowflake, record: cache_utility.GuildRecord, /
    ) -> None:
        if guild_id in self._guild_entries and record.empty():
            del self._guild_entries[guild_id]

    def _get_or_create_guild_record(self, guild_id: snowflakes.Snowflake) -> cache_utility.GuildRecord:
        if guild_id not in self._guild_entries:
            self._guild_entries[guild_id] = cache_utility.GuildRecord()

        return self._guild_entries[guild_id]

    @typing_extensions.override
    def clear_guilds(self) -> cache.CacheView[snowflakes.Snowflake, guilds.GatewayGuild]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILDS):
            return cache_utility.EmptyCacheView()

        cached_guilds = {}

        for guild_id, guild_record in self._guild_entries.freeze().items():
            if guild_record.guild:
                cached_guilds[guild_id] = guild_record.guild
                guild_record.guild = None
                guild_record.is_available = None
                self._remove_guild_record_if_empty(guild_id, guild_record)

        return cache_utility.CacheMappingView(cached_guilds) if cached_guilds else cache_utility.EmptyCacheView()

    @typing_extensions.override
    def delete_guild(self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /) -> guilds.GatewayGuild | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILDS):
            return None

        guild_id = snowflakes.Snowflake(guild)
        guild_record = self._guild_entries.get(guild_id)
        if not guild_record:
            return None

        guild = guild_record.guild

        if guild:
            guild_record.guild = None
            guild_record.is_available = None
            self._remove_guild_record_if_empty(guild_id, guild_record)

        return guild

    def _get_guild(
        self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /, *, availability: bool
    ) -> guilds.GatewayGuild | None:
        guild_record = self._guild_entries.get(snowflakes.Snowflake(guild))
        if not guild_record or not guild_record.guild or guild_record.is_available is not availability:
            return None

        return copy.copy(guild_record.guild)

    @typing_extensions.override
    def get_guild(self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /) -> guilds.GatewayGuild | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILDS):
            return None

        guild_record = self._guild_entries.get(snowflakes.Snowflake(guild))
        return copy.copy(guild_record.guild) if guild_record and guild_record.guild else None

    @typing_extensions.override
    def get_available_guild(
        self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /
    ) -> guilds.GatewayGuild | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILDS):
            return None

        return self._get_guild(guild, availability=True)

    @typing_extensions.override
    def get_unavailable_guild(
        self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /
    ) -> guilds.GatewayGuild | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILDS):
            return None

        return self._get_guild(guild, availability=False)

    def _get_guilds_view(self, *, availability: bool) -> cache.CacheView[snowflakes.Snowflake, guilds.GatewayGuild]:
        # We may have a guild record without a guild object in cases where we're caching other entities that belong to
        # the guild therefore we want to make sure record.guild isn't None.
        results = {
            sf: guild_record.guild
            for sf, guild_record in self._guild_entries.items()
            if guild_record.guild and guild_record.is_available is availability
        }
        return cache_utility.CacheMappingView(results) if results else cache_utility.EmptyCacheView()

    @typing_extensions.override
    def get_guilds_view(self) -> cache.CacheView[snowflakes.Snowflake, guilds.GatewayGuild]:
        return cache_utility.CacheMappingView(
            {guild_id: record.guild for guild_id, record in self._guild_entries.items() if record.guild}
        )

    @typing_extensions.override
    def get_available_guilds_view(self) -> cache.CacheView[snowflakes.Snowflake, guilds.GatewayGuild]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILDS):
            return cache_utility.EmptyCacheView()

        return self._get_guilds_view(availability=True)

    @typing_extensions.override
    def get_unavailable_guilds_view(self) -> cache.CacheView[snowflakes.Snowflake, guilds.GatewayGuild]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILDS):
            return cache_utility.EmptyCacheView()

        return self._get_guilds_view(availability=False)

    @typing_extensions.override
    def set_guild(self, guild: guilds.GatewayGuild, /) -> None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILDS):
            return

        guild_record = self._get_or_create_guild_record(guild.id)
        guild_record.guild = copy.copy(guild)
        guild_record.is_available = True

    @typing_extensions.override
    def set_guild_availability(
        self,
        guild: snowflakes.SnowflakeishOr[guilds.PartialGuild],
        is_available: bool,  # noqa: FBT001 - Boolean-typed positional argument
        /,
    ) -> None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILDS):
            return

        guild_record = self._guild_entries.get(snowflakes.Snowflake(guild))
        if guild_record and guild_record.guild:
            guild_record.is_available = is_available

    @typing_extensions.override
    def update_guild(
        self, guild: guilds.GatewayGuild, /
    ) -> tuple[guilds.GatewayGuild | None, guilds.GatewayGuild | None]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILDS):
            return None, None

        guild = copy.copy(guild)
        cached_guild = self.get_guild(guild.id)

        # We have to manually update these because Inconsistency is Discord's middle name.
        if cached_guild:
            guild.member_count = cached_guild.member_count
            guild.joined_at = cached_guild.joined_at
            guild.is_large = cached_guild.is_large

        self.set_guild(guild)
        return cached_guild, self.get_guild(guild.id)

    @typing_extensions.override
    def clear_threads(self) -> cache.CacheView[snowflakes.Snowflake, channels_.GuildThreadChannel]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_THREADS):
            return cache_utility.EmptyCacheView()

        cached_threads = self._guild_thread_entries
        self._guild_thread_entries = collections.FreezableDict()

        for guild_id, guild_record in self._guild_entries.freeze().items():
            if guild_record.threads:
                guild_record.threads = None
                self._remove_guild_record_if_empty(guild_id, guild_record)

        return cache_utility.CacheMappingView(cached_threads)

    @typing_extensions.override
    def clear_threads_for_guild(
        self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /
    ) -> cache.CacheView[snowflakes.Snowflake, channels_.GuildThreadChannel]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_THREADS):
            return cache_utility.EmptyCacheView()

        guild_id = snowflakes.Snowflake(guild)
        guild_record = self._guild_entries.get(guild_id)
        if not guild_record or not guild_record.threads:
            return cache_utility.EmptyCacheView()

        cached_threads = {sf: self._guild_thread_entries.pop(sf) for sf in guild_record.threads}
        guild_record.threads = None
        self._remove_guild_record_if_empty(guild_id, guild_record)
        return cache_utility.CacheMappingView(cached_threads)

    @typing_extensions.override
    def clear_threads_for_channel(
        self,
        guild: snowflakes.SnowflakeishOr[guilds.PartialGuild],
        channel: snowflakes.SnowflakeishOr[channels_.PartialChannel],
        /,
    ) -> cache.CacheView[snowflakes.Snowflake, channels_.GuildThreadChannel]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_THREADS):
            return cache_utility.EmptyCacheView()

        channel_id = snowflakes.Snowflake(channel)
        guild = snowflakes.Snowflake(guild)
        guild_record = self._guild_entries.get(guild)
        if not guild_record or not guild_record.threads:
            return cache_utility.EmptyCacheView()

        threads: dict[snowflakes.Snowflake, channels_.GuildThreadChannel] = {}
        for thread in map(self._guild_thread_entries.__getitem__, tuple(guild_record.threads)):
            if thread.parent_id == channel_id:
                del self._guild_thread_entries[thread.id]
                guild_record.threads.remove(thread.id)

        if not guild_record.threads:
            guild_record.threads = None
            self._remove_guild_record_if_empty(guild, guild_record)

        return cache_utility.CacheMappingView(threads)

    @typing_extensions.override
    def delete_thread(
        self, thread: snowflakes.SnowflakeishOr[channels_.PartialChannel], /
    ) -> channels_.GuildThreadChannel | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_THREADS):
            return None

        thread_id = snowflakes.Snowflake(thread)
        thread = self._guild_thread_entries.pop(thread_id, None)

        if not thread:
            return None

        guild_record = self._guild_entries.get(thread.guild_id)
        if guild_record and guild_record.threads:
            guild_record.threads.remove(thread_id)
            if not guild_record.threads:
                guild_record.threads = None
                self._remove_guild_record_if_empty(thread.guild_id, guild_record)

        return thread

    @typing_extensions.override
    def get_thread(
        self, thread: snowflakes.SnowflakeishOr[channels_.PartialChannel], /
    ) -> channels_.GuildThreadChannel | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_THREADS):
            return None

        thread = self._guild_thread_entries.get(snowflakes.Snowflake(thread))
        return copy.copy(thread) if thread else None

    @typing_extensions.override
    def get_threads_view(self) -> cache.CacheView[snowflakes.Snowflake, channels_.GuildThreadChannel]:
        return cache_utility.CacheMappingView(self._guild_thread_entries.freeze())

    @typing_extensions.override
    def get_threads_view_for_guild(
        self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /
    ) -> cache.CacheView[snowflakes.Snowflake, channels_.GuildThreadChannel]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_THREADS):
            return cache_utility.EmptyCacheView()

        guild_record = self._guild_entries.get(snowflakes.Snowflake(guild))
        if not guild_record or not guild_record.threads:
            return cache_utility.EmptyCacheView()

        return cache_utility.CacheMappingView({sf: self._guild_thread_entries[sf] for sf in guild_record.threads})

    @typing_extensions.override
    def get_threads_view_for_channel(
        self,
        guild: snowflakes.SnowflakeishOr[guilds.PartialGuild],
        channel: snowflakes.SnowflakeishOr[channels_.PartialChannel],
        /,
    ) -> cache.CacheView[snowflakes.Snowflake, channels_.GuildThreadChannel]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_THREADS):
            return cache_utility.EmptyCacheView()

        record = self._guild_entries.get(snowflakes.Snowflake(guild))
        if not record or not record.threads:
            return cache_utility.EmptyCacheView()

        threads = map(self._guild_thread_entries.__getitem__, record.threads)
        channel = snowflakes.Snowflake(channel)
        return cache_utility.CacheMappingView({thread.id: thread for thread in threads if thread.parent_id == channel})

    @typing_extensions.override
    def set_thread(self, thread: channels_.GuildThreadChannel, /) -> None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_THREADS):
            return

        self._guild_thread_entries[thread.id] = copy.copy(thread)
        guild_record = self._get_or_create_guild_record(thread.guild_id)

        if guild_record.threads is None:
            guild_record.threads = collections.SnowflakeSet()

        guild_record.threads.add(thread.id)

    @typing_extensions.override
    def update_thread(
        self, thread: channels_.GuildThreadChannel, /
    ) -> tuple[channels_.GuildThreadChannel | None, channels_.GuildThreadChannel | None]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_THREADS):
            return None, None

        cached_thread = self.get_thread(thread.id)
        self.set_thread(thread)
        return cached_thread, self.get_thread(thread.id)

    @typing_extensions.override
    def clear_guild_channels(self) -> cache.CacheView[snowflakes.Snowflake, channels_.PermissibleGuildChannel]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_CHANNELS):
            return cache_utility.EmptyCacheView()

        cached_channels = self._guild_channel_entries
        self._guild_channel_entries = collections.FreezableDict()

        for guild_id, guild_record in self._guild_entries.freeze().items():
            if guild_record.channels:
                guild_record.channels = None
                self._remove_guild_record_if_empty(guild_id, guild_record)

        return cache_utility.CacheMappingView(cached_channels)

    @typing_extensions.override
    def clear_guild_channels_for_guild(
        self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /
    ) -> cache.CacheView[snowflakes.Snowflake, channels_.PermissibleGuildChannel]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_CHANNELS):
            return cache_utility.EmptyCacheView()

        guild_id = snowflakes.Snowflake(guild)
        guild_record = self._guild_entries.get(guild_id)
        if not guild_record or not guild_record.channels:
            return cache_utility.EmptyCacheView()

        cached_channels = {sf: self._guild_channel_entries.pop(sf) for sf in guild_record.channels}
        guild_record.channels = None
        self._remove_guild_record_if_empty(guild_id, guild_record)
        return cache_utility.CacheMappingView(cached_channels)

    @typing_extensions.override
    def delete_guild_channel(
        self, channel: snowflakes.SnowflakeishOr[channels_.PartialChannel], /
    ) -> channels_.PermissibleGuildChannel | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_CHANNELS):
            return None

        channel_id = snowflakes.Snowflake(channel)
        channel = self._guild_channel_entries.pop(channel_id, None)

        if not channel:
            return None

        guild_record = self._guild_entries.get(channel.guild_id)
        if guild_record and guild_record.channels:
            guild_record.channels.remove(channel_id)
            if not guild_record.channels:
                guild_record.channels = None
                self._remove_guild_record_if_empty(channel.guild_id, guild_record)

        return channel

    @typing_extensions.override
    def get_guild_channel(
        self, channel: snowflakes.SnowflakeishOr[channels_.PartialChannel], /
    ) -> channels_.PermissibleGuildChannel | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_CHANNELS):
            return None

        channel = self._guild_channel_entries.get(snowflakes.Snowflake(channel))
        return cache_utility.copy_guild_channel(channel) if channel else None

    @typing_extensions.override
    def get_guild_channels_view(self) -> cache.CacheView[snowflakes.Snowflake, channels_.PermissibleGuildChannel]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_CHANNELS):
            return cache_utility.EmptyCacheView()

        return cache_utility.CacheMappingView(
            self._guild_channel_entries.freeze(),
            builder=cache_utility.copy_guild_channel,  # type: ignore[type-var]
        )

    @typing_extensions.override
    def get_guild_channels_view_for_guild(
        self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /
    ) -> cache.CacheView[snowflakes.Snowflake, channels_.PermissibleGuildChannel]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_CHANNELS):
            return cache_utility.EmptyCacheView()

        guild_record = self._guild_entries.get(snowflakes.Snowflake(guild))
        if not guild_record or not guild_record.channels:
            return cache_utility.EmptyCacheView()

        cached_channels = {sf: self._guild_channel_entries[sf] for sf in guild_record.channels}

        def sorter(args: tuple[snowflakes.Snowflake, channels_.PermissibleGuildChannel]) -> tuple[int, int, int]:
            channel = args[1]
            if isinstance(channel, channels_.GuildCategory):
                return channel.position, -1, 0

            parent_position = -1 if channel.parent_id is None else cached_channels[channel.parent_id].position

            if not isinstance(channel, channels_.GuildVoiceChannel):
                return parent_position, 0, channel.position

            return parent_position, 1, channel.position

        cached_channels = dict(sorted(cached_channels.items(), key=sorter))
        return cache_utility.CacheMappingView(
            cached_channels,
            builder=cache_utility.copy_guild_channel,  # type: ignore[type-var]
        )

    @typing_extensions.override
    def set_guild_channel(self, channel: channels_.PermissibleGuildChannel, /) -> None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_CHANNELS):
            return

        self._guild_channel_entries[channel.id] = cache_utility.copy_guild_channel(channel)
        guild_record = self._get_or_create_guild_record(channel.guild_id)

        if guild_record.channels is None:
            guild_record.channels = collections.SnowflakeSet()

        guild_record.channels.add(channel.id)

    @typing_extensions.override
    def update_guild_channel(
        self, channel: channels_.PermissibleGuildChannel, /
    ) -> tuple[channels_.PermissibleGuildChannel | None, channels_.PermissibleGuildChannel | None]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.GUILD_CHANNELS):
            return None, None

        cached_channel = self.get_guild_channel(channel.id)
        self.set_guild_channel(channel)
        return cached_channel, self.get_guild_channel(channel.id)

    def _build_invite(self, invite_data: cache_utility.InviteData) -> invites.InviteWithMetadata:
        return invite_data.build_entity(self._app)

    def _remove_invite_users(self, invite: cache_utility.InviteData) -> None:
        if invite.inviter:
            self._garbage_collect_user(invite.inviter, decrement=1)

        if invite.target_user:
            self._garbage_collect_user(invite.target_user, decrement=1)

    @typing_extensions.override
    def clear_invites(self) -> cache.CacheView[str, invites.InviteWithMetadata]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.INVITES):
            return cache_utility.EmptyCacheView()

        cached_invites = self._invite_entries
        self._invite_entries = collections.FreezableDict()

        for invite_data in cached_invites.values():
            self._remove_invite_users(invite_data)

        for guild_id, guild_record in self._guild_entries.freeze().items():
            if guild_record.invites:
                guild_record.invites = None
                self._remove_guild_record_if_empty(guild_id, guild_record)

        return cache_utility.CacheMappingView(cached_invites, builder=self._build_invite)

    @typing_extensions.override
    def clear_invites_for_guild(
        self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /
    ) -> cache.CacheView[str, invites.InviteWithMetadata]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.INVITES):
            return cache_utility.EmptyCacheView()

        guild_id = snowflakes.Snowflake(guild)
        guild_record = self._guild_entries.get(guild_id)

        if not guild_record or not guild_record.invites:
            return cache_utility.EmptyCacheView()

        cached_invites = {invite_code: self._invite_entries.pop(invite_code) for invite_code in guild_record.invites}
        guild_record.invites = None
        self._remove_guild_record_if_empty(guild_id, guild_record)

        for invite_data in cached_invites.values():
            self._remove_invite_users(invite_data)

        return cache_utility.CacheMappingView(cached_invites, builder=self._build_invite)

    @typing_extensions.override
    def clear_invites_for_channel(
        self,
        guild: snowflakes.SnowflakeishOr[guilds.PartialGuild],
        channel: snowflakes.SnowflakeishOr[channels_.PartialChannel],
        /,
    ) -> cache.CacheView[str, invites.InviteWithMetadata]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.INVITES):
            return cache_utility.EmptyCacheView()

        guild_id = snowflakes.Snowflake(guild)
        channel_id = snowflakes.Snowflake(channel)
        guild_record = self._guild_entries.get(guild_id)
        if not guild_record or not guild_record.invites:
            return cache_utility.EmptyCacheView()

        cached_invites = {}

        for code in tuple(guild_record.invites):
            invite_data = self._invite_entries[code]
            if invite_data.channel_id != channel_id:
                continue

            cached_invites[code] = invite_data
            del self._invite_entries[code]
            guild_record.invites.remove(code)
            self._remove_invite_users(invite_data)

        if not guild_record.invites:
            guild_record.invites = None
            self._remove_guild_record_if_empty(guild_id, guild_record)

        return cache_utility.CacheMappingView(cached_invites, builder=self._build_invite)

    @typing_extensions.override
    def delete_invite(self, code: invites.InviteCode | str, /) -> invites.InviteWithMetadata | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.INVITES):
            return None

        code = code if isinstance(code, str) else code.code
        invite_data = self._invite_entries.pop(code, None)
        if not invite_data:
            return None

        self._remove_invite_users(invite_data)

        if invite_data.guild_id is not None:  # TODO: test case when this is None?
            guild_record = self._guild_entries.get(invite_data.guild_id)
            if guild_record and guild_record.invites:
                guild_record.invites.remove(code)

                if not guild_record.invites:
                    guild_record.invites = None  # TODO: test when this is set to None
                    self._remove_guild_record_if_empty(invite_data.guild_id, guild_record)

        return self._build_invite(invite_data)

    @typing_extensions.override
    def get_invite(self, code: invites.InviteCode | str, /) -> invites.InviteWithMetadata | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.INVITES):
            return None

        code = code if isinstance(code, str) else code.code
        invite_data = self._invite_entries.get(code)
        return self._build_invite(invite_data) if invite_data else None

    @typing_extensions.override
    def get_invites_view(self) -> cache.CacheView[str, invites.InviteWithMetadata]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.INVITES):
            return cache_utility.EmptyCacheView()

        return cache_utility.CacheMappingView(self._invite_entries.freeze(), builder=self._build_invite)

    @typing_extensions.override
    def get_invites_view_for_guild(
        self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /
    ) -> cache.CacheView[str, invites.InviteWithMetadata]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.INVITES):
            return cache_utility.EmptyCacheView()

        guild_id = snowflakes.Snowflake(guild)
        guild_entry = self._guild_entries.get(guild_id)
        if not guild_entry or not guild_entry.invites:
            return cache_utility.EmptyCacheView()

        cached_invites = {code: self._invite_entries[code] for code in guild_entry.invites}
        return cache_utility.CacheMappingView(cached_invites, builder=self._build_invite)

    @typing_extensions.override
    def get_invites_view_for_channel(
        self,
        guild: snowflakes.SnowflakeishOr[guilds.PartialGuild],
        channel: snowflakes.SnowflakeishOr[channels_.PartialChannel],
        /,
    ) -> cache.CacheView[str, invites.InviteWithMetadata]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.INVITES):
            return cache_utility.EmptyCacheView()

        guild_id = snowflakes.Snowflake(guild)
        channel_id = snowflakes.Snowflake(channel)
        guild_entry = self._guild_entries.get(guild_id)
        if not guild_entry or not guild_entry.invites:
            return cache_utility.EmptyCacheView()

        cached_invites = {
            invite.code: invite
            for invite in map(self._invite_entries.get, guild_entry.invites)
            if invite and invite.channel_id == channel_id
        }
        return cache_utility.CacheMappingView(cached_invites, builder=self._build_invite)

    @typing_extensions.override
    def set_invite(self, invite: invites.InviteWithMetadata, /) -> None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.INVITES):
            return

        inviter: cache_utility.RefCell[users.User] | None = None
        if invite.inviter:
            inviter = self._set_user(invite.inviter)
            if invite.code not in self._invite_entries:
                self._increment_ref_count(inviter)

        target_user: cache_utility.RefCell[users.User] | None = None
        if invite.target_user:
            target_user = self._set_user(invite.target_user)
            if invite.code not in self._invite_entries:
                self._increment_ref_count(target_user)

        self._invite_entries[invite.code] = cache_utility.InviteData.build_from_entity(
            invite, inviter=inviter, target_user=target_user
        )
        if invite.guild_id:
            guild_entry = self._get_or_create_guild_record(invite.guild_id)

            if guild_entry.invites is None:
                guild_entry.invites = []

            guild_entry.invites.append(invite.code)

    @typing_extensions.override
    def update_invite(
        self, invite: invites.InviteWithMetadata, /
    ) -> tuple[invites.InviteWithMetadata | None, invites.InviteWithMetadata | None]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.INVITES):
            return None, None

        cached_invite = self.get_invite(invite.code)
        self.set_invite(invite)
        return cached_invite, self.get_invite(invite.code)

    @typing_extensions.override
    def delete_me(self) -> users.OwnUser | None:
        cached_user = self._me
        self._me = None
        return cached_user

    @typing_extensions.override
    def get_me(self) -> users.OwnUser | None:
        return copy.copy(self._me)

    @typing_extensions.override
    def set_me(self, user: users.OwnUser, /) -> None:
        if self._is_cache_enabled_for(config_api.CacheComponents.ME):
            _LOGGER.debug("setting my user to %s", user)
            self._me = copy.copy(user)

    @typing_extensions.override
    def update_me(self, user: users.OwnUser, /) -> tuple[users.OwnUser | None, users.OwnUser | None]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.ME):
            return None, None

        cached_user = self.get_me()
        self.set_me(user)
        return cached_user, self.get_me()

    def _build_member(self, member_data: cache_utility.RefCell[cache_utility.MemberData]) -> guilds.Member:
        return member_data.object.build_entity(self._app)

    @staticmethod
    def _can_remove_member(member: cache_utility.RefCell[cache_utility.MemberData]) -> bool:
        return member.ref_count < 1 and member.object.has_been_deleted

    def _garbage_collect_member(
        self,
        guild_record: cache_utility.GuildRecord,
        member: cache_utility.RefCell[cache_utility.MemberData],
        *,
        decrement: int | None = None,
        deleting: bool = False,
    ) -> cache_utility.RefCell[cache_utility.MemberData] | None:
        if deleting:
            member.object.has_been_deleted = True

        if decrement is not None:
            self._increment_ref_count(member, -decrement)

        user_id = member.object.user.object.id
        if not guild_record.members or user_id not in guild_record.members:
            return None

        if not self._can_remove_member(member):
            return None

        del guild_record.members[user_id]
        self._garbage_collect_user(member.object.user, decrement=1)

        if not guild_record.members:
            guild_record.members = None
            self._remove_guild_record_if_empty(member.object.guild_id, guild_record)

        return member

    @typing_extensions.override
    def clear_members(
        self,
    ) -> cache.CacheView[snowflakes.Snowflake, cache.CacheView[snowflakes.Snowflake, guilds.Member]]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.MEMBERS):
            return cache_utility.EmptyCacheView()

        views = ((guild_id, self.clear_members_for_guild(guild_id)) for guild_id in self._guild_entries.freeze())
        return cache_utility.CacheMappingView({guild_id: view for guild_id, view in views if view})

    @typing_extensions.override
    def clear_members_for_guild(
        self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /
    ) -> cache.CacheView[snowflakes.Snowflake, guilds.Member]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.MEMBERS):
            return cache_utility.EmptyCacheView()

        guild_id = snowflakes.Snowflake(guild)
        guild_record = self._guild_entries.get(guild_id)
        if not guild_record or not guild_record.members:
            return cache_utility.EmptyCacheView()

        cached_members = guild_record.members.freeze()
        members_gen = (self._garbage_collect_member(guild_record, m, deleting=True) for m in cached_members.values())
        # _garbage_collect_member will only return the member data object if they could be removed, else None.
        cached_members = {member.object.user.object.id: member for member in members_gen if member}
        self._remove_guild_record_if_empty(guild_id, guild_record)
        return cache_utility.CacheMappingView(cached_members, builder=self._build_member)  # type: ignore[type-var]

    @typing_extensions.override
    def delete_member(
        self,
        guild: snowflakes.SnowflakeishOr[guilds.PartialGuild],
        user: snowflakes.SnowflakeishOr[users.PartialUser],
        /,
    ) -> guilds.Member | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.MEMBERS):
            return None

        guild_id = snowflakes.Snowflake(guild)
        user_id = snowflakes.Snowflake(user)
        guild_record = self._guild_entries.get(guild_id)
        if not guild_record or not guild_record.members:
            return None

        member_data = guild_record.members.get(user_id)
        if not member_data:
            return None

        if not guild_record.members:
            guild_record.members = None
            self._remove_guild_record_if_empty(guild_id, guild_record)

        # _garbage_collect_member will only return the member data object if they could be removed, else None.
        garbage_collected = self._garbage_collect_member(guild_record, member_data, deleting=True)
        return self._build_member(member_data) if garbage_collected else None

    @typing_extensions.override
    def get_member(
        self,
        guild: snowflakes.SnowflakeishOr[guilds.PartialGuild],
        user: snowflakes.SnowflakeishOr[users.PartialUser],
        /,
    ) -> guilds.Member | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.MEMBERS):
            return None

        guild_id = snowflakes.Snowflake(guild)
        user_id = snowflakes.Snowflake(user)
        guild_record = self._guild_entries.get(guild_id)
        if not guild_record or not guild_record.members:
            return None

        member = guild_record.members.get(user_id)
        return self._build_member(member) if member else None

    @typing_extensions.override
    def get_members_view(
        self,
    ) -> cache.CacheView[snowflakes.Snowflake, cache.CacheView[snowflakes.Snowflake, guilds.Member]]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.MEMBERS):
            return cache_utility.EmptyCacheView()

        views: typing.Mapping[snowflakes.Snowflake, cache.CacheView[snowflakes.Snowflake, guilds.Member]] = {
            guild_id: cache_utility.CacheMappingView(view.members.freeze(), builder=self._build_member)  # type: ignore[type-var]
            for guild_id, view in self._guild_entries.items()
            if view.members
        }
        return cache_utility.Cache3DMappingView(views)

    @typing_extensions.override
    def get_members_view_for_guild(
        self, guild_id: snowflakes.Snowflakeish, /
    ) -> cache.CacheView[snowflakes.Snowflake, guilds.Member]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.MEMBERS):
            return cache_utility.EmptyCacheView()

        guild_id = snowflakes.Snowflake(guild_id)
        guild_record = self._guild_entries.get(guild_id)
        if not guild_record or not guild_record.members:
            return cache_utility.EmptyCacheView()

        cached_members = {
            user_id: member for user_id, member in guild_record.members.items() if not member.object.has_been_deleted
        }

        return cache_utility.CacheMappingView(cached_members, builder=self._build_member)  # type: ignore[type-var]

    @typing_extensions.override
    def set_member(self, member: guilds.Member, /) -> None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.MEMBERS):
            return

        self._set_member(member, is_reference=False)

    def _set_member(
        self, member: guilds.Member, /, *, is_reference: bool = True
    ) -> cache_utility.RefCell[cache_utility.MemberData]:
        guild_record = self._get_or_create_guild_record(member.guild_id)
        user = self._set_user(member.user)
        member_data = cache_utility.MemberData.build_from_entity(member, user=user)

        if guild_record.members is None:  # TODO: test when this is not None
            guild_record.members = collections.FreezableDict()

        if member.user.id not in guild_record.members:
            self._increment_ref_count(member_data.user)

        try:
            member_data.has_been_deleted = False
            if is_reference:
                member_data.has_been_deleted = guild_record.members[member.id].object.has_been_deleted

            guild_record.members[member.id].object = member_data

        except KeyError:
            member_data.has_been_deleted = is_reference
            guild_record.members[member.id] = cache_utility.RefCell(member_data)

        return guild_record.members[member.id]

    @typing_extensions.override
    def update_member(self, member: guilds.Member, /) -> tuple[guilds.Member | None, guilds.Member | None]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.MEMBERS):
            return None, None

        cached_member = self.get_member(member.guild_id, member.user.id)
        self.set_member(member)
        return cached_member, self.get_member(member.guild_id, member.user.id)

    def _build_presence(self, presence_data: cache_utility.MemberPresenceData) -> presences.MemberPresence:
        return presence_data.build_entity(self._app)

    def _garbage_collect_unknown_custom_emoji(
        self, emoji: cache_utility.RefCell[emojis.CustomEmoji], *, decrement: int | None = None
    ) -> None:
        if decrement is not None:
            self._increment_ref_count(emoji, -decrement)

        if emoji.ref_count < 1 and emoji.object.id in self._unknown_custom_emoji_entries:
            del self._unknown_custom_emoji_entries[emoji.object.id]

    def _remove_presence_assets(self, presence_data: cache_utility.MemberPresenceData) -> None:
        for activity_data in presence_data.activities:
            if isinstance(activity_data.emoji, cache_utility.RefCell):
                self._garbage_collect_unknown_custom_emoji(activity_data.emoji, decrement=1)

    @typing_extensions.override
    def clear_presences(
        self,
    ) -> cache.CacheView[snowflakes.Snowflake, cache.CacheView[snowflakes.Snowflake, presences.MemberPresence]]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.PRESENCES):
            return cache_utility.EmptyCacheView()

        views = ((guild_id, self.clear_presences_for_guild(guild_id)) for guild_id in self._guild_entries.freeze())
        return cache_utility.CacheMappingView({guild_id: view for guild_id, view in views if view})

    @typing_extensions.override
    def clear_presences_for_guild(
        self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /
    ) -> cache.CacheView[snowflakes.Snowflake, presences.MemberPresence]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.PRESENCES):
            return cache_utility.EmptyCacheView()

        guild_id = snowflakes.Snowflake(guild)
        guild_record = self._guild_entries.get(guild_id)
        if not guild_record or not guild_record.presences:
            return cache_utility.EmptyCacheView()

        cached_presences = guild_record.presences
        guild_record.presences = None

        for presence in cached_presences.values():
            self._remove_presence_assets(presence)

        self._remove_guild_record_if_empty(guild_id, guild_record)
        return cache_utility.CacheMappingView(cached_presences, builder=self._build_presence)

    @typing_extensions.override
    def delete_presence(
        self,
        guild: snowflakes.SnowflakeishOr[guilds.PartialGuild],
        user: snowflakes.SnowflakeishOr[users.PartialUser],
        /,
    ) -> presences.MemberPresence | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.PRESENCES):
            return None

        guild_id = snowflakes.Snowflake(guild)
        user_id = snowflakes.Snowflake(user)
        guild_record = self._guild_entries.get(guild_id)
        if not guild_record or not guild_record.presences:
            return None

        presence_data = guild_record.presences.pop(user_id, None)

        if not presence_data:
            return None

        self._remove_presence_assets(presence_data)

        if not guild_record.presences:
            guild_record.presences = None
            self._remove_guild_record_if_empty(guild_id, guild_record)

        return self._build_presence(presence_data)

    @typing_extensions.override
    def get_presence(
        self,
        guild: snowflakes.SnowflakeishOr[guilds.PartialGuild],
        user: snowflakes.SnowflakeishOr[users.PartialUser],
        /,
    ) -> presences.MemberPresence | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.PRESENCES):
            return None

        guild_id = snowflakes.Snowflake(guild)
        user_id = snowflakes.Snowflake(user)
        guild_record = self._guild_entries.get(guild_id)
        if not guild_record or not guild_record.presences:
            return None

        return self._build_presence(guild_record.presences[user_id]) if user_id in guild_record.presences else None

    @typing_extensions.override
    def get_presences_view(
        self,
    ) -> cache.CacheView[snowflakes.Snowflake, cache.CacheView[snowflakes.Snowflake, presences.MemberPresence]]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.PRESENCES):
            return cache_utility.EmptyCacheView()

        views = {
            guild_id: cache_utility.CacheMappingView(guild_record.presences.freeze(), builder=self._build_presence)
            for guild_id, guild_record in self._guild_entries.items()
            if guild_record.presences
        }
        return cache_utility.Cache3DMappingView(views)

    @typing_extensions.override
    def get_presences_view_for_guild(
        self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /
    ) -> cache.CacheView[snowflakes.Snowflake, presences.MemberPresence]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.PRESENCES):
            return cache_utility.EmptyCacheView()

        guild_record = self._guild_entries.get(snowflakes.Snowflake(guild))
        if not guild_record or not guild_record.presences:
            return cache_utility.EmptyCacheView()

        return cache_utility.CacheMappingView(guild_record.presences.freeze(), builder=self._build_presence)

    @typing_extensions.override
    def set_presence(self, presence: presences.MemberPresence, /) -> None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.PRESENCES):
            return

        presence_data = cache_utility.MemberPresenceData.build_from_entity(presence)
        for activity, activity_data in zip(presence.activities, presence_data.activities):
            emoji = activity.emoji
            if not isinstance(emoji, emojis.CustomEmoji):
                continue

            if emoji.id in self._unknown_custom_emoji_entries:
                self._unknown_custom_emoji_entries[emoji.id].object = copy.copy(emoji)
                emoji_data = self._unknown_custom_emoji_entries[emoji.id]

            else:
                emoji_data = cache_utility.RefCell(copy.copy(emoji))
                self._unknown_custom_emoji_entries[emoji.id] = emoji_data

            self._increment_ref_count(emoji_data)
            activity_data.emoji = emoji_data

        guild_record = self._get_or_create_guild_record(presence.guild_id)
        if guild_record.presences is None:
            guild_record.presences = collections.FreezableDict()

        guild_record.presences[presence.user_id] = presence_data

    @typing_extensions.override
    def update_presence(
        self, presence: presences.MemberPresence, /
    ) -> tuple[presences.MemberPresence | None, presences.MemberPresence | None]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.PRESENCES):
            return None, None

        cached_presence = self.get_presence(presence.guild_id, presence.user_id)
        self.set_presence(presence)
        return cached_presence, self.get_presence(presence.guild_id, presence.user_id)

    @typing_extensions.override
    def clear_roles(self) -> cache.CacheView[snowflakes.Snowflake, guilds.Role]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.ROLES) or not self._role_entries:
            return cache_utility.EmptyCacheView()

        roles = self._role_entries
        self._role_entries = collections.FreezableDict()

        for guild_id, guild_record in self._guild_entries.freeze().items():
            if guild_record.roles:  # TODO: test coverage for when not this
                guild_record.roles = None
                self._remove_guild_record_if_empty(guild_id, guild_record)

        return cache_utility.CacheMappingView(roles)

    @typing_extensions.override
    def clear_roles_for_guild(
        self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /
    ) -> cache.CacheView[snowflakes.Snowflake, guilds.Role]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.ROLES):
            return cache_utility.EmptyCacheView()

        guild_id = snowflakes.Snowflake(guild)
        guild_record = self._guild_entries.get(guild_id)
        if not guild_record or not guild_record.roles:
            return cache_utility.EmptyCacheView()

        view = cache_utility.CacheMappingView(
            {role_id: self._role_entries.pop(role_id) for role_id in guild_record.roles}
        )
        guild_record.roles = None
        self._remove_guild_record_if_empty(guild_id, guild_record)
        return view

    @typing_extensions.override
    def delete_role(self, role: snowflakes.SnowflakeishOr[guilds.PartialRole], /) -> guilds.Role | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.ROLES):
            return None

        role_id = snowflakes.Snowflake(role)
        role = self._role_entries.pop(role_id, None)
        if not role:
            return None

        guild_record = self._guild_entries.get(role.guild_id)
        if guild_record and guild_record.roles:
            guild_record.roles.remove(role_id)

            if not guild_record.roles:
                guild_record.roles = None
                self._remove_guild_record_if_empty(role.guild_id, guild_record)

        return role

    @typing_extensions.override
    def get_role(self, role: snowflakes.SnowflakeishOr[guilds.PartialRole], /) -> guilds.Role | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.ROLES):
            return None

        role = self._role_entries.get(snowflakes.Snowflake(role))
        return copy.copy(role) if role else None

    @typing_extensions.override
    def get_roles_view(self) -> cache.CacheView[snowflakes.Snowflake, guilds.Role]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.ROLES):
            return cache_utility.EmptyCacheView()

        return cache_utility.CacheMappingView(self._role_entries.freeze())

    @typing_extensions.override
    def get_roles_view_for_guild(
        self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /
    ) -> cache.CacheView[snowflakes.Snowflake, guilds.Role]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.ROLES):
            return cache_utility.EmptyCacheView()

        guild_record = self._guild_entries.get(snowflakes.Snowflake(guild))
        if not guild_record or not guild_record.roles:
            return cache_utility.EmptyCacheView()

        return cache_utility.CacheMappingView({role_id: self._role_entries[role_id] for role_id in guild_record.roles})

    @typing_extensions.override
    def set_role(self, role: guilds.Role, /) -> None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.ROLES):
            return

        self._role_entries[role.id] = role
        guild_record = self._get_or_create_guild_record(role.guild_id)

        if guild_record.roles is None:  # TODO: test when this is not None
            guild_record.roles = collections.SnowflakeSet()

        guild_record.roles.add(role.id)

    @typing_extensions.override
    def update_role(self, role: guilds.Role, /) -> tuple[guilds.Role | None, guilds.Role | None]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.ROLES):
            return None, None

        cached_role = self.get_role(role.id)
        self.set_role(role)
        return cached_role, self.get_role(role.id)

    @staticmethod
    def _can_remove_user(user_data: cache_utility.RefCell[users.User]) -> bool:
        return user_data.ref_count < 1

    def _garbage_collect_user(self, user: cache_utility.RefCell[users.User], *, decrement: int | None = None) -> None:
        if decrement is not None:
            self._increment_ref_count(user, -decrement)

        if self._can_remove_user(user) and user.object.id in self._user_entries:
            del self._user_entries[user.object.id]
            self._dm_channel_entries.pop(user.object.id, None)

    @typing_extensions.override
    def get_user(self, user: snowflakes.SnowflakeishOr[users.PartialUser], /) -> users.User | None:
        user = self._user_entries.get(snowflakes.Snowflake(user))
        return user.copy() if user else None

    @typing_extensions.override
    def get_users_view(self) -> cache.CacheView[snowflakes.Snowflake, users.User]:
        if not self._user_entries:
            return cache_utility.EmptyCacheView()

        cached_users = self._user_entries.freeze()
        unwrapper = typing.cast(
            "typing.Callable[[cache_utility.RefCell[users.User]], users.User]", cache_utility.unwrap_ref_cell
        )
        return cache_utility.CacheMappingView(cached_users, builder=unwrapper)  # type: ignore[type-var]

    def _set_user(self, user: users.User, /) -> cache_utility.RefCell[users.User]:
        try:
            self._user_entries[user.id].object = copy.copy(user)
            cell = self._user_entries[user.id]
        except KeyError:
            cell = cache_utility.RefCell(copy.copy(user))
            self._user_entries[user.id] = cell

        return cell

    def _build_voice_state(self, voice_data: cache_utility.VoiceStateData) -> voices.VoiceState:
        return voice_data.build_entity(self._app)

    @typing_extensions.override
    def clear_voice_states(
        self,
    ) -> cache.CacheView[snowflakes.Snowflake, cache.CacheView[snowflakes.Snowflake, voices.VoiceState]]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.VOICE_STATES):
            return cache_utility.EmptyCacheView()

        views = ((guild_id, self.clear_voice_states_for_guild(guild_id)) for guild_id in self._guild_entries.freeze())
        return cache_utility.CacheMappingView({guild_id: view for guild_id, view in views if view})

    @typing_extensions.override
    def clear_voice_states_for_channel(
        self,
        guild: snowflakes.SnowflakeishOr[guilds.PartialGuild],
        channel: snowflakes.SnowflakeishOr[channels_.PartialChannel],
        /,
    ) -> cache.CacheView[snowflakes.Snowflake, voices.VoiceState]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.VOICE_STATES):
            return cache_utility.EmptyCacheView()

        guild_id = snowflakes.Snowflake(guild)
        channel_id = snowflakes.Snowflake(channel)
        guild_record = self._guild_entries.get(guild_id)
        if not guild_record or not guild_record.voice_states:
            return cache_utility.EmptyCacheView()

        cached_voice_states = {}

        for user_id, voice_state in guild_record.voice_states.items():
            if voice_state.channel_id == channel_id:
                cached_voice_states[user_id] = voice_state
                if voice_state.member:
                    self._garbage_collect_member(guild_record, voice_state.member, decrement=1)

        if not guild_record.voice_states:
            guild_record.voice_states = None
            self._remove_guild_record_if_empty(guild_id, guild_record)

        return cache_utility.CacheMappingView(cached_voice_states, builder=self._build_voice_state)

    @typing_extensions.override
    def clear_voice_states_for_guild(
        self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /
    ) -> cache.CacheView[snowflakes.Snowflake, voices.VoiceState]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.VOICE_STATES):
            return cache_utility.EmptyCacheView()

        guild_id = snowflakes.Snowflake(guild)
        guild_record = self._guild_entries.get(guild_id)

        if not guild_record or not guild_record.voice_states:
            return cache_utility.EmptyCacheView()

        cached_voice_states = guild_record.voice_states
        guild_record.voice_states = None

        for voice_state in cached_voice_states.values():
            if voice_state.member:
                self._garbage_collect_member(guild_record, voice_state.member, decrement=1)

        self._remove_guild_record_if_empty(guild_id, guild_record)
        return cache_utility.CacheMappingView(cached_voice_states, builder=self._build_voice_state)

    @typing_extensions.override
    def delete_voice_state(
        self,
        guild: snowflakes.SnowflakeishOr[guilds.PartialGuild],
        user: snowflakes.SnowflakeishOr[users.PartialUser],
        /,
    ) -> voices.VoiceState | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.VOICE_STATES):
            return None

        guild_id = snowflakes.Snowflake(guild)
        user_id = snowflakes.Snowflake(user)
        guild_record = self._guild_entries.get(guild_id)
        if not guild_record or not guild_record.voice_states:
            return None

        voice_state_data = guild_record.voice_states.pop(user_id, None)
        if not voice_state_data:
            return None

        if not guild_record.voice_states:
            guild_record.voice_states = None

        if voice_state_data.member:
            self._garbage_collect_member(guild_record, voice_state_data.member, decrement=1)

        self._remove_guild_record_if_empty(guild_id, guild_record)
        return self._build_voice_state(voice_state_data)

    @typing_extensions.override
    def get_voice_state(
        self,
        guild: snowflakes.SnowflakeishOr[guilds.PartialGuild],
        user: snowflakes.SnowflakeishOr[users.PartialUser],
        /,
    ) -> voices.VoiceState | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.VOICE_STATES):
            return None

        guild_id = snowflakes.Snowflake(guild)
        user_id = snowflakes.Snowflake(user)
        guild_record = self._guild_entries.get(guild_id)
        voice_data = guild_record.voice_states.get(user_id) if guild_record and guild_record.voice_states else None
        return self._build_voice_state(voice_data) if voice_data else None

    @typing_extensions.override
    def get_voice_states_view(
        self,
    ) -> cache.CacheView[snowflakes.Snowflake, cache.CacheView[snowflakes.Snowflake, voices.VoiceState]]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.VOICE_STATES):
            return cache_utility.EmptyCacheView()

        views = {
            guild_id: cache_utility.CacheMappingView(
                guild_record.voice_states.freeze(), builder=self._build_voice_state
            )
            for guild_id, guild_record in self._guild_entries.items()
            if guild_record.voice_states
        }
        return cache_utility.Cache3DMappingView(views)

    @typing_extensions.override
    def get_voice_states_view_for_channel(
        self,
        guild: snowflakes.SnowflakeishOr[guilds.PartialGuild],
        channel: snowflakes.SnowflakeishOr[channels_.PartialChannel],
        /,
    ) -> cache.CacheView[snowflakes.Snowflake, voices.VoiceState]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.VOICE_STATES):
            return cache_utility.EmptyCacheView()

        guild_id = snowflakes.Snowflake(guild)
        channel_id = snowflakes.Snowflake(channel)
        guild_record = self._guild_entries.get(guild_id)
        if not guild_record or not guild_record.voice_states:
            return cache_utility.EmptyCacheView()

        cached_voice_states = {
            user_id: voice_state
            for user_id, voice_state in guild_record.voice_states.items()
            if voice_state.channel_id == channel_id
        }

        return cache_utility.CacheMappingView(cached_voice_states, builder=self._build_voice_state)

    @typing_extensions.override
    def get_voice_states_view_for_guild(
        self, guild: snowflakes.SnowflakeishOr[guilds.PartialGuild], /
    ) -> cache.CacheView[snowflakes.Snowflake, voices.VoiceState]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.VOICE_STATES):
            return cache_utility.EmptyCacheView()

        guild_record = self._guild_entries.get(snowflakes.Snowflake(guild))
        if not guild_record or not guild_record.voice_states:
            return cache_utility.EmptyCacheView()

        return cache_utility.CacheMappingView(guild_record.voice_states.freeze(), builder=self._build_voice_state)

    @typing_extensions.override
    def set_voice_state(self, voice_state: voices.VoiceState, /) -> None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.VOICE_STATES):
            return

        guild_record = self._get_or_create_guild_record(voice_state.guild_id)

        if guild_record.voice_states is None:  # TODO: test when this is not None
            guild_record.voice_states = collections.FreezableDict()

        # If the member is missing for some reason, try our best to find
        # it and store it along side the voice state
        if voice_state.member:
            member = self._set_member(voice_state.member)
            if voice_state.user_id not in guild_record.voice_states:
                self._increment_ref_count(member)
        else:
            member = None

        voice_state_data = cache_utility.VoiceStateData.build_from_entity(voice_state, member=member)

        guild_record.voice_states[voice_state.user_id] = voice_state_data

    @typing_extensions.override
    def update_voice_state(
        self, voice_state: voices.VoiceState, /
    ) -> tuple[voices.VoiceState | None, voices.VoiceState | None]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.VOICE_STATES):
            return None, None

        cached_voice_state = self.get_voice_state(voice_state.guild_id, voice_state.user_id)
        self.set_voice_state(voice_state)
        return cached_voice_state, self.get_voice_state(voice_state.guild_id, voice_state.user_id)

    def _build_message(self, message_data: cache_utility.RefCell[cache_utility.MessageData]) -> messages.Message:
        return message_data.object.build_entity(self._app)

    def _can_remove_message(self, message: cache_utility.RefCell[cache_utility.MessageData]) -> bool:
        return message.object.id not in self._message_entries and message.ref_count < 1

    def _garbage_collect_message(
        self,
        message: cache_utility.RefCell[cache_utility.MessageData],
        *,
        decrement: int | None = None,
        override_ref: bool = False,
    ) -> cache_utility.RefCell[cache_utility.MessageData] | None:
        if decrement is not None:
            self._increment_ref_count(message, -decrement)

        if not self._can_remove_message(message) or override_ref:
            return None

        self._garbage_collect_user(message.object.author, decrement=1)

        if message.object.member:
            guild_record = self._guild_entries.get(message.object.member.object.guild_id)
            if guild_record:
                self._garbage_collect_member(guild_record, message.object.member, decrement=1)

        if message.object.referenced_message:
            self._garbage_collect_message(message.object.referenced_message, decrement=1)

        if message.object.user_mentions:
            for user in message.object.user_mentions.values():
                self._garbage_collect_user(user, decrement=1)

        # If we got this far the message won't be in _message_entries as that'd infer that it hasn't been marked as
        # deleted yet.
        if message.object.id in self._referenced_messages:
            del self._referenced_messages[message.object.id]

        return message

    def _on_message_expire(self, message: cache_utility.RefCell[cache_utility.MessageData], /) -> None:
        if not self._garbage_collect_message(message):
            self._referenced_messages[message.object.id] = message

    @typing_extensions.override
    def clear_messages(self) -> cache.CacheView[snowflakes.Snowflake, messages.Message]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.MESSAGES) or not self._message_entries:
            return cache_utility.EmptyCacheView()

        # As the only entry which references messages is other messages, this is enough for now.
        cached_messages = self._message_entries.freeze()
        self._message_entries.clear()
        cached_messages.update(self._referenced_messages)
        self._referenced_messages.clear()

        for message in cached_messages.values():
            self._garbage_collect_message(message, override_ref=True)

        return cache_utility.CacheMappingView(cached_messages, builder=self._build_message)  # type: ignore[type-var]

    @typing_extensions.override
    def delete_message(self, message: snowflakes.SnowflakeishOr[messages.PartialMessage], /) -> messages.Message | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.MESSAGES):
            return None

        message_id = snowflakes.Snowflake(message)
        message_data = self._message_entries.pop(message_id, None)

        if not message_data:
            return None

        if not self._garbage_collect_message(message_data):
            self._referenced_messages[message_id] = message_data
            return None

        return self._build_message(message_data)

    @typing_extensions.override
    def get_message(self, message: snowflakes.SnowflakeishOr[messages.PartialMessage], /) -> messages.Message | None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.MESSAGES):
            return None

        message_id = snowflakes.Snowflake(message)
        message_data = self._message_entries.get(message_id) or self._referenced_messages.get(message_id)
        return self._build_message(message_data) if message_data else None

    @typing_extensions.override
    def get_messages_view(self) -> cache.CacheView[snowflakes.Snowflake, messages.Message]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.MESSAGES):
            return cache_utility.EmptyCacheView()

        cached_messages = self._message_entries.freeze()
        cached_messages.update(self._referenced_messages)
        return cache_utility.CacheMappingView(cached_messages, builder=self._build_message)  # type: ignore[type-var]

    # We rather keep everything we can here inline.
    def _set_message(  # noqa: PLR0912
        self, message: messages.Message, /, *, is_reference: bool = True
    ) -> cache_utility.RefCell[cache_utility.MessageData]:
        author = self._set_user(message.author)
        member = self._set_member(message.member) if message.member else None

        user_mentions: undefined.UndefinedOr[
            typing.Mapping[snowflakes.Snowflake, cache_utility.RefCell[users.User]]
        ] = undefined.UNDEFINED
        if message.user_mentions is not undefined.UNDEFINED:
            user_mentions = {user_id: self._set_user(user) for user_id, user in message.user_mentions.items()}

        referenced_message: cache_utility.RefCell[cache_utility.MessageData] | None = None
        if message.referenced_message:
            reference_id = message.referenced_message.id
            referenced_message = self._message_entries.get(reference_id) or self._referenced_messages.get(reference_id)

            if referenced_message:
                # Since the message is partial, if we don't have it cached, there is nothing we can do about it
                referenced_message.object.update(message.referenced_message)

        # Only increment ref counts if this wasn't previously cached.
        if message.id not in self._referenced_messages and message.id not in self._message_entries:
            if member:
                self._increment_ref_count(member)

            if referenced_message:
                self._increment_ref_count(referenced_message)

            if user_mentions is not undefined.UNDEFINED:
                for user in user_mentions.values():
                    self._increment_ref_count(user)

        message_data = cache_utility.MessageData.build_from_entity(
            message, author=author, member=member, user_mentions=user_mentions, referenced_message=referenced_message
        )

        # Ensure any previously set message ref cell is in the right place before updating the cache.
        if not is_reference and message.id in self._referenced_messages:
            self._message_entries[message.id] = self._referenced_messages.pop(message.id)

        if message.id in self._message_entries:
            self._message_entries[message.id].object = message_data

        elif not is_reference:
            self._message_entries[message.id] = cache_utility.RefCell(message_data)

        elif message.id in self._referenced_messages:
            self._referenced_messages[message.id].object = message_data

        else:
            self._referenced_messages[message.id] = cache_utility.RefCell(message_data)

        return self._message_entries.get(message.id) or self._referenced_messages[message.id]

    @typing_extensions.override
    def set_message(self, message: messages.Message, /) -> None:
        if not self._is_cache_enabled_for(config_api.CacheComponents.MESSAGES):
            return

        self._set_message(message, is_reference=False)

    @typing_extensions.override
    def update_message(
        self, message: messages.PartialMessage | messages.Message, /
    ) -> tuple[messages.Message | None, messages.Message | None]:
        if not self._is_cache_enabled_for(config_api.CacheComponents.MESSAGES):
            return None, None

        cached_message = self.get_message(message.id)

        if isinstance(message, messages.Message):
            self.set_message(message)

        elif cached_message_data := self._message_entries.get(message.id) or self._referenced_messages.get(message.id):
            user_mentions: undefined.UndefinedOr[
                typing.Mapping[snowflakes.Snowflake, cache_utility.RefCell[users.User]]
            ] = undefined.UNDEFINED
            if message.user_mentions is not undefined.UNDEFINED:
                user_mentions = {user_id: self._set_user(user) for user_id, user in message.user_mentions.items()}

                # We want to ensure that any previously mentioned users are garbage collected if they're no longer
                # being mentioned.
                if cached_message_data.object.user_mentions is not undefined.UNDEFINED:
                    for user_id, user in cached_message_data.object.user_mentions.items():
                        if user_id not in user_mentions:
                            self._garbage_collect_user(user, decrement=1)

            cached_message_data.object.update(message, user_mentions=user_mentions)

        return cached_message, self.get_message(message.id)
