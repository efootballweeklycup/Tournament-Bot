"""eFootball Mobile Tournament Discord bot.

Run with:
    python bot.py

The Discord token is read only from the DISCORD_BOT_TOKEN environment variable.
"""
from __future__ import annotations
import io, asyncio
import logging
import os
import re
from dotenv import load_dotenv
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('efootball-tournament')
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Callable, Coroutine


async def _call_off_loop(callable_obj: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run blocking repository/helper work in a worker thread.

    TournamentStore is deliberately synchronous for SQLite/PostgreSQL portability.
    Discord interactions must never wait on a blocking database reconnect on the
    event loop, or the interaction can expire and appear as "application did not respond".
    """
    return await asyncio.to_thread(callable_obj, *args, **kwargs)


_store_call = _call_off_loop
from PIL import Image, ImageDraw, ImageFont, ImageOps
import discord
from discord import app_commands
from discord.ext import commands, tasks
from tournament import CLOSED, COMPLETED, DRAFT, GROUP_KNOCKOUT, GROUP_STAGE_DEADLINE_HOURS, QUARTERFINAL_DEADLINE_HOURS, SEMIFINAL_DEADLINE_HOURS, FINAL_DEADLINE_HOURS, GROUPS, GROUP_LETTERS, LEAGUE, OPEN, ROUND_ROBIN, TEMPLATES, TEMPLATE_PREMIER_LEAGUE, TEMPLATE_WEEKLY_CHAMPIONSHIP, TEMPLATE_CHAMPIONS_LEAGUE, TEMPLATE_WORLD_CUP_32, WORLD_CUP_TEMPLATE_IDS, CHAMPION_TITLE_KEYS, Standing, TournamentError, TournamentStore
TOKEN_ENV = 'DISCORD_BOT_TOKEN'
PARTICIPANT_ROLE = 'Participant'
MANAGER_ROLE = 'Manager'
PLAYOFF_QUALIFIED_ROLE = 'Playoff Qualified'
GROUP_ROLE_NAMES = {group: f'Group {group}' for group in GROUPS}
TOURNAMENT_ROLE_NAMES = (PARTICIPANT_ROLE, *GROUP_ROLE_NAMES.values(), PLAYOFF_QUALIFIED_ROLE)
DATABASE_URL = (os.getenv('DATABASE_URL') or '').strip() or None
DATABASE_PATH = os.getenv('TOURNAMENT_DB_PATH', 'tournament.db')
IS_RAILWAY = bool(os.getenv('RAILWAY_ENVIRONMENT') or os.getenv('RAILWAY_PROJECT_ID'))
REQUIRE_POSTGRES = os.getenv('REQUIRE_POSTGRES', '1' if IS_RAILWAY else '0') == '1'
ARCHIVE_RETENTION_DAYS = int(os.getenv('ARCHIVE_RETENTION_DAYS', '7'))
ARCHIVE_SWEEP_INTERVAL_MINUTES = int(os.getenv('ARCHIVE_SWEEP_INTERVAL_MINUTES', '60'))
REGISTRATION_CHANNEL = 'tournament-registration'
RESULT_CHANNEL = 'result-submission'
FIXTURE_CHANNELS = {'tournament-fixture', 'tournament-fixtures'}
ORGANIZER_CHANNEL = 'tournament-organizer'
PARTICIPANT_CHANNEL = 'tournament-chat'
GROUP_CHANNELS = {f'group-{group.lower()}-chat': group for group in GROUPS}
STAGE_LABELS = {'group': 'Group Stage', 'league': 'League', 'round_of_32': 'Round of 32', 'round_of_16': 'Round of 16', 'quarterfinal': 'Quarter-finals', 'semifinal': 'Semi-finals', 'final': 'Grand Final'}

def stage_label(stage: str | None) -> str:
    """Human-readable label for a stage name, with a generic fallback."""
    if not stage:
        return 'Next stage'
    return STAGE_LABELS.get(stage, str(stage).replace('_', ' ').title())
STAGE_DEADLINE_TITLES = {'group': '⏰ **GROUP STAGE MATCH DEADLINE**', 'league': '⏰ **LEAGUE MATCH DEADLINE**', 'round_of_32': '⚔️ **ROUND OF 32 MATCH DEADLINE**', 'round_of_16': '⚔️ **ROUND OF 16 MATCH DEADLINE**', 'quarterfinal': '⚔️ **QUARTERFINAL MATCH DEADLINE**', 'semifinal': '🔥 **SEMIFINAL MATCH DEADLINE**', 'final': '🏆 **GRAND FINAL MATCH DEADLINE**'}

def tournament_type_label(tournament: dict[str, Any]) -> str:
    """Human-readable tournament format, e.g. for /tournament_list and
    /tournament_status.

    Prefers the tournament's own template display name (so Champions League/
    World Cup show their real name instead of being lumped in with every
    other group+knockout tournament), falling back to the generic per-engine
    labels only for tournaments created without a template.
    """
    template = TEMPLATES.get(tournament.get('template_id'))
    if template:
        return str(template['display_name'])
    ttype = tournament['tournament_type']
    if ttype == ROUND_ROBIN:
        return 'KO Match'
    if ttype == LEAGUE:
        return 'League'
    return 'Groups+KO'
PLAYOFFS_CHAT_CHANNEL = 'playoffs-chat'
PLAYOFFS_FIXTURES_CHANNEL = 'playoffs-fixtures'
PLAYOFFS_STANDINGS_CHANNEL = 'playoffs-standings'
PARTICIPANT_COMMANDS_CHANNEL = 'participant-commands'
ANNOUNCEMENTS_CHANNEL = 'tournament-announcements'
RULES_CHANNEL = 'tournament-rules'
MATCH_RULES_CHANNEL = 'match-rules'
STANDINGS_CHANNEL = 'standings'
SUPPORT_CHANNEL = 'match-support'
WELCOME_CHANNEL = 'welcome'
HELP_CHANNEL = 'help'
STAFF_CHAT_CHANNEL = 'staff-chat'
STAFF_LOGS_CHANNEL = 'staff-logs'
PUNISHMENT_LOG_CHANNEL = 'punishment-log'
TOURNAMENTS_DIRECTORY_CHANNEL = 'tournaments'
TOURNAMENT_TEMPLATES_CHANNEL = 'tournament-templates'
TOURNAMENT_HISTORY_CHANNEL = 'tournament-history'
SERVER_PROMOTIONS_CHANNEL = 'server-promotions'
ADVERTISEMENTS_CHANNEL = 'advertisements'
PARTNER_PROMOTIONS_CHANNEL = 'partner-promotions'
GENERAL_CHAT_CHANNEL = 'general-chat'
FOOTBALL_CHAT_CHANNEL = 'football-chat'
EFOOTBALL_CHAT_CHANNEL = 'efootball-chat'
MEMES_CHANNEL = 'memes'
EFOOTBALL_ADVICE_CHANNEL = 'efootball-advice'
FRIENDLY_MATCHES_CHANNEL = 'friendly-matches'
HIGHLIGHTS_CHANNEL = 'highlights'
LIVE_STREAMS_CHANNEL = 'live-streams'
SCREENSHOTS_CHANNEL = 'screenshots'
CONTENT_CREATORS_CHANNEL = 'content-creators'
INFORMATION_CATEGORY = '👋 INFORMATION'
GROUP_STAGE_CATEGORY = '🏟️ GROUP STAGE'
PLAYOFFS_CATEGORY = '🏆 PLAYOFFS'
TOURNAMENT_CATEGORY = '📋 TOURNAMENT'
STAFF_CATEGORY = '🔐 STAFF'
TOURNAMENT_HUB_CATEGORY = '🏆 TOURNAMENT HUB'
ADVERTISEMENTS_CATEGORY = '📢 ADVERTISEMENTS'
COMMUNITY_CATEGORY = '💬 COMMUNITY'
MEDIA_CATEGORY = '🎥 MEDIA'
CATEGORY_ORDER = (INFORMATION_CATEGORY, TOURNAMENT_HUB_CATEGORY, ADVERTISEMENTS_CATEGORY, COMMUNITY_CATEGORY, MEDIA_CATEGORY, STAFF_CATEGORY)
LEGACY_CATEGORY_NAMES: dict[str, tuple[str, ...]] = {GROUP_STAGE_CATEGORY: ('🏆 GROUP STAGE',), INFORMATION_CATEGORY: ('📢 INFORMATION',), STAFF_CATEGORY: ('🔒 STAFF',)}

def _channel_scope(channel_name: str) -> str | None:
    name = channel_name.lower().strip()
    if name == REGISTRATION_CHANNEL:
        return 'registration'
    if name == RESULT_CHANNEL:
        return 'result'
    if name in FIXTURE_CHANNELS:
        return 'fixture'
    if name == ORGANIZER_CHANNEL:
        return 'organizer'
    if name == PARTICIPANT_CHANNEL:
        return 'participant'
    if name in GROUP_CHANNELS:
        return 'group'
    if name == PLAYOFFS_CHAT_CHANNEL:
        return 'playoffs'
    if name == PLAYOFFS_FIXTURES_CHANNEL:
        return 'playoffs_fixtures'
    if name == PLAYOFFS_STANDINGS_CHANNEL:
        return 'playoffs_standings_channel'
    if name == PARTICIPANT_COMMANDS_CHANNEL:
        return 'commands_help'
    if name == ANNOUNCEMENTS_CHANNEL:
        return 'announcements'
    if name == RULES_CHANNEL:
        return 'rules'
    if name == MATCH_RULES_CHANNEL:
        return 'match_rules'
    if name == STANDINGS_CHANNEL:
        return 'standings_channel'
    if name == SUPPORT_CHANNEL:
        return 'support'
    return None

def _tournament_channel_scope(guild_id: int, channel_id: int) -> str | None:
    """Resolve a per-tournament channel's scope by looking it up in the DB.

    Unlike the legacy name-based _channel_scope, this distinguishes which of
    a tournament's own channels (registration/fixtures/results/chat) matched,
    since a tournament can have several of its own channels at once.
    """
    try:
        tournament = bot.store.get_tournament_by_channel(guild_id, channel_id)
    except Exception:
        logger.exception('Database error while resolving tournament channel scope for guild %s channel %s.', guild_id, channel_id)
        raise app_commands.CheckFailure('The tournament database is temporarily unavailable. Please try again.')
    if not tournament:
        return None
    if tournament.get('registration_channel_id') and int(tournament['registration_channel_id']) == channel_id:
        return 'registration'
    if tournament.get('fixtures_channel_id') and int(tournament['fixtures_channel_id']) == channel_id:
        return 'fixture'
    if tournament.get('results_channel_id') and int(tournament['results_channel_id']) == channel_id:
        return 'result'
    if tournament.get('tournament_chat_channel_id') and int(tournament['tournament_chat_channel_id']) == channel_id:
        return 'chat'
    if tournament.get('announcements_channel_id') and int(tournament['announcements_channel_id']) == channel_id:
        return 'announcements'
    if tournament.get('standings_channel_id') and int(tournament['standings_channel_id']) == channel_id:
        return 'standings_channel'
    if tournament.get('match_schedule_channel_id') and int(tournament['match_schedule_channel_id']) == channel_id:
        return 'match_schedule'
    if tournament.get('playoffs_fixtures_channel_id') and int(tournament['playoffs_fixtures_channel_id']) == channel_id:
        return 'playoffs_fixtures'
    if tournament.get('playoffs_standings_channel_id') and int(tournament['playoffs_standings_channel_id']) == channel_id:
        return 'playoffs_standings_channel'
    if tournament.get('playoffs_chat_channel_id') and int(tournament['playoffs_chat_channel_id']) == channel_id:
        return 'playoffs'
    for group_resource in bot.store.get_tournament_group_resources(int(tournament['id'])).values():
        group_channel_id = group_resource.get('channel_id')
        if group_channel_id and int(group_channel_id) == channel_id:
            return 'group'
    return None

def resolve_tournament_for_interaction(guild: discord.Guild, interaction: discord.Interaction, status_tuples: tuple[tuple[str, ...], ...], tournament_id: int | None=None) -> dict[str, Any] | None:
    """Resolve which tournament a command invoked inside a Discord channel
    should operate on.

    Multiple tournaments can be open/closed concurrently in the same guild,
    each with its own channels. Falling back to "the most recent tournament
    in this guild" (the old behaviour) means a command run inside Tournament
    A's own channel could silently read or modify Tournament B's data
    whenever B is newer. To prevent that:

    1. An explicit ``tournament_id`` always wins (verified against this
       guild) — this is the escape hatch for commands run from a shared/
       legacy channel (e.g. the global organizer channel) that isn't tied
       to one tournament.
    2. Otherwise, if the channel the command was invoked in is one of a
       tournament's own stored channels (registration/fixtures/results/
       chat/announcements/standings/playoffs/group), that tournament is
       used — this is what makes /matches, /set_result, /players, etc. safe
       when several tournaments are running at once.
    3. Only when neither of the above applies (e.g. invoked from the shared
       organizer channel with no explicit ID) does this fall back to the
       most recent tournament in the requested status(es), matching the
       single-tournament behaviour this bot has always had.

    ``status_tuples`` is tried in order, so callers that used to cascade
    through several ``current_tournament`` calls (e.g. prefer OPEN, then
    CLOSED/completed, then DRAFT) can express the same cascade here.
    """
    if tournament_id is not None:
        tournament = bot.store.get_tournament(tournament_id)
        if tournament and int(tournament['guild_id']) == guild.id:
            return tournament
        return None
    if interaction.channel is not None:
        by_channel = bot.store.get_tournament_by_channel(guild.id, interaction.channel.id)
        if by_channel:
            for statuses in status_tuples:
                if by_channel['status'] in statuses:
                    return by_channel
    for statuses in status_tuples:
        found = bot.store.current_tournament(guild.id, statuses)
        if found:
            return found
    return None

def tournament_channel_only(*allowed_scopes: str) -> Callable[[Any], Any]:
    """Restrict a slash command to its intended Discord channel(s).

    The organizer channel is only an automatic bypass when explicitly included
    in ``allowed_scopes``. This keeps /register and /unregister exclusive to
    #tournament-registration as requested.

    "registration", "fixture", and "result" additionally match a
    per-tournament channel auto-provisioned for a specific tournament (see
    provision_tournament_resources), so this also works when several
    tournaments are running concurrently, each in its own channels.
    """

    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild or not interaction.channel:
            raise app_commands.CheckFailure('This command can only be used in a server channel.')
        scope = await _call_off_loop(_tournament_channel_scope, interaction.guild.id, interaction.channel.id)
        if scope is None and 'organizer' in allowed_scopes:
            named_scope = _channel_scope(getattr(interaction.channel, 'name', ''))
            if named_scope == 'organizer':
                scope = named_scope
        if scope in allowed_scopes:
            return True
        raise app_commands.CheckFailure('Use this command in the correct tournament channel.')
    return app_commands.check(predicate)

def _is_manager_with_organizer_access(interaction: discord.Interaction) -> bool:
    """Return True for Administrators or the Manager role with organizer access.

    The Manager role must be able to view #tournament-organizer. This keeps
    playoff management commands restricted even when the command is invoked
    from another tournament channel.
    """
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False
    member = interaction.user
    if member.guild_permissions.administrator:
        return True
    if not any((role.name.casefold() == MANAGER_ROLE.casefold() for role in member.roles)):
        return False
    organizer = discord.utils.get(interaction.guild.text_channels, name=ORGANIZER_CHANNEL)
    if organizer is None:
        return False
    return organizer.permissions_for(member).view_channel

def staff_messaging_access() -> Callable[[Any], Any]:
    """Allow authorized staff to use staff messaging from any server channel.

    Discord Administrators are allowed, and Managers are allowed only when
    they can view #tournament-organizer. The invocation channel is deliberately
    unrestricted so /send_message can be used from announcements, Hall of Fame,
    tournament channels, or any other server text channel.
    """

    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild or not interaction.channel:
            raise app_commands.CheckFailure('This command can only be used in a server channel.')
        if not _is_manager_with_organizer_access(interaction):
            raise app_commands.CheckFailure('Only Administrators or the Manager role with access to #tournament-organizer can use this command.')
        return True
    return app_commands.check(predicate)

def playoffs_fixture_access() -> Callable[[Any], Any]:
    """Allow /playoffs_fixture in any tournament-related channel.

    Authorization is independent of the invocation channel: only Discord
    Administrators or members with the Manager role who can access
    #tournament-organizer may use the command.
    """

    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild or not interaction.channel:
            raise app_commands.CheckFailure('This command can only be used in a server channel.')
        if _channel_scope(getattr(interaction.channel, 'name', '')) is None and await _call_off_loop(_tournament_channel_scope, interaction.guild.id, interaction.channel.id) is None:
            raise app_commands.CheckFailure('Use this command in a tournament-related channel.')
        if not _is_manager_with_organizer_access(interaction):
            raise app_commands.CheckFailure('Only Administrators or the Manager role with access to #tournament-organizer can use /playoffs_fixture.')
        return True
    return app_commands.check(predicate)

def _has_any_participant_role(member: discord.Member) -> bool:
    """True if the member holds the legacy Participant role or any
    per-tournament "<Tournament Name> Participant" role."""
    return any((role.name == PARTICIPANT_ROLE or role.name.endswith(f' {PARTICIPANT_ROLE}') for role in member.roles))

def participant_only() -> Callable[[Any], Any]:

    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            raise app_commands.CheckFailure('This command can only be used in a server.')
        if interaction.user.guild_permissions.manage_guild:
            return True
        if not _has_any_participant_role(interaction.user):
            raise app_commands.CheckFailure(f'You need the {PARTICIPANT_ROLE} role to use this command.')
        return True
    return app_commands.check(predicate)

def group_channel_only() -> Callable[[Any], Any]:
    """Allow participant commands in a group chat only to that group's players."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            raise app_commands.CheckFailure('This command can only be used in a server.')
        name = getattr(interaction.channel, 'name', '').lower().strip()
        group = GROUP_CHANNELS.get(name)
        if not group:
            raise app_commands.CheckFailure('This command is only available in a group chat.')
        if interaction.user.guild_permissions.manage_guild:
            return True
        if not any((role.name == PARTICIPANT_ROLE for role in interaction.user.roles)):
            raise app_commands.CheckFailure(f'You need the {PARTICIPANT_ROLE} role to use this command.')
        group_role = GROUP_ROLE_NAMES[group]
        if not any((role.name == group_role for role in interaction.user.roles)):
            raise app_commands.CheckFailure(f'You are not assigned to Group {group}.')
        return True
    return app_commands.check(predicate)

def format_user(guild: discord.Guild, user_id: int, player_names: dict[int, str] | None=None) -> str:
    # Tournament-specific labels (World Cup nations) replace Discord names in
    # public tournament displays while user IDs remain the internal identity.
    if player_names is not None and user_id in player_names:
        return str(player_names[user_id])
    member = guild.get_member(user_id)
    if member:
        return member.mention
    return f'<@{user_id}>'

def tournament_player_labels(players: list[dict[str, Any]]) -> dict[int, str]:
    """Return only World Cup nation labels; non-World-Cup players fall back to normal Discord mentions."""
    return {int(row['user_id']): str(row['nation_name']) for row in players if row.get('nation_name')}

async def sync_world_cup_nation_roles(guild: discord.Guild, tournament_id: int) -> dict[int, discord.Role]:
    """Ensure every World Cup nation is a Discord role and assign it to its player.

    Nation names are the role names (for example ``Türkiye``), so Discord
    renders ``<@&ROLE_ID>`` as a clean ``@Türkiye`` mention in fixture posts.
    Existing assignments are preserved in the database; this function only
    repairs/reflects the Discord roles from that authoritative mapping.
    """
    tournament = await _store_call(bot.store.get_tournament, tournament_id)
    if not tournament or str(tournament.get('template_id') or '') not in WORLD_CUP_TEMPLATE_IDS:
        raise TournamentError('This command is only available for a World Cup tournament.')
    players = await _store_call(bot.store.players, tournament_id)
    nation_roles: dict[str, discord.Role] = {}
    for player in players:
        nation = str(player.get('nation_name') or '').strip()
        if not nation:
            continue
        role = discord.utils.get(guild.roles, name=nation)
        if role is None:
            role = await ensure_role(guild, nation)
        nation_roles[nation] = role

    assigned_roles: dict[int, discord.Role] = {}
    current_nation_roles = set(nation_roles.values())
    for player in players:
        nation = str(player.get('nation_name') or '').strip()
        if not nation:
            continue
        user_id = int(player['user_id'])
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                logger.warning('World Cup nation role sync: player %s is no longer in guild %s.', user_id, guild.id)
                continue
            except discord.HTTPException as error:
                logger.warning('World Cup nation role sync: could not fetch player %s: %s', user_id, error)
                continue
        role = nation_roles[nation]
        try:
            if role not in member.roles:
                await member.add_roles(role, reason=f'World Cup nation assignment: {nation}')
            stale = [r for r in current_nation_roles if r != role and r in member.roles]
            if stale:
                await member.remove_roles(*stale, reason='Removing stale World Cup nation role')
            assigned_roles[user_id] = role
        except discord.Forbidden as error:
            raise TournamentError(f'I cannot assign the `{nation}` role to {member.mention}. Move the bot role above the country roles and ensure it has Manage Roles permission.') from error
        except discord.HTTPException as error:
            raise TournamentError(f'I could not assign the `{nation}` role to {member.mention}: {error}') from error
    return assigned_roles

async def world_cup_player_role_mentions(guild: discord.Guild, tournament_id: int, players: list[dict[str, Any]]) -> dict[int, str]:
    """Return player -> Discord country-role mention for World Cup displays."""
    roles = await sync_world_cup_nation_roles(guild, tournament_id)
    return {int(row['user_id']): roles[int(row['user_id'])].mention for row in players if int(row['user_id']) in roles}

def format_match(guild: discord.Guild, match: dict[str, Any], player_names: dict[int, str] | None=None, display_number: int | None=None) -> str:
    first = format_user(guild, int(match['player1_id']), player_names)
    second = format_user(guild, int(match['player2_id']), player_names)
    score = f"**{match['score1']} - {match['score2']}**" if match['status'] == 'completed' else 'pending'
    stage = 'KO Match' if str(match['stage']) == 'round_robin' else str(match['stage']).replace('_', ' ').title()
    group = f" · Group {match['group_name']}" if match.get('group_name') else ''
    number = display_number if display_number is not None else int(match['id'])
    return f'`#{number}` {stage}{group}: **{first}** vs **{second}** — {score}'

def format_standings_table(rows: list[Standing]) -> str:
    """Render standings as one compact, fixed-width Discord code-block table.

    This is presentation-only: the incoming ``rows`` order and every
    calculated Standing value are displayed as-is. Names are truncated only
    for the Discord display so a long name can never wrap the statistics onto
    another line.
    """
    if not rows:
        return '```text\n#  Player  P W D L GF:GA  GD PTS\n```'
    max_rank_width = max(1, len(str(len(rows))))
    displayed_names = [_truncate_standing_name(str(row.display_name), 20) for row in rows]
    player_width = max(6, min(20, max(len('Player'), *(len(name) for name in displayed_names))))
    header = f"{'#':>{max_rank_width}}  {'Player':<{player_width}}  P W D L {'GF:GA':>5} {'GD':>4} {'PTS':>3}"
    separator = '-' * len(header)
    lines = [header, separator]
    for index, (row, name) in enumerate(zip(rows, displayed_names), 1):
        gfga = f'{int(row.goals_for)}:{int(row.goals_against)}'
        gd = f'{int(row.goal_difference):+d}'
        lines.append(f'{index:>{max_rank_width}}  {name:<{player_width}}  {int(row.played):>1} {int(row.wins):>1} {int(row.draws):>1} {int(row.losses):>1} {gfga:>5} {gd:>4} {int(row.points):>3}')
    return '```text\n' + '\n'.join(lines) + '\n```'

def _truncate_standing_name(name: str, max_width: int) -> str:
    """Truncate only the displayed standings name, preserving the data."""
    if len(name) <= max_width:
        return name
    if max_width <= 1:
        return '…'[:max_width]
    return name[:max_width - 1] + '…'

class TournamentBot(commands.Bot):

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, description='eFootball Mobile tournament management bot')
        if REQUIRE_POSTGRES and (not DATABASE_URL):
            raise RuntimeError('DATABASE_URL is required on Railway. Add a Railway PostgreSQL database and expose its DATABASE_URL variable to this service. Refusing to use ephemeral SQLite so tournament data cannot disappear after a deploy.')
        self.store = TournamentStore(DATABASE_PATH, database_url=DATABASE_URL)
        logger.info('Tournament database backend: %s%s', self.store.backend, ' (Railway PostgreSQL required)' if REQUIRE_POSTGRES else '')

    async def setup_hook(self) -> None:
        synced = await self.tree.sync()
        self.add_view(RegisterNowView())
        self.add_dynamic_items(ConfirmResultButton, DisputeResultButton, MatchSchedulePromptButton, MatchScheduleAcceptButton, MatchScheduleDeclineButton, MatchScheduleAnotherButton)
        logger.info('Synced %d slash commands.', len(synced))

    async def close(self) -> None:
        if lifecycle_sweep.is_running():
            lifecycle_sweep.cancel()
        self.store.close()
        await super().close()
bot = TournamentBot()

def _validate_server_config(guild: discord.Guild) -> None:
    """Log (never modify) any missing tournament server setup on startup.

    This is intentionally read-only. /setup_server is the only command that
    actually creates or changes server structure, per design.
    """
    missing_roles: list[str] = []
    missing_categories: list[str] = []
    missing_channels = [name for name in (ORGANIZER_CHANNEL,) if not discord.utils.get(guild.text_channels, name=name)]
    if missing_roles or missing_categories or missing_channels:
        logger.warning("Guild '%s' (%s): tournament server setup is incomplete. Missing roles: %s | Missing categories: %s | Missing channels: %s. An organizer can run /setup_server in #%s to fix this.", guild.name, guild.id, missing_roles or 'none', missing_categories or 'none', missing_channels or 'none', ORGANIZER_CHANNEL)

async def _ensure_existing_open_tournament_registration_button(guild: discord.Guild) -> None:
    """Ensure an already-open tournament has the registration button after deploy/restart.

    This is a UI recovery check only. It never reopens, resets, or changes
    tournament state or registration data.
    """
    try:
        tournament = await _store_call(bot.store.current_tournament, guild.id, (OPEN,))
        if not tournament:
            return
        channel: discord.TextChannel | None = None
        if tournament.get('registration_channel_id'):
            maybe_channel = guild.get_channel(int(tournament['registration_channel_id']))
            if isinstance(maybe_channel, discord.TextChannel):
                channel = maybe_channel
        if channel is None:
            logger.warning('Open tournament %s found, but its stored registration channel ID does not resolve to a text channel in guild %s. Not recovering the Register Now button automatically.', tournament.get('id'), guild.id)
            return
        async for message in channel.history(limit=100):
            if message.author.id != bot.user.id or not message.components:
                continue
            for row in message.components:
                for component in row.children:
                    if getattr(component, 'custom_id', None) == 'tournament_register_now':
                        logger.info('Register Now button already exists for open tournament %s.', tournament.get('id'))
                        return
        await post_registration_button(guild, tournament)
        logger.info('Posted missing Register Now button for already-open tournament %s.', tournament.get('id'))
    except Exception:
        logger.exception('Failed to recover the Register Now button for an already-open tournament.')

@bot.event
async def on_ready() -> None:
    for _guild in list(bot.guilds):
        try:
            await _ensure_champions_infrastructure(_guild)
        except Exception:
            logger.exception('Failed to initialize Champions infrastructure in guild %s', _guild.id)
    logger.info('Logged in as %s (%s).', bot.user, getattr(bot.user, 'id', 'unknown'))
    for guild in bot.guilds:
        try:
            _validate_server_config(guild)
        except Exception:
            logger.exception('Startup server-config check failed for guild %s', guild.id)
        try:
            await _ensure_existing_open_tournament_registration_button(guild)
        except Exception:
            logger.exception('Startup registration-button recovery failed for guild %s', guild.id)
        try:
            await _recover_confirmed_schedules_on_startup(guild)
        except Exception:
            logger.exception('Startup confirmed-schedule recovery failed for guild %s', guild.id)
    if not lifecycle_sweep.is_running():
        lifecycle_sweep.start()
COMMAND_ONLY_CHANNELS = {REGISTRATION_CHANNEL, RESULT_CHANNEL, *FIXTURE_CHANNELS}

async def post_registration_button(guild: discord.Guild, tournament: dict) -> None:
    """Post the registration announcement and persistent Register Now button.

    Posts into this tournament's own dedicated registration channel
    (auto-provisioned by provision_tournament_resources) so multiple
    tournaments each get their own registration post, instead of sharing one
    #tournament-registration channel.
    """
    channel = None
    if tournament.get('registration_channel_id'):
        maybe_channel = guild.get_channel(int(tournament['registration_channel_id']))
        if isinstance(maybe_channel, discord.TextChannel):
            channel = maybe_channel
    if channel is None:
        logger.warning('Could not post registration button for tournament %s: its stored registration_channel_id does not resolve to a text channel.', tournament.get('id'))
        return
    embed = discord.Embed(title='🏆 REGISTRATION IS NOW OPEN!', description=f"**{tournament['name']}** is now open for registration!\n\n👥 **Player limit:** {tournament['player_limit']}\n\n👇 Click **Register Now** below to join the tournament.", color=discord.Color.green())
    embed.set_footer(text='Register early — limited slots available!')
    await channel.send(embed=embed, view=RegisterNowView())

class RegisterNowView(discord.ui.View):
    """Persistent one-click button for the existing /register command."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Register Now', style=discord.ButtonStyle.success, emoji='📝', custom_id='tournament_register_now')
    async def register_now(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        command = interaction.client.tree.get_command('register')
        if command is None:
            await respond(interaction, '❌ The registration command is currently unavailable.', ephemeral=True)
            return
        try:
            await command.callback(interaction)
        except Exception:
            logger.exception('Register Now button failed')
            if interaction.response.is_done():
                await interaction.followup.send('❌ Something went wrong while starting registration.', ephemeral=True)
            else:
                await interaction.response.send_message('❌ Something went wrong while starting registration.', ephemeral=True)

@bot.event
async def on_message(message: discord.Message) -> None:
    """Delete participant text messages from command-only tournament channels.

    Participants can still use slash commands because slash commands do not
    arrive through on_message(). Moderators with Manage Server are exempt so
    they can post notices/corrections when needed.
    """
    if message.author.bot or not message.guild:
        return
    channel_name = getattr(message.channel, 'name', '').lower().strip()
    is_command_only = channel_name in COMMAND_ONLY_CHANNELS or await _call_off_loop(_tournament_channel_scope, message.guild.id, message.channel.id) in ('registration', 'fixture', 'result')
    if not is_command_only:
        return
    if isinstance(message.author, discord.Member) and message.author.guild_permissions.manage_guild:
        return
    try:
        await message.delete()
    except discord.NotFound:
        pass
    except discord.Forbidden:
        logger.warning('Cannot delete a participant message in #%s. Give the bot Manage Messages permission.', channel_name)
    except discord.HTTPException as error:
        logger.warning('Failed to delete message in #%s: %s', channel_name, error)

async def ensure_participant_role(guild: discord.Guild) -> discord.Role:
    return await ensure_role(guild, PARTICIPANT_ROLE)

def _slugify(name: str) -> str:
    """Turn a tournament name into a Discord-safe channel-name slug."""
    slug = re.sub('[^a-z0-9]+', '-', name.lower()).strip('-')
    return slug[:80] or 'tournament'

def get_tournament_channel_prefix(tournament: dict[str, Any]) -> str:
    """Return a short deterministic display prefix for tournament channels.

    Channel names are display labels only; stored Discord IDs remain the
    authoritative resource identifiers. Known templates use compact aliases.
    """
    template_id = str(tournament.get('template_id') or '').strip().lower()
    display_name = str(tournament.get('name') or '').strip().lower()
    known = {'premier_league': 'pl', 'weekly_championship': 'wc', 'champions_league': 'cl', 'world_cup_32': 'wc32', 'world_cup_48': 'wc48', 'world_cup_64': 'wc64'}
    alias = known.get(template_id)
    if alias is None:
        normalized = re.sub('[^a-z0-9]+', ' ', display_name).strip()
        if 'weekly championship' in normalized:
            alias = 'wc'
        elif 'premier league' in normalized:
            alias = 'pl'
        elif 'champions league' in normalized:
            alias = 'cl'
        elif 'world cup' in normalized:
            match = re.search('world cup\\s*(?:\\(|)?(32|48|64)', normalized)
            alias = f'wc{match.group(1)}' if match else 'wc'
        elif 'ko match' in normalized:
            alias = 'ko'
        else:
            words = [word for word in normalized.split() if word]
            alias = ''.join((word[0] for word in words))[:6] or 't'
    return alias

async def get_tournament_participant_role(guild: discord.Guild, tournament: dict[str, Any]) -> discord.Role:
    """Get (or lazily create) this specific tournament's own Participant role.

    Each newly-provisioned tournament gets its own role (e.g. "Chess Cup
    Participant") so several tournaments can run at once without mixing up
    who's in which. A tournament that was opened before per-tournament
    channels existed (no category_id stored) keeps using the original
    shared "Participant" role instead, since existing channel permission
    overwrites (group chats, playoffs, etc.) already reference that role by
    name — creating a new role for it would silently cut off members who
    register after this update.
    """
    role_id = tournament.get('participant_role_id')
    if role_id:
        role = guild.get_role(int(role_id))
        if role:
            return role
    if not tournament.get('category_id'):
        legacy_role = discord.utils.get(guild.roles, name=PARTICIPANT_ROLE)
        if legacy_role:
            await _store_call(bot.store.set_tournament_channels, int(tournament['id']), participant_role_id=legacy_role.id)
            return legacy_role
    role_name = f"{tournament['name']} {PARTICIPANT_ROLE}"
    role = await ensure_role(guild, role_name)
    await _store_call(bot.store.set_tournament_channels, int(tournament['id']), participant_role_id=role.id)
    return role

async def provision_tournament_resources(guild: discord.Guild, tournament: dict[str, Any]) -> dict[str, Any]:
    """Create (or reuse) this tournament's own category, channels, and role.

    Idempotent: re-running it (e.g. because channels were already created for
    this tournament earlier) reuses whatever is already stored instead of
    creating duplicates. This is what lets several tournaments run
    concurrently, each fully isolated in its own channels.
    """
    tid = int(tournament['id'])
    category = None
    if tournament.get('category_id'):
        maybe_category = guild.get_channel(int(tournament['category_id']))
        if isinstance(maybe_category, discord.CategoryChannel):
            category = maybe_category
    if category is None:
        try:
            category = await guild.create_category(f"🏆 {tournament['name']}", reason='eFootball tournament bot: new tournament')
        except discord.Forbidden as error:
            raise TournamentError('I cannot create tournament categories. Give me Manage Channels permission.') from error
    await _store_call(bot.store.set_tournament_channels, tid, category_id=category.id)
    prefix = get_tournament_channel_prefix(tournament)
    reg_name, fix_name = (f"{prefix}-registration", f"{prefix}-fixtures")
    registration_channel = None
    if tournament.get('registration_channel_id'):
        maybe_channel = guild.get_channel(int(tournament['registration_channel_id']))
        if isinstance(maybe_channel, discord.TextChannel):
            registration_channel = maybe_channel
    if registration_channel is None:
        try:
            registration_channel = await guild.create_text_channel(reg_name, category=category, topic=f"Register for 🏆 {tournament['name']} with /register.", reason='eFootball tournament bot: new tournament')
        except discord.Forbidden as error:
            raise TournamentError('I cannot create tournament channels. Give me Manage Channels permission.') from error
    fixtures_channel = None
    if tournament.get('fixtures_channel_id'):
        maybe_channel = guild.get_channel(int(tournament['fixtures_channel_id']))
        if isinstance(maybe_channel, discord.TextChannel):
            fixtures_channel = maybe_channel
    if fixtures_channel is None:
        try:
            fixtures_channel = await guild.create_text_channel(fix_name, category=category, topic=f"Fixtures for 🏆 {tournament['name']}. View only — results are reported in the results channel.", reason='eFootball tournament bot: new tournament')
        except discord.Forbidden as error:
            raise TournamentError('I cannot create tournament channels. Give me Manage Channels permission.') from error
    await _store_call(bot.store.set_tournament_channels, tid, registration_channel_id=registration_channel.id)
    try:
        bot_member = guild.me
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False)}
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True)
        await _apply_overwrites(fixtures_channel, overwrites)
    except discord.Forbidden:
        logger.warning('Could not lock #%s to view-only for tournament %s (missing Manage Roles).', fixtures_channel.name, tid)
    results_name = f"{prefix}-results"
    results_channel = None
    if tournament.get('results_channel_id'):
        maybe_channel = guild.get_channel(int(tournament['results_channel_id']))
        if isinstance(maybe_channel, discord.TextChannel):
            results_channel = maybe_channel
    if results_channel is None:
        try:
            results_channel = await guild.create_text_channel(results_name, category=category, topic=f"Report results for 🏆 {tournament['name']} with /report. Disputes are handled here too.", reason='eFootball tournament bot: new tournament')
        except discord.Forbidden as error:
            raise TournamentError('I cannot create tournament channels. Give me Manage Channels permission.') from error
    await _store_call(bot.store.set_tournament_channels, tid, fixtures_channel_id=fixtures_channel.id)
    role = None
    if tournament.get('participant_role_id'):
        role = guild.get_role(int(tournament['participant_role_id']))
    if role is None:
        role = await ensure_role(guild, f"{tournament['name']} {PARTICIPANT_ROLE}")
    await _store_call(bot.store.set_tournament_channels, tid, results_channel_id=results_channel.id, participant_role_id=role.id)
    chat_name = f"{prefix}-chat"
    chat_channel = None
    if tournament.get('tournament_chat_channel_id'):
        maybe_channel = guild.get_channel(int(tournament['tournament_chat_channel_id']))
        if isinstance(maybe_channel, discord.TextChannel):
            chat_channel = maybe_channel
    if chat_channel is None:
        try:
            chat_channel = await guild.create_text_channel(chat_name, category=category, topic=f"Chat for players registered in 🏆 {tournament['name']} only.", reason='eFootball tournament bot: new tournament')
        except discord.Forbidden as error:
            raise TournamentError('I cannot create tournament channels. Give me Manage Channels permission.') from error
    try:
        bot_member = guild.me
        chat_overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {guild.default_role: discord.PermissionOverwrite(view_channel=False), role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, add_reactions=True)}
        if bot_member:
            chat_overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, read_message_history=True)
        for staff_role in _staff_roles(guild):
            chat_overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        await _apply_overwrites(chat_channel, chat_overwrites)
    except discord.Forbidden:
        logger.warning('Could not set participant-only permissions on #%s for tournament %s (missing Manage Roles).', chat_channel.name, tid)
    await _store_call(bot.store.set_tournament_channels, tid, tournament_chat_channel_id=chat_channel.id)
    announcements_name = f"{prefix}-announcements"
    announcements_channel = None
    if tournament.get('announcements_channel_id'):
        maybe_channel = guild.get_channel(int(tournament['announcements_channel_id']))
        if isinstance(maybe_channel, discord.TextChannel):
            announcements_channel = maybe_channel
    if announcements_channel is None:
        try:
            announcements_channel = await guild.create_text_channel(announcements_name, category=category, topic=f"Announcements for 🏆 {tournament['name']} (deadlines, fixtures, results).", reason='eFootball tournament bot: new tournament')
        except discord.Forbidden as error:
            raise TournamentError('I cannot create tournament channels. Give me Manage Channels permission.') from error
    await _store_call(bot.store.set_tournament_channels, tid, announcements_channel_id=announcements_channel.id)
    try:
        bot_member = guild.me
        announcements_overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False)}
        if bot_member:
            announcements_overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True)
        await _apply_overwrites(announcements_channel, announcements_overwrites)
    except discord.Forbidden:
        logger.warning('Could not lock #%s to view-only for tournament %s (missing Manage Roles).', announcements_channel.name, tid)
    # Dedicated public schedule board. It is view-only for players; only the
    # bot/staff can post confirmed schedules here. The channel name is exactly
    # `match-schedule` because it lives inside this tournament's own category.
    match_schedule_channel = None
    if tournament.get('match_schedule_channel_id'):
        maybe_channel = guild.get_channel(int(tournament['match_schedule_channel_id']))
        if isinstance(maybe_channel, discord.TextChannel):
            match_schedule_channel = maybe_channel
    if match_schedule_channel is None:
        try:
            match_schedule_channel = await guild.create_text_channel('match-schedule', category=category, topic=f"Confirmed match schedules for 🏆 {tournament['name']}. View only.", reason='eFootball tournament bot: new tournament')
        except discord.Forbidden as error:
            raise TournamentError('I cannot create tournament channels. Give me Manage Channels permission.') from error
    try:
        bot_member = guild.me
        schedule_overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)}
        if bot_member:
            schedule_overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, read_message_history=True)
        for staff_role in _staff_roles(guild):
            schedule_overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        await _apply_overwrites(match_schedule_channel, schedule_overwrites)
    except discord.Forbidden:
        logger.warning('Could not lock #match-schedule to view-only for tournament %s (missing Manage Roles).', tid)
    await _store_call(bot.store.set_tournament_channels, tid, match_schedule_channel_id=match_schedule_channel.id)
    standings_channels = await _ensure_standings_and_playoffs_channels(guild, await _store_call(bot.store.get_tournament, tid) or tournament, category=category, role=role)
    return {'category': category, 'registration_channel': registration_channel, 'fixtures_channel': fixtures_channel, 'results_channel': results_channel, 'chat_channel': chat_channel, 'announcements_channel': announcements_channel, 'match_schedule_channel': match_schedule_channel, 'role': role, **standings_channels}

async def _ensure_standings_and_playoffs_channels(guild: discord.Guild, tournament: dict[str, Any], *, category: discord.CategoryChannel | None=None, role: discord.Role | None=None) -> dict[str, discord.TextChannel | None]:
    """Create (or reuse) this tournament's own standings/playoffs channels.

    Mirrors provision_tournament_resources's per-tournament isolation: every
    tournament gets its own #<slug>-standings, and (for group/knockout
    tournaments only) its own #<slug>-playoffs-fixtures and
    #<slug>-playoffs-standings, all tracked by stored channel ID rather than
    resolved by name. This is idempotent and safe to call repeatedly — it
    only creates what isn't already stored, so it also backfills tournaments
    that were provisioned before these columns existed.

    Returns a dict with keys "standings_channel", "playoffs_fixtures_channel",
    and "playoffs_standings_channel" — any entry may be None if it couldn't
    be created (e.g. missing permissions, or no category to place it in).
    """
    tid = int(tournament['id'])
    result: dict[str, discord.TextChannel | None] = {'standings_channel': None, 'playoffs_fixtures_channel': None, 'playoffs_standings_channel': None}
    if category is None and tournament.get('category_id'):
        maybe_category = guild.get_channel(int(tournament['category_id']))
        if isinstance(maybe_category, discord.CategoryChannel):
            category = maybe_category
    if category is None:
        return result
    if role is None and tournament.get('participant_role_id'):
        role = guild.get_role(int(tournament['participant_role_id']))
    prefix = get_tournament_channel_prefix(tournament)
    bot_member = guild.me
    staff_roles = _staff_roles(guild)

    def _read_only_overwrites() -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False)}
        if role is not None:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True, read_message_history=True)
        for staff_role in staff_roles:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        return overwrites

    async def _get_or_create(attr: str, base_name: str, topic: str) -> discord.TextChannel | None:
        existing_id = tournament.get(attr)
        if existing_id:
            maybe_channel = guild.get_channel(int(existing_id))
            if isinstance(maybe_channel, discord.TextChannel):
                return maybe_channel
        name = base_name
        try:
            channel = await guild.create_text_channel(name, category=category, topic=topic, reason='eFootball tournament bot: new tournament')
        except discord.Forbidden:
            logger.warning('Could not create #%s for tournament %s (missing Manage Channels).', name, tid)
            return None
        try:
            await _apply_overwrites(channel, _read_only_overwrites())
        except discord.Forbidden:
            logger.warning('Could not set read-only permissions on #%s for tournament %s (missing Manage Roles).', channel.name, tid)
        return channel
    result['standings_channel'] = await _get_or_create('standings_channel_id', f"{prefix}-standings", f"Live standings for 🏆 {tournament['name']}.")
    if tournament.get('tournament_type') == GROUP_KNOCKOUT:
        result['playoffs_fixtures_channel'] = await _get_or_create('playoffs_fixtures_channel_id', f"{prefix}-playoffs-fixtures", f"Knockout-stage fixtures for 🏆 {tournament['name']}.")
        result['playoffs_standings_channel'] = await _get_or_create('playoffs_standings_channel_id', f"{prefix}-playoffs-standings", f"Knockout-stage standings/bracket for 🏆 {tournament['name']}.")
    await _store_call(bot.store.set_tournament_channels, tid, standings_channel_id=result['standings_channel'].id if result['standings_channel'] else None, playoffs_fixtures_channel_id=result['playoffs_fixtures_channel'].id if result['playoffs_fixtures_channel'] else None, playoffs_standings_channel_id=result['playoffs_standings_channel'].id if result['playoffs_standings_channel'] else None)
    if tournament.get('tournament_type') == GROUP_KNOCKOUT:
        group_resources = await _ensure_group_and_playoffs_chat_resources(guild, tournament, category=category, staff_roles=staff_roles, bot_member=bot_member)
        result.update(group_resources)
    return result

async def _ensure_group_and_playoffs_chat_resources(guild: discord.Guild, tournament: dict[str, Any], *, category: discord.CategoryChannel, staff_roles: list[discord.Role], bot_member: discord.Member | None) -> dict[str, discord.TextChannel | discord.Role | None]:
    """Create (or reuse) this tournament's own group-chat and playoffs-chat
    roles/channels.

    Previously group chat and playoffs chat both used a single global role
    ("Group A", ..., "Playoff Qualified") and a single shared channel by
    name (#group-a-chat, ..., #playoffs-chat). That meant two group/knockout
    tournaments active at once (e.g. one still finishing its playoffs while
    another opens) would incorrectly share group and playoffs chat access
    with each other's players. This gives every tournament its own roles
    and channels, tracked by stored ID.
    """
    tid = int(tournament['id'])
    prefix = get_tournament_channel_prefix(tournament)
    out: dict[str, discord.TextChannel | discord.Role | None] = {'group_roles': {}, 'group_channels': {}, 'playoff_qualified_role': None, 'playoffs_chat_channel': None}
    groups = (await _store_call(bot.store.knockout_shape_for, tid))['groups']
    existing_group_resources = await _store_call(bot.store.get_tournament_group_resources, tid)
    group_roles: dict[str, discord.Role] = {}
    for group in groups:
        role_id = existing_group_resources.get(group, {}).get('role_id')
        existing_role = guild.get_role(int(role_id)) if role_id else None
        group_roles[group] = existing_role or await ensure_role(guild, f"{tournament['name']} Group {group}")
    qualified_role_id = tournament.get('playoff_qualified_role_id')
    qualified_role = guild.get_role(int(qualified_role_id)) if qualified_role_id else None
    if qualified_role is None:
        qualified_role = await ensure_role(guild, f"{tournament['name']} {PLAYOFF_QUALIFIED_ROLE}")

    async def _get_or_create_chat(existing_id: int | None, base_name: str, topic: str, access_role: discord.Role) -> discord.TextChannel | None:
        if existing_id:
            maybe_channel = guild.get_channel(int(existing_id))
            if isinstance(maybe_channel, discord.TextChannel):
                return maybe_channel
        name = base_name
        try:
            channel = await guild.create_text_channel(name, category=category, topic=topic, reason='eFootball tournament bot: new tournament')
        except discord.Forbidden:
            logger.warning('Could not create #%s for tournament %s (missing Manage Channels).', name, tid)
            return None
        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {guild.default_role: discord.PermissionOverwrite(view_channel=False), access_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, add_reactions=True)}
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, read_message_history=True)
        for staff_role in staff_roles:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        try:
            await _apply_overwrites(channel, overwrites)
        except discord.Forbidden:
            logger.warning('Could not set participant-only permissions on #%s for tournament %s (missing Manage Roles).', channel.name, tid)
        return channel
    group_channels: dict[str, discord.TextChannel | None] = {}
    for group in groups:
        existing_channel_id = existing_group_resources.get(group, {}).get('channel_id')
        group_channels[group] = await _get_or_create_chat(existing_channel_id, f"{prefix}-group-{group.lower()}-chat", f"Group {group} — coordinate your matches here. ({tournament['name']})", group_roles[group])
    playoffs_chat_channel = await _get_or_create_chat(tournament.get('playoffs_chat_channel_id'), f"{prefix}-playoffs-chat", f"Knockout stage discussion for 🏆 {tournament['name']}.", qualified_role)
    await _store_call(bot.store.set_tournament_group_resources, tid, group_role_ids={group: role.id for group, role in group_roles.items()}, group_channel_ids={group: channel.id for group, channel in group_channels.items() if channel is not None}, playoffs_chat_channel_id=playoffs_chat_channel.id if playoffs_chat_channel else None, playoff_qualified_role_id=qualified_role.id)
    out['group_roles'] = group_roles
    out['group_channels'] = group_channels
    out['playoff_qualified_role'] = qualified_role
    out['playoffs_chat_channel'] = playoffs_chat_channel
    return out

async def cleanup_tournament_resources(guild: discord.Guild, tournament: dict[str, Any]) -> None:
    """Delete the category/channels/role auto-provisioned for a tournament.

    Called from /tournament_delete so removing a tournament doesn't leave
    orphaned per-tournament channels and roles behind. Best-effort: logs and
    continues past permission/not-found errors instead of blocking deletion.

    SAFETY: this must never delete shared/legacy infrastructure (the plain
    "Participant" role, #tournament-registration, #tournament-fixtures,
    #result-submission, the shared #tournament-chat, etc.) even if a
    tournament's stored IDs incorrectly point at one of those (e.g. from the
    get_tournament_participant_role legacy fallback). Two independent guards
    below both have to pass before anything is actually deleted.
    """
    tid = int(tournament['id'])
    protected_role_names = {PARTICIPANT_ROLE, *GROUP_ROLE_NAMES.values(), PLAYOFF_QUALIFIED_ROLE}
    protected_channel_names = {REGISTRATION_CHANNEL, RESULT_CHANNEL, PARTICIPANT_CHANNEL, *FIXTURE_CHANNELS, STANDINGS_CHANNEL, PLAYOFFS_FIXTURES_CHANNEL, PLAYOFFS_STANDINGS_CHANNEL, PLAYOFFS_CHAT_CHANNEL, *GROUP_CHANNELS.keys()}
    resources: list[tuple[str, int | None, str]] = [('registration_channel_id', tournament.get('registration_channel_id'), 'channel'), ('fixtures_channel_id', tournament.get('fixtures_channel_id'), 'channel'), ('match_schedule_channel_id', tournament.get('match_schedule_channel_id'), 'channel'), ('results_channel_id', tournament.get('results_channel_id'), 'channel'), ('tournament_chat_channel_id', tournament.get('tournament_chat_channel_id'), 'channel'), ('standings_channel_id', tournament.get('standings_channel_id'), 'channel'), ('playoffs_fixtures_channel_id', tournament.get('playoffs_fixtures_channel_id'), 'channel'), ('playoffs_standings_channel_id', tournament.get('playoffs_standings_channel_id'), 'channel'), ('playoffs_chat_channel_id', tournament.get('playoffs_chat_channel_id'), 'channel'), ('category_id', tournament.get('category_id'), 'channel'), ('participant_role_id', tournament.get('participant_role_id'), 'role'), ('playoff_qualified_role_id', tournament.get('playoff_qualified_role_id'), 'role')]
    for group, group_resource in (await _store_call(bot.store.get_tournament_group_resources, tid)).items():
        resources.append((f'group_{group.lower()}_role_id', group_resource.get('role_id'), 'role'))
        resources.append((f'group_{group.lower()}_channel_id', group_resource.get('channel_id'), 'channel'))
    for attr, object_id, kind in resources:
        if not object_id:
            continue
        try:
            obj = guild.get_channel(int(object_id)) if kind == 'channel' else guild.get_role(int(object_id))
            if obj is None:
                continue
            protected_names = protected_role_names if kind == 'role' else protected_channel_names
            if obj.name in protected_names:
                logger.warning("Refusing to delete %s '%s' for tournament %s: it matches a shared/legacy name, not something this tournament exclusively owns.", kind, obj.name, tid)
                continue
            if await _store_call(bot.store.count_tournaments_referencing_resource, guild.id, int(object_id), tid) > 0:
                logger.warning("Refusing to delete %s '%s' for tournament %s: still referenced by another tournament.", kind, obj.name, tid)
                continue
            await obj.delete(reason='Tournament deleted')
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            logger.warning('Could not clean up %s (id=%s) for deleted tournament %s.', attr, object_id, tid)
ARCHIVED_CATEGORY_PREFIX = '📦 [ARCHIVED]'

def _archived_channel_overwrites(guild: discord.Guild, channel_roles: list[discord.Role]) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
    """Build the staff-only overwrite set for one archived tournament channel.

    Every participant-facing role (``@everyone``, this tournament's
    Participant role, its group roles, Playoff Qualified) loses
    ``view_channel`` entirely — archived channels must not merely go
    read-only, they must become invisible to former participants. Staff
    (Administrator/Manage-Server roles), the Manager role, and the bot's own
    member keep full view/read access so history and moderation stay
    possible.
    """
    overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False)}
    for role in channel_roles:
        if role is None:
            continue
        overwrites[role] = discord.PermissionOverwrite(view_channel=False, send_messages=False)
    manager_role = discord.utils.find(lambda r: r.name.casefold() == MANAGER_ROLE.casefold(), guild.roles)
    staff_roles = list(_staff_roles(guild))
    if manager_role is not None:
        staff_roles.append(manager_role)
    for staff_role in staff_roles:
        overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)
    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True)
    return overwrites

async def archive_tournament_resources(guild: discord.Guild, tournament: dict[str, Any]) -> bool:
    """Lock a completed tournament's own channels to staff-only.

    Every tournament-specific channel (resolved strictly by its stored
    Discord ID — never by name) has ``view_channel`` revoked for
    ``@everyone``, the tournament's Participant role, its group roles, and
    Playoff Qualified, so archived channels disappear for former
    participants entirely, not just go read-only. Tournament Staff, Manager,
    Administrator, and the bot itself keep full view access (and the bot
    keeps send/embed/attach access) so history stays available to staff and
    the notice/cleanup messages can still be posted. The archived category
    itself gets the same staff-only overwrites. Processing continues past a
    failure on one resource so the rest still get locked down, but any
    resource that has a stored ID which can no longer be resolved (deleted
    channel/category, stale ID) — or a permission failure while locking one
    down — marks the overall result False. A resource whose stored ID is
    simply absent (never provisioned, e.g. playoffs/group channels for a
    tournament that never reached that stage) is not a failure. This never
    touches the database rows for the tournament, its players, or its
    matches; the caller is responsible for not marking the tournament
    archived when this returns False.
    """
    tid = int(tournament['id'])
    ok = True
    required_resources = ('category_id', 'registration_channel_id', 'fixtures_channel_id', 'results_channel_id', 'announcements_channel_id', 'standings_channel_id', 'tournament_chat_channel_id')
    missing_required = [name for name in required_resources if not tournament.get(name)]
    if missing_required:
        logger.error('Tournament %s archive incomplete: missing required resource ID(s): %s.', tid, ', '.join(missing_required))
        return False
    category = None
    if tournament.get('category_id'):
        maybe_category = guild.get_channel(int(tournament['category_id']))
        if isinstance(maybe_category, discord.CategoryChannel):
            category = maybe_category
        else:
            logger.error('Tournament %s archive incomplete: stored category ID %s could not be resolved to a category.', tid, tournament['category_id'])
            ok = False
    if category is not None and (not category.name.startswith(ARCHIVED_CATEGORY_PREFIX)):
        try:
            await category.edit(name=f"{ARCHIVED_CATEGORY_PREFIX} {tournament['name']}"[:100], reason='Tournament completed: archiving')
        except discord.Forbidden:
            logger.warning('Could not rename category for archived tournament %s (missing Manage Channels).', tid)
            ok = False
        except discord.HTTPException as error:
            logger.warning('Could not rename category for archived tournament %s: %s', tid, error)
            ok = False
    if category is not None:
        try:
            await _apply_overwrites(category, _archived_channel_overwrites(guild, []))
        except discord.Forbidden:
            logger.warning('Could not lock archived category for tournament %s (missing Manage Roles).', tid)
            ok = False
        except discord.HTTPException as error:
            logger.warning('Could not lock archived category for tournament %s: %s', tid, error)
            ok = False
    role = guild.get_role(int(tournament['participant_role_id'])) if tournament.get('participant_role_id') else None
    qualified_role = guild.get_role(int(tournament['playoff_qualified_role_id'])) if tournament.get('playoff_qualified_role_id') else None
    channel_role_pairs: list[tuple[int | None, list[discord.Role]]] = [(tournament.get('registration_channel_id'), [role]), (tournament.get('fixtures_channel_id'), [role]), (tournament.get('match_schedule_channel_id'), [role]), (tournament.get('results_channel_id'), [role]), (tournament.get('tournament_chat_channel_id'), [role]), (tournament.get('announcements_channel_id'), [role]), (tournament.get('standings_channel_id'), [role]), (tournament.get('playoffs_fixtures_channel_id'), [role]), (tournament.get('playoffs_standings_channel_id'), [role]), (tournament.get('playoffs_chat_channel_id'), [role, qualified_role])]
    for group_resource in (await _store_call(bot.store.get_tournament_group_resources, tid)).values():
        group_role_id = group_resource.get('role_id')
        group_role = guild.get_role(int(group_role_id)) if group_role_id else None
        channel_role_pairs.append((group_resource.get('channel_id'), [role, group_role]))
    notice_channel = None
    for attr in ('tournament_chat_channel_id', 'results_channel_id'):
        if tournament.get(attr):
            maybe = guild.get_channel(int(tournament[attr]))
            if isinstance(maybe, discord.TextChannel):
                notice_channel = maybe
                break
    if notice_channel is not None:
        delete_date = datetime.now(UTC) + timedelta(days=ARCHIVE_RETENTION_DAYS)
        try:
            await notice_channel.send(embed=discord.Embed(title='📦 Tournament archived', description=f"**{tournament['name']}** has finished. This channel is now staff-only and will no longer be visible to participants.\n\nThese channels will be automatically removed {_to_discord_timestamp(delete_date.isoformat(), 'R')} ({_to_discord_timestamp(delete_date.isoformat(), 'f')}). The tournament's results and Champions history are kept permanently.", color=discord.Color.dark_gray()))
        except (discord.Forbidden, discord.HTTPException):
            pass
    for channel_id, channel_roles in channel_role_pairs:
        if not channel_id:
            continue
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            logger.error('Tournament %s archive incomplete: stored channel ID %s could not be resolved to a text channel.', tid, channel_id)
            ok = False
            continue
        read_only = _archived_channel_overwrites(guild, [r for r in channel_roles if r is not None])
        try:
            await _apply_overwrites(channel, read_only)
        except discord.Forbidden:
            logger.warning('Could not lock #%s to staff-only for archived tournament %s (missing Manage Roles).', channel.name, tid)
            ok = False
        except discord.HTTPException as error:
            logger.warning('Could not lock #%s to staff-only for tournament %s: %s', channel.name, tid, error)
            ok = False
    return ok

async def run_archive_sweep() -> None:
    """Lock the channels of every newly-completed tournament, across all guilds."""
    for tournament in await _store_call(bot.store.tournaments_pending_archive):
        tid = int(tournament['id'])
        guild = bot.get_guild(int(tournament['guild_id']))
        if guild is None:
            logger.warning('Skipping archive sweep for tournament %s: bot is no longer in guild %s.', tid, tournament['guild_id'])
            continue
        try:
            ok = await archive_tournament_resources(guild, tournament)
        except Exception:
            logger.exception('Failed to archive tournament %s.', tid)
            continue
        if not ok:
            logger.error('Tournament %s archive incomplete; refusing to mark archived.', tid)
            continue
        await _store_call(bot.store.mark_tournament_archived, tid, retention_days=ARCHIVE_RETENTION_DAYS)
        logger.info('Archived tournament %s (%s); pod deletion eligible in %d day(s).', tid, tournament['name'], ARCHIVE_RETENTION_DAYS)

async def run_deletion_sweep() -> None:
    """Delete the Discord pod of every archived tournament past its retention window.

    Reuses cleanup_tournament_resources()'s existing safety guards — this
    never deletes anything by name and never deletes a resource another
    tournament still references. Only the pod's Discord objects go away;
    the tournament row, its matches, players, and Champions data are kept.
    """
    for tournament in await _store_call(bot.store.tournaments_pending_resource_deletion):
        tid = int(tournament['id'])
        guild = bot.get_guild(int(tournament['guild_id']))
        if guild is None:
            logger.warning('Skipping pod deletion for tournament %s: bot is no longer in guild %s.', tid, tournament['guild_id'])
            continue
        try:
            await cleanup_tournament_resources(guild, tournament)
        except Exception:
            logger.exception('Failed to delete the Discord pod for tournament %s.', tid)
            continue
        await _store_call(bot.store.mark_tournament_resources_deleted, tid)
        logger.info('Deleted the archived Discord pod for tournament %s (%s).', tid, tournament['name'])

@tasks.loop(minutes=ARCHIVE_SWEEP_INTERVAL_MINUTES)
async def lifecycle_sweep() -> None:
    try:
        await run_archive_sweep()
        await run_deletion_sweep()
    except Exception:
        logger.exception('Lifecycle sweep failed.')

@lifecycle_sweep.before_loop
async def _before_lifecycle_sweep() -> None:
    await bot.wait_until_ready()

async def ensure_role(guild: discord.Guild, name: str) -> discord.Role:
    role = discord.utils.get(guild.roles, name=name)
    if role:
        return role
    try:
        return await guild.create_role(name=name, reason='Required by the eFootball tournament bot')
    except discord.Forbidden as error:
        raise TournamentError(f'I cannot create the `{name}` role. Give me Manage Roles permission.') from error

def _staff_roles(guild: discord.Guild) -> list[discord.Role]:
    """Roles that should be treated as tournament staff for channel access.

    Discord's Administrator permission already bypasses channel permission
    overwrites entirely, so this is really only needed to give Manage-Server
    moderators (who are not full Administrators) explicit access to
    staff/group-restricted channels.
    """
    return [role for role in guild.roles if role != guild.default_role and (role.permissions.administrator or role.permissions.manage_guild)]

async def ensure_category(guild: discord.Guild, name: str) -> discord.CategoryChannel:
    category = discord.utils.get(guild.categories, name=name)
    if category:
        return category
    try:
        return await guild.create_category(name, reason='Required by the eFootball tournament bot')
    except discord.Forbidden as error:
        raise TournamentError(f'I cannot create the `{name}` category. Give me Manage Channels permission.') from error

async def ensure_text_channel(guild: discord.Guild, name: str, category: discord.CategoryChannel | None=None, *, topic: str | None=None) -> discord.TextChannel:
    """Reuse an existing channel by name, or create it under ``category``.

    An existing channel is never duplicated. If it already exists but sits
    in a different category, it is moved into the correct one so /setup_server
    can improve organization without losing the channel's history or
    permissions set elsewhere.
    """
    channel = discord.utils.get(guild.text_channels, name=name)
    if channel:
        if category is not None and channel.category_id != category.id:
            try:
                await channel.edit(category=category, reason='Tournament server organization')
            except discord.Forbidden:
                pass
        return channel
    try:
        return await guild.create_text_channel(name, category=category, topic=topic, reason='Required by the eFootball tournament bot')
    except discord.Forbidden as error:
        raise TournamentError(f'I cannot create the `#{name}` channel. Give me Manage Channels permission.') from error

async def _apply_overwrites(channel: discord.TextChannel, overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite]) -> None:
    """Update only the overwrites that actually differ from what's already set."""
    for target, overwrite in overwrites.items():
        current = channel.overwrites_for(target)
        if current.pair() == overwrite.pair():
            continue
        await channel.set_permissions(target, overwrite=overwrite, reason='Tournament permission setup')

async def _upsert_bot_embed(channel: discord.TextChannel, embed: discord.Embed) -> None:
    """Post an embed, or edit the bot's previous copy of it if one exists.

    This keeps /setup_server idempotent for reference channels like
    #tournament-rules and #match-rules instead of reposting duplicates every
    time an organizer re-runs the command.
    """
    async for message in channel.history(limit=50):
        if message.author.id == bot.user.id and message.embeds and (message.embeds[0].title == embed.title):
            await message.edit(content=None, embed=embed)
            return
    await channel.send(embed=embed)

async def _replace_bot_embeds(channel: discord.TextChannel, embeds: list[discord.Embed]) -> None:
    """Delete the bot's previous message(s) in this channel, then post fresh embeds.

    Unlike _upsert_bot_embed (which edits a message in place), the standings
    channels should only ever show the latest snapshot with nothing older
    left behind, so the previous standings message is deleted outright and a
    new one is sent, rather than edited.
    """
    async for message in channel.history(limit=25):
        if message.author.id == bot.user.id:
            try:
                await message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
    await channel.send(embeds=embeds)

def build_match_rules_embed() -> discord.Embed:
    embed = discord.Embed(title='⚙️ Match Room Rules', description=f"**GROUP STAGE**\n• Smart Assist: **OFF**\n• Match Duration: **6 minutes**\n• Extra Time: **OFF**\n• Penalties: **OFF**\n• Condition: **Good**\n\n**KNOCKOUT STAGE**\n• Smart Assist: **OFF**\n• Match Duration: **8 minutes**\n• Extra Time: **ON**\n• Penalties: **ON**\n• Condition: **Good**\n\nBoth players must agree to these settings before kicking off. If your opponent won't use these settings, contact staff in #{SUPPORT_CHANNEL} before you play.", color=discord.Color.dark_gold())
    return embed

def build_tournament_rules_embed() -> discord.Embed:
    embed = discord.Embed(title='📖 Tournament Rules', description=f"**1. Registration** — Use `/register` in #{REGISTRATION_CHANNEL} while registration is open. `/unregister` to withdraw.\n\n**2. Group Assignment** — Once registration closes, players are randomly split into Group A/B/C/D and receive the matching Discord role.\n\n**3. Match Coordination** — Find your opponent, agree on a time to play in your group chat, and complete the match by the deadline.\n\n**4. Group-Stage Deadline** — All group-stage matches must be completed within **{GROUP_STAGE_DEADLINE_HOURS} hours** of fixtures being posted. Don't wait until the final hours — contact staff in #{SUPPORT_CHANNEL} before the deadline if your opponent is unavailable or unresponsive.\n\n**5. Result Reporting** — Submit the exact final score with `/report` in #{RESULT_CHANNEL}.\n\n**6. Score Confirmation** — Your opponent must confirm the exact same score before a match is marked complete. If the scores don't match, either player can dispute the result and staff will resolve it.\n\n**7. Match Settings** — See #{MATCH_RULES_CHANNEL} for the required in-game settings for group-stage and knockout matches.\n\n**8. Disconnects / Technical Issues** — Replay the match if possible. If not, contact staff in #{SUPPORT_CHANNEL} before submitting a result.\n\n**9. Unresponsive Opponents** — A win is never awarded automatically for a no-show. Contact staff in #{SUPPORT_CHANNEL} before the deadline; only staff can decide the outcome.\n\n**10. Staff Decisions** — Staff rulings on disputes and unresponsive-opponent cases are final.\n\n**11. Knockout Qualification** — The top 2 players from each group advance to the knockout stage and receive the **Playoff Qualified** role.\n\n**12. Fair Play** — No cheating, exploits, or match-fixing. Staff may ask for evidence such as screenshots or a screen recording when reviewing a dispute.", color=discord.Color.blurple())
    return embed

def build_participant_commands_embed() -> discord.Embed:
    embed = discord.Embed(title='🧭 Participant Command Guide', description=f'**Registration** — #{REGISTRATION_CHANNEL}\n`/register` · `/unregister` · `/tournament_status`\n\n**Matches** — #tournament-fixtures, your group chat, #{PLAYOFFS_CHAT_CHANNEL}\n`/matches`\n\n**Results** — #{RESULT_CHANNEL}\n`/report`\n\n**Information** — anywhere participant commands are allowed\n`/players` · `/standings` · `/tournament_status` · `/tournament_help`', color=discord.Color.teal())
    return embed

async def announce(guild: discord.Guild, content: str='', *, embed: discord.Embed | None=None, tournament: dict[str, Any] | None=None) -> None:
    """Post a tournament event notice to that tournament's own announcements
    channel (tournament['announcements_channel_id']).

    Every tournament-specific announcement (registration open/closed,
    fixtures generated, stage deadlines, qualification, champion, playoff
    updates) MUST pass its ``tournament`` dict so two tournaments running
    concurrently never have their announcements interleaved in one shared
    channel.

    Falls back to the permanent guild-wide #tournament-announcements channel
    only when no tournament is given at all (a genuinely general/server-wide
    notice with no single tournament to attribute it to). A tournament-
    specific notice (``tournament`` given) NEVER falls back to that shared
    channel, even if its own announcements_channel_id is missing/invalid —
    posting a tournament A notice into the shared channel risks it being
    read as belonging to, or interleaved with, tournament B. It is instead
    logged and skipped so this never blocks the underlying command.

    Silently does nothing if no channel can be resolved (e.g. before
    /setup_server or provision_tournament_resources has run), so this never
    blocks the underlying command.
    """
    channel: discord.TextChannel | None = None
    if tournament:
        if tournament.get('announcements_channel_id'):
            maybe_channel = guild.get_channel(int(tournament['announcements_channel_id']))
            if isinstance(maybe_channel, discord.TextChannel):
                channel = maybe_channel
        if channel is None:
            logger.warning('Tournament %s has no resolvable announcements_channel_id; skipping this tournament-specific announcement instead of posting it to the shared announcements channel.', tournament.get('id'))
            return
    else:
        channel = discord.utils.get(guild.text_channels, name=ANNOUNCEMENTS_CHANNEL)
    if channel is None:
        return
    try:
        await channel.send(content=content, embed=embed)
    except discord.Forbidden:
        logger.warning('Cannot post to #%s; give the bot Send Messages there.', channel.name)
    except discord.HTTPException as error:
        logger.warning('Failed to post a tournament announcement: %s', error)

def _to_discord_timestamp(iso_value: str, style: str='f') -> str:
    """Render a stored ISO timestamp as a Discord auto-localized timestamp."""
    try:
        dt = datetime.fromisoformat(iso_value)
    except (TypeError, ValueError):
        return str(iso_value)
    return f'<t:{int(dt.timestamp())}:{style}>'

async def announce_stage_deadline(guild: discord.Guild, tournament_id: int, stage: str, *, qualifier_ids: list[int] | None=None) -> None:
    """Post a stage deadline announcement with Discord's live countdown."""
    tournament = await _store_call(bot.store.get_tournament, tournament_id)
    if not tournament:
        return
    deadline = await _store_call(bot.store.stage_deadline, tournament_id, stage)
    if not deadline:
        logger.warning('No deadline stored for tournament %s stage %s; skipping announcement.', tournament_id, stage)
        return
    title = STAGE_DEADLINE_TITLES.get(stage)
    if not title:
        return
    label = stage_label(stage)
    knockout_stages: tuple[str, ...] = ()
    if tournament['tournament_type'] == GROUP_KNOCKOUT:
        knockout_stages = tuple((await _store_call(bot.store.knockout_shape_for, tournament_id))['knockout_stages'])
    if stage == 'group':
        next_stage_label = stage_label(knockout_stages[0]) if knockout_stages else 'the knockout stage'
        is_last_stage = False
    elif stage == 'league':
        next_stage_label = 'the final standings'
        is_last_stage = True
    elif stage in knockout_stages:
        index = knockout_stages.index(stage)
        if index + 1 < len(knockout_stages):
            next_stage_label = stage_label(knockout_stages[index + 1])
            is_last_stage = False
        else:
            next_stage_label = 'the tournament champion'
            is_last_stage = True
    else:
        next_stage_label = 'the next stage'
        is_last_stage = False
    mentions = ''
    if stage in ('group', 'league'):
        participant_role = await get_tournament_participant_role(guild, tournament)
        if participant_role:
            mentions = f'\n\n{participant_role.mention}'
    elif stage in knockout_stages and stage != knockout_stages[-1]:
        playoff_role = guild.get_role(int(tournament['playoff_qualified_role_id'])) if tournament.get('playoff_qualified_role_id') else None
        if playoff_role:
            mentions = f'\n\n{playoff_role.mention}'
        elif qualifier_ids:
            mentions = '\n\n' + ' '.join((f'<@{int(uid)}>' for uid in qualifier_ids))
    elif qualifier_ids:
        mentions = '\n\n' + ' '.join((f'<@{int(uid)}>' for uid in qualifier_ids))
    message = f"{title}\n\nAll **{label} matches** must be completed by:\n\n📅 {_to_discord_timestamp(deadline, 'F')}\n\n⏳ **Time remaining:** {_to_discord_timestamp(deadline, 'R')}\n\nPlayers are responsible for **coordinating with their opponents** and completing their matches before the deadline.\n\n⚠️ Failure to complete your match within the deadline may result in a **loss or disqualification**, according to the tournament rules.\n\n🤝 Please communicate with your opponent and complete your match on time.\n\n🏆 **Once all {label} matches are completed, {next_stage_label} will begin.**\n\n"
    if is_last_stage:
        if stage == 'league':
            message += '🎉 **The final standings will be posted once the League is completed.**'
        else:
            message += '👑 **The champion will be announced when the Grand Final is completed.**'
    else:
        message += f'🔥 **The {next_stage_label} fixtures will be announced as soon as all {label} matches are completed.**'
    if mentions:
        message += mentions
    await announce(guild, message, tournament=tournament)

async def sync_group_roles(guild: discord.Guild, tournament_id: int) -> None:
    """Assign Participant + exactly one randomized Group role to each player.

    Uses this tournament's own Group A-D roles (never the global "Group A"
    etc. roles), so players in a different concurrently-running tournament
    never end up sharing group-chat access with these players.
    """
    tournament = await _store_call(bot.store.get_tournament, tournament_id)
    if not tournament:
        return
    players = await _store_call(bot.store.players, tournament_id)
    participant_role = await get_tournament_participant_role(guild, tournament)
    category = None
    if tournament.get('category_id'):
        maybe_category = guild.get_channel(int(tournament['category_id']))
        if isinstance(maybe_category, discord.CategoryChannel):
            category = maybe_category
    roles: dict[str, discord.Role]
    if category is not None:
        group_resources = await _ensure_group_and_playoffs_chat_resources(guild, tournament, category=category, staff_roles=_staff_roles(guild), bot_member=guild.me)
        roles = group_resources['group_roles']
    else:
        groups = (await _store_call(bot.store.knockout_shape_for, tournament_id))['groups']
        roles = {group: await ensure_role(guild, f"{tournament['name']} Group {group}") for group in groups}
    failed: list[str] = []
    for player in players:
        group = player.get('group_name')
        if not group:
            continue
        user_id = int(player['user_id'])
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                failed.append(f"{player.get('display_name', user_id)} (not in server)")
                continue
            except discord.HTTPException as error:
                failed.append(f"{player.get('display_name', user_id)} ({error})")
                continue
        try:
            await member.add_roles(participant_role, roles[group], reason='Assigned to tournament group')
            stale_group_roles = [role for name, role in roles.items() if name != group and role in member.roles]
            if stale_group_roles:
                await member.remove_roles(*stale_group_roles, reason='Removing stale tournament group role')
        except discord.Forbidden:
            failed.append(f'{member.display_name} (missing permissions/role hierarchy)')
        except discord.HTTPException as error:
            failed.append(f'{member.display_name} ({error})')
    if failed:
        logger.warning('sync_group_roles: failed to assign group roles for: %s', ', '.join(failed))
        raise TournamentError('Group roles could not be assigned for: ' + ', '.join(failed))

async def sync_playoff_qualified_role(guild: discord.Guild, tournament_id: int, qualifier_ids: list[int]) -> None:
    """Refresh the qualification role, including after moderator corrections.

    Uses this tournament's own Playoff Qualified role (never the global
    "Playoff Qualified" role), so this doesn't grant playoffs-chat access in
    a different, concurrently-running tournament.
    """
    tournament = await _store_call(bot.store.get_tournament, tournament_id)
    role = None
    if tournament and tournament.get('playoff_qualified_role_id'):
        role = guild.get_role(int(tournament['playoff_qualified_role_id']))
    if role is None:
        name = f"{tournament['name']} {PLAYOFF_QUALIFIED_ROLE}" if tournament else PLAYOFF_QUALIFIED_ROLE
        role = await ensure_role(guild, name)
        if tournament:
            await _store_call(bot.store.set_tournament_group_resources, tournament_id, playoff_qualified_role_id=role.id)
    qualifiers = {int(user_id) for user_id in qualifier_ids}
    for player in await _store_call(bot.store.players, tournament_id):
        member = guild.get_member(int(player['user_id']))
        if not member:
            continue
        if int(player['user_id']) in qualifiers:
            await member.add_roles(role, reason='Qualified for playoff knockout')
        elif role in member.roles:
            await member.remove_roles(role, reason='No longer qualified after result correction')

async def clear_tournament_roles(guild: discord.Guild, tournament_id: int) -> None:
    """Remove tournament roles from players after the tournament is complete.

    Resolves this tournament's own dedicated Participant/Group A-D/Playoff
    Qualified roles by their stored IDs first (the modern per-tournament
    architecture creates roles like "<Tournament Name> Participant" rather
    than the legacy shared "Participant" role), falling back to the legacy
    globally-named roles too so older tournaments (created before
    per-tournament roles existed) still get cleaned up correctly.
    """
    tournament = await _store_call(bot.store.get_tournament, tournament_id)
    roles: list[discord.Role] = []
    seen_role_ids: set[int] = set()

    def _add(role: discord.Role | None) -> None:
        if role is not None and role.id not in seen_role_ids:
            seen_role_ids.add(role.id)
            roles.append(role)
    if tournament:
        if tournament.get('participant_role_id'):
            _add(guild.get_role(int(tournament['participant_role_id'])))
        if tournament.get('playoff_qualified_role_id'):
            _add(guild.get_role(int(tournament['playoff_qualified_role_id'])))
        for group_resource in (await _store_call(bot.store.get_tournament_group_resources, tournament_id)).values():
            role_id = group_resource.get('role_id')
            if role_id:
                _add(guild.get_role(int(role_id)))
    for role_name in TOURNAMENT_ROLE_NAMES:
        _add(discord.utils.get(guild.roles, name=role_name))
    if not roles:
        return
    failed: list[str] = []
    for player in await _store_call(bot.store.players, tournament_id):
        user_id = int(player['user_id'])
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                continue
            except discord.HTTPException as error:
                failed.append(f"{player.get('display_name', user_id)} ({error})")
                continue
        assigned_roles = [role for role in roles if role in member.roles]
        if not assigned_roles:
            continue
        try:
            await member.remove_roles(*assigned_roles, reason='Tournament ended; clearing tournament roles')
        except discord.Forbidden:
            failed.append(f'{member.display_name} (missing permissions/role hierarchy)')
        except discord.HTTPException as error:
            failed.append(f'{member.display_name} ({error})')
    if failed:
        logger.warning('clear_tournament_roles: failed to clear roles for: %s', ', '.join(failed))
        raise TournamentError('Tournament roles could not be cleared for: ' + ', '.join(failed))

async def respond(interaction: discord.Interaction, content: str, *, embed: discord.Embed | None=None, ephemeral: bool=False, view: discord.ui.View | None=None) -> None:
    """Safely answer an interaction without causing a second interaction error."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, embed=embed, ephemeral=ephemeral, view=view or discord.utils.MISSING)
        else:
            await interaction.response.send_message(content, embed=embed, ephemeral=ephemeral, view=view or discord.utils.MISSING)
    except discord.NotFound:
        logger.warning('Interaction %s could not be answered (expired/unknown).', interaction.id)
    except discord.HTTPException as error:
        logger.warning('Failed to answer interaction %s: %s', interaction.id, error)

def require_guild(interaction: discord.Interaction) -> discord.Guild:
    if not interaction.guild:
        raise TournamentError('This command can only be used in a server.')
    return interaction.guild

def get_fixture_channel(guild: discord.Guild, tournament: dict[str, Any] | None=None) -> discord.TextChannel | None:
    """Resolve this tournament's fixtures channel strictly by its stored ID.

    A known tournament MUST resolve by its stored ``fixtures_channel_id``
    alone. If that ID is missing or can't be resolved (deleted channel,
    stale ID, never provisioned, etc.) this fails safely and returns None
    rather than falling back to a name-based search, which could silently
    return a different tournament's channel. With no tournament context, the
    resolver fails closed because there is no authoritative resource ID.
    """
    if tournament is not None:
        fixtures_channel_id = tournament.get('fixtures_channel_id')
        if fixtures_channel_id:
            channel = guild.get_channel(int(fixtures_channel_id))
            if isinstance(channel, discord.TextChannel):
                return channel
            logger.warning('Tournament %s has a stored fixtures_channel_id that no longer resolves to a text channel in guild %s.', tournament.get('id'), guild.id)
            return None
        logger.warning('Tournament %s has no stored fixtures_channel_id; refusing to guess by channel name.', tournament.get('id'))
        return None
    return None

def mention_channel(channel: discord.TextChannel | None) -> str:
    return channel.mention if channel else '#tournament-fixtures'


# Resource identity regression sentinel: guild.get_channel(int(tournament["fixtures_channel_id"]))
# get_tournament_group_resources(tournament_id)
# channel = guild.get_channel(int(resource_id))
# channel.edit(name=target_name
SCHEDULE_TIMEZONE = ZoneInfo('Asia/Kolkata')
SCHEDULE_TZ_LABEL = 'IST'


def _match_schedule_label(match: dict[str, Any]) -> str:
    stage = stage_label(str(match.get('stage') or 'match'))
    group = match.get('group_name')
    return f"{stage} — Group {group}" if group and match.get('stage') == 'group' else stage


def _schedule_timestamp(value: str | None) -> str:
    if not value:
        return '—'
    try:
        dt = datetime.fromisoformat(str(value))
        return _to_discord_timestamp(dt.isoformat(), 'F')
    except Exception:
        return str(value)


async def _get_schedule_channel(guild: discord.Guild, tournament: dict[str, Any]) -> discord.TextChannel | None:
    """Resolve the tournament's dedicated match-schedule channel.

    Older tournaments may have a stored channel ID that is not currently in
    cache, so fall back to fetch_channel(). If no ID is stored, backfill the
    channel inside the tournament category and persist its ID.
    """
    channel_id = tournament.get('match_schedule_channel_id')
    if channel_id:
        try:
            channel = guild.get_channel(int(channel_id))
            if isinstance(channel, discord.TextChannel):
                return channel
            fetched = await guild.fetch_channel(int(channel_id))
            if isinstance(fetched, discord.TextChannel):
                return fetched
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.warning(
                'Stored #match-schedule channel %s could not be resolved for tournament %s.',
                channel_id, tournament.get('id')
            )

    category = guild.get_channel(int(tournament['category_id'])) if tournament.get('category_id') else None
    if not isinstance(category, discord.CategoryChannel):
        return None
    try:
        channel = await guild.create_text_channel(
            'match-schedule',
            category=category,
            topic=f"Confirmed match schedules for 🏆 {tournament['name']}. View only.",
            reason='eFootball tournament bot: scheduling backfill'
        )
        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True, send_messages=False, read_message_history=True
            )
        }
        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True, read_message_history=True
            )
        for staff_role in _staff_roles(guild):
            overwrites[staff_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )
        await _apply_overwrites(channel, overwrites)
        await _store_call(
            bot.store.set_tournament_channels,
            int(tournament['id']),
            match_schedule_channel_id=channel.id
        )
        return channel
    except (discord.Forbidden, discord.HTTPException):
        logger.exception('Could not backfill #match-schedule for tournament %s.', tournament.get('id'))
        return None


async def _post_confirmed_schedule_message(
    guild: discord.Guild,
    tournament: dict[str, Any],
    match: dict[str, Any],
    *,
    channel: discord.TextChannel | None = None,
) -> discord.Message:
    """Publish one confirmed schedule and persist its Discord message ID."""
    player_rows = await _store_call(bot.store.players, int(match['tournament_id']))
    labels = tournament_player_labels(player_rows)
    role_mentions: dict[int, str] = {}
    if str(tournament.get('template_id') or '') in WORLD_CUP_TEMPLATE_IDS:
        role_mentions = await world_cup_player_role_mentions(
            guild, int(match['tournament_id']), player_rows
        )

    def scheduled_player(user_id: int) -> str:
        if user_id in role_mentions:
            return role_mentions[user_id]
        return format_user(guild, user_id, labels)

    if channel is None:
        channel = await _get_schedule_channel(guild, tournament)
    if channel is None:
        raise TournamentError("I could not find or create the tournament's #match-schedule channel.")

    allowed_mentions = discord.AllowedMentions(
        roles=bool(role_mentions),
        users=not bool(role_mentions),
        everyone=False,
    )
    message = await channel.send(
        content=(
            f"📅 **Match Scheduled**\n\n"
            f"⚔️ {scheduled_player(int(match['player1_id']))} vs "
            f"{scheduled_player(int(match['player2_id']))}\n\n"
            f"🏆 **{_match_schedule_label(match)}**\n"
            f"📅 {_schedule_timestamp(match.get('schedule_proposed_for'))}\n"
            f"⏰ **{SCHEDULE_TZ_LABEL}**\n\n"
            f"✅ **Both players have confirmed.**"
        ),
        allowed_mentions=allowed_mentions,
    )
    await _store_call(bot.store.mark_schedule_message_posted, int(match['id']), int(message.id))
    return message


async def _schedule_signature_exists(
    channel: discord.TextChannel,
    tournament: dict[str, Any],
    match: dict[str, Any],
) -> discord.Message | None:
    """Return an existing schedule post, including posts created by V32."""
    player_rows = await _store_call(bot.store.players, int(match['tournament_id']))
    labels = tournament_player_labels(player_rows)
    role_mentions: dict[int, str] = {}
    if str(tournament.get('template_id') or '') in WORLD_CUP_TEMPLATE_IDS:
        role_mentions = await world_cup_player_role_mentions(
            channel.guild, int(match['tournament_id']), player_rows
        )

    def scheduled_player(user_id: int) -> str:
        if user_id in role_mentions:
            return role_mentions[user_id]
        return format_user(channel.guild, user_id, labels)

    required = (
        '📅 **Match Scheduled**',
        scheduled_player(int(match['player1_id'])),
        scheduled_player(int(match['player2_id'])),
        f"🏆 **{_match_schedule_label(match)}**",
        f"📅 {_schedule_timestamp(match.get('schedule_proposed_for'))}",
        f"⏰ **{SCHEDULE_TZ_LABEL}**",
    )
    try:
        async for message in channel.history(limit=1000):
            content = message.content or ''
            if all(part in content for part in required):
                return message
    except (discord.Forbidden, discord.HTTPException):
        logger.warning('Could not scan #match-schedule for existing message for match %s.', match.get('id'))
    return None


async def _backfill_confirmed_schedule_messages(
    guild: discord.Guild,
    tournament: dict[str, Any],
) -> int:
    """Reconcile confirmed schedules after a deploy/restart without duplicates."""
    if str(tournament.get('template_id') or '') not in WORLD_CUP_TEMPLATE_IDS:
        return 0
    rows = await _store_call(bot.store.matches, int(tournament['id']))
    confirmed = [
        row for row in rows
        if str(row.get('schedule_status') or '').lower() == 'confirmed'
        and row.get('schedule_proposed_for')
    ]
    if not confirmed:
        return 0

    channel = await _get_schedule_channel(guild, tournament)
    if channel is None:
        logger.warning('Could not resolve #match-schedule for World Cup %s during startup backfill.', tournament.get('id'))
        return 0

    posted = 0
    for match in confirmed:
        if match.get('schedule_message_id'):
            try:
                await channel.fetch_message(int(match['schedule_message_id']))
                continue
            except discord.NotFound:
                pass
            except (discord.Forbidden, discord.HTTPException):
                # Fall through to signature matching so an inaccessible/deleted
                # message does not permanently block recovery.
                pass

        existing = await _schedule_signature_exists(channel, tournament, match)
        if existing:
            await _store_call(bot.store.mark_schedule_message_posted, int(match['id']), int(existing.id))
            continue

        try:
            await _post_confirmed_schedule_message(guild, tournament, match, channel=channel)
            posted += 1
        except (discord.Forbidden, discord.HTTPException, TournamentError):
            logger.exception('Failed to backfill confirmed schedule for match %s in tournament %s.', match.get('id'), tournament.get('id'))
    return posted


async def _recover_confirmed_schedules_on_startup(guild: discord.Guild) -> None:
    """Recover already-confirmed World Cup schedules after a bot deployment."""
    tournaments = await _store_call(bot.store.list_tournaments, guild.id, (OPEN, CLOSED))
    for tournament in tournaments:
        if str(tournament.get('template_id') or '') not in WORLD_CUP_TEMPLATE_IDS:
            continue
        try:
            posted = await _backfill_confirmed_schedule_messages(guild, tournament)
            if posted:
                logger.info('Startup schedule recovery posted %d confirmed World Cup schedule(s) for tournament %s.', posted, tournament.get('id'))
        except Exception:
            logger.exception('Startup confirmed-schedule recovery failed for tournament %s.', tournament.get('id'))


async def _send_schedule_prompt(guild: discord.Guild, match: dict[str, Any]) -> None:
    """DM both players once per fixture, immediately after fixture release."""
    if match.get('status') == 'completed':
        return
    tournament_id = int(match['tournament_id'])
    tournament = await _store_call(bot.store.get_tournament, tournament_id)
    if not tournament:
        return
    deadline = match.get('stage_deadline_at') or await _store_call(bot.store.stage_deadline, tournament_id, str(match.get('stage') or ''))
    deadline_text = _schedule_timestamp(deadline)
    label = _match_schedule_label(match)
    for slot, key in ((1, 'schedule_prompted_player1_at'), (2, 'schedule_prompted_player2_at')):
        player_id = int(match[f'player{slot}_id'])
        if match.get(key):
            continue
        member = guild.get_member(player_id)
        if member is None:
            try:
                member = await guild.fetch_member(player_id)
            except discord.HTTPException:
                logger.warning('Could not fetch player %s for scheduling prompt for match %s.', player_id, match['id'])
                continue
        try:
            view = MatchSchedulePromptView(int(match['id']))
            labels = tournament_player_labels(await _store_call(bot.store.players, int(match['tournament_id'])))
            embed = discord.Embed(
                title='📅 Match Scheduling Required',
                description=(f"Your **{label}** fixture is ready.\n\n"
                             f"⚔️ **Opponent:** {format_user(guild, int(match['player2_id'] if slot == 1 else match['player1_id']), labels)}\n"
                             f"⏰ **Scheduling deadline:** {deadline_text}\n\n"
                             f"You must **mutually confirm a match time before the deadline**. "
                             f"Use **📅 Propose Time** below. The agreed schedule will be posted publicly in the tournament's `#match-schedule` channel."),
                color=discord.Color.blurple(),
            )
            await member.send(embed=embed, view=view)
            await _store_call(bot.store.mark_schedule_prompted, int(match['id']), player_id)
        except discord.Forbidden:
            logger.warning('Could not DM player %s for match scheduling; DMs may be disabled.', player_id)
        except discord.HTTPException as error:
            logger.warning('Discord error while DMing player %s for match %s scheduling: %s', player_id, match['id'], error)


async def _prompt_scheduling_for_matches(guild: discord.Guild, tournament_id: int, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        try:
            enriched = await _store_call(bot.store.get_match_schedule, int(row['id']))
            await _send_schedule_prompt(guild, enriched or row)
        except Exception:
            logger.exception('Failed to send scheduling prompt for match %s in tournament %s.', row.get('id'), tournament_id)


class MatchScheduleModal(discord.ui.Modal):
    def __init__(self, match_id: int):
        super().__init__(title='Propose Match Time')
        self.match_id = int(match_id)
        self.date_input = discord.ui.TextInput(label='Date (YYYY-MM-DD)', placeholder='2026-09-14', min_length=10, max_length=10)
        self.time_input = discord.ui.TextInput(label='Time (24h IST)', placeholder='21:00', min_length=5, max_length=5)
        self.add_item(self.date_input)
        self.add_item(self.time_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            proposed_dt = datetime.strptime(f'{self.date_input.value.strip()} {self.time_input.value.strip()}', '%Y-%m-%d %H:%M').replace(tzinfo=SCHEDULE_TIMEZONE)
            if proposed_dt <= datetime.now(SCHEDULE_TIMEZONE):
                raise TournamentError('The proposed match time must be in the future.')
            match = await _store_call(bot.store.get_match_schedule, self.match_id)
            if not match:
                raise TournamentError('Match not found.')
            deadline = match.get('stage_deadline_at')
            if deadline and proposed_dt >= datetime.fromisoformat(str(deadline)):
                raise TournamentError('The proposed match time must be before the fixture deadline.')
            result = await _store_call(bot.store.propose_match_schedule, self.match_id, interaction.user.id, proposed_dt.astimezone(UTC).isoformat())
            guild = bot.get_guild(int(result['guild_id']))
            if guild is None:
                raise TournamentError('The tournament server is not available to the bot right now.')
            opponent_id = int(result['player2_id']) if int(result['player1_id']) == interaction.user.id else int(result['player1_id'])
            opponent = guild.get_member(opponent_id)
            if opponent is None:
                try:
                    opponent = await guild.fetch_member(opponent_id)
                except discord.HTTPException:
                    opponent = None
            if opponent:
                labels = tournament_player_labels(await _store_call(bot.store.players, int(result['tournament_id'])))
                proposer_label = labels.get(int(interaction.user.id), interaction.user.display_name)
                await opponent.send(embed=discord.Embed(title='📅 Match Time Proposal', description=(f"**{proposer_label}** proposed a time for your **{_match_schedule_label(result)}**.\n\n📅 **When:** {_schedule_timestamp(result.get('schedule_proposed_for'))}\n⏰ **Deadline:** {_schedule_timestamp(result.get('stage_deadline_at'))}\n\nPlease choose **Accept**, **Decline**, or **Propose Another Time**."), color=discord.Color.blurple()), view=MatchScheduleResponseView(self.match_id))
            await interaction.response.send_message('✅ Your proposal was sent privately to your opponent.', ephemeral=True)
        except ValueError:
            await interaction.response.send_message('❌ Use date `YYYY-MM-DD` and time `HH:MM` (24-hour IST).', ephemeral=True)
        except TournamentError as error:
            await interaction.response.send_message(f'❌ {error}', ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message('❌ Your opponent has DMs disabled, so I could not deliver the proposal. Please contact staff.', ephemeral=True)
        except Exception:
            logger.exception('Failed to submit schedule proposal for match %s.', self.match_id)
            await interaction.response.send_message('❌ Something went wrong sending the proposal. Please try again or contact staff.', ephemeral=True)


class MatchSchedulePromptButton(discord.ui.DynamicItem[discord.ui.Button], template=r'match_schedule_prompt:(?P<match_id>\d+)'):
    def __init__(self, match_id: int):
        super().__init__(discord.ui.Button(label='Propose Time', style=discord.ButtonStyle.primary, emoji='📅', custom_id=f'match_schedule_prompt:{match_id}'))
        self.match_id = int(match_id)

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]) -> 'MatchSchedulePromptButton':
        return cls(int(match['match_id']))

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(MatchScheduleModal(self.match_id))


class MatchSchedulePromptView(discord.ui.View):
    def __init__(self, match_id: int):
        super().__init__(timeout=None)
        self.add_item(MatchSchedulePromptButton(match_id))


class MatchScheduleResponseView(discord.ui.View):
    def __init__(self, match_id: int):
        super().__init__(timeout=None)
        self.add_item(MatchScheduleAcceptButton(match_id))
        self.add_item(MatchScheduleDeclineButton(match_id))
        self.add_item(MatchScheduleAnotherButton(match_id))


class MatchScheduleAcceptButton(discord.ui.DynamicItem[discord.ui.Button], template=r'match_schedule_accept:(?P<match_id>\d+)'):
    def __init__(self, match_id: int):
        super().__init__(discord.ui.Button(label='Accept', style=discord.ButtonStyle.success, emoji='✅', custom_id=f'match_schedule_accept:{match_id}'))
        self.match_id = int(match_id)
    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match['match_id']))
    async def callback(self, interaction):
        try:
            # Button interactions happen in DMs, so interaction.guild is None.
            # Resolve the authoritative guild from the match's stored guild ID.
            match_before = await _store_call(bot.store.get_match_schedule, self.match_id)
            if not match_before:
                raise TournamentError('Match not found.')
            guild = bot.get_guild(int(match_before['guild_id']))
            if guild is None:
                raise TournamentError('The tournament server is not available to the bot right now.')
            result = await _store_call(bot.store.respond_match_schedule, self.match_id, interaction.user.id, 'accept')

            # Only publish after the database confirms the schedule. The
            # previous version could report success even when the channel
            # message was never sent.
            if str(result.get('schedule_status') or '').lower() != 'confirmed':
                raise TournamentError('The schedule was not confirmed. Please try again or contact staff.')

            tournament = await _store_call(bot.store.get_tournament, int(result['tournament_id']))
            if not tournament:
                raise TournamentError('The tournament for this match could not be found.')

            await _post_confirmed_schedule_message(guild, tournament, result)
            proposer = guild.get_member(int(result['schedule_proposer_id'])) if result.get('schedule_proposer_id') else None
            if proposer is None and result.get('schedule_proposer_id'):
                try:
                    proposer = await guild.fetch_member(int(result['schedule_proposer_id']))
                except discord.HTTPException:
                    proposer = None
            if proposer:
                try:
                    labels = tournament_player_labels(await _store_call(bot.store.players, int(result['tournament_id'])))
                    opponent_label = labels.get(int(interaction.user.id), interaction.user.display_name)
                    await proposer.send(f"✅ **Schedule confirmed!** Your match with **{opponent_label}** is scheduled for **{_schedule_timestamp(result.get('schedule_proposed_for'))}**.")
                except discord.HTTPException: pass
            await interaction.response.edit_message(content='✅ **Schedule accepted.** The confirmed match time has been posted successfully in the tournament `#match-schedule` channel.', embed=None, view=None)
        except TournamentError as error:
            await interaction.response.send_message(f'❌ {error}', ephemeral=True)
        except Exception:
            logger.exception('Failed to accept match schedule %s.', self.match_id)
            await interaction.response.send_message('❌ Failed to confirm the schedule. Please contact staff.', ephemeral=True)


class MatchScheduleDeclineButton(discord.ui.DynamicItem[discord.ui.Button], template=r'match_schedule_decline:(?P<match_id>\d+)'):
    def __init__(self, match_id: int):
        super().__init__(discord.ui.Button(label='Decline', style=discord.ButtonStyle.danger, emoji='❌', custom_id=f'match_schedule_decline:{match_id}'))
        self.match_id = int(match_id)
    @classmethod
    async def from_custom_id(cls, interaction, item, match): return cls(int(match['match_id']))
    async def callback(self, interaction):
        try:
            match_before = await _store_call(bot.store.get_match_schedule, self.match_id)
            if not match_before:
                raise TournamentError('Match not found.')
            proposer_id = int(match_before['schedule_proposer_id']) if match_before.get('schedule_proposer_id') else None
            guild = bot.get_guild(int(match_before['guild_id']))
            result = await _store_call(bot.store.respond_match_schedule, self.match_id, interaction.user.id, 'decline')
            if guild:
                proposer = guild.get_member(proposer_id) if proposer_id else None
                if proposer is None and proposer_id:
                    try:
                        proposer = await guild.fetch_member(proposer_id)
                    except discord.HTTPException:
                        proposer = None
                if proposer:
                    try: await proposer.send(f"❌ **Schedule declined.** {interaction.user.mention} declined the proposed time for your match. You can propose another time.")
                    except discord.HTTPException: pass
            await interaction.response.edit_message(content='❌ **Schedule declined.** You can propose another time.', embed=None, view=None)
        except TournamentError as error:
            await interaction.response.send_message(f'❌ {error}', ephemeral=True)
        except Exception:
            logger.exception('Failed to decline match schedule %s.', self.match_id)
            await interaction.response.send_message('❌ Failed to decline the schedule. Please contact staff.', ephemeral=True)


class MatchScheduleAnotherButton(discord.ui.DynamicItem[discord.ui.Button], template=r'match_schedule_another:(?P<match_id>\d+)'):
    def __init__(self, match_id: int):
        super().__init__(discord.ui.Button(label='Propose Another Time', style=discord.ButtonStyle.secondary, emoji='🔄', custom_id=f'match_schedule_another:{match_id}'))
        self.match_id = int(match_id)
    @classmethod
    async def from_custom_id(cls, interaction, item, match): return cls(int(match['match_id']))
    async def callback(self, interaction):
        await interaction.response.send_modal(MatchScheduleModal(self.match_id))

async def post_fixtures(guild: discord.Guild, tournament_id: int) -> bool:
    """Post generated fixtures to this tournament's own fixtures channel."""
    tournament = await _store_call(bot.store.get_tournament, tournament_id)
    if not tournament:
        return False
    channel = get_fixture_channel(guild, tournament)
    if channel is None:
        logger.warning('Fixture channel not found in guild %s', guild.id)
        return False
    rows = await _store_call(bot.store.matches, tournament_id)
    if not rows:
        return False
    player_rows = await _store_call(bot.store.players, tournament_id)
    player_labels = tournament_player_labels(player_rows)
    use_country_roles = str(tournament.get('template_id') or '') in WORLD_CUP_TEMPLATE_IDS
    role_mentions: dict[int, str] = {}
    if use_country_roles:
        role_mentions = await world_cup_player_role_mentions(guild, tournament_id, player_rows)
    player_ids = {int(r['player1_id']) for r in rows} | {int(r['player2_id']) for r in rows}
    lines = []
    def fixture_player(user_id: int) -> str:
        if use_country_roles and user_id in role_mentions:
            return role_mentions[user_id]
        return format_user(guild, user_id, player_labels)
    if tournament['tournament_type'] == GROUP_KNOCKOUT:
        tournament_groups = (await _store_call(bot.store.knockout_shape_for, tournament_id))['groups']
        for group in tournament_groups:
            group_rows = [r for r in rows if r.get('group_name') == group and r.get('stage') == 'group']
            if not group_rows:
                continue
            lines.append(f'**Group {group}**')
            for i, row in enumerate(group_rows, 1):
                lines.append(f"`#{i}` {fixture_player(int(row['player1_id']))} vs {fixture_player(int(row['player2_id']))}")
            lines.append('')
    else:
        for i, row in enumerate(rows, 1):
            lines.append(f"`#{i}` {fixture_player(int(row['player1_id']))} vs {fixture_player(int(row['player2_id']))}")
    if use_country_roles:
        role_ping_ids = []
        seen_roles: set[int] = set()
        for uid in sorted(player_ids):
            role_mention = role_mentions.get(uid)
            if not role_mention:
                continue
            role_id = int(re.search(r'<@&(\d+)>', role_mention).group(1))
            if role_id not in seen_roles:
                seen_roles.add(role_id)
                role_ping_ids.append(role_id)
        mentions = ' '.join((f'<@&{role_id}>' for role_id in role_ping_ids))
        allowed_mentions = discord.AllowedMentions(roles=True, users=False, everyone=False)
    else:
        mentions = ' '.join((f'<@{uid}>' for uid in sorted(player_ids)))
        allowed_mentions = discord.AllowedMentions(users=True, roles=False, everyone=False)
    embed = discord.Embed(title=f"🏆 {tournament['name']} — Fixtures", description='\n'.join(lines).strip(), color=discord.Color.blurple())
    if tournament['tournament_type'] == GROUP_KNOCKOUT and tournament.get('group_stage_deadline_at'):
        embed.add_field(name='⏰ Group-stage deadline', value=f"All group-stage matches must be completed by {_to_discord_timestamp(tournament['group_stage_deadline_at'])}. Coordinate with your opponent in your group chat and contact #{SUPPORT_CHANNEL} early if they're unresponsive.", inline=False)
    embed.set_footer(text='Use /matches to view your fixtures and /report to submit a result.')
    await channel.send(content=f'📢 Fixtures are now available!\n{mentions}', embed=embed, allowed_mentions=allowed_mentions)
    await _prompt_scheduling_for_matches(guild, tournament_id, rows)
    return True

def _bracket_font(size: int, bold: bool=False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a common Unicode-capable font, with a Pillow fallback."""
    candidates = ('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf')
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()

def _bracket_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, fill: tuple[int, int, int], anchor: str='la') -> None:
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)

def _crop_avatar(image_bytes: bytes, size: int) -> Image.Image:
    """Convert a Discord avatar into a circular RGBA image."""
    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            image = ImageOps.fit(source.convert('RGBA'), (size, size), method=Image.Resampling.LANCZOS)
            mask = Image.new('L', (size, size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
            image.putalpha(mask)
            return image
    except Exception:
        return Image.new('RGBA', (size, size), (42, 48, 62, 255))

async def _get_player_avatar(guild: discord.Guild, user_id: int, size: int) -> Image.Image:
    """Fetch a player's current Discord avatar, falling back to a letter tile."""
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            member = None
    if member is not None:
        try:
            data = await member.display_avatar.replace(size=max(128, size), static_format='png').read()
            return _crop_avatar(data, size)
        except (discord.HTTPException, OSError, ValueError):
            pass
    image = Image.new('RGBA', (size, size), (47, 62, 91, 255))
    draw = ImageDraw.Draw(image)
    initial = str(user_id)[-1]
    font = _bracket_font(max(18, size // 2), True)
    draw.text((size // 2, size // 2), initial, font=font, fill=(220, 230, 255), anchor='mm')
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    image.putalpha(mask)
    return image
_BRACKET_TIERS = [(4, 420, 170, 64, 50, 24, 19, 25, 26, 21, 70), (8, 380, 120, 46, 28, 19, 15, 19, 21, 18, 60), (16, 340, 78, 30, 14, 14, 11, 14, 16, 14, 50)]
_BRACKET_SLOT_CODES = {'round_of_32': 'R32', 'round_of_16': 'R16', 'quarterfinal': 'QF', 'semifinal': 'SF'}

def _bracket_tier_for(first_round_count: int) -> tuple:
    for max_matches, *rest in _BRACKET_TIERS:
        if first_round_count <= max_matches:
            return tuple(rest)
    return tuple(_BRACKET_TIERS[-1][1:])

async def _build_playoff_bracket_image(guild: discord.Guild, tournament_id: int) -> tuple[discord.File, set[int]]:
    """Render this tournament's complete knockout bracket as a PNG.

    Draws a standard single-elimination tree for however many rounds this
    tournament's template actually has (3 rounds for Weekly Championship,
    4 for Champions League, 5 for World Cup 48/64 -- see
    TournamentStore.knockout_shape_for), rather than a fixed
    Quarterfinal->Semifinal->Final layout. Card size, fonts, and avatar size
    scale down for brackets with more first-round matches so the whole tree
    stays readable in one image.
    """
    shape = await _store_call(bot.store.knockout_shape_for, tournament_id)
    rounds: list[str] = list(shape['knockout_stages'])
    rows_by_stage = {stage: await _store_call(bot.store.matches, tournament_id, stage=stage) for stage in rounds}
    player_rows = await _store_call(bot.store.players, tournament_id)
    display_names = {int(row['user_id']): str(row.get('nation_name') or row['display_name']) for row in player_rows}
    first_round_count = 2 ** (len(rounds) - 1)
    card_w, card_h, avatar, leaf_gap, name_font_size, small_font_size, score_font_size, stage_font_size, name_trunc, col_gap = _bracket_tier_for(first_round_count)
    rows_by_round: list[list[dict[str, Any] | None]] = []
    for index, stage in enumerate(rounds):
        expected = 2 ** (len(rounds) - 1 - index)
        stage_rows = rows_by_stage[stage][:expected]
        stage_rows = stage_rows + [None] * (expected - len(stage_rows))
        rows_by_round.append(stage_rows)
    all_ids: set[int] = set()
    for stage_rows in rows_by_stage.values():
        for row in stage_rows:
            all_ids.add(int(row['player1_id']))
            all_ids.add(int(row['player2_id']))
    bg = (8, 14, 24)
    panel = (18, 27, 42)
    line = (77, 112, 174)
    text = (239, 243, 250)
    muted = (151, 166, 190)
    gold = (245, 191, 72)
    green = (62, 211, 151)
    pending = (108, 124, 150)
    title_font = _bracket_font(48, True)
    stage_font = _bracket_font(stage_font_size, True)
    name_font = _bracket_font(name_font_size, True)
    small_font = _bracket_font(small_font_size, False)
    score_font = _bracket_font(score_font_size, True)
    left_margin, top_margin, bottom_margin = (45, 150, 200)
    x_positions = [left_margin + col * (card_w + col_gap) for col in range(len(rounds) + 1)]
    slot_height = card_h + leaf_gap
    centers_by_round: list[list[int]] = [[top_margin + i * slot_height + card_h // 2 for i in range(first_round_count)]]
    for _ in range(1, len(rounds)):
        previous = centers_by_round[-1]
        centers_by_round.append([(previous[2 * i] + previous[2 * i + 1]) // 2 for i in range(len(previous) // 2)])
    champion_center = centers_by_round[-1][0]
    width = x_positions[-1] + card_w + left_margin
    height = top_margin + (first_round_count - 1) * slot_height + card_h + bottom_margin
    image = Image.new('RGB', (width, height), bg)
    draw = ImageDraw.Draw(image)
    draw.text((width // 2, 45), 'PLAYOFFS KNOCKOUT BRACKET', font=title_font, fill=text, anchor='ma')
    subtitle = '  →  '.join((stage_label(stage) for stage in rounds))
    draw.text((width // 2, 96), subtitle, font=small_font, fill=muted, anchor='ma')
    row_first_offset = max(int(card_h * 0.26), avatar // 3)
    row_spacing = max(int(card_h * 0.34), avatar + 8)
    name_x_offset = avatar + 34

    def draw_header(x: int, y: int, label: str) -> None:
        draw.text((x + card_w // 2, y - stage_font_size - 8), label, font=stage_font, fill=gold, anchor='ma')

    def draw_match_card(x: int, y: int, row: dict[str, Any] | None, slot_label: str) -> tuple[int, int]:
        draw.rounded_rectangle((x, y, x + card_w, y + card_h), radius=16, fill=panel, outline=(44, 62, 88), width=2)
        draw.text((x + 16, y + 10), slot_label, font=small_font, fill=muted)
        if row is None:
            draw.text((x + card_w // 2, y + card_h // 2), 'PENDING', font=name_font, fill=pending, anchor='mm')
            return (x + card_w, y + card_h // 2)
        ids = [int(row['player1_id']), int(row['player2_id'])]
        scores = [row.get('score1'), row.get('score2')]
        status = str(row.get('status'))
        for index, user_id in enumerate(ids):
            row_y = y + row_first_offset + index * row_spacing
            avatar_img = avatar_cache.get(user_id)
            if avatar_img is None:
                avatar_img = Image.new('RGBA', (avatar, avatar), (47, 62, 91, 255))
            image.paste(avatar_img, (x + 14, row_y), avatar_img)
            label = display_names.get(user_id, f'Player {user_id}')
            if len(label) > name_trunc:
                label = label[:name_trunc - 1] + '…'
            draw.text((x + name_x_offset, row_y + avatar // 2), f'@{label}', font=name_font, fill=text, anchor='lm')
            if status == 'completed' and scores[index] is not None:
                draw.text((x + card_w - 20, row_y + avatar // 2), str(scores[index]), font=score_font, fill=green if scores[index] == max(scores[0], scores[1]) else text, anchor='rm')
            else:
                draw.text((x + card_w - 20, row_y + avatar // 2), '–', font=score_font, fill=pending, anchor='rm')
        return (x + card_w, y + card_h // 2)
    avatar_cache: dict[int, Image.Image] = {}
    for user_id in sorted(all_ids):
        avatar_cache[user_id] = await _get_player_avatar(guild, user_id, avatar)
    all_centers: list[list[tuple[int, int]]] = []
    for round_index, stage in enumerate(rounds):
        is_final = round_index == len(rounds) - 1
        header = 'GRAND FINAL' if is_final else stage_label(stage).upper()
        draw_header(x_positions[round_index], centers_by_round[round_index][0] - card_h // 2, header)
        round_centers = []
        for slot_index, row in enumerate(rows_by_round[round_index]):
            slot_label = 'GRAND FINAL' if is_final else f'{_BRACKET_SLOT_CODES.get(stage, stage.upper())} {slot_index + 1}'
            top_y = centers_by_round[round_index][slot_index] - card_h // 2
            right_x, center_y = draw_match_card(x_positions[round_index], top_y, row, slot_label)
            round_centers.append((right_x, center_y))
        all_centers.append(round_centers)
    final_row = rows_by_round[-1][0] if rows_by_round[-1] else None
    x_champ = x_positions[-1]
    champ_y = champion_center - card_h // 2
    draw_header(x_champ, champ_y, 'CHAMPION')
    champion_id: int | None = None
    if final_row and str(final_row.get('status')) == 'completed':
        champion_id = int(final_row['player1_id']) if int(final_row['score1']) > int(final_row['score2']) else int(final_row['player2_id'])
    if champion_id is not None:
        draw.rounded_rectangle((x_champ, champ_y, x_champ + card_w, champ_y + card_h), radius=16, fill=(38, 34, 24), outline=gold, width=3)
        champion_avatar = avatar_cache.get(champion_id)
        avatar_y = champ_y + max(card_h // 3, 12)
        if champion_avatar:
            image.paste(champion_avatar, (x_champ + card_w // 2 - avatar // 2, avatar_y), champion_avatar)
        champ_name = display_names.get(champion_id, f'Player {champion_id}')
        draw.text((x_champ + card_w // 2, avatar_y + avatar + 22), champ_name[:name_trunc], font=name_font, fill=text, anchor='ma')
    else:
        draw.rounded_rectangle((x_champ, champ_y, x_champ + card_w, champ_y + card_h), radius=16, fill=panel, outline=(44, 62, 88), width=2)
        draw.text((x_champ + card_w // 2, champ_y + card_h // 2), 'WINNER PENDING', font=name_font, fill=pending, anchor='mm')

    def connector(points: list[tuple[int, int]], target: tuple[int, int]) -> None:
        mid_x = (points[0][0] + target[0]) // 2
        for px, py in points:
            draw.line((px, py, mid_x, py), fill=line, width=4)
            draw.line((mid_x, min(py, target[1]), mid_x, max(py, target[1])), fill=line, width=4)
        draw.line((mid_x, target[1], target[0], target[1]), fill=line, width=4)
    for round_index in range(1, len(rounds)):
        children = all_centers[round_index - 1]
        for slot_index, (_, center_y) in enumerate(all_centers[round_index]):
            target_x = x_positions[round_index]
            connector([children[2 * slot_index], children[2 * slot_index + 1]], (target_x, center_y))
    final_right_x, final_center_y = all_centers[-1][0]
    draw.line((final_right_x, final_center_y, x_champ, champ_y + card_h // 2), fill=gold, width=5)
    draw.text((width // 2, height - 28), 'Use /matches for the current active stage. Scores shown are official tournament results.', font=small_font, fill=muted, anchor='ms')
    output = io.BytesIO()
    image.save(output, format='PNG', optimize=True)
    output.seek(0)
    return (discord.File(output, filename='playoffs-bracket.png'), all_ids)

def _bracket_image_supported(tournament_id: int) -> bool:
    """Whether _build_playoff_bracket_image can render this tournament's
    bracket.

    The renderer draws a standard single-elimination tree, where each round
    halves the previous round's match count down to 1 (the final) -- true
    for every current template's shape (3, 4, and 5 knockout rounds), so
    this only returns False for a hypothetical future template that isn't
    shaped that way, in which case post_playoff_fixtures falls back to a
    text-only fixture list rather than the renderer guessing at a layout.
    """
    shape = bot.store.knockout_shape_for(tournament_id)
    stages = shape['knockout_stages']
    if not stages:
        return False
    total_qualifiers = len(shape['groups']) * shape['qualify_per_group']
    if total_qualifiers % 2 != 0:
        return False
    return total_qualifiers // 2 == 2 ** (len(stages) - 1)

def _build_text_fixture_list(guild: discord.Guild, rows: list[dict[str, Any]], player_labels: dict[int, str] | None=None) -> str:
    """Plain-text fixture list, used where the PNG bracket can't be rendered."""
    return '\n'.join((f"`#{index}` {format_user(guild, int(row['player1_id']), player_labels)} vs {format_user(guild, int(row['player2_id']), player_labels)}" for index, row in enumerate(rows, 1)))

async def post_playoff_fixtures(guild: discord.Guild, tournament_id: int, stage: str | None=None) -> bool:
    """Post the current knockout bracket to this tournament's own
    #<slug>-playoffs-fixtures channel.

    Where the fixed-layout bracket renderer supports this tournament's shape
    (see _bracket_image_supported), the bracket is rendered as a PNG with
    avatars and connector lines. Otherwise (Champions League/World Cup, whose
    extra knockout rounds the renderer can't yet draw) a plain-text fixture
    list is posted instead. Either way the accompanying message contains real
    Discord mentions. Resolved by this tournament's stored channel ID, never
    by a shared channel name, so concurrent tournaments never collide.
    """
    tournament = await _store_call(bot.store.get_tournament, tournament_id)
    if not tournament:
        return False
    channels = await _ensure_standings_and_playoffs_channels(guild, tournament)
    channel = channels['playoffs_fixtures_channel']
    if channel is None:
        logger.warning('Playoff fixture channel not found for tournament %s in guild %s', tournament_id, guild.id)
        return False
    active_stage = stage or await _call_off_loop(get_active_stage, tournament_id)
    shape = await _store_call(bot.store.knockout_shape_for, tournament_id)
    if active_stage not in shape['knockout_stages']:
        return False
    rows = await _store_call(bot.store.matches, tournament_id, stage=active_stage)
    if not rows:
        return False
    player_labels = tournament_player_labels(await _store_call(bot.store.players, tournament_id))
    player_ids = {int(row['player1_id']) for row in rows} | {int(row['player2_id']) for row in rows}
    label = stage_label(active_stage)
    mentions = ' '.join((f'<@{uid}>' for uid in sorted(player_ids)))
    if not _bracket_image_supported(tournament_id):
        await channel.send(content=f"🏆 **{tournament['name']} — {label} fixtures are ready!**\n{mentions}\n\n{_build_text_fixture_list(guild, rows, player_labels)}\n\nUse `/matches` to see only the current active stage.")
        return True
    try:
        bracket_file, _all_bracket_player_ids = await _build_playoff_bracket_image(guild, tournament_id)
    except Exception:
        logger.exception('Failed to render playoff bracket for tournament %s', tournament_id)
        return False
    await channel.send(content=f"🏆 **{tournament['name']} — {label} fixtures are ready!**\n{mentions}\nUse `/matches` to see only the current active stage.", file=bracket_file)
    return True

async def announce_fixtures_in_registration(guild: discord.Guild, tournament_id: int) -> None:
    """Post a fixtures-ready ping in this tournament's own registration
    channel (resolved via its stored registration_channel_id), not a
    global/shared channel that could belong to a different tournament.
    """
    tournament = await _store_call(bot.store.get_tournament, tournament_id)
    channel: discord.TextChannel | None = None
    if tournament and tournament.get('registration_channel_id'):
        maybe_channel = guild.get_channel(int(tournament['registration_channel_id']))
        if isinstance(maybe_channel, discord.TextChannel):
            channel = maybe_channel
    if channel is None:
        logger.warning('Tournament %s has no resolvable registration_channel_id; skipping the fixtures-ready ping instead of posting it to the shared registration channel.', tournament_id)
        return
    fixture_channel = get_fixture_channel(guild, tournament)
    players = await _store_call(bot.store.players, tournament_id)
    mentions = ' '.join((f"<@{int(p['user_id'])}>" for p in players))
    await channel.send(f'🏆 **Fixtures are ready!**\n{mentions}\nYour fixtures are available in {mention_channel(fixture_channel)}.')
CHAMPION_ROLE_NAME = 'Tournament Champion'  # Legacy role; no longer used for new title awards.
CHAMPION_TITLE_ROLE_NAMES = {
    'weekly_championship_winner': 'Weekly Championship Winner',
    'premier_league_champion': 'Premier League Champion',
    'champions_league_champion': 'Champions League Champion',
    'world_cup_champion': 'World Cup Champion',
}
CHAMPIONS_CATEGORY_NAME = '🥇 HALL OF FAME'
CHAMPIONS_CHANNEL_NAME = 'champions-leaderboard'
CHAMPIONS_MOMENTS_CHANNEL_NAME = 'tournament-moments'
AWARDS_CHANNEL_NAME = 'awards'
LEGACY_CHAMPIONS_CATEGORY_NAMES = ('Champions',)

GOLDEN_BOOT_SCOPE_NAMES = {
    'pl': 'PL',
    'wc': 'WC',
    'cl': 'CL',
    'combined': 'Combined',
}

def _golden_boot_month_label(month: str) -> str:
    year, month_number = (int(part) for part in month.split('-'))
    return datetime(year, month_number, 1).strftime("%b '%y")

async def _ensure_ballon_dor_role(guild: discord.Guild, month: str) -> discord.Role:
    label = _golden_boot_month_label(month)
    role_name = f"🏆 Ballon d'Or {label}"
    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        role = await guild.create_role(name=role_name, reason="Create monthly Ballon d'Or title role")
    return role


def _ballon_dor_display_name(row: dict[str, Any]) -> str:
    return str(row.get('nation_name') or row.get('display_name') or f"Player {row['user_id']}")


def _format_ballon_dor_ranking(rows: list[dict[str, Any]], limit: int = 20) -> str:
    lines = []
    for row in rows[:limit]:
        placement = int(row.get('placement_bonus', 0))
        lines.append(
            f"**#{int(row['rank'])}** {_ballon_dor_display_name(row)} — **{int(row['score'])} pts** "
            f"(⚽ {int(row['goals'])} + 🏆/🏅 {placement} + ✅ {int(row['wins'])} wins)"
        )
    return '\n'.join(lines) or 'No qualifying competitive players were found for this month.'


async def _award_monthly_ballon_dor(
    guild: discord.Guild, month: str, override_member: discord.Member | None = None
) -> tuple[discord.Member, discord.Role, dict[str, Any]]:
    rows = await _store_call(bot.store.ballon_dor_candidates_for_guild, guild.id, month)
    if not rows:
        raise ValueError(f'No completed competitive tournaments were found for {month}.')
    if override_member is not None:
        winner = next((row for row in rows if int(row['user_id']) == int(override_member.id)), None)
        if winner is None:
            raise ValueError("The selected player is not in the Ballon d'Or ranking for the requested month.")
    else:
        winner = rows[0]

    winner_member = guild.get_member(int(winner['user_id'])) or override_member
    if winner_member is None:
        try:
            winner_member = await guild.fetch_member(int(winner['user_id']))
        except discord.HTTPException as exc:
            raise ValueError("The Ballon d'Or winner is not currently available in this server.") from exc

    role = await _ensure_ballon_dor_role(guild, month)
    previous = await _store_call(bot.store.ballon_dor_award_record, guild.id, month)
    if previous and int(previous['user_id']) != int(winner_member.id):
        old_member = guild.get_member(int(previous['user_id']))
        if old_member is None:
            try:
                old_member = await guild.fetch_member(int(previous['user_id']))
            except discord.HTTPException:
                old_member = None
        if old_member is not None and role in old_member.roles:
            try:
                await old_member.remove_roles(role, reason=f'Replaced {role.name}')
            except discord.HTTPException:
                logger.exception("Failed to remove previous Ballon d'Or role from %s.", old_member.id)
    if role not in winner_member.roles:
        await winner_member.add_roles(role, reason=f'Award {role.name}')
    await _store_call(bot.store.ballon_dor_award, guild.id, month, int(winner_member.id), role.id, int(winner['score']))
    return winner_member, role, winner


async def _ensure_golden_boot_role(guild: discord.Guild, month: str, scope: str) -> discord.Role:
    label = _golden_boot_month_label(month)
    suffix = f" ({GOLDEN_BOOT_SCOPE_NAMES[scope]})" if scope != 'combined' else ''
    role_name = f"Golden Boot {label}{suffix}"
    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        role = await guild.create_role(name=role_name, reason='Create monthly Golden Boot title role')
    return role

async def _award_monthly_golden_boot(
    guild: discord.Guild, month: str, scope: str, override_member: discord.Member | None = None
) -> tuple[discord.Member, discord.Role, int]:
    rows = await _store_call(bot.store.golden_boot_candidates_for_guild, guild.id, month, scope)
    if not rows:
        raise ValueError(f'No completed competitive goals were recorded for {month} for the selected Golden Boot category.')
    top_goals = int(rows[0]['goals'])
    tied = [row for row in rows if int(row['goals']) == top_goals]
    if len(tied) > 1 and override_member is None:
        names = ', '.join(str(row.get('display_name') or row['user_id']) for row in tied[:10])
        raise ValueError(f'There is a tie at {top_goals} goals between: {names}. Re-run the command with the tied player selected in the optional player field.')
    if override_member is not None:
        matching = next((row for row in rows if int(row['user_id']) == int(override_member.id)), None)
        if matching is None:
            raise ValueError('The selected player has no qualifying competitive goals in the requested month/category.')
        if int(matching['goals']) != top_goals:
            raise ValueError(f'The selected player has {int(matching["goals"])} goals, but the highest total is {top_goals}.')
        winner = matching
    else:
        winner = rows[0]

    winner_member = guild.get_member(int(winner['user_id'])) or override_member
    if winner_member is None:
        try:
            winner_member = await guild.fetch_member(int(winner['user_id']))
        except discord.HTTPException as exc:
            raise ValueError('The Golden Boot winner is not currently available in this server.') from exc
    role = await _ensure_golden_boot_role(guild, month, scope)
    award_key = f'{month}:{scope}'
    previous = await _store_call(bot.store.golden_boot_award_record, guild.id, award_key)
    if previous and int(previous['user_id']) != int(winner_member.id):
        old_member = guild.get_member(int(previous['user_id']))
        if old_member is None:
            try:
                old_member = await guild.fetch_member(int(previous['user_id']))
            except discord.HTTPException:
                old_member = None
        if old_member is not None and role in old_member.roles:
            try:
                await old_member.remove_roles(role, reason=f'Replaced {role.name}')
            except discord.HTTPException:
                logger.exception('Failed to remove previous Golden Boot role from %s.', old_member.id)
    if role not in winner_member.roles:
        await winner_member.add_roles(role, reason=f'Award {role.name}')
    await _store_call(
        bot.store.golden_boot_award,
        guild.id, award_key, month, scope, int(winner_member.id), role.id, top_goals, [],
    )
    return winner_member, role, top_goals

def _champions_staff(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    if guild is None:
        return False
    if interaction.user.guild_permissions.administrator:
        return True
    manager = discord.utils.get(guild.roles, name='Manager')
    return manager is not None and manager in getattr(interaction.user, 'roles', [])

async def _ensure_champion_title_role(guild: discord.Guild, title_key: str) -> discord.Role:
    """Create/reuse the role representing the *current* holder of a
    competitive tournament title.  The role itself persists; its membership
    is exclusive to the latest champion of that title.
    """
    role_name = CHAMPION_TITLE_ROLE_NAMES.get(title_key)
    if not role_name:
        raise ValueError(f'Unknown champion title key: {title_key}')
    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        role = await guild.create_role(name=role_name, reason=f'Create current {role_name} title role')
    return role


async def _set_current_champion_title(guild: discord.Guild, tournament: dict[str, Any], champion_id: int) -> discord.Role | None:
    """Assign exactly one current champion title for this template.

    The previous holder is removed from the same title role before the new
    champion is assigned.  KO Match intentionally has no title role.
    Holder state is persisted so the replacement still works after a restart.
    """
    template_id = tournament.get('template_id') if tournament else None
    title_key = CHAMPION_TITLE_KEYS.get(template_id)
    if not title_key:
        return None

    role = await _ensure_champion_title_role(guild, title_key)
    previous_user_id = None
    previous_role_id = None
    try:
        holder = await _store_call(bot.store.current_champion_title_holder, guild.id, title_key)
        if holder:
            previous_user_id = int(holder['user_id'])
            previous_role_id = int(holder['role_id']) if holder.get('role_id') else None
    except Exception:
        logger.exception('Failed to read current %s holder state.', title_key)

    # Remove the title from the previous holder first.  The DB record is also
    # enough to target the exact member even if the old holder is not cached.
    if previous_user_id and previous_user_id != int(champion_id):
        previous_member = guild.get_member(previous_user_id)
        if previous_member is None:
            try:
                previous_member = await guild.fetch_member(previous_user_id)
            except discord.HTTPException:
                previous_member = None
        if previous_member is not None and role in previous_member.roles:
            try:
                await previous_member.remove_roles(role, reason=f'Replaced as current {role.name}')
            except discord.HTTPException:
                logger.exception('Failed to remove %s from previous holder %s.', role.name, previous_user_id)

    champion_member = guild.get_member(int(champion_id))
    if champion_member is None:
        try:
            champion_member = await guild.fetch_member(int(champion_id))
        except discord.HTTPException:
            champion_member = None
    if champion_member is not None and role not in champion_member.roles:
        await champion_member.add_roles(role, reason=f'Current {role.name}')

    await _store_call(bot.store.set_current_champion_title_holder, guild.id, title_key, role.id, int(champion_id))
    return role


async def _announce_hall_of_fame_award(
    guild: discord.Guild,
    *,
    award_type: str,
    winner: str,
    title: str | None = None,
    tournament: dict[str, Any] | None = None,
    details: str | None = None,
    photo: discord.Attachment | None = None,
) -> None:
    """Announce a completed tournament/award only in Hall of Fame #awards."""
    try:
        _, category, _ = await _ensure_champions_infrastructure(guild)
        channel = discord.utils.get(guild.text_channels, name=AWARDS_CHANNEL_NAME)
        if channel is None or channel.category_id != category.id:
            logger.warning('Hall of Fame #awards channel is unavailable in guild %s; skipping award announcement.', guild.id)
            return
        embed = discord.Embed(title=f'🏆 {award_type}', description=f'🥇 **Winner:** {winner}', color=discord.Color.gold())
        if tournament:
            embed.add_field(name='Tournament', value=f"**{tournament.get('name', 'Tournament')}**", inline=False)
        if title:
            embed.add_field(name='Title', value=f'**{title}**', inline=False)
        if details:
            embed.add_field(name='Details', value=details, inline=False)
        embed.set_footer(text='Hall of Fame • Awards')
        if photo is not None:
            embed.set_image(url=photo.url)
        await channel.send(embed=embed)
    except discord.Forbidden:
        logger.warning('Cannot post to Hall of Fame #awards in guild %s; give the bot Send Messages permission.', guild.id)
    except discord.HTTPException:
        logger.exception('Failed to post Hall of Fame award announcement in guild %s.', guild.id)
    except Exception:
        logger.exception('Unexpected failure while announcing Hall of Fame award in guild %s.', guild.id)

async def _ensure_champions_infrastructure(guild: discord.Guild):
    role = discord.utils.get(guild.roles, name=CHAMPION_ROLE_NAME)
    if role is None:
        role = await guild.create_role(name=CHAMPION_ROLE_NAME, reason='Create permanent Tournament Champion role')
    category = discord.utils.get(guild.categories, name=CHAMPIONS_CATEGORY_NAME)
    if category is None:
        for legacy_name in LEGACY_CHAMPIONS_CATEGORY_NAMES:
            legacy = discord.utils.get(guild.categories, name=legacy_name)
            if legacy:
                try:
                    await legacy.edit(name=CHAMPIONS_CATEGORY_NAME, reason='Hall of Fame rename')
                except discord.Forbidden:
                    pass
                category = legacy
                break
    if category is None:
        category = await guild.create_category(CHAMPIONS_CATEGORY_NAME, reason='Create Hall of Fame category')
    channel = discord.utils.get(guild.text_channels, name=CHAMPIONS_CHANNEL_NAME)
    if channel is None:
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=False)}
        manager = discord.utils.get(guild.roles, name='Manager')
        if manager:
            overwrites[manager] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True)
        channel = await guild.create_text_channel(CHAMPIONS_CHANNEL_NAME, category=category, overwrites=overwrites, reason='Create Champions leaderboard channel')
    elif channel.category_id != category.id:
        await channel.edit(category=category, reason='Keep Champions leaderboard in Hall of Fame category')
    moments_channel = discord.utils.get(guild.text_channels, name=CHAMPIONS_MOMENTS_CHANNEL_NAME)
    if moments_channel is None:
        moments_overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=False)}
        manager = discord.utils.get(guild.roles, name='Manager')
        if manager:
            moments_overwrites[manager] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True)
        moments_channel = await guild.create_text_channel(CHAMPIONS_MOMENTS_CHANNEL_NAME, category=category, overwrites=moments_overwrites, reason='Create tournament-moments channel')
    elif moments_channel.category_id != category.id:
        await moments_channel.edit(category=category, reason='Keep tournament-moments in Hall of Fame category')
    awards_channel = discord.utils.get(guild.text_channels, name=AWARDS_CHANNEL_NAME)
    awards_overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=False)}
    if awards_channel is None:
        awards_channel = await guild.create_text_channel(
            AWARDS_CHANNEL_NAME,
            category=category,
            overwrites=awards_overwrites,
            topic='Official tournament and award winner announcements. View-only for members.',
            reason='Create Hall of Fame awards channel',
        )
    elif awards_channel.category_id != category.id:
        await awards_channel.edit(category=category, reason='Keep awards channel in Hall of Fame category')
    else:
        try:
            await awards_channel.set_permissions(guild.default_role, view_channel=True, read_message_history=True, send_messages=False, reason='Keep Hall of Fame awards channel view-only')
        except discord.HTTPException:
            logger.exception('Could not enforce view-only permissions on Hall of Fame #awards')
    if guild.me:
        try:
            await channel.set_permissions(guild.me, view_channel=True, read_message_history=True, send_messages=True, reason='Allow bot to manage Champions leaderboard')
            await moments_channel.set_permissions(guild.me, view_channel=True, read_message_history=True, send_messages=True, reason='Allow bot to manage tournament-moments')
            await awards_channel.set_permissions(guild.me, view_channel=True, read_message_history=True, send_messages=True, reason='Allow bot to announce Hall of Fame awards')
        except discord.HTTPException:
            logger.exception('Could not set bot permissions for Hall of Fame channels')
    return (role, category, channel)

async def _update_champions_leaderboard(guild: discord.Guild):
    _, _, channel = await _ensure_champions_infrastructure(guild)
    rows = await _store_call(bot.store.champions_leaderboard)
    lines = ['🏆 **CHAMPIONS LEADERBOARD**', '', 'Rank | Player | Points | Titles | Runner-up | SF', '---- | ------ | ------ | ------ | --------- | --']
    if not rows:
        lines.append('No Champions recorded yet.')
    else:
        for rank, row in enumerate(rows, 1):
            member = guild.get_member(int(row['discord_user_id']))
            mention = member.mention if member else f"<@{int(row['discord_user_id'])}>"
            lines.append(f"**#{rank}** | {mention} | **{int(row['total_points'])}** | 🏆 {int(row['championships'])} | 🥈 {int(row['runner_ups'])} | ⚔️ {int(row['semifinal_appearances'])}")
    content = '\n'.join(lines)
    existing = None
    try:
        async for message in channel.history(limit=100):
            if bot.user and message.author.id == bot.user.id and message.content.startswith('🏆 **CHAMPIONS LEADERBOARD**'):
                existing = message
                break
    except discord.HTTPException:
        pass
    if existing:
        await existing.edit(content=content)
    else:
        await channel.send(content)
    return channel

@bot.tree.command(name='champions_leaderboard', description='Update and display the Champions leaderboard')
async def champions_leaderboard(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message('❌ This command can only be used in a server.', ephemeral=True)
        return
    if not _champions_staff(interaction):
        await interaction.response.send_message('❌ You do not have permission to use this command.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        channel = await _update_champions_leaderboard(interaction.guild)
        await interaction.followup.send(f'✅ Champions leaderboard updated in {channel.mention}.', ephemeral=True)
    except Exception:
        logger.exception('Failed to update Champions leaderboard')
        await interaction.followup.send('❌ Failed to update the Champions leaderboard. Check bot permissions and logs.', ephemeral=True)

@bot.tree.command(name='champions_backfill', description='Safely backfill Champions awards for a completed tournament')
@app_commands.describe(tournament_id='Existing completed tournament ID')
async def champions_backfill(interaction: discord.Interaction, tournament_id: int):
    if interaction.guild is None:
        await interaction.response.send_message('❌ This command can only be used in a server.', ephemeral=True)
        return
    if not _champions_staff(interaction):
        await interaction.response.send_message('❌ You do not have permission to use this command.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        result = await _store_call(bot.store.backfill_champions_for_completed_tournament, int(tournament_id))
        role, _, _ = await _ensure_champions_infrastructure(interaction.guild)
        champion_id = int(result['champion_id'])
        champion_member = interaction.guild.get_member(champion_id)
        if champion_member is None:
            try:
                champion_member = await interaction.guild.fetch_member(champion_id)
            except discord.NotFound:
                logger.warning('Champions backfill: champion %s is not currently a member of guild %s.', champion_id, interaction.guild.id)
            except discord.HTTPException:
                logger.exception('Champions backfill: failed to fetch champion %s from guild %s.', champion_id, interaction.guild.id)
        role_assigned = False
        role_assignment_error = None
        if champion_member is not None:
            try:
                if role not in champion_member.roles:
                    await champion_member.add_roles(role, reason=f'Champions backfill for tournament {tournament_id}')
                role_assigned = True
            except discord.Forbidden:
                role_assignment_error = 'Discord denied the Champion role assignment.'
                logger.exception('Champions backfill: insufficient permission to assign Champion role to %s in guild %s.', champion_id, interaction.guild.id)
            except discord.HTTPException:
                role_assignment_error = 'Discord API error while assigning the Champion role.'
                logger.exception('Champions backfill: Discord API error assigning Champion role to %s.', champion_id)
        else:
            role_assignment_error = 'Champion is not currently a member of this server.'
        channel = await _update_champions_leaderboard(interaction.guild)
        champion_mention = f"<@{int(result['champion_id'])}>"
        runner_mention = f"<@{int(result['runner_up_id'])}>"
        sf_mentions = ' '.join((f'<@{int(uid)}>' for uid in result['semifinalist_ids']))
        if result['already_awarded']:
            status = '⚠️ **This tournament was already backfilled.** No additional points were awarded.'
        else:
            status = '✅ **Backfill completed successfully.**'
        await interaction.followup.send('\n'.join(['🏆 **Champions Backfill**', f'**Tournament:** `{tournament_id}`', '', f'🥇 **Champion:** {champion_mention} → **3 points**', f'🥈 **Runner-up:** {runner_mention} → **2 points**', f'⚔️ **Semifinalists:** {sf_mentions} → **1 point each**', '', status, '🏅 Champion role assigned / already present.' if role_assigned else f'⚠️ Champion role was not assigned: {role_assignment_error}', f'📊 Leaderboard: {channel.mention}']), ephemeral=True)
        if not result['already_awarded']:
            backfill_tournament = await _store_call(bot.store.get_tournament, int(tournament_id))
            await _announce_hall_of_fame_award(
                interaction.guild,
                award_type='Tournament Winner',
                winner=champion_mention,
                tournament=backfill_tournament,
                details='Champions Leaderboard backfill: **3 points** awarded.',
            )
    except ValueError as exc:
        logger.warning('Champions backfill rejected: %s', exc)
        await interaction.followup.send(f'❌ **Backfill cancelled.**\n\n{exc}\n\nNo Champions points or tournament match results were changed.', ephemeral=True)
    except Exception:
        logger.exception('Unexpected error during Champions backfill for tournament %s', tournament_id)
        await interaction.followup.send('❌ Backfill failed safely. No tournament match results were changed. Check the bot logs for details.', ephemeral=True)

@bot.tree.command(name='setup_server', description='Automatically arrange/configure this server for the tournament. Safe to run multiple times.')
@tournament_channel_only('organizer')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def setup_server(interaction: discord.Interaction) -> None:
    guild = require_guild(interaction)
    if not interaction.response.is_done():
        await interaction.response.defer()
    created_categories: list[str] = []
    reused_categories: list[str] = []
    created_channels: list[str] = []
    reused_channels: list[str] = []
    created_roles: list[str] = []
    reused_roles: list[str] = []
    failures: list[tuple[str, str]] = []

    async def category(name: str) -> discord.CategoryChannel:
        existing = discord.utils.get(guild.categories, name=name)
        if not existing:
            for legacy_name in LEGACY_CATEGORY_NAMES.get(name, ()):
                legacy = discord.utils.get(guild.categories, name=legacy_name)
                if legacy:
                    try:
                        await legacy.edit(name=name, reason='Tournament server organization')
                    except discord.Forbidden:
                        failures.append((f"Rename '{legacy_name}' to '{name}'", 'Manage Channels'))
                    existing = legacy
                    break
        result = existing or await ensure_category(guild, name)
        (reused_categories if existing else created_categories).append(name)
        return result

    async def text_channel(name: str, parent: discord.CategoryChannel, *, topic: str | None=None) -> discord.TextChannel:
        existing = discord.utils.get(guild.text_channels, name=name)
        result = await ensure_text_channel(guild, name, parent, topic=topic)
        (reused_channels if existing else created_channels).append(name)
        return result

    async def role(name: str) -> discord.Role:
        existing = discord.utils.get(guild.roles, name=name)
        result = existing or await ensure_role(guild, name)
        (reused_roles if existing else created_roles).append(name)
        return result
    try:
        information_category = await category(INFORMATION_CATEGORY)
        tournament_hub_category = await category(TOURNAMENT_HUB_CATEGORY)
        advertisements_category = await category(ADVERTISEMENTS_CATEGORY)
        community_category = await category(COMMUNITY_CATEGORY)
        media_category = await category(MEDIA_CATEGORY)
        staff_category = await category(STAFF_CATEGORY)
        staff_roles = _staff_roles(guild)
        bot_member = guild.me
        other_specs = ((WELCOME_CHANNEL, 'Welcome to the server!', information_category), (ANNOUNCEMENTS_CHANNEL, 'Tournament announcements.', information_category), (RULES_CHANNEL, 'Tournament rules.', information_category), (MATCH_RULES_CHANNEL, 'Required in-match settings.', information_category), (PARTICIPANT_COMMANDS_CHANNEL, 'Participant command guide.', information_category), (HELP_CHANNEL, 'Ask a question — staff and the community can help.', information_category), (ORGANIZER_CHANNEL, 'Organizer/staff only.', staff_category), (STAFF_CHAT_CHANNEL, 'Staff-only discussion.', staff_category), (STAFF_LOGS_CHANNEL, 'Automated/staff action logs.', staff_category), (PUNISHMENT_LOG_CHANNEL, 'Record of warnings, mutes, kicks, and bans.', staff_category), (TOURNAMENTS_DIRECTORY_CHANNEL, 'Browse all tournaments — active, upcoming, and past.', tournament_hub_category), (TOURNAMENT_TEMPLATES_CHANNEL, 'Available tournament formats and their rules.', tournament_hub_category), (TOURNAMENT_HISTORY_CHANNEL, 'Past tournament results and champions.', tournament_hub_category), (SERVER_PROMOTIONS_CHANNEL, 'Our own server/tournament/event promotions.', advertisements_category), (ADVERTISEMENTS_CHANNEL, 'Approved community advertisements.', advertisements_category), (PARTNER_PROMOTIONS_CHANNEL, 'Official partners and approved partner servers/creators.', advertisements_category), (GENERAL_CHAT_CHANNEL, 'General server chat.', community_category), (FOOTBALL_CHAT_CHANNEL, 'Talk about real-world football.', community_category), (EFOOTBALL_CHAT_CHANNEL, 'Talk about eFootball.', community_category), (MEMES_CHANNEL, 'Memes.', community_category), (EFOOTBALL_ADVICE_CHANNEL, 'Tips, tactics, and squad advice.', community_category), (FRIENDLY_MATCHES_CHANNEL, 'Arrange friendly matches outside of tournaments.', community_category), (HIGHLIGHTS_CHANNEL, 'Share your best clips.', media_category), (LIVE_STREAMS_CHANNEL, 'Share/announce live streams.', media_category), (SCREENSHOTS_CHANNEL, 'Share screenshots.', media_category), (CONTENT_CREATORS_CHANNEL, 'For approved content creators.', media_category))
        tournament_channels: dict[str, discord.TextChannel] = {}
        for name, topic, parent in other_specs:
            tournament_channels[name] = await text_channel(name, parent, topic=topic)
        organizer_overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
        if bot_member:
            organizer_overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True)
        for staff_role in staff_roles:
            organizer_overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        try:
            await _apply_overwrites(tournament_channels[ORGANIZER_CHANNEL], organizer_overwrites)
        except discord.Forbidden:
            failures.append((f'Permissions for #{ORGANIZER_CHANNEL}', 'Manage Roles'))
        for name in (ANNOUNCEMENTS_CHANNEL, RULES_CHANNEL, MATCH_RULES_CHANNEL):
            overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False)}
            if bot_member:
                overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True)
            for staff_role in staff_roles:
                overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
            try:
                await _apply_overwrites(tournament_channels[name], overwrites)
            except discord.Forbidden:
                failures.append((f'Permissions for #{name}', 'Manage Roles'))
        for name in (STAFF_CHAT_CHANNEL, STAFF_LOGS_CHANNEL, PUNISHMENT_LOG_CHANNEL):
            overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
            if bot_member:
                overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True)
            for staff_role in staff_roles:
                overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
            try:
                await _apply_overwrites(tournament_channels[name], overwrites)
            except discord.Forbidden:
                failures.append((f'Permissions for #{name}', 'Manage Roles'))
        for name in (WELCOME_CHANNEL, TOURNAMENTS_DIRECTORY_CHANNEL, TOURNAMENT_TEMPLATES_CHANNEL, TOURNAMENT_HISTORY_CHANNEL, SERVER_PROMOTIONS_CHANNEL, ADVERTISEMENTS_CHANNEL, PARTNER_PROMOTIONS_CHANNEL):
            overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False)}
            if bot_member:
                overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True)
            for staff_role in staff_roles:
                overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
            try:
                await _apply_overwrites(tournament_channels[name], overwrites)
            except discord.Forbidden:
                failures.append((f'Permissions for #{name}', 'Manage Roles'))
        try:
            await _upsert_bot_embed(tournament_channels[MATCH_RULES_CHANNEL], build_match_rules_embed())
        except discord.Forbidden:
            failures.append((f'Posting to #{MATCH_RULES_CHANNEL}', 'Send Messages / Embed Links'))
        try:
            await _upsert_bot_embed(tournament_channels[RULES_CHANNEL], build_tournament_rules_embed())
        except discord.Forbidden:
            failures.append((f'Posting to #{RULES_CHANNEL}', 'Send Messages / Embed Links'))
        try:
            await _upsert_bot_embed(tournament_channels[PARTICIPANT_COMMANDS_CHANNEL], build_participant_commands_embed())
        except discord.Forbidden:
            failures.append((f'Posting to #{PARTICIPANT_COMMANDS_CHANNEL}', 'Send Messages / Embed Links'))
        legacy_candidates = {GROUP_STAGE_CATEGORY, PLAYOFFS_CATEGORY, TOURNAMENT_CATEGORY}
        legacy_channel_names = {*GROUP_CHANNELS.keys(), PLAYOFFS_CHAT_CHANNEL, PLAYOFFS_FIXTURES_CHANNEL, PLAYOFFS_STANDINGS_CHANNEL, REGISTRATION_CHANNEL, 'tournament-fixtures', RESULT_CHANNEL, PARTICIPANT_CHANNEL, SUPPORT_CHANNEL, STANDINGS_CHANNEL}
        all_tournaments = await _store_call(bot.store.list_tournaments, guild.id)
        referenced_ids: set[int] = set()
        resource_fields = ('category_id', 'registration_channel_id', 'fixtures_channel_id', 'results_channel_id', 'participant_role_id', 'tournament_chat_channel_id', 'announcements_channel_id', 'match_schedule_channel_id', 'standings_channel_id', 'playoffs_fixtures_channel_id', 'playoffs_standings_channel_id', 'playoffs_chat_channel_id', 'playoff_qualified_role_id')
        for t in all_tournaments:
            for field in resource_fields:
                value = t.get(field)
                if value:
                    referenced_ids.add(int(value))
            for resource in (await _store_call(bot.store.get_tournament_group_resources, int(t['id']))).values():
                for field in ('role_id', 'channel_id'):
                    value = resource.get(field)
                    if value:
                        referenced_ids.add(int(value))
        for legacy_category in list(guild.categories):
            if legacy_category.name not in legacy_candidates:
                continue
            children = list(legacy_category.channels)
            if children and any((getattr(child, 'name', '') not in legacy_channel_names for child in children)):
                logger.warning('Not deleting category %s (id=%s): it contains non-legacy channels.', legacy_category.name, legacy_category.id)
                continue
            category_resource_ids = {legacy_category.id} | {child.id for child in children}
            referenced = category_resource_ids & referenced_ids
            if referenced:
                for t in all_tournaments:
                    owned = {int(t[field]) for field in resource_fields if t.get(field)}
                    for resource in (await _store_call(bot.store.get_tournament_group_resources, int(t['id']))).values():
                        for field in ('role_id', 'channel_id'):
                            if resource.get(field):
                                owned.add(int(resource[field]))
                    overlap = category_resource_ids & owned
                    if overlap:
                        logger.warning('Legacy resource still referenced by tournament %s: category/channel IDs=%s.', t['id'], sorted(overlap))
                continue
            try:
                await legacy_category.delete(reason='Remove obsolete global tournament infrastructure')
                logger.info('Removed obsolete legacy tournament category id=%s name=%s.', legacy_category.id, legacy_category.name)
            except discord.Forbidden:
                failures.append((f"Delete legacy category '{legacy_category.name}'", 'Manage Channels'))
            except discord.HTTPException as error:
                failures.append((f"Delete legacy category '{legacy_category.name}'", str(error)))
        ordered_categories = [information_category, tournament_hub_category, advertisements_category, community_category, media_category, staff_category]
        for expected_name, actual_category in zip(CATEGORY_ORDER, ordered_categories):
            if actual_category.name != expected_name:
                logger.warning('Category name mismatch: expected %r, got %r (id=%s)', expected_name, actual_category.name, actual_category.id)
                failures.append((f"Rename to '{expected_name}' (currently '{actual_category.name}')", 'Manage Channels'))
        for previous_category, current_category in zip(ordered_categories, ordered_categories[1:]):
            if current_category.position > previous_category.position:
                continue
            try:
                await current_category.move(after=previous_category, reason='Tournament server organization')
            except discord.Forbidden:
                failures.append((f'Position of {current_category.name}', 'Manage Channels'))
            except (discord.HTTPException, ValueError) as error:
                logger.warning('Could not reposition category %s: %s', current_category.name, error)
                failures.append((f'Position of {current_category.name}', 'another /setup_server run'))
        ordering_sections: list[tuple[str, discord.CategoryChannel, list[discord.TextChannel]]] = [('Information channels', information_category, [tournament_channels[WELCOME_CHANNEL], tournament_channels[ANNOUNCEMENTS_CHANNEL], tournament_channels[RULES_CHANNEL], tournament_channels[MATCH_RULES_CHANNEL], tournament_channels[PARTICIPANT_COMMANDS_CHANNEL], tournament_channels[HELP_CHANNEL]]), ('Tournament Hub channels', tournament_hub_category, [tournament_channels[TOURNAMENTS_DIRECTORY_CHANNEL], tournament_channels[TOURNAMENT_TEMPLATES_CHANNEL], tournament_channels[TOURNAMENT_HISTORY_CHANNEL]]), ('Advertisement channels', advertisements_category, [tournament_channels[SERVER_PROMOTIONS_CHANNEL], tournament_channels[ADVERTISEMENTS_CHANNEL], tournament_channels[PARTNER_PROMOTIONS_CHANNEL]]), ('Community channels', community_category, [tournament_channels[GENERAL_CHAT_CHANNEL], tournament_channels[FOOTBALL_CHAT_CHANNEL], tournament_channels[EFOOTBALL_CHAT_CHANNEL], tournament_channels[MEMES_CHANNEL], tournament_channels[EFOOTBALL_ADVICE_CHANNEL], tournament_channels[FRIENDLY_MATCHES_CHANNEL]]), ('Media channels', media_category, [tournament_channels[HIGHLIGHTS_CHANNEL], tournament_channels[LIVE_STREAMS_CHANNEL], tournament_channels[SCREENSHOTS_CHANNEL], tournament_channels[CONTENT_CREATORS_CHANNEL]]), ('Staff channels', staff_category, [tournament_channels[ORGANIZER_CHANNEL], tournament_channels[STAFF_CHAT_CHANNEL], tournament_channels[STAFF_LOGS_CHANNEL], tournament_channels[PUNISHMENT_LOG_CHANNEL]])]
        for _, category_obj, channels in ordering_sections:
            previous_channel: discord.TextChannel | None = None
            for channel_obj in channels:
                already_after_previous = previous_channel is not None and channel_obj.category_id == category_obj.id and (channel_obj.position > previous_channel.position)
                try:
                    if previous_channel is None:
                        await channel_obj.move(beginning=True, category=category_obj, reason='Tournament server organization')
                    elif not already_after_previous:
                        await channel_obj.move(after=previous_channel, category=category_obj, reason='Tournament server organization')
                except discord.Forbidden:
                    failures.append((f'Position of #{channel_obj.name}', 'Manage Channels'))
                except (discord.HTTPException, ValueError) as error:
                    logger.warning('Could not reposition #%s: %s', channel_obj.name, error)
                    failures.append((f'Position of #{channel_obj.name}', 'another /setup_server run'))
                previous_channel = channel_obj
        try:
            fresh_channels = await guild.fetch_channels()
        except discord.HTTPException:
            fresh_channels = list(guild.channels)
        fresh_positions = {ch.id: ch.position for ch in fresh_channels}

        def _in_order(items: list[Any]) -> bool:
            positions = [fresh_positions.get(item.id) for item in items]
            if any((pos is None for pos in positions)):
                return False
            return positions == sorted(positions)
        ordering_ok = _in_order(ordered_categories) and all((_in_order(channels) for _, _, channels in ordering_sections))
        lines = ['**Category order**']
        for index, name in enumerate(CATEGORY_ORDER, start=1):
            lines.append(f'{index}. {name}')
        lines.append('')
        lines.append('**Channel order verified**' if ordering_ok else '**Channel order**')
        for label, _, _ in ordering_sections:
            lines.append(f"{('✅' if ordering_ok else 'ℹ️')} {label}")
        lines.append('')
        lines.append(f'**Channels:** ✅ {len(created_channels)} created, {len(reused_channels)} reused ({len(created_channels) + len(reused_channels)} total).')
        lines.append('')
        lines.append('**Roles:**')
        lines.append('Permanent tournament-specific roles are created only when their tournament is provisioned.')
        lines.append('')
        if failures:
            lines.append('⚠️ **Some steps need manual attention:**')
            for item, permission in failures:
                lines.append(f'❌ {item} — needs **{permission}**')
        else:
            lines.append('**Permissions:** ✅ Configured')
        lines.append('\nNo active tournament resources were deleted or reassigned.')
        lines.append('Tournament categories/channels are provisioned per tournament; permanent server infrastructure was preserved.')
        embed = discord.Embed(title='✅ Tournament server organization complete!' if not failures else '⚠️ Tournament server setup finished with warnings.', description='\n'.join(lines), color=discord.Color.green() if not failures else discord.Color.orange())
        await respond(interaction, '', embed=embed)
    except TournamentError as error:
        await respond(interaction, f'❌ {error}', ephemeral=True)
    except discord.Forbidden:
        await respond(interaction, "❌ I'm missing a required permission (Manage Channels / Manage Roles) to finish setting up the server.", ephemeral=True)

@bot.tree.command(name='post_registration_guide', description='Post registration instructions with a Register Now button.')
@tournament_channel_only('registration')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def post_registration_guide(interaction: discord.Interaction) -> None:
    embed = discord.Embed(title='📋 How to Register', description="1️⃣ Click **Register Now** below.\n2️⃣ Follow the registration prompts.\n3️⃣ ✅ You're in the tournament!\n\n🏆 **Limited slots — register early!**", color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed, view=RegisterNowView())

@bot.tree.command(name='post_registration_button', description='Post the Register Now button for the current open tournament if it is missing.')
@staff_messaging_access()
@app_commands.describe(tournament_id="Optional tournament ID; defaults to this channel's tournament")
@app_commands.guild_only()
async def post_registration_button_command(interaction: discord.Interaction, tournament_id: int | None=None) -> None:
    """Staff-only manual recovery for the current tournament registration button.

    This command never opens/reopens a tournament and never changes registration
    data. It only ensures the existing RegisterNowView is posted in the
    configured #tournament-registration channel.
    """
    guild = require_guild(interaction)
    tournament = await _call_off_loop(resolve_tournament_for_interaction, guild, interaction, ((OPEN,),), tournament_id)
    if not tournament:
        await respond(interaction, '❌ There is no currently open tournament. The Register Now button can only be posted while registration is open.', ephemeral=True)
        return
    channel: discord.TextChannel | None = None
    if tournament.get('registration_channel_id'):
        maybe_channel = guild.get_channel(int(tournament['registration_channel_id']))
        if isinstance(maybe_channel, discord.TextChannel) and maybe_channel.guild.id == guild.id:
            channel = maybe_channel
    if channel is None:
        await respond(interaction, f"❌ No registration channel is set up for **{tournament['name']}**. Run `/setup_server` first.", ephemeral=True)
        return
    try:
        async for message in channel.history(limit=100):
            if message.author.id != bot.user.id or not message.components:
                continue
            for row in message.components:
                for component in row.children:
                    if getattr(component, 'custom_id', None) == 'tournament_register_now':
                        await respond(interaction, f"✅ The **Register Now** button is already posted in {channel.mention} for **{tournament['name']}**.", ephemeral=True)
                        return
        await post_registration_button(guild, tournament)
        await respond(interaction, f"✅ Posted the **Register Now** button in {channel.mention} for **{tournament['name']}**.", ephemeral=True)
    except discord.Forbidden:
        await respond(interaction, f"❌ I don't have permission to send messages in {channel.mention}.", ephemeral=True)
    except discord.HTTPException:
        logger.exception('Discord API error while manually posting the Register Now button for tournament %s.', tournament.get('id'))
        await respond(interaction, "❌ Discord rejected the registration-button message. Check the bot's channel permissions and try again.", ephemeral=True)
    except Exception:
        logger.exception('Unexpected error while manually posting the Register Now button for tournament %s.', tournament.get('id'))
        await respond(interaction, '❌ Failed to post the Register Now button. Check the bot logs for details.', ephemeral=True)

@bot.tree.command(name='tournament_create', description='Create a new tournament.')
@tournament_channel_only('organizer')
@app_commands.describe(name='Tournament name', tournament_type='KO match or 16-player groups + knockout (ignored if template is set)', player_limit='Maximum players (ignored if template is set)', template='Optional named format. When set, it fixes tournament_type and player_limit for you.')
@app_commands.choices(tournament_type=[app_commands.Choice(name='KO Match', value=ROUND_ROBIN), app_commands.Choice(name='16-player groups + knockout', value=GROUP_KNOCKOUT)], template=[app_commands.Choice(name=config['display_name'], value=template_id) for template_id, config in TEMPLATES.items()])
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def tournament_create(interaction: discord.Interaction, name: str, tournament_type: app_commands.Choice[str] | None=None, player_limit: app_commands.Range[int, 2, 64] | None=None, template: app_commands.Choice[str] | None=None) -> None:
    guild = require_guild(interaction)
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    template_id = template.value if template is not None else None
    if template_id is not None:
        config = TEMPLATES[template_id]
        resolved_type = config['tournament_type']
        resolved_limit = int(config['player_limit'])
    else:
        if tournament_type is None or player_limit is None:
            await respond(interaction, 'Provide either `template`, or both `tournament_type` and `player_limit`.', ephemeral=True)
            return
        resolved_type = tournament_type.value
        resolved_limit = int(player_limit)
    try:
        tournament_id = await _store_call(bot.store.create_tournament, guild.id, name, resolved_type, resolved_limit, interaction.user.id, template_id=template_id)
        tournament = await _store_call(bot.store.get_tournament, tournament_id)
        if not tournament:
            raise TournamentError('Tournament was created in the database but could not be loaded for Discord provisioning.')
        try:
            resources = await provision_tournament_resources(guild, tournament)
        except Exception as provisioning_error:
            logger.exception('Discord provisioning failed for new tournament %s.', tournament_id)
            latest = await _store_call(bot.store.get_tournament, tournament_id) or tournament
            try:
                await cleanup_tournament_resources(guild, latest)
            except Exception:
                logger.exception('Failed to clean up partial Discord resources for tournament %s.', tournament_id)
            try:
                await _store_call(bot.store.delete_tournament, guild.id, tournament_id)
            except Exception:
                logger.exception('Failed to roll back tournament database row %s after provisioning failure.', tournament_id)
            raise TournamentError('Tournament creation could not finish Discord provisioning. No fully provisioned tournament was created; check the bot logs.') from provisioning_error
        template_note = f" using the **{TEMPLATES[template_id]['display_name']}** template" if template_id else ''
        await respond(interaction, f"Tournament **{name.strip()}** created as draft (ID `{tournament_id}`){template_note}. Discord category: {resources['category'].mention}. Registration: {resources['registration_channel'].mention}.")
    except TournamentError as error:
        await respond(interaction, str(error), ephemeral=True)

@bot.tree.command(name='tournament_rename_channels', description="Safely rename a tournament's mapped Discord channels to the short naming format.")
@tournament_channel_only('organizer')
@app_commands.describe(tournament_id='Tournament ID whose mapped channels should be renamed')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def tournament_rename_channels(interaction: discord.Interaction, tournament_id: int) -> None:
    """Rename only channels already mapped to the selected tournament.

    Resource identity comes exclusively from stored Discord IDs. Names are
    used only for the requested display rename and for collision preflight;
    no channel is discovered by name and no database resource IDs are changed.
    """
    guild = require_guild(interaction)
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    tournament = await _store_call(bot.store.get_tournament, tournament_id)
    if not tournament or int(tournament['guild_id']) != guild.id:
        await respond(interaction, 'No matching tournament was found in this server.', ephemeral=True)
        return
    prefix = get_tournament_channel_prefix(tournament)
    mappings: list[tuple[str, int | None]] = [('registration', tournament.get('registration_channel_id')), ('fixtures', tournament.get('fixtures_channel_id')), ('match-schedule', tournament.get('match_schedule_channel_id')), ('results', tournament.get('results_channel_id')), ('chat', tournament.get('tournament_chat_channel_id')), ('announcements', tournament.get('announcements_channel_id')), ('standings', tournament.get('standings_channel_id')), ('playoffs-fixtures', tournament.get('playoffs_fixtures_channel_id')), ('playoffs-standings', tournament.get('playoffs_standings_channel_id')), ('playoffs-chat', tournament.get('playoffs_chat_channel_id'))]
    group_resources = await _store_call(bot.store.get_tournament_group_resources, tournament_id)
    for group, resource in group_resources.items():
        mappings.append((f'group-{str(group).lower()}-chat', resource.get('channel_id')))
    targets: list[tuple[discord.TextChannel, str]] = []
    for purpose, resource_id in mappings:
        if not resource_id:
            continue
        channel = guild.get_channel(int(resource_id))
        if not isinstance(channel, discord.TextChannel):
            await respond(interaction, f'❌ Cannot safely rename `{purpose}`: stored Discord channel ID `{resource_id}` no longer exists.', ephemeral=True)
            return
        target_name = f'{prefix}-{purpose}'
        if channel.name == target_name:
            continue
        collision = next((c for c in guild.text_channels if c.name == target_name and c.id != channel.id), None)
        if collision is not None:
            await respond(interaction, f'❌ Cannot safely rename `{channel.name}` to `#{target_name}` because another channel already uses that name.', ephemeral=True)
            return
        targets.append((channel, target_name))
    renamed: list[str] = []
    try:
        for channel, target_name in targets:
            await channel.edit(name=target_name, reason=f'Short tournament channel names for tournament {tournament_id}')
            renamed.append(f'#{target_name}')
    except discord.Forbidden as error:
        logger.exception('Could not finish channel rename for tournament %s', tournament_id)
        await respond(interaction, '❌ Discord denied a channel rename. No database mappings were changed; check Manage Channels permissions.', ephemeral=True)
        return
    except discord.HTTPException as error:
        logger.exception('Discord rejected a channel rename for tournament %s', tournament_id)
        await respond(interaction, '❌ Discord rejected a channel rename. Check the bot logs for details.', ephemeral=True)
        return
    await respond(interaction, f'✅ Renamed {len(renamed)} mapped channel(s) for tournament `{tournament_id}` using prefix `{prefix}`. Channel IDs and database mappings were preserved.', ephemeral=True)

@bot.tree.command(name='tournament_delete', description='Delete a tournament and all of its players/matches.')
@tournament_channel_only('organizer')
@app_commands.describe(tournament_id='Optional tournament ID; defaults to the current draft')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def tournament_delete(interaction: discord.Interaction, tournament_id: int | None=None) -> None:
    guild = require_guild(interaction)
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        tournament = await _store_call(bot.store.get_tournament, tournament_id) if tournament_id is not None else await _store_call(bot.store.current_tournament, guild.id, (DRAFT, OPEN, CLOSED, 'completed'))
        if not tournament or int(tournament['guild_id']) != guild.id:
            raise TournamentError('No matching tournament was found.')
        try:
            await clear_tournament_roles(guild, int(tournament['id']))
        except discord.Forbidden as error:
            raise TournamentError('I cannot remove the tournament roles. Give me Manage Roles and move my bot role above Participant, Group A/B/C/D and Playoff Qualified.') from error
        await cleanup_tournament_resources(guild, tournament)
        deleted = await _store_call(bot.store.delete_tournament, guild.id, tournament_id)
        await respond(interaction, f"Deleted tournament **{deleted['name']}** (ID `{deleted['id']}`), including all registered players and generated matches.")
    except TournamentError as error:
        await respond(interaction, str(error), ephemeral=True)
    except discord.Forbidden as error:
        await respond(interaction, 'I do not have permission to remove the tournament roles. Give the bot Manage Roles and move its role above the tournament roles.', ephemeral=True)
    except Exception:
        logger.exception('Unexpected error while deleting tournament')
        await respond(interaction, 'An unexpected error occurred while deleting the tournament. Check the Railway logs.', ephemeral=True)

@bot.tree.command(name='tournament_archive', description="Archive a completed tournament's channels now, instead of waiting for the automatic sweep.")
@tournament_channel_only('organizer')
@app_commands.describe(tournament_id="Optional tournament ID; defaults to the server's current completed tournament")
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def tournament_archive(interaction: discord.Interaction, tournament_id: int | None=None) -> None:
    """Manually trigger the same archiving step the periodic sweep performs.

    Useful for staff who don't want to wait up to ARCHIVE_SWEEP_INTERVAL_MINUTES
    for the automatic sweep to notice a just-completed tournament. Does not
    delete anything — only locks channels to staff-only, exactly like the
    sweep does, and (only on success) schedules the same automatic
    pod-deletion window.
    """
    guild = require_guild(interaction)
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    tournament = await _store_call(bot.store.get_tournament, tournament_id) if tournament_id is not None else await _store_call(bot.store.current_tournament, guild.id, (COMPLETED,))
    if not tournament or int(tournament['guild_id']) != guild.id:
        await respond(interaction, 'No matching tournament was found.', ephemeral=True)
        return
    if tournament['status'] != COMPLETED:
        await respond(interaction, f"**{tournament['name']}** is not completed yet (status: {tournament['status']}).", ephemeral=True)
        return
    if tournament.get('archived_at'):
        await respond(interaction, f"**{tournament['name']}** is already archived.", ephemeral=True)
        return
    try:
        ok = await archive_tournament_resources(guild, tournament)
    except Exception:
        logger.exception('Manual archive failed for tournament %s.', tournament['id'])
        await respond(interaction, 'An unexpected error occurred while archiving.', ephemeral=True)
        return
    if not ok:
        logger.error('Tournament %s archive incomplete; refusing to mark archived.', tournament['id'])
        await respond(interaction, "Tournament archive failed or was incomplete. The tournament was NOT marked archived. Check the bot's permissions (Manage Channels/Manage Roles) and try again.", ephemeral=True)
        return
    await _store_call(bot.store.mark_tournament_archived, int(tournament['id']), retention_days=ARCHIVE_RETENTION_DAYS)
    delete_date = datetime.now(UTC) + timedelta(days=ARCHIVE_RETENTION_DAYS)
    await respond(interaction, f"📦 Archived **{tournament['name']}**. Tournament channels are now staff-only and will be deleted after the configured archive retention period ({_to_discord_timestamp(delete_date.isoformat(), 'R')}). The tournament's results and Champions history are kept permanently.")

@bot.tree.command(name='database_status', description='Show the tournament database backend and persistence status.')
@tournament_channel_only('organizer')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def database_status(interaction: discord.Interaction) -> None:
    backend = bot.store.backend
    if backend == 'postgres':
        message = '🗄️ Database: **PostgreSQL** — persistent across Railway deployments.'
    else:
        message = '🗄️ Database: **SQLite** — local storage. On Railway this is only safe when TOURNAMENT_DB_PATH points to a persistent volume.'
    await respond(interaction, message, ephemeral=True)

@bot.tree.command(name='tournament_list', description='List tournaments in this server with their IDs.')
@tournament_channel_only('organizer')
@app_commands.describe(include_completed='Include finished tournaments (default: on)')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def tournament_list(interaction: discord.Interaction, include_completed: bool=True) -> None:
    guild = require_guild(interaction)
    statuses = (DRAFT, OPEN, CLOSED, 'completed') if include_completed else (DRAFT, OPEN, CLOSED)
    tournaments = await _store_call(bot.store.list_tournaments, guild.id, statuses)
    if not tournaments:
        await respond(interaction, 'No tournaments found for this server.', ephemeral=True)
        return
    lines = []
    for tournament in tournaments:
        player_count = len(await _store_call(bot.store.players, int(tournament['id'])))
        type_name = tournament_type_label(tournament)
        lifecycle_note = ''
        if tournament.get('resources_deleted_at'):
            lifecycle_note = ' · 🗑️ pod removed'
        elif tournament.get('archived_at') and tournament.get('delete_after'):
            lifecycle_note = f" · 📦 archived, pod removal {_to_discord_timestamp(tournament['delete_after'], 'R')}"
        lines.append(f"`ID {tournament['id']}` · **{tournament['name']}** — {str(tournament['status']).title()} · {type_name} · {player_count}/{tournament['player_limit']} players{lifecycle_note}")
    embed = discord.Embed(title=f'{guild.name} — Tournaments', description='\n'.join(lines), color=discord.Color.blurple())
    embed.set_footer(text='Use an ID with /tournament_delete.')
    await respond(interaction, '', embed=embed, ephemeral=True)

@bot.tree.command(name='tournament_open', description='Open the draft tournament for registration.')
@tournament_channel_only('organizer')
@app_commands.describe(tournament_id='Optional tournament ID; defaults to the server draft')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def tournament_open(interaction: discord.Interaction, tournament_id: int | None=None) -> None:
    guild = require_guild(interaction)
    await interaction.response.defer(ephemeral=True)
    try:
        tournament = await _store_call(bot.store.open_tournament, guild.id, tournament_id)
        resources = await provision_tournament_resources(guild, tournament)
        tournament = await _store_call(bot.store.get_tournament, int(tournament['id'])) or tournament
        registration_channel = resources['registration_channel']
        chat_channel = resources.get('chat_channel')
        chat_line = f'\nTournament chat: {chat_channel.mention}' if chat_channel else ''
        await respond(interaction, f"Registration is now open for **{tournament['name']}**. Limit: **{tournament['player_limit']}** players.\nRegistration channel: {registration_channel.mention}{chat_line}")
        await announce(guild, f"📢 **Registration is now open for {tournament['name']}!** Head to {registration_channel.mention} and use `/register`. Limit: **{tournament['player_limit']}** players.", tournament=tournament)
        try:
            await post_registration_button(guild, tournament)
        except discord.Forbidden:
            logger.exception('Bot lacks permission to post the registration button for tournament %s.', tournament.get('id'))
        except discord.HTTPException:
            logger.exception('Discord API error while posting the registration button for tournament %s.', tournament.get('id'))
    except TournamentError as error:
        await respond(interaction, str(error), ephemeral=True)

@bot.tree.command(name='tournament_sync_roles', description='Re-assign Group A/B/C/D roles from the existing draw (fixes missed role assignments).')
@tournament_channel_only('organizer')
@app_commands.describe(tournament_id='Optional tournament ID; defaults to the most recent closed tournament')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def tournament_sync_roles(interaction: discord.Interaction, tournament_id: int | None=None) -> None:
    guild = require_guild(interaction)
    await interaction.response.defer(ephemeral=True)
    try:
        tournament = await _store_call(bot.store.get_tournament, tournament_id) if tournament_id is not None else await _store_call(bot.store.current_tournament, guild.id, (CLOSED,))
        if not tournament or tournament['guild_id'] != guild.id:
            await respond(interaction, 'No matching tournament was found.', ephemeral=True)
            return
        if tournament['tournament_type'] != GROUP_KNOCKOUT:
            await respond(interaction, 'This tournament has no groups to sync.', ephemeral=True)
            return
        await sync_group_roles(guild, int(tournament['id']))
        await respond(interaction, f"Group roles re-synced for **{tournament['name']}** from the existing draw.", ephemeral=True)
    except TournamentError as error:
        await respond(interaction, str(error), ephemeral=True)

@bot.tree.command(name='tournament_close', description='Close registration and generate fixtures.')
@tournament_channel_only('organizer')
@app_commands.describe(tournament_id='Optional tournament ID; defaults to the open tournament')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def tournament_close(interaction: discord.Interaction, tournament_id: int | None=None) -> None:
    guild = require_guild(interaction)
    await interaction.response.defer(ephemeral=True)
    try:
        result = await _store_call(bot.store.close_and_generate, guild.id, tournament_id)
        tournament = result['tournament']
        message = f"Registration closed for **{tournament['name']}**."
        await announce(guild, f"🚪 Registration for **{tournament['name']}** has closed.", tournament=tournament)
        await _send_world_cup_nation_assignments(guild, int(tournament['id']))
        if str(tournament.get('template_id') or '') in WORLD_CUP_TEMPLATE_IDS:
            message += '\n🌍 National teams were randomly assigned and each player was notified privately.'
            await announce(guild, '🌍 National teams have been randomly assigned to the World Cup participants.', tournament=tournament)
        if tournament['tournament_type'] == GROUP_KNOCKOUT:
            try:
                await sync_group_roles(guild, int(tournament['id']))
                message += '\nGroup roles assigned: **Participant + Group A/B/C/D**.'
                await announce(guild, '🧩 Players have been assigned to **Group A/B/C/D**.', tournament=tournament)
            except TournamentError as role_error:
                message += f'\nRole warning: {role_error}'
        posted = await post_fixtures(guild, int(tournament['id']))
        if posted:
            await announce_fixtures_in_registration(guild, int(tournament['id']))
            await announce(guild, '📋 Fixtures have been generated and posted!', tournament=tournament)
            if tournament['tournament_type'] == GROUP_KNOCKOUT:
                await announce_stage_deadline(guild, int(tournament['id']), 'group')
        else:
            message += '\n⚠️ I could not find/post to #tournament-fixtures.'
        await respond(interaction, message)
    except TournamentError as error:
        await respond(interaction, str(error), ephemeral=True)

async def _send_world_cup_nation_assignments(guild: discord.Guild, tournament_id: int) -> None:
    """DM each World Cup participant their permanent assigned nation."""
    tournament = await _store_call(bot.store.get_tournament, tournament_id)
    if not tournament or str(tournament.get('template_id') or '') not in WORLD_CUP_TEMPLATE_IDS:
        return
    rows = await _store_call(bot.store.players, tournament_id)
    for row in rows:
        nation = row.get('nation_name')
        if not nation:
            continue
        member = guild.get_member(int(row['user_id']))
        if member is None:
            try:
                member = await guild.fetch_member(int(row['user_id']))
            except discord.HTTPException:
                continue
        try:
            await member.send(embed=discord.Embed(
                title=f"🌍 Your World Cup Nation — {tournament['name']}",
                description=f"You have been randomly assigned **{nation}**.\n\nFixtures, schedules, results, standings and knockout displays will use **{nation}** as your tournament identity.",
                color=discord.Color.green(),
            ))
        except discord.HTTPException:
            logger.warning('Could not DM World Cup nation assignment to player %s for tournament %s.', row['user_id'], tournament_id)

async def _handle_auto_close(guild: discord.Guild, registration: dict[str, Any]) -> str:
    """Shared follow-up for a registration that just filled the tournament.

    Announces the closure, assigns group roles for GROUP_KNOCKOUT tournaments,
    and posts fixtures. Returns a message suffix describing what happened.
    """
    closed_tournament = registration['tournament']
    message = f"Registration is now full for **{closed_tournament['name']}** and has been closed automatically. No further registrations are accepted."
    await announce(guild, f"🚪 **{closed_tournament['name']}** is now full — registration has closed automatically.", tournament=closed_tournament)
    await _send_world_cup_nation_assignments(guild, int(closed_tournament['id']))
    if str(closed_tournament.get('template_id') or '') in WORLD_CUP_TEMPLATE_IDS:
        message += '\n🌍 National teams were randomly assigned and each player was notified privately.'
        await announce(guild, '🌍 National teams have been randomly assigned to the World Cup participants.', tournament=closed_tournament)
    if closed_tournament['tournament_type'] == GROUP_KNOCKOUT:
        try:
            await sync_group_roles(guild, int(closed_tournament['id']))
            message += '\nPlayers were randomly assigned to **Group A/B/C/D** and their group roles were added.'
            await announce(guild, '🧩 Players have been assigned to **Group A/B/C/D**.', tournament=closed_tournament)
        except (TournamentError, discord.Forbidden) as role_error:
            message += f'\nRole warning: {role_error}'
    posted = await post_fixtures(guild, int(closed_tournament['id']))
    if posted:
        await announce_fixtures_in_registration(guild, int(closed_tournament['id']))
        await announce(guild, '📋 Fixtures have been generated and posted!', tournament=closed_tournament)
        if closed_tournament['tournament_type'] == GROUP_KNOCKOUT:
            await announce_stage_deadline(guild, int(closed_tournament['id']), 'group')
    else:
        message += '\n⚠️ I could not find/post to #tournament-fixtures.'
    return message

@bot.tree.command(name='register', description='Register for the open tournament.')
@tournament_channel_only('registration')
@app_commands.guild_only()
async def register(interaction: discord.Interaction) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    guild = require_guild(interaction)
    tournament = await _store_call(bot.store.get_tournament_by_channel, guild.id, interaction.channel.id)
    if not tournament or tournament['status'] != OPEN:
        await respond(interaction, 'There is no tournament open for registration.', ephemeral=True)
        return
    role = await get_tournament_participant_role(guild, tournament)
    registered = False
    try:
        if not isinstance(interaction.user, discord.Member):
            raise TournamentError('Discord did not provide a server member for this registration.')
        if role >= guild.me.top_role:
            raise TournamentError('I cannot assign the Participant role. Move my bot role above Participant and grant Manage Roles, then try again.')
        await interaction.user.add_roles(role, reason='Registered for tournament')
        registration = await _store_call(bot.store.register, int(tournament['id']), interaction.user.id, getattr(interaction.user, 'display_name', interaction.user.name))
        registered = True
        chat_channel = None
        if tournament.get('tournament_chat_channel_id'):
            chat_channel = guild.get_channel(int(tournament['tournament_chat_channel_id']))
        chat_line = f' Head to {chat_channel.mention} to chat with other players.' if chat_channel else ''
        message = f"You are registered for **{tournament['name']}** and received the **{role.name}** role.{chat_line}"
        if registration.get('auto_closed'):
            message = await _handle_auto_close(guild, registration)
        await respond(interaction, message)
    except discord.Forbidden:
        if registered:
            await respond(interaction, 'I cannot manage the tournament roles. Check Manage Roles and role hierarchy.', ephemeral=True)
        else:
            await respond(interaction, 'I cannot assign the Participant role. Move my bot role above Participant and grant Manage Roles, then try again.', ephemeral=True)
    except TournamentError as error:
        await respond(interaction, str(error), ephemeral=True)

@bot.tree.command(name='admin_register', description='Manually register a player for the open tournament (staff only).')
@tournament_channel_only('organizer', 'registration')
@app_commands.describe(member='The player to register.', tournament_id='Optional tournament ID; defaults to the currently open tournament.')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def admin_register(interaction: discord.Interaction, member: discord.Member, tournament_id: int | None=None) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    guild = require_guild(interaction)
    if tournament_id is not None:
        tournament = await _store_call(bot.store.get_tournament, tournament_id)
        if not tournament or int(tournament['guild_id']) != guild.id:
            await respond(interaction, 'No tournament with that ID was found.', ephemeral=True)
            return
        if tournament['status'] != OPEN:
            await respond(interaction, f"**{tournament['name']}** is not open for registration (status: {tournament['status']}).", ephemeral=True)
            return
    else:
        tournament = await _store_call(bot.store.get_tournament_by_channel, guild.id, interaction.channel.id)
        if not tournament:
            await respond(interaction, 'There is no tournament open for registration.', ephemeral=True)
            return
    role = await get_tournament_participant_role(guild, tournament)
    registered = False
    try:
        if role >= guild.me.top_role:
            raise TournamentError('I cannot assign the Participant role. Move my bot role above Participant and grant Manage Roles, then try again.')
        await member.add_roles(role, reason=f'Manually registered by {interaction.user}')
        registration = await _store_call(bot.store.register, int(tournament['id']), member.id, getattr(member, 'display_name', member.name))
        registered = True
        message = f"✅ {member.mention} was registered for **{tournament['name']}** and received the **{role.name}** role."
        if registration.get('auto_closed'):
            message += '\n\n' + await _handle_auto_close(guild, registration)
        await respond(interaction, message)
    except discord.Forbidden:
        if registered:
            await respond(interaction, 'I cannot manage the tournament roles. Check Manage Roles and role hierarchy.', ephemeral=True)
        else:
            await respond(interaction, 'I cannot assign the Participant role. Move my bot role above Participant and grant Manage Roles, then try again.', ephemeral=True)
    except TournamentError as error:
        await respond(interaction, str(error), ephemeral=True)

@bot.tree.command(name='participant_replace', description='Replace an unplayed participant in a tournament (staff only).')
@tournament_channel_only('organizer')
@playoffs_fixture_access()
@app_commands.describe(tournament='Explicit tournament ID to modify.', remove='Registered participant to remove; they must have zero finalized matches.', replace_with='Server member who is not already registered in this tournament.')
@app_commands.guild_only()
async def participant_replace(interaction: discord.Interaction, tournament: int, remove: discord.Member, replace_with: discord.Member) -> None:
    """Replace one participant without regenerating the tournament."""
    guild = require_guild(interaction)
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    tournament_row = await _store_call(bot.store.get_tournament, int(tournament))
    if not tournament_row or int(tournament_row['guild_id']) != guild.id:
        await respond(interaction, '❌ Replacement denied. No matching tournament was found in this server.', ephemeral=True)
        return
    tournament_id = int(tournament_row['id'])
    removed_id = int(remove.id)
    replacement_id = int(replace_with.id)
    if removed_id == replacement_id:
        await respond(interaction, '❌ Replacement denied. The removed participant and replacement participant must be different.', ephemeral=True)
        return
    if tournament_row['status'] not in (DRAFT, OPEN, CLOSED):
        await respond(interaction, '❌ Replacement denied. This tournament is no longer in its starting stage.', ephemeral=True)
        return
    removed_rows = [row for row in await _store_call(bot.store.players, tournament_id) if int(row['user_id']) == removed_id]
    if not removed_rows:
        await respond(interaction, f'❌ Replacement denied. {remove.mention} is not registered in this tournament.', ephemeral=True)
        return
    if any((int(row['user_id']) == replacement_id for row in await _store_call(bot.store.players, tournament_id))):
        await respond(interaction, f'❌ Replacement denied. {replace_with.mention} is already registered in this tournament.', ephemeral=True)
        return
    removed_matches = await _store_call(bot.store.matches, tournament_id, user_id=removed_id)
    if any((str(row.get('status')) == 'completed' for row in removed_matches)):
        await respond(interaction, f'❌ Replacement denied. {remove.mention} has already played a match in this tournament.', ephemeral=True)
        return
    tournament_type = str(tournament_row['tournament_type'])
    for row in removed_matches:
        stage = str(row.get('stage') or '')
        round_number = int(row.get('round_number') or 1)
        initial_stage = stage == 'group' if tournament_type == GROUP_KNOCKOUT else stage == 'league' if tournament_type == LEAGUE else stage == 'round_robin' and round_number == 1
        if not initial_stage:
            await respond(interaction, '❌ Replacement denied. This tournament is no longer in its starting stage.', ephemeral=True)
            return
    removed_row = removed_rows[0]
    group_name = removed_row.get('group_name')
    participant_role = None
    participant_role_id = tournament_row.get('participant_role_id')
    if participant_role_id:
        participant_role = guild.get_role(int(participant_role_id))
        if participant_role is None:
            await respond(interaction, '❌ Replacement denied. The tournament data is inconsistent. No changes were made.', ephemeral=True)
            return
    elif not tournament_row.get('category_id'):
        participant_role = discord.utils.get(guild.roles, name=PARTICIPANT_ROLE)
    if participant_role is None and (not group_name):
        await respond(interaction, '❌ Replacement denied. The tournament data is inconsistent. No changes were made.', ephemeral=True)
        return
    group_role = None
    if group_name:
        group_resource = (await _store_call(bot.store.get_tournament_group_resources, tournament_id)).get(str(group_name), {})
        group_role_id = group_resource.get('role_id')
        if group_role_id:
            group_role = guild.get_role(int(group_role_id))
            if group_role is None:
                await respond(interaction, '❌ Replacement denied. The tournament data is inconsistent. No changes were made.', ephemeral=True)
                return
        else:
            await respond(interaction, '❌ Replacement denied. The tournament data is inconsistent. No changes were made.', ephemeral=True)
            return
    roles_to_add = [role for role in (participant_role, group_role) if role is not None]
    roles_to_remove = []
    if participant_role is not None:
        shared_participant = not participant_role_id
        if not shared_participant or not await _store_call(bot.store.player_has_other_tournament_registration, removed_id, tournament_id):
            roles_to_remove.append(participant_role)
    if group_role is not None and group_role not in roles_to_remove:
        if await _store_call(bot.store.count_tournaments_referencing_resource, guild.id, int(group_role.id), tournament_id) == 0:
            roles_to_remove.append(group_role)
    bot_member = guild.me
    if bot_member is None:
        await respond(interaction, '❌ Replacement denied. The bot member could not be resolved.', ephemeral=True)
        return
    for role in roles_to_add + roles_to_remove:
        if role >= bot_member.top_role:
            await respond(interaction, '❌ Replacement denied. I cannot manage the tournament role. Move my bot role above the tournament role and grant Manage Roles.', ephemeral=True)
            return
    old_roles_removed = False
    new_roles_added = False
    try:
        if roles_to_remove:
            await remove.remove_roles(*roles_to_remove, reason=f'Participant replaced in tournament {tournament_id}')
            old_roles_removed = True
        if roles_to_add:
            await replace_with.add_roles(*roles_to_add, reason=f'Participant replaced in tournament {tournament_id}')
            new_roles_added = True
        result = await _store_call(bot.store.replace_participant, tournament_id, removed_id, replacement_id, getattr(replace_with, 'display_name', replace_with.name))
        try:
            pass
        except Exception:
            raise
    except (TournamentError, discord.Forbidden, discord.HTTPException) as error:
        if new_roles_added:
            try:
                await replace_with.remove_roles(*roles_to_add, reason=f'Rolling back failed participant replacement in tournament {tournament_id}')
            except Exception:
                logger.exception('Failed to remove replacement roles during rollback for tournament %s.', tournament_id)
        if old_roles_removed:
            try:
                await remove.add_roles(*roles_to_remove, reason=f'Rolling back failed participant replacement in tournament {tournament_id}')
            except Exception:
                logger.exception('Failed to restore removed participant roles during rollback for tournament %s.', tournament_id)
        if isinstance(error, TournamentError):
            message = str(error)
            if message == 'The removed participant has already played a match in this tournament.':
                message = f'❌ Replacement denied. {remove.mention} has already played a match in this tournament.'
            elif message == 'The replacement participant is already registered in this tournament.':
                message = f'❌ Replacement denied. {replace_with.mention} is already registered in this tournament.'
            elif message == 'The removed participant is not registered in this tournament.':
                message = f'❌ Replacement denied. {remove.mention} is not registered in this tournament.'
            elif message == 'This tournament is no longer in its starting stage.':
                message = '❌ Replacement denied. This tournament is no longer in its starting stage.'
            elif message == 'The tournament data is inconsistent. No changes were made.':
                message = '❌ Replacement denied. The tournament data is inconsistent. No changes were made.'
            else:
                message = f'❌ Replacement denied. {message}'
            await respond(interaction, message, ephemeral=True)
        else:
            logger.exception('Discord failure during participant replacement for tournament %s.', tournament_id)
            await respond(interaction, '❌ Replacement denied. I could not update tournament access. No database changes were made.', ephemeral=True)
        return
    staff_logs = discord.utils.get(guild.text_channels, name=STAFF_LOGS_CHANNEL)
    if staff_logs is not None:
        try:
            fixture_note = 'Existing unplayed fixtures were updated.' if result['fixtures_generated'] else 'The replacement will be included when fixtures are generated.'
            await staff_logs.send(f"🔄 **PARTICIPANT REPLACED**\nTournament: **{tournament_row['name']}**\nRemoved: {remove.mention}\nReplacement: {replace_with.mention}\nGroup: **{('Group ' + str(group_name) if group_name else '—')}**\nMatches Played by Removed Player: **0**\n{fixture_note}")
        except (discord.Forbidden, discord.HTTPException):
            logger.warning('Could not write participant replacement audit log for tournament %s.', tournament_id)
    group_text = f'\nGroup: **Group {group_name}**' if group_name else ''
    fixture_text = '\nExisting unplayed fixtures were updated.' if result['fixtures_generated'] else '\nThe replacement will be included when fixtures are generated.'
    await respond(interaction, f"✅ **Participant replaced successfully.**\n\nTournament: **{tournament_row['name']}**\nRemoved: {remove.mention}\nReplacement: {replace_with.mention}{group_text}\nPlayed Matches: **0**{fixture_text}", ephemeral=True)

@bot.tree.command(name='unregister', description='Leave the open tournament.')
@tournament_channel_only('registration')
@participant_only()
@app_commands.guild_only()
async def unregister(interaction: discord.Interaction) -> None:
    guild = require_guild(interaction)
    tournament = await _store_call(bot.store.get_tournament_by_channel, guild.id, interaction.channel.id)
    if not tournament or tournament['status'] != OPEN:
        await respond(interaction, 'Registration is closed or no tournament is open.', ephemeral=True)
        return
    try:
        await _store_call(bot.store.unregister, int(tournament['id']), interaction.user.id)
        role: discord.Role | None = None
        if tournament.get('participant_role_id'):
            role = guild.get_role(int(tournament['participant_role_id']))
        if role is None and tournament.get('participant_role_id'):
            logger.warning('Tournament %s has a stored participant_role_id that no longer resolves to a role in guild %s; not falling back to the shared global Participant role.', tournament.get('id'), guild.id)
        if role and isinstance(interaction.user, discord.Member):
            await interaction.user.remove_roles(role, reason='Unregistered from tournament')
        await respond(interaction, f"You left **{tournament['name']}**.")
    except TournamentError as error:
        await respond(interaction, str(error), ephemeral=True)

@bot.tree.command(name='players', description='List players in the current tournament.')
@tournament_channel_only('participant', 'group', 'organizer', 'playoffs')
@app_commands.describe(tournament_id="Optional tournament ID; defaults to this channel's tournament")
@participant_only()
@app_commands.guild_only()
async def players(interaction: discord.Interaction, tournament_id: int | None=None) -> None:
    guild = require_guild(interaction)
    tournament = await _call_off_loop(resolve_tournament_for_interaction, guild, interaction, ((OPEN, CLOSED, 'completed'),), tournament_id)
    if not tournament:
        await respond(interaction, 'There is no current tournament.')
        return
    rows = await _store_call(bot.store.players, int(tournament['id']))
    labels = tournament_player_labels(rows)
    lines = [f"`{index}.` {labels.get(int(row['user_id']), row['display_name'])}" + (f" · Group {row['group_name']}" if row.get('group_name') else '') for index, row in enumerate(rows, 1)]
    embed = discord.Embed(title=f"{tournament['name']} — Players", description='\n'.join(lines) if lines else 'No players registered.', color=discord.Color.blurple())
    await respond(interaction, '', embed=embed)

def get_active_stage(tournament_id: int) -> str:
    """Return the one stage players should currently see/use.

    For a group-to-knockout tournament the most recently created knockout
    stage is the active stage. Once a later stage exists, earlier completed
    stages are hidden from /matches so players are not shown stale fixtures.
    """
    tournament = bot.store.get_tournament(tournament_id)
    if not tournament:
        return 'group'
    if tournament['tournament_type'] != GROUP_KNOCKOUT:
        return ROUND_ROBIN
    shape = bot.store.knockout_shape_for(tournament_id)
    for stage in reversed(shape['knockout_stages']):
        if bot.store.matches(tournament_id, stage=stage):
            return stage
    return 'group'

@bot.tree.command(name='world_cup_sync_country_roles', description='Sync existing World Cup players with their saved country roles.')
@app_commands.describe(tournament_id='World Cup 32 tournament ID')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def world_cup_sync_country_roles(interaction: discord.Interaction, tournament_id: int) -> None:
    """Repair country roles for players already assigned in an existing World Cup 32."""
    guild = require_guild(interaction)
    tournament = await _store_call(bot.store.get_tournament, tournament_id)
    if not tournament or int(tournament.get('guild_id', 0)) != guild.id or str(tournament.get('template_id') or '') != TEMPLATE_WORLD_CUP_32:
        await respond(interaction, '❌ That is not a World Cup 32 tournament in this server.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        roles = await sync_world_cup_nation_roles(guild, tournament_id)
        await respond(
            interaction,
            f'✅ **World Cup country roles synchronized.** Fixed country-role assignments for the existing players ({len(roles)} player assignment(s)).',
            ephemeral=True
        )
    except TournamentError as error:
        await respond(interaction, f'❌ {error}', ephemeral=True)
    except discord.Forbidden:
        await respond(
            interaction,
            '❌ I cannot manage the country roles. Give the bot **Manage Roles** permission and move the bot role above the country roles.',
            ephemeral=True
        )

@bot.tree.command(name='wc32_assign_country', description='Manually assign an unused country to a World Cup 32 player.')
@tournament_channel_only('fixture', 'organizer')
@app_commands.describe(tournament_id='World Cup 32 tournament ID', user='Registered player to assign', country='Exact country name, e.g. Türkiye')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def wc32_assign_country(interaction: discord.Interaction, tournament_id: int, user: discord.Member, country: str) -> None:
    """Staff-only manual correction of a World Cup nation's assignment."""
    guild = require_guild(interaction)
    tournament = await _store_call(bot.store.get_tournament, tournament_id)
    if not tournament or int(tournament.get('guild_id', 0)) != guild.id or str(tournament.get('template_id') or '') != TEMPLATE_WORLD_CUP_32:
        await respond(interaction, '❌ That is not a World Cup 32 tournament in this server.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        await _store_call(bot.store.assign_world_cup_nation, tournament_id, user.id, country.strip())
        await sync_world_cup_nation_roles(guild, tournament_id)
        await respond(interaction, f'✅ **{user.display_name}** is now assigned to **{country.strip()}**. The country role has been synced and future fixture posts will mention the role.', ephemeral=True)
    except TournamentError as error:
        await respond(interaction, f'❌ {error}', ephemeral=True)
    except discord.Forbidden:
        await respond(interaction, '❌ I cannot manage the country role. Give the bot Manage Roles permission and move the bot role above the country roles.', ephemeral=True)

@bot.tree.command(name='wc32_post_fixtures_with_roles', description='Repost World Cup 32 group fixtures using country role mentions.')
@tournament_channel_only('fixture', 'organizer')
@app_commands.describe(tournament_id='World Cup 32 tournament ID; defaults to this fixtures channel')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def wc32_post_fixtures_with_roles(interaction: discord.Interaction, tournament_id: int | None=None) -> None:
    """Staff-only repair/repost command for the active World Cup 32 fixtures."""
    guild = require_guild(interaction)
    tournament = await _call_off_loop(resolve_tournament_for_interaction, guild, interaction, ((OPEN, CLOSED, COMPLETED),), tournament_id)
    if not tournament or str(tournament.get('template_id') or '') != TEMPLATE_WORLD_CUP_32:
        await respond(interaction, '❌ No World Cup 32 tournament was found for this channel/ID.', ephemeral=True)
        return
    if not tournament.get('fixtures_channel_id') or int(tournament['fixtures_channel_id']) != interaction.channel.id:
        await respond(interaction, '❌ Run this command in the World Cup 32 fixtures channel.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        # This both repairs missing country roles on members and makes the
        # fixture renderer use real Discord role mentions instead of text.
        await sync_world_cup_nation_roles(guild, int(tournament['id']))
        posted = await post_fixtures(guild, int(tournament['id']))
        if not posted:
            await respond(interaction, '❌ I could not find any World Cup 32 fixtures to repost.', ephemeral=True)
            return
        await respond(interaction, '✅ World Cup 32 fixtures reposted with country role mentions. No tournament data, results, groups, or deadlines were changed.', ephemeral=True)
    except TournamentError as error:
        await respond(interaction, f'❌ {error}', ephemeral=True)
    except discord.Forbidden:
        await respond(interaction, '❌ Discord permissions prevented the country-role assignment/post. Ensure the bot has Manage Roles and Send Messages/Embed Links, with the bot role above the country roles.', ephemeral=True)
    except discord.HTTPException as error:
        logger.exception('Failed to repost World Cup 32 fixtures with roles for tournament %s.', tournament.get('id'))
        await respond(interaction, f'❌ Discord rejected the fixture repost: {error}', ephemeral=True)

@bot.tree.command(name='playoffs_fixture', description='Post the current playoff knockout bracket with player avatars.')
@app_commands.describe(tournament_id="Optional tournament ID; defaults to this channel's tournament")
@playoffs_fixture_access()
@app_commands.guild_only()
async def playoffs_fixture(interaction: discord.Interaction, tournament_id: int | None=None) -> None:
    """Render and post the current QF/SF/Final bracket in #playoffs-fixtures."""
    guild = require_guild(interaction)
    tournament = await _call_off_loop(resolve_tournament_for_interaction, guild, interaction, ((CLOSED, 'completed'),), tournament_id)
    if not tournament or tournament['tournament_type'] != GROUP_KNOCKOUT:
        await respond(interaction, 'There is no active group-stage + knockout tournament.', ephemeral=True)
        return
    active_stage = await _call_off_loop(get_active_stage, int(tournament['id']))
    shape = await _store_call(bot.store.knockout_shape_for, int(tournament['id']))
    if active_stage not in shape['knockout_stages']:
        await respond(interaction, 'The playoff bracket is not available yet. Complete the group stage first.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        posted = await post_playoff_fixtures(guild, int(tournament['id']), active_stage)
        if posted:
            refreshed = await _store_call(bot.store.get_tournament, int(tournament['id'])) or tournament
            channel_id = refreshed.get('playoffs_fixtures_channel_id')
            channel = guild.get_channel(int(channel_id)) if channel_id else None
            destination = channel.mention if channel else "this tournament's playoffs-fixtures channel"
            await interaction.followup.send(f'✅ Playoff bracket posted in {destination}.', ephemeral=True)
        else:
            await interaction.followup.send('⚠️ I could not post the playoff bracket. Check that I have Manage Channels permission for this tournament.', ephemeral=True)
    except Exception:
        logger.exception('Failed to execute /playoffs_fixture')
        await interaction.followup.send('⚠️ Failed to generate the playoff bracket. Check the Railway logs for details.', ephemeral=True)

@bot.tree.command(name='invite_user', description='Create a server invite and DM it to a Discord user by ID.')
@app_commands.describe(user_id='The Discord user ID to invite to this server.')
@app_commands.guild_only()
async def invite_user(interaction: discord.Interaction, user_id: str) -> None:
    """Create a one-use server invite and send it privately to a user by ID.

    Discord does not provide a bot API to add an arbitrary user directly to a
    guild. This command therefore creates a one-use, never-expiring invite and
    sends the invite URL to the requested Discord account by DM.
    """
    if interaction.guild is None:
        await interaction.response.send_message('❌ This command can only be used in a server.', ephemeral=True)
        return
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message('❌ Only server Administrators can invite users with this command.', ephemeral=True)
        return
    raw_id = user_id.strip()
    if not raw_id.isdigit():
        await interaction.response.send_message('❌ Enter a valid numeric Discord User ID.', ephemeral=True)
        return
    target_id = int(raw_id)
    if target_id <= 0:
        await interaction.response.send_message('❌ Enter a valid Discord User ID.', ephemeral=True)
        return
    if interaction.guild.get_member(target_id) is not None:
        await interaction.response.send_message(f'ℹ️ <@{target_id}> is already a member of this server.', ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        target = await bot.fetch_user(target_id)
    except discord.NotFound:
        await interaction.followup.send('❌ No Discord user was found with that ID.', ephemeral=True)
        return
    except discord.HTTPException:
        await interaction.followup.send('❌ Discord could not look up that user ID. Please verify the ID and try again.', ephemeral=True)
        return

    # Prefer the server system channel, then fall back to any text channel where
    # the bot can create invites. The admin does not need to specify a channel.
    candidates: list[discord.TextChannel] = []
    if interaction.guild.system_channel is not None:
        candidates.append(interaction.guild.system_channel)
    candidates.extend(c for c in interaction.guild.text_channels if c not in candidates)
    invite_channel = None
    for candidate in candidates:
        me = interaction.guild.me
        if me is None:
            continue
        perms = candidate.permissions_for(me)
        if perms.view_channel and perms.create_instant_invite:
            invite_channel = candidate
            break
    if invite_channel is None:
        await interaction.followup.send('❌ I cannot create a server invite. Give the bot **Create Instant Invite** permission in at least one text channel.', ephemeral=True)
        return

    try:
        invite = await invite_channel.create_invite(
            max_age=0,
            max_uses=1,
            unique=True,
            reason=f'Administrator invite for Discord user {target_id}',
        )
    except discord.Forbidden:
        await interaction.followup.send(f'❌ Discord denied invite creation in {invite_channel.mention}. Check **Create Instant Invite** permission.', ephemeral=True)
        return
    except discord.HTTPException:
        await interaction.followup.send('❌ Failed to create the server invite. Please try again.', ephemeral=True)
        return

    dm_message = (
        f'👋 You have been invited to **{interaction.guild.name}**!\n\n'
        f'🔗 **Server Invite:** {invite.url}\n\n'
        f'This invitation can be used once.'
    )
    try:
        await target.send(dm_message)
    except discord.Forbidden:
        await interaction.followup.send(
            f'⚠️ I created the invite, but I could not DM <@{target_id}>. Their DMs may be disabled or the bot may be blocked.\n\n'
            f'Invite: {invite.url}',
            ephemeral=True,
        )
        return
    except discord.HTTPException:
        await interaction.followup.send(
            f'⚠️ I created the invite, but Discord rejected the DM.\n\nInvite: {invite.url}',
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        f'✅ Server invite sent by DM to <@{target_id}>.\n'
        f'📍 Invite created in {invite_channel.mention} (1 use, no expiry).',
        ephemeral=True,
    )

@bot.tree.command(name='send_message', description='Send a message to a tournament channel or DM a participant.')
@staff_messaging_access()
@app_commands.describe(message='The message to send (up to 2000 characters).', channel='The tournament text channel to receive the message.', participant='The current tournament participant to DM.', tournament_id="Optional tournament ID for the DM case; defaults to this channel's tournament")
@app_commands.guild_only()
async def send_message(interaction: discord.Interaction, message: str, channel: discord.TextChannel | None=None, participant: discord.Member | None=None, tournament_id: int | None=None) -> None:
    """Send staff-written text to one channel or one current participant by DM."""
    guild = require_guild(interaction)
    if not message.strip():
        await respond(interaction, '❌ The message cannot be empty.', ephemeral=True)
        return
    if len(message) > 2000:
        await respond(interaction, '❌ Discord messages can contain at most 2000 characters.', ephemeral=True)
        return
    if (channel is None) == (participant is None):
        await respond(interaction, '❌ Choose exactly one destination: a **channel** or a **participant**.', ephemeral=True)
        return
    if channel is not None:
        permissions = channel.permissions_for(guild.me) if guild.me else None
        if permissions is None or not permissions.view_channel or (not permissions.send_messages):
            await respond(interaction, f'❌ I cannot send messages in {channel.mention}. Check my channel permissions.', ephemeral=True)
            return
        try:
            await channel.send(str(message))
        except discord.Forbidden:
            await respond(interaction, f'❌ I do not have permission to send messages in {channel.mention}.', ephemeral=True)
            return
        except discord.HTTPException as error:
            logger.warning('Failed to send staff message to channel %s: %s', channel.id, error)
            await respond(interaction, '❌ Discord rejected the message. Please check the channel and message content.', ephemeral=True)
            return
        await respond(interaction, f'✅ Message sent to {channel.mention}.', ephemeral=True)
        return
    tournament = await _call_off_loop(resolve_tournament_for_interaction, guild, interaction, ((OPEN, CLOSED, 'completed'),), tournament_id)
    if not tournament:
        await respond(interaction, '❌ There is no current tournament with registered participants.', ephemeral=True)
        return
    participant_ids = {int(row['user_id']) for row in await _store_call(bot.store.players, int(tournament['id']))}
    if participant.id not in participant_ids:
        await respond(interaction, f'❌ {participant.mention} is not a participant in the current tournament.', ephemeral=True)
        return
    if participant.bot:
        await respond(interaction, '❌ Tournament participant DMs can only be sent to human participants.', ephemeral=True)
        return
    try:
        await participant.send(str(message))
    except discord.Forbidden:
        await respond(interaction, f'❌ I could not DM {participant.mention}. Their DMs may be disabled or the bot may be blocked.', ephemeral=True)
        return
    except discord.HTTPException as error:
        logger.warning('Failed to DM participant %s: %s', participant.id, error)
        await respond(interaction, '❌ Discord rejected the DM. Please try again later.', ephemeral=True)
        return
    await respond(interaction, f'✅ Message sent privately to {participant.mention}.', ephemeral=True)

@bot.tree.command(name='matches', description='Show tournament fixtures.')
@tournament_channel_only('fixture', 'participant', 'group', 'organizer', 'playoffs', 'playoffs_fixtures')
@app_commands.describe(only_mine='Show only your matches', group='For group tournaments, optionally show one group')
@app_commands.choices(group=[app_commands.Choice(name=f'Group {group}', value=group) for group in GROUP_LETTERS])
@app_commands.describe(tournament_id="Optional tournament ID; defaults to this channel's tournament")
@participant_only()
@app_commands.guild_only()
async def matches(interaction: discord.Interaction, only_mine: bool=False, group: app_commands.Choice[str] | None=None, tournament_id: int | None=None) -> None:
    guild = require_guild(interaction)
    tournament = await _call_off_loop(resolve_tournament_for_interaction, guild, interaction, ((OPEN, CLOSED, 'completed'),), tournament_id)
    if not tournament:
        await respond(interaction, 'There is no current tournament.', ephemeral=True)
        return
    tournament_id = int(tournament['id'])
    player_names = tournament_player_labels(await _store_call(bot.store.players, tournament_id))
    active_stage = await _call_off_loop(get_active_stage, tournament_id)
    all_rows = await _store_call(bot.store.matches, tournament_id, stage=active_stage)
    number_by_id = {int(row['id']): index for index, row in enumerate(all_rows, 1)}
    rows = await _store_call(bot.store.matches, tournament_id, stage=active_stage, group_name=group.value if group and active_stage == 'group' else None, user_id=interaction.user.id if only_mine else None)
    if not rows:
        await respond(interaction, 'No fixtures match those filters.', ephemeral=True)
        return
    embed = discord.Embed(title=f"{tournament['name']} — Matches", description='\n'.join((format_match(guild, row, player_names, number_by_id[int(row['id'])]) for row in rows[:25])), color=discord.Color.blurple())
    if len(rows) > 25:
        embed.set_footer(text=f'Showing 25 of {len(rows)} matches.')
    await respond(interaction, '', embed=embed)

async def report_opponent_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Only show opponents who are actually matched with the user.

    Resolved the same way /report itself resolves its tournament: strictly
    via the channel's stored tournament-ID mapping (falling back to the
    latest closed/completed tournament only when the channel isn't tied to
    one specific tournament, e.g. the shared organizer channel). This must
    never show another tournament's opponents just because it happens to be
    the most recently completed one in the guild.
    """
    try:
        if not interaction.guild or not interaction.channel:
            return []
        tournament = await _store_call(bot.store.get_tournament_by_channel, interaction.guild.id, interaction.channel.id)
        if not tournament:
            return []
        tournament_id = int(tournament['id'])
        rows = await _store_call(bot.store.matches, tournament_id, user_id=interaction.user.id)
        player_names = tournament_player_labels(await _store_call(bot.store.players, tournament_id))
        choices: list[app_commands.Choice[str]] = []
        search = current.strip().lower()
        for row in rows:
            if row['status'] == 'completed':
                continue
            player1 = int(row['player1_id'])
            player2 = int(row['player2_id'])
            if interaction.user.id == player1:
                opponent_id = player2
            elif interaction.user.id == player2:
                opponent_id = player1
            else:
                continue
            saved_opponent = next((p for p in await _store_call(bot.store.players, tournament_id) if int(p['user_id']) == opponent_id), None)
            name = player_names.get(opponent_id, str(saved_opponent['display_name']) if saved_opponent else f'Player {opponent_id}')
            member = interaction.guild.get_member(opponent_id)
            username = member.name if member else ''
            if search and search not in name.lower() and (search not in username.lower()):
                continue
            choices.append(app_commands.Choice(name=name[:100], value=str(opponent_id)))
        return choices[:25]
    except Exception:
        logger.exception('Failed to generate report opponent choices')
        return []

def _build_group_standings_embed(tournament: dict[str, Any]) -> discord.Embed | None:
    """Group-stage standings (or the overall table for a KO-match tournament).

    Returns None when there is no standings data yet (e.g. no completed
    matches), so callers can skip posting an empty table.
    """
    tournament_id = int(tournament['id'])
    if tournament['tournament_type'] == GROUP_KNOCKOUT:
        sections = []
        for group_name in bot.store.knockout_shape_for(tournament_id)['groups']:
            rows = bot.store.standings(tournament_id, group_name)
            if not rows:
                continue
            sections.append(f'**Group {group_name}**\n' + format_standings_table(rows))
        if not sections:
            return None
        title = f"{tournament['name']} — Group Stage Standings"
        description = '\n\n'.join(sections)
    else:
        rows = bot.store.standings(tournament_id)
        if not rows:
            return None
        title = f"{tournament['name']} — Standings"
        description = format_standings_table(rows)
    embed = discord.Embed(title=title, description=description)
    embed.set_footer(text='Points: win 3 · draw 1 · loss 0')
    return embed

def _build_playoffs_standings_embed(guild: discord.Guild, tournament: dict[str, Any]) -> discord.Embed | None:
    """Knockout-stage results, grouped by round.

    Returns None until at least one knockout-stage match exists, so the
    playoffs standings channel stays empty until the knockout stage starts.
    """
    tournament_id = int(tournament['id'])
    stages = bot.store.knockout_shape_for(tournament_id)['knockout_stages'] if tournament['tournament_type'] == GROUP_KNOCKOUT else (ROUND_ROBIN,)
    all_rows = bot.store.matches(tournament_id)
    number_by_id = {int(row['id']): index for index, row in enumerate(all_rows, 1)}
    sections = []
    player_labels = tournament_player_labels(bot.store.players(tournament_id))
    for stage in stages:
        rows = bot.store.matches(tournament_id, stage=stage)
        if not rows:
            continue
        label = 'Knockout' if stage == ROUND_ROBIN else stage_label(stage)
        lines = [format_match(guild, row, player_labels, number_by_id[int(row['id'])]) for row in rows]
        sections.append(f'**{label}**\n' + '\n'.join(lines))
    if not sections:
        return None
    embed = discord.Embed(title=f"{tournament['name']} — Playoffs Standings", description='\n\n'.join(sections), color=discord.Color.gold())
    if tournament['status'] == 'completed':
        embed.set_footer(text='🏆 Tournament complete')
    return embed

async def _refresh_standings_channels(guild: discord.Guild, tournament_id: int) -> None:
    """Post the latest standings to this tournament's own standings channels.

    Each tournament has its own #<slug>-standings channel (group-stage
    standings, plus knockout-stage results once the knockout stage has
    started) and, for group/knockout tournaments, its own
    #<slug>-playoffs-standings channel (knockout-stage results only, empty
    until the knockout stage starts). Channels are resolved by the ID stored
    for THIS tournament — never by a shared channel name — so concurrent
    tournaments never collide on each other's standings. The bot's previous
    standings message in each channel is deleted before the new one is
    posted, so only the latest snapshot is ever visible.
    """
    tournament = await _store_call(bot.store.get_tournament, tournament_id)
    if not tournament:
        return
    channels = await _ensure_standings_and_playoffs_channels(guild, tournament)
    standings_channel = channels['standings_channel']
    playoffs_standings_channel = channels['playoffs_standings_channel']
    if not standings_channel and (not playoffs_standings_channel):
        return
    group_embed = await _call_off_loop(_build_group_standings_embed, tournament)
    playoffs_embed = await _call_off_loop(_build_playoffs_standings_embed, guild, tournament)
    if standings_channel:
        embeds = [embed for embed in (group_embed, playoffs_embed) if embed is not None]
        if embeds:
            try:
                await _replace_bot_embeds(standings_channel, embeds)
            except discord.Forbidden:
                logger.warning('Cannot post standings to #%s. Give the bot Manage Messages/Send Messages permission.', standings_channel.name)
    if playoffs_standings_channel and playoffs_embed is not None:
        try:
            await _replace_bot_embeds(playoffs_standings_channel, [playoffs_embed])
        except discord.Forbidden:
            logger.warning('Cannot post standings to #%s. Give the bot Manage Messages/Send Messages permission.', playoffs_standings_channel.name)

async def _apply_event_side_effects(guild: discord.Guild, tournament_id: int, event: dict[str, Any] | None) -> str:
    """Handle role syncing for a result event and return text to append.

    Shared by /set_result, the /report modal, and the Confirm button so all
    three paths that can complete a match apply the exact same role logic.
    """
    try:
        await _refresh_standings_channels(guild, tournament_id)
    except Exception:
        logger.exception('Failed to refresh standings channels for tournament %s', tournament_id)
    if not event:
        return ''
    event_tournament = await _store_call(bot.store.get_tournament, tournament_id)
    event_players = await _store_call(bot.store.players, tournament_id)
    message = _event_message(event, tournament_player_labels(event_players))
    if event['type'] == 'champion':
        # Competitive title roles are exclusive: only the latest champion of
        # each supported tournament type keeps that title.  KO Match has no
        # title role because it is a practice/fun tournament.
        try:
            if event_tournament and event.get('user_id') is not None:
                title_role = await _set_current_champion_title(
                    guild, event_tournament, int(event['user_id'])
                )
                if title_role:
                    message += f'\nTitle awarded: **{title_role.name}**.'
        except Exception:
            logger.exception('Failed to assign current champion title for tournament %s', tournament_id)
        try:
            award = await _store_call(bot.store.award_champions_for_completed_tournament, tournament_id)
            if award and award.get('champion_id') is not None:
                await _update_champions_leaderboard(guild)
        except ValueError as exc:
            logger.error('Champions leaderboard award rejected for tournament %s: %s', tournament_id, exc)
        except Exception:
            logger.exception('Failed to persist Champions leaderboard award for tournament %s', tournament_id)
        try:
            await clear_tournament_roles(guild, tournament_id)
            message += '\nTournament roles have been cleared.'
        except discord.Forbidden:
            message += '\nRole warning: I could not remove tournament roles. Check Manage Roles and role hierarchy.'
        except TournamentError as error:
            message += f'\nRole warning: {error}'
        champion_labels = tournament_player_labels(event_players)
        champion_mention = champion_labels.get(int(event['user_id'])) if event.get('user_id') is not None else None
        champion_mention = champion_mention or (f"<@{int(event['user_id'])}>" if event.get('user_id') is not None else f"**{event['name']}**")
        await _announce_hall_of_fame_award(
            guild,
            award_type='Tournament Winner',
            winner=champion_mention,
            title=title_role.name if 'title_role' in locals() and title_role else None,
            tournament=event_tournament,
        )
        try:
            await post_playoff_fixtures(guild, tournament_id, 'final')
        except Exception:
            logger.exception('Failed to refresh the final playoff bracket after tournament completion')
    elif event['type'] == 'qualified':
        try:
            await sync_playoff_qualified_role(guild, tournament_id, event['qualifier_ids'])
            message += '\nRole assigned: **Playoff Qualified**.'
        except TournamentError as role_error:
            message += f'\nRole warning: {role_error}'
        first_stage = str(event.get('stage') or 'quarterfinal')
        label = stage_label(first_stage)
        labels = tournament_player_labels(await _store_call(bot.store.players, tournament_id))
        qualifier_mentions = ', '.join((labels.get(int(user_id), format_user(guild, int(user_id))) for user_id in event['qualifier_ids']))
        row_for_channels = await _store_call(bot.store.get_tournament, tournament_id)
        fixtures_channel = None
        if row_for_channels:
            channels_now = await _ensure_standings_and_playoffs_channels(guild, row_for_channels)
            fixtures_channel = channels_now['playoffs_fixtures_channel']
        fixtures_mention = fixtures_channel.mention if fixtures_channel else "this tournament's playoffs-fixtures channel"
        await announce(guild, f'🏁 **Group stage completed!**\n🎟️ **Qualified for the {label}:** ' + qualifier_mentions + f'\n\n📋 {label} fixtures are now available in {fixtures_mention}.' + f'\n💬 {label} discussion: #{PLAYOFFS_CHAT_CHANNEL}.', tournament=row_for_channels or event_tournament)
        await announce_stage_deadline(guild, tournament_id, first_stage, qualifier_ids=[int(uid) for uid in event['qualifier_ids']])
        await post_playoff_fixtures(guild, tournament_id, first_stage)
    elif event['type'] == 'stage':
        stage = str(event.get('stage') or await _call_off_loop(get_active_stage, tournament_id))
        rows = await _store_call(bot.store.matches, tournament_id, stage=stage)
        qualified_ids = []
        for row in rows:
            qualified_ids.extend([int(row['player1_id']), int(row['player2_id'])])
        qualified_ids = list(dict.fromkeys(qualified_ids))
        label = stage_label(stage) if stage in STAGE_LABELS else event.get('name', 'Next stage')
        qualified_ids = [int(user_id) for user_id in event.get('qualifier_ids', qualified_ids)]
        labels = tournament_player_labels(await _store_call(bot.store.players, tournament_id))
        mentions = ', '.join((labels.get(int(user_id), format_user(guild, user_id)) for user_id in qualified_ids))
        row_for_channels = await _store_call(bot.store.get_tournament, tournament_id)
        fixtures_channel = None
        if row_for_channels:
            channels_now = await _ensure_standings_and_playoffs_channels(guild, row_for_channels)
            fixtures_channel = channels_now['playoffs_fixtures_channel']
        fixtures_mention = fixtures_channel.mention if fixtures_channel else "this tournament's playoffs-fixtures channel"
        await announce(guild, f'🎟️ **Qualified for the {label}:** {mentions}\n📋 {label} fixtures are now available in {fixtures_mention}.\n💬 Discussion: #{PLAYOFFS_CHAT_CHANNEL}.', tournament=row_for_channels or event_tournament)
        await announce_stage_deadline(guild, tournament_id, stage, qualifier_ids=qualified_ids)
        await post_playoff_fixtures(guild, tournament_id, stage)
    return message

async def _ensure_dispute_thread_permissions(guild: discord.Guild, parent: discord.TextChannel, tournament: dict[str, Any] | None=None) -> None:
    """Make sure players and staff can actually send messages/attachments
    inside private threads created under ``parent``.

    Threads never have permissions of their own — they always inherit the
    parent channel's overwrites, and Discord swaps in the thread-specific
    versions of a few permissions: ``send_messages_in_threads`` instead of
    ``send_messages``, with ``attach_files``/``embed_links`` following the
    same swap. Adding a player to the private dispute thread
    (``thread.add_user``) only makes the thread visible to them — unless
    #result-submission's overwrites already allow those thread-specific
    bits for that player, every message or photo/video they try to send in
    the thread is silently rejected by Discord even though they can see it
    and appear to be a member of it. Since threads can't carry their own
    overwrites, the only place this can be granted is on the parent
    channel, so this repairs it there for the Participant role, staff, and
    the bot — merging onto whatever overwrite each target already has
    rather than replacing it, so nothing else configured on the channel is
    lost.

    When ``tournament`` is given, this repairs the permission bits for
    THAT tournament's own Participant role (its actual disputants), not the
    shared legacy role — a tournament running its own isolated results
    channel has disputants holding its own role, not the global one, so
    fixing the global role's overwrite here would do nothing for them.
    ``tournament`` is omitted only for the permanent/legacy shared results
    channel, which has no single owning tournament.
    """
    targets: list[discord.Role | discord.Member] = list(_staff_roles(guild))
    participant_role = await get_tournament_participant_role(guild, tournament) if tournament is not None else discord.utils.get(guild.roles, name=PARTICIPANT_ROLE)
    if participant_role:
        targets.append(participant_role)
    if guild.me:
        targets.append(guild.me)
    overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {}
    for target in targets:
        overwrite = parent.overwrites_for(target)
        overwrite.send_messages_in_threads = True
        overwrite.attach_files = True
        overwrite.embed_links = True
        overwrites[target] = overwrite
    try:
        await _apply_overwrites(parent, overwrites)
    except discord.Forbidden as error:
        raise TournamentError("I can't grant permission to post in dispute threads. Give me the Manage Roles permission.") from error

async def _find_or_create_dispute_thread(guild: discord.Guild, match: dict[str, Any]) -> discord.Thread:
    """Create (or reuse) the private thread used to collect dispute proof.

    Resolved through Match -> Tournament ID -> that tournament's stored
    results_channel_id, so a dispute for one tournament's match always opens
    in that tournament's own results channel rather than a global shared
    #result-submission channel that could belong to (or be confused with)
    a different tournament.
    """
    parent: discord.TextChannel | None = None
    tournament_id = match.get('tournament_id')
    match_tournament: dict[str, Any] | None = None
    if tournament_id is not None:
        match_tournament = await _store_call(bot.store.get_tournament, int(tournament_id))
        if match_tournament and match_tournament.get('results_channel_id'):
            maybe_channel = guild.get_channel(int(match_tournament['results_channel_id']))
            if isinstance(maybe_channel, discord.TextChannel):
                parent = maybe_channel
    if parent is None:
        logger.warning('Match %s (tournament %s) has no resolvable results_channel_id for opening a dispute thread.', match.get('id'), tournament_id)
        raise TournamentError("I can't find this tournament's results channel to open a dispute thread in.")
    await _ensure_dispute_thread_permissions(guild, parent, match_tournament)
    existing_id = match.get('dispute_channel_id')
    if existing_id:
        channel = guild.get_channel_or_thread(int(existing_id))
        if isinstance(channel, discord.Thread) and (not channel.archived):
            return channel
    try:
        thread = await parent.create_thread(name=f"dispute-match-{match['id']}", type=discord.ChannelType.private_thread, invitable=False, reason=f"Result dispute raised for match {match['id']}")
    except discord.Forbidden as error:
        raise TournamentError("I don't have permission to create a private thread. Give me the Create Private Threads permission.") from error
    for user_id in (int(match['player1_id']), int(match['player2_id'])):
        member = guild.get_member(user_id)
        if member:
            try:
                await thread.add_user(member)
            except discord.HTTPException:
                pass
    return thread

class ConfirmResultButton(discord.ui.DynamicItem[discord.ui.Button], template='confirm_result:(?P<match_id>\\d+):(?P<opponent_id>\\d+)'):
    """Persistent button: lets the opponent confirm a submitted score."""

    def __init__(self, match_id: int, opponent_id: int) -> None:
        super().__init__(discord.ui.Button(label='Confirm Result', style=discord.ButtonStyle.success, emoji='✅', custom_id=f'confirm_result:{match_id}:{opponent_id}'))
        self.match_id = match_id
        self.opponent_id = opponent_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]) -> 'ConfirmResultButton':
        return cls(int(match['match_id']), int(match['opponent_id']))

    async def callback(self, interaction: discord.Interaction) -> None:
        await handle_confirm_result(interaction, self.match_id, self.opponent_id)

class DisputeResultButton(discord.ui.DynamicItem[discord.ui.Button], template='dispute_result:(?P<match_id>\\d+):(?P<opponent_id>\\d+)'):
    """Persistent button: lets the opponent dispute a submitted score."""

    def __init__(self, match_id: int, opponent_id: int) -> None:
        super().__init__(discord.ui.Button(label='Dispute', style=discord.ButtonStyle.danger, emoji='❌', custom_id=f'dispute_result:{match_id}:{opponent_id}'))
        self.match_id = match_id
        self.opponent_id = opponent_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]) -> 'DisputeResultButton':
        return cls(int(match['match_id']), int(match['opponent_id']))

    async def callback(self, interaction: discord.Interaction) -> None:
        await handle_dispute_result(interaction, self.match_id, self.opponent_id)

class ResultActionView(discord.ui.View):
    """Confirm/Dispute buttons attached to a freshly submitted result.

    Persistent (timeout=None) - both buttons are backed by the
    ``DynamicItem`` classes above, which are registered once in
    ``setup_hook`` and keep working after a bot restart because the match
    and opponent IDs are encoded directly in each button's custom_id.
    """

    def __init__(self, match_id: int, opponent_id: int) -> None:
        super().__init__(timeout=None)
        self.add_item(ConfirmResultButton(match_id, opponent_id))
        self.add_item(DisputeResultButton(match_id, opponent_id))

async def handle_confirm_result(interaction: discord.Interaction, match_id: int, opponent_id: int) -> None:
    if interaction.user.id != opponent_id:
        await respond(interaction, 'Only the other player in this match can confirm this result.', ephemeral=True)
        return
    guild = interaction.guild
    if guild is None:
        return
    try:
        match = await _store_call(bot.store.get_match, match_id)
        if not match:
            await respond(interaction, 'That match no longer exists.', ephemeral=True)
            return
        if match['status'] == 'completed':
            await respond(interaction, 'This match has already been completed.', ephemeral=True)
            await _disable_result_buttons(interaction)
            return
        if match['reported_by'] is None:
            await respond(interaction, 'There is no submitted result to confirm.', ephemeral=True)
            return
        result = await _store_call(bot.store.report_result, match_id, opponent_id, int(match['score1']), int(match['score2']))
        reporter_id = int(match['reported_by'])
        labels = tournament_player_labels(await _store_call(bot.store.players, int(match['tournament_id'])))
        message = f"✅ **{labels.get(int(interaction.user.id), interaction.user.display_name)}** confirmed the result: **{match['score1']} - {match['score2']}** (submitted by {format_user(guild, reporter_id, labels)})."
        event = result.get('event')
        message += await _apply_event_side_effects(guild, int(result['tournament_id']), event)
        await respond(interaction, message)
        await _disable_result_buttons(interaction, note='Confirmed.')
    except TournamentError as error:
        await respond(interaction, str(error), ephemeral=True)
    except Exception:
        logger.exception('Unhandled error confirming match %s', match_id)
        await respond(interaction, 'Something went wrong confirming that result. Please try again, or ask a moderator to use `/set_result`.', ephemeral=True)

async def handle_dispute_result(interaction: discord.Interaction, match_id: int, opponent_id: int) -> None:
    if interaction.user.id != opponent_id:
        await respond(interaction, 'Only the other player in this match can dispute this result.', ephemeral=True)
        return
    guild = interaction.guild
    if guild is None:
        return
    try:
        match = await _store_call(bot.store.get_match, match_id)
        if not match:
            await respond(interaction, 'That match no longer exists.', ephemeral=True)
            return
        thread = await _find_or_create_dispute_thread(guild, match)
        await _store_call(bot.store.mark_disputed, match_id, opponent_id, thread.id)
        reporter_id = int(match['reported_by'])
        labels = tournament_player_labels(await _store_call(bot.store.players, int(match['tournament_id'])))
        await thread.send(f"⚠️ **Result dispute for match `#{match_id}`**\n{format_user(guild, reporter_id, labels)} submitted **{match['score1']} - {match['score2']}**, which {interaction.user.mention} disputed.\n\nBoth players: please post your proof here (screenshots or a screen recording of the final score). A moderator will review this and resolve it with `/set_result`.")
        await respond(interaction, f"I've opened {thread.mention} for this dispute — please post your proof there.", ephemeral=True)
        await _disable_result_buttons(interaction, note='Disputed — see the thread.')
    except TournamentError as error:
        await respond(interaction, str(error), ephemeral=True)
    except Exception:
        logger.exception('Unhandled error disputing match %s', match_id)
        await respond(interaction, 'Something went wrong opening that dispute. Please try again, or ask a moderator to use `/set_result`.', ephemeral=True)

async def _disable_result_buttons(interaction: discord.Interaction, note: str | None=None) -> None:
    """Remove the Confirm/Dispute buttons from the original report message."""
    try:
        message = interaction.message
        if message is None:
            return
        content = message.content
        if note:
            content = f'{content}\n\n_{note}_'
        await message.edit(content=content, view=None)
    except discord.HTTPException:
        pass

class ScoreReportModal(discord.ui.Modal):

    def __init__(self, *, guild: discord.Guild, tournament_id: int, match_id: int, reporter_id: int, reporter_name: str, opponent_id: int, opponent_name: str) -> None:
        reporter_label = f'{reporter_name[:35]} score'
        opponent_label = f'{opponent_name[:35]} score'
        super().__init__(title=f'Report: {reporter_name[:20]} vs {opponent_name[:20]}')
        self.guild = guild
        self.tournament_id = tournament_id
        self.match_id = match_id
        self.reporter_id = reporter_id
        self.opponent_id = opponent_id
        self.reporter_score = discord.ui.TextInput(label=reporter_label, placeholder=f'Goals scored by {reporter_name}', required=True, min_length=1, max_length=2)
        self.opponent_score = discord.ui.TextInput(label=opponent_label, placeholder=f'Goals scored by {opponent_name}', required=True, min_length=1, max_length=2)
        self.add_item(self.reporter_score)
        self.add_item(self.opponent_score)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            rows = await _store_call(bot.store.matches, self.tournament_id, user_id=self.reporter_id)
            match = next((row for row in rows if int(row['id']) == self.match_id and row['status'] != 'completed' and ({int(row['player1_id']), int(row['player2_id'])} == {self.reporter_id, self.opponent_id})), None)
            if not match:
                await respond(interaction, 'This fixture is no longer pending or is no longer your match.', ephemeral=True)
                return
            try:
                your_score = int(self.reporter_score.value.strip())
                opponent_score = int(self.opponent_score.value.strip())
            except ValueError:
                await respond(interaction, 'Scores must be whole numbers from 0 to 99.', ephemeral=True)
                return
            if not (0 <= your_score <= 99 and 0 <= opponent_score <= 99):
                await respond(interaction, 'Scores must be between 0 and 99.', ephemeral=True)
                return
            if self.reporter_id == int(match['player1_id']):
                db_score1 = your_score
                db_score2 = opponent_score
            else:
                db_score1 = opponent_score
                db_score2 = your_score
            result = await _store_call(bot.store.report_result, self.match_id, self.reporter_id, db_score1, db_score2)
            shown_score = f'{your_score} - {opponent_score}'
            opponent_name = format_user(self.guild, self.opponent_id, tournament_player_labels(await _store_call(bot.store.players, self.tournament_id)))
            event = result.get('event')
            labels = tournament_player_labels(await _store_call(bot.store.players, self.tournament_id))
            reporter_label = labels.get(int(self.reporter_id), interaction.user.display_name)
            view: discord.ui.View | None = None
            if event and event.get('type') == 'awaiting_confirmation':
                message = f"Result submitted by **{reporter_label}** for **{opponent_name}**: **{shown_score}**.\n\n⏳ {opponent_name}, please confirm this is correct, or dispute it if it isn't. The match will not be completed or advanced until then."
                view = ResultActionView(self.match_id, self.opponent_id)
            else:
                message = f'Result confirmed for **{reporter_label}** vs **{opponent_name}**: **{shown_score}**.'
                message += await _apply_event_side_effects(self.guild, int(result['tournament_id']), event)
            await respond(interaction, message, view=view)
        except TournamentError as error:
            await respond(interaction, str(error), ephemeral=True)
        except Exception:
            logger.exception('Unhandled error reporting match %s (reporter %s vs %s)', self.match_id, self.reporter_id, self.opponent_id)
            await respond(interaction, 'Something went wrong recording that result. Please try again, or ask a moderator to use `/set_result`.', ephemeral=True)

@bot.tree.command(name='report', description='Report a result against your tournament opponent.')
@tournament_channel_only('result', 'organizer')
@app_commands.describe(opponent='Select the opponent from your pending fixture', tournament_id="Optional tournament ID; defaults to this channel's tournament")
@participant_only()
@app_commands.guild_only()
async def report(interaction: discord.Interaction, opponent: str, tournament_id: int | None=None) -> None:
    try:
        guild = require_guild(interaction)
        tournament = await _call_off_loop(resolve_tournament_for_interaction, guild, interaction, ((CLOSED, 'completed'),), tournament_id)
        if not tournament:
            await respond(interaction, 'There is no tournament with fixtures available for reporting.', ephemeral=True)
            return
        try:
            opponent_id = int(opponent)
        except ValueError:
            await respond(interaction, 'Please select an opponent from the suggestions.', ephemeral=True)
            return
        tournament_id = int(tournament['id'])
        rows = await _store_call(bot.store.matches, tournament_id, user_id=interaction.user.id)
        match = next((row for row in rows if row['status'] != 'completed' and {int(row['player1_id']), int(row['player2_id'])} == {interaction.user.id, opponent_id}), None)
        opponent_member = guild.get_member(opponent_id)
        saved_player = next((player for player in await _store_call(bot.store.players, tournament_id) if int(player['user_id']) == opponent_id), None)
        labels = tournament_player_labels(await _store_call(bot.store.players, tournament_id))
        opponent_name = labels.get(opponent_id, opponent_member.display_name if opponent_member else f'Player {opponent_id}')
        if not match:
            await respond(interaction, f'**{opponent_name}** is not your opponent in a pending fixture.', ephemeral=True)
            return
        await interaction.response.send_modal(ScoreReportModal(guild=guild, tournament_id=tournament_id, match_id=int(match['id']), reporter_id=interaction.user.id, reporter_name=labels.get(int(interaction.user.id), interaction.user.display_name), opponent_id=opponent_id, opponent_name=opponent_name))
    except TournamentError as error:
        await respond(interaction, str(error), ephemeral=True)
report.autocomplete('opponent')(report_opponent_autocomplete)

@bot.tree.command(name='post_pending_confirmations', description='Post Confirm/Dispute buttons for results that are still awaiting confirmation.')
@tournament_channel_only('organizer')
@app_commands.describe(tournament_id="Optional tournament ID; defaults to this channel's tournament")
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def post_pending_confirmations(interaction: discord.Interaction, tournament_id: int | None=None) -> None:
    """Backfill Confirm/Dispute buttons for results submitted before this
    feature existed (or after any message with the buttons was lost)."""
    guild = require_guild(interaction)
    tournament = await _call_off_loop(resolve_tournament_for_interaction, guild, interaction, ((CLOSED, 'completed'),), tournament_id)
    if not tournament:
        await respond(interaction, 'There is no tournament with fixtures right now.', ephemeral=True)
        return
    tournament_id = int(tournament['id'])
    pending = [row for row in await _store_call(bot.store.matches, tournament_id) if row['status'] != 'completed' and row['reported_by'] is not None]
    if not pending:
        await respond(interaction, 'No results are currently awaiting confirmation.', ephemeral=True)
        return
    result_channel: discord.TextChannel | None = None
    if tournament.get('results_channel_id'):
        maybe_channel = guild.get_channel(int(tournament['results_channel_id']))
        if isinstance(maybe_channel, discord.TextChannel):
            result_channel = maybe_channel
        else:
            logger.warning('Tournament %s has a stored results_channel_id that no longer resolves to a text channel in guild %s.', tournament.get('id'), guild.id)
    else:
        logger.warning('Tournament %s has no stored results_channel_id.', tournament.get('id'))
    if result_channel is None:
        await respond(interaction, "This tournament's results channel could not be resolved from its stored channel ID. No confirmation buttons were posted.", ephemeral=True)
        return
    target_channel = result_channel
    player_names = tournament_player_labels(await _store_call(bot.store.players, tournament_id))
    all_rows = await _store_call(bot.store.matches, tournament_id)
    number_by_id = {int(row['id']): index for index, row in enumerate(all_rows, 1)}
    posted = 0
    for match in pending:
        reporter_id = int(match['reported_by'])
        player1_id = int(match['player1_id'])
        player2_id = int(match['player2_id'])
        opponent_id = player2_id if reporter_id == player1_id else player1_id
        reporter_mention = format_user(guild, reporter_id, player_names)
        opponent_mention = format_user(guild, opponent_id, player_names)
        number = number_by_id.get(int(match['id']), int(match['id']))
        message = f"`#{number}` **{reporter_mention}** vs **{opponent_mention}** — **{match['score1']} - {match['score2']}** submitted by {reporter_mention}.\n\n⏳ {opponent_mention}, please confirm this is correct, or dispute it if it isn't. The match will not be completed or advanced until then."
        await target_channel.send(content=message, view=ResultActionView(int(match['id']), opponent_id))
        posted += 1
    await respond(interaction, f"Posted confirmation buttons for {posted} pending result{('s' if posted != 1 else '')} in {target_channel.mention}.", ephemeral=True)

@bot.tree.command(name='set_result', description='Set or correct any tournament result.')
@tournament_channel_only('organizer')
@app_commands.describe(match_id='The fixture number shown by /matches', score1='Goals scored by the first player shown in the fixture', score2='Goals scored by the second player shown in the fixture', tournament_id="Optional tournament ID; defaults to this channel's tournament")
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def set_result(interaction: discord.Interaction, match_id: int, score1: app_commands.Range[int, 0, 99], score2: app_commands.Range[int, 0, 99], tournament_id: int | None=None) -> None:
    guild = require_guild(interaction)
    try:
        tournament = await _call_off_loop(resolve_tournament_for_interaction, guild, interaction, ((CLOSED, 'completed'),), tournament_id)
        if not tournament:
            raise TournamentError('There is no tournament with fixtures available for setting results.')
        tournament_id = int(tournament['id'])
        active_stage = await _call_off_loop(get_active_stage, tournament_id)
        visible_matches = await _store_call(bot.store.matches, tournament_id, stage=active_stage)
        if match_id < 1 or match_id > len(visible_matches):
            raise TournamentError(f'Fixture `{match_id}` is not valid for the current {active_stage} stage. Use the fixture number shown by `/matches` (1-{len(visible_matches)}).')
        actual_match_id = int(visible_matches[match_id - 1]['id'])
        result = await _store_call(bot.store.set_result, guild.id, actual_match_id, interaction.user.id, int(score1), int(score2))
        message = f'Result set for fixture `{match_id}`: **{score1} - {score2}**.'
        event = result.get('event')
        message += await _apply_event_side_effects(guild, int(result['tournament_id']), event)
        await respond(interaction, message)
    except TournamentError as error:
        await respond(interaction, str(error), ephemeral=True)

def _event_message(event: dict[str, Any], player_labels: dict[int, str] | None=None) -> str:
    if event['type'] == 'awaiting_confirmation':
        return '\n\n⏳ Waiting for the opponent to confirm the submitted score. The fixture remains pending until both players agree.'
    if event['type'] == 'qualified':
        label = stage_label(str(event.get('stage') or 'quarterfinal'))
        mentions = ', '.join((player_labels.get(int(user_id), f'<@{int(user_id)}>') if player_labels else f'<@{int(user_id)}>') for user_id in event.get('qualifier_ids', []))
        return f'\nQualified for the **{label}**: ' + mentions + '.'
    if event['type'] == 'champion':
        mention = (player_labels.get(int(event['user_id'])) if player_labels and event.get('user_id') is not None else None) or (f"<@{int(event['user_id'])}>" if event.get('user_id') is not None else f"**{event['name']}**")
        return f'\n\n🏆 Champion: {mention}!'
    if event['type'] == 'stage':
        stage = str(event.get('stage'))
        label = stage_label(stage) if stage in STAGE_LABELS else event.get('name', 'Next stage')
        mentions = ', '.join((player_labels.get(int(user_id), f'<@{int(user_id)}>') if player_labels else f'<@{int(user_id)}>') for user_id in event.get('qualifier_ids', []))
        return f'\n\nQualified for the **{label}**: {mentions}.'
    return f"\n\nThe **{event['name']}** fixtures are now available."

@bot.tree.command(name='standings', description='Show live standings.')
@tournament_channel_only('participant', 'group', 'organizer', 'playoffs', 'standings_channel', 'playoffs_standings_channel')
@app_commands.describe(group="For group tournaments, show one group's table")
@app_commands.choices(group=[app_commands.Choice(name=f'Group {group}', value=group) for group in GROUP_LETTERS])
@app_commands.describe(tournament_id="Optional tournament ID; defaults to this channel's tournament")
@participant_only()
@app_commands.guild_only()
async def standings(interaction: discord.Interaction, group: app_commands.Choice[str] | None=None, tournament_id: int | None=None) -> None:
    guild = require_guild(interaction)
    tournament = await _call_off_loop(resolve_tournament_for_interaction, guild, interaction, ((OPEN, CLOSED, 'completed'),), tournament_id)
    if not tournament:
        await respond(interaction, 'There is no current tournament.', ephemeral=True)
        return
    if tournament['status'] == OPEN:
        await respond(interaction, 'Standings will be available after registration closes.', ephemeral=True)
        return
    if tournament['tournament_type'] == GROUP_KNOCKOUT and (not group):
        sections = []
        for group_name in (await _store_call(bot.store.knockout_shape_for, int(tournament['id'])))['groups']:
            rows = await _store_call(bot.store.standings, int(tournament['id']), group_name)
            sections.append(f'**Group {group_name}**\n' + format_standings_table(rows))
        description = '\n\n'.join(sections)
    else:
        rows = await _store_call(bot.store.standings, int(tournament['id']), group.value if group else None)
        description = format_standings_table(rows) if rows else 'No standings yet.'
    embed = discord.Embed(title=f"{tournament['name']} — Standings", description=description)
    embed.set_footer(text='Points: win 3 · draw 1 · loss 0')
    await respond(interaction, '', embed=embed)

@bot.tree.command(name='ballon_dor_ranking', description="Show the automatic monthly Ballon d'Or ranking.")
@app_commands.describe(month="Competition month in YYYY-MM format, e.g. 2026-09")
@app_commands.guild_only()
async def ballon_dor_ranking(interaction: discord.Interaction, month: str) -> None:
    if interaction.guild is None:
        await interaction.response.send_message('❌ This command can only be used in a server.', ephemeral=True)
        return
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Only server Administrators can view the Ballon d'Or ranking.", ephemeral=True)
        return
    month = month.strip()
    if not re.fullmatch(r'\d{4}-\d{2}', month):
        await interaction.response.send_message('❌ Month must use **YYYY-MM** format, for example `2026-09`.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        rows = await _store_call(bot.store.ballon_dor_candidates_for_guild, interaction.guild.id, month)
        period = _golden_boot_month_label(month)
        embed = discord.Embed(
            title=f"🏆 Ballon d'Or Ranking — {period}",
            description=_format_ballon_dor_ranking(rows),
        )
        embed.set_footer(text="Competitive tournaments only · KO Match excluded · No assists required")
        await interaction.followup.send(embed=embed, ephemeral=True)
    except ValueError as exc:
        await interaction.followup.send(f'❌ **Ranking unavailable.**\n\n{exc}', ephemeral=True)
    except Exception:
        logger.exception("Failed to calculate Ballon d'Or ranking for %s", month)
        await interaction.followup.send("❌ Failed to calculate the Ballon d'Or ranking. Check the bot logs.", ephemeral=True)


@bot.tree.command(name='ballon_dor_award', description="Award the monthly Ballon d'Or title role.")
@app_commands.describe(month="Competition month in YYYY-MM format, e.g. 2026-09", player="Optional winner override from the monthly ranking", photo="Optional award photo/banner to display in Hall of Fame #awards")
@app_commands.guild_only()
async def ballon_dor_award(interaction: discord.Interaction, month: str, player: discord.Member | None = None, photo: discord.Attachment | None = None) -> None:
    if interaction.guild is None:
        await interaction.response.send_message('❌ This command can only be used in a server.', ephemeral=True)
        return
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Only server Administrators can award the Ballon d'Or.", ephemeral=True)
        return
    month = month.strip()
    if not re.fullmatch(r'\d{4}-\d{2}', month):
        await interaction.response.send_message('❌ Month must use **YYYY-MM** format, for example `2026-09`.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        winner, role, row = await _award_monthly_ballon_dor(interaction.guild, month, player)
        period = _golden_boot_month_label(month)
        await interaction.followup.send(
            f"✅ **Ballon d'Or awarded for {period}.**\n\n"
            f"Winner: {winner.mention} — **{int(row['score'])} points** (Rank #{int(row['rank'])})\n"
            f"Title: **{role.name}**", ephemeral=True
        )
        await _announce_hall_of_fame_award(
            interaction.guild,
            award_type=f"Monthly Ballon d'Or — {period}",
            winner=winner.mention,
            title=role.name,
            details=f"Automatic ranking score: **{int(row['score'])} points** (Rank #{int(row['rank'])}).",
            photo=photo,
        )
    except ValueError as exc:
        await interaction.followup.send(f"❌ **Ballon d'Or not awarded.**\n\n{exc}", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("❌ I could not manage the Ballon d'Or role. Give the bot **Manage Roles** permission and place its role above the Ballon d'Or roles.", ephemeral=True)
    except Exception:
        logger.exception("Failed to award Ballon d'Or for %s", month)
        await interaction.followup.send("❌ Failed to award the Ballon d'Or. Check the bot logs and role permissions.", ephemeral=True)


@bot.tree.command(name='hall_of_fame_announce', description='Announce a tournament or award winner in Hall of Fame #awards.')
@app_commands.describe(award='Award or tournament name', winner='Winner to announce', title='Optional title/role name', details='Optional award details', photo='Optional award photo/banner')
@app_commands.guild_only()
async def hall_of_fame_announce(interaction: discord.Interaction, award: str, winner: discord.Member, title: str | None = None, details: str | None = None, photo: discord.Attachment | None = None) -> None:
    if interaction.guild is None:
        await interaction.response.send_message('❌ This command can only be used in a server.', ephemeral=True)
        return
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message('❌ Only server Administrators can make Hall of Fame announcements.', ephemeral=True)
        return
    if photo is not None and not (photo.content_type or '').lower().startswith('image/'):
        await interaction.response.send_message('❌ The photo must be an image attachment.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await _announce_hall_of_fame_award(interaction.guild, award_type=award.strip(), winner=winner.mention, title=title.strip() if title else None, details=details.strip() if details else None, photo=photo)
    await interaction.followup.send(f'✅ Hall of Fame announcement posted in **#{AWARDS_CHANNEL_NAME}**.', ephemeral=True)


@bot.tree.command(name='golden_boot_award', description='Award a monthly Golden Boot to the highest-scoring competitive player.')
@app_commands.describe(month="Competition month in YYYY-MM format, e.g. 2026-09", scope='PL, World Cup, Champions League, or all competitive tournaments', player='Optional tie-break selection; must be tied for the highest total', photo='Optional award photo/banner to display in Hall of Fame #awards')
@app_commands.choices(scope=[
    app_commands.Choice(name='Premier League (PL)', value='pl'),
    app_commands.Choice(name='World Cup (WC)', value='wc'),
    app_commands.Choice(name='Champions League (CL)', value='cl'),
    app_commands.Choice(name='Combined (PL + WC + CL + Weekly Championship)', value='combined'),
])
@app_commands.guild_only()
async def golden_boot_award(interaction: discord.Interaction, month: str, scope: app_commands.Choice[str], player: discord.Member | None = None, photo: discord.Attachment | None = None) -> None:
    if interaction.guild is None:
        await interaction.response.send_message('❌ This command can only be used in a server.', ephemeral=True)
        return
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message('❌ Only server Administrators can award the Golden Boot.', ephemeral=True)
        return
    month = month.strip()
    if not re.fullmatch(r'\d{4}-\d{2}', month):
        await interaction.response.send_message('❌ Month must use **YYYY-MM** format, for example `2026-09`.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        winner, role, goals = await _award_monthly_golden_boot(interaction.guild, month, scope.value, player)
        category = GOLDEN_BOOT_SCOPE_NAMES[scope.value]
        period = _golden_boot_month_label(month)
        if scope.value == 'combined':
            description = (
                f'Combined competitive Golden Boot for **{period}** (PL + WC + CL + Weekly Championship).\n\n'
                f'Winner: {winner.mention} — **{goals} goals**\nTitle: **{role.name}**'
            )
        else:
            description = (
                f'**{category} Golden Boot — {period}**\n\n'
                f'Winner: {winner.mention} — **{goals} goals**\nTitle: **{role.name}**'
            )
        await interaction.followup.send(f'✅ Golden Boot awarded.\n\n{description}', ephemeral=True)
        await _announce_hall_of_fame_award(
            interaction.guild,
            award_type=f"{category} Golden Boot — {period}",
            winner=winner.mention,
            title=role.name,
            details=f"Goals: **{goals}**.",
        )
    except ValueError as exc:
        await interaction.followup.send(f'❌ **Golden Boot not awarded.**\n\n{exc}', ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send('❌ I could not manage the Golden Boot role. Give the bot **Manage Roles** permission and place its role above the Golden Boot roles.', ephemeral=True)
    except Exception:
        logger.exception('Failed to award monthly Golden Boot for %s/%s', month, scope.value)
        await interaction.followup.send('❌ Failed to award the Golden Boot. Check the bot logs and role permissions.', ephemeral=True)

async def goal_records_tournament_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    """Offer completed competitive tournaments for /goal_records."""
    if not interaction.guild:
        return []
    try:
        tournaments = await _store_call(bot.store.list_tournaments, interaction.guild.id, ('completed',))
    except Exception:
        logger.exception('Failed to load tournaments for goal-record autocomplete')
        return []

    query = (current or '').strip().casefold()
    choices: list[app_commands.Choice[int]] = []
    for tournament in tournaments:
        if tournament.get('tournament_type') == ROUND_ROBIN:
            continue
        name = str(tournament.get('name') or f"Tournament {tournament.get('id')}")
        tournament_id = int(tournament['id'])
        type_name = tournament_type_label(tournament)
        searchable = f"{tournament_id} {name} {type_name}".casefold()
        if query and query not in searchable:
            continue
        label = f"ID {tournament_id} · {name} · {type_name}"
        choices.append(app_commands.Choice(name=label[:100], value=tournament_id))
        if len(choices) >= 25:
            break
    return choices

@bot.tree.command(name='golden_boot_standings', description='Show the monthly Golden Boot standings.')
@app_commands.describe(month="Competition month in YYYY-MM format, e.g. 2026-09", scope='PL, World Cup, Champions League, or all competitive tournaments')
@app_commands.choices(scope=[
    app_commands.Choice(name='Premier League (PL)', value='pl'),
    app_commands.Choice(name='World Cup (WC)', value='wc'),
    app_commands.Choice(name='Champions League (CL)', value='cl'),
    app_commands.Choice(name='Combined (PL + WC + CL + Weekly Championship)', value='combined'),
])
@app_commands.guild_only()
async def golden_boot_standings(interaction: discord.Interaction, month: str, scope: app_commands.Choice[str]) -> None:
    """Display the automatic monthly Golden Boot goal ranking.

    This is a read-only standings command and is available to all server
    members. It is intentionally not limited to tournament channels.
    """
    if interaction.guild is None:
        await interaction.response.send_message('❌ This command can only be used in a server.', ephemeral=True)
        return
    month = month.strip()
    if not re.fullmatch(r'\d{4}-\d{2}', month):
        await interaction.response.send_message('❌ Month must use **YYYY-MM** format, for example `2026-09`.', ephemeral=True)
        return
    await interaction.response.defer()
    try:
        rows = await _store_call(bot.store.golden_boot_candidates_for_guild, interaction.guild.id, month, scope.value)
        period = _golden_boot_month_label(month)
        category = GOLDEN_BOOT_SCOPE_NAMES[scope.value]
        lines = []
        for rank, row in enumerate(rows[:20], 1):
            label = str(row.get('nation_name') or row.get('display_name') or f"Player {row['user_id']}")
            goals = int(row.get('goals') or 0)
            tournaments_count = int(row.get('tournaments_count') or 0)
            lines.append(
                f"**#{rank}** {label} — **{goals}** goal{'' if goals == 1 else 's'} "
                f"· {tournaments_count} tournament{'' if tournaments_count == 1 else 's'}"
            )
        description = '\n'.join(lines) or 'No qualifying competitive players were found for this month.'
        embed = discord.Embed(
            title=f"🥇 Golden Boot Standings — {period}",
            description=description,
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"{category} · Official completed results only · KO Match excluded")
        await interaction.followup.send(embed=embed)
    except ValueError as exc:
        await interaction.followup.send(f'❌ **Standings unavailable.**\n\n{exc}', ephemeral=True)
    except Exception:
        logger.exception('Failed to calculate Golden Boot standings for %s/%s', month, scope.value)
        await interaction.followup.send('❌ Failed to calculate the Golden Boot standings. Check the bot logs.', ephemeral=True)


@bot.tree.command(name='goal_records', description='Show goals scored by players in a competitive tournament.')
@tournament_channel_only('participant', 'group', 'organizer', 'playoffs', 'standings_channel', 'playoffs_standings_channel', 'chat')
@app_commands.describe(tournament_id="Optional tournament ID; defaults to this channel's tournament")
@participant_only()
@app_commands.guild_only()
async def goal_records(interaction: discord.Interaction, tournament_id: int | None=None) -> None:
    """Show the tournament's official goal-scoring record.

    KO Match is intentionally excluded because it is a practice/fun format.
    World Cup nation names are displayed automatically through the stored
    tournament identity, while normal tournaments use the player's name.
    """
    guild = require_guild(interaction)
    tournament = await _call_off_loop(
        resolve_tournament_for_interaction,
        guild,
        interaction,
        ((OPEN, CLOSED, 'completed'),),
        tournament_id,
    )
    if not tournament:
        await respond(interaction, 'There is no tournament available.', ephemeral=True)
        return
    if tournament['tournament_type'] == ROUND_ROBIN:
        await respond(
            interaction,
            '⚽ Goal records are not kept for **KO Match** tournaments because they are practice/fun tournaments.',
            ephemeral=True,
        )
        return
    if tournament['status'] == OPEN:
        await respond(interaction, 'Goal records will be available after registration closes and results are recorded.', ephemeral=True)
        return

    rows = await _store_call(bot.store.goal_records_for_tournament, int(tournament['id']))
    if not rows:
        await respond(interaction, f'⚽ No completed goals have been recorded yet for **{tournament["name"]}**.', ephemeral=True)
        return

    lines = []
    for rank, row in enumerate(rows, 1):
        label = str(row.get('nation_name') or row.get('display_name') or f"Player {row['user_id']}")
        lines.append(f"**#{rank}** {label} — **{int(row['goals'])}** goal{'' if int(row['goals']) == 1 else 's'}")
    embed = discord.Embed(
        title=f"⚽ {tournament['name']} — Goal Records",
        description='\n'.join(lines),
        color=discord.Color.green(),
    )
    embed.set_footer(text='Official completed results only · KO Match excluded')
    await respond(interaction, '', embed=embed)

@bot.tree.command(name='tournament_status', description='Show the current tournament status.')
@tournament_channel_only('registration', 'participant', 'group', 'organizer', 'playoffs', 'chat')
@app_commands.describe(tournament_id="Optional tournament ID; defaults to this channel's tournament")
@app_commands.guild_only()
async def tournament_status(interaction: discord.Interaction, tournament_id: int | None=None) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message('Loading tournament status…')
    except discord.NotFound:
        logger.warning('Interaction %s expired before tournament_status could be acknowledged.', interaction.id)
        return
    guild = require_guild(interaction)
    tournament = await _call_off_loop(resolve_tournament_for_interaction, guild, interaction, ((OPEN,), (CLOSED, 'completed'), (DRAFT,)), tournament_id)
    if not tournament:
        await respond(interaction, 'There is no current tournament.', ephemeral=True)
        return
    tournament_id = int(tournament['id'])
    players = await _store_call(bot.store.players, tournament_id)
    player_count = len(players)
    status = str(tournament['status']).title()
    type_name = tournament_type_label(tournament)
    if tournament['tournament_type'] == GROUP_KNOCKOUT:
        participant_sections = []
        has_group_assignments = any((p.get('group_name') for p in players))
        if has_group_assignments:
            for group_name in (await _store_call(bot.store.knockout_shape_for, tournament_id))['groups']:
                group_players = [p for p in players if p.get('group_name') == group_name]
                if not group_players:
                    continue
                mentions = ' '.join((f"<@{int(p['user_id'])}>" for p in group_players))
                participant_sections.append(f'**Group {group_name}:** {mentions}')
        else:
            mentions = ' '.join((f"<@{int(p['user_id'])}>" for p in players))
            if mentions:
                participant_sections.append(mentions)
        participants_text = '\n'.join(participant_sections) or 'No participants yet.'
    else:
        participants_text = ' '.join((f"<@{int(p['user_id'])}>" for p in players)) or 'No participants yet.'
    description = f"**Status:** {status}\n**Format:** {type_name}\n**Players:** {player_count}/{tournament['player_limit']}\n"
    if tournament['tournament_type'] == GROUP_KNOCKOUT and tournament.get('group_stage_deadline_at'):
        description += f"**Group-stage deadline:** {_to_discord_timestamp(tournament['group_stage_deadline_at'])}\n"
    description += f'\n**Participants:**\n{participants_text}'
    embed = discord.Embed(title=tournament['name'], description=description, color=discord.Color.blurple())
    try:
        await interaction.edit_original_response(content='', embed=embed)
    except discord.NotFound:
        logger.warning('Interaction %s response token expired before tournament_status update.', interaction.id)

@bot.tree.command(name='tournament_help', description='Show participant command help.')
@tournament_channel_only('participant', 'group', 'organizer', 'playoffs', 'commands_help')
@app_commands.guild_only()
async def tournament_help(interaction: discord.Interaction) -> None:
    embed = discord.Embed(title='eFootball Tournament Help', description='#tournament-registration: `/register`, `/unregister`, `/tournament_status`.\n#result-submission: `/report`.\n#tournament-fixture: `/matches`.\n#tournament-chat and your Group chat: participant commands such as `/players`, `/matches`, `/standings`, `/tournament_status`, and `/tournament_help`.\n\n`/report` requires both players to confirm the exact same score before the match is completed.', color=discord.Color.blurple())
    await respond(interaction, '', embed=embed, ephemeral=True)

@bot.tree.command(name='clear', description='Delete recent messages in this channel.')
@tournament_channel_only('registration', 'result', 'fixture', 'organizer', 'participant', 'group', 'playoffs', 'playoffs_fixtures', 'playoffs_standings_channel', 'rules', 'match_rules', 'announcements', 'standings_channel', 'support', 'commands_help')
@app_commands.describe(amount='Number of recent messages to delete (1-100)')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def clear(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]) -> None:
    if not isinstance(interaction.channel, discord.TextChannel):
        await respond(interaction, 'This command can only be used in a text channel.', ephemeral=True)
        return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        deleted = await interaction.channel.purge(limit=int(amount), bulk=True, reason=f'/clear by {interaction.user}')
        await interaction.followup.send(f'🧹 Deleted **{len(deleted)}** message(s).', ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send('I need **Manage Messages** and **Read Message History** to clear messages.', ephemeral=True)
    except discord.HTTPException as error:
        await interaction.followup.send(f'Could not clear messages: {error}', ephemeral=True)

@bot.tree.command(name='tournament_help_admin', description='Show moderator command help.')
@tournament_channel_only('organizer')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def tournament_help_admin(interaction: discord.Interaction) -> None:
    embed = discord.Embed(title='Moderator Tournament Help', description="`/setup_server` creates/reuses the tournament categories, channels, and roles. Safe to run multiple times.\n`/tournament_create` creates a draft.\n`/tournament_open` opens registration.\n`/tournament_close` closes registration and generates all first-stage fixtures.\n`/tournament_delete` deletes a tournament and all its players/matches.\n`/tournament_list` lists tournaments and their IDs.\n`/set_result` records or corrects any result — always takes priority over any pending confirmation or open dispute.\n`/post_pending_confirmations` posts Confirm/Dispute buttons for any result that's still awaiting the opponent's confirmation.\n`/send_message` sends a staff message to any server channel or privately to one current tournament participant.\n\nModerators need Discord's Manage Server permission. The bot needs Manage Roles, Send Messages, Embed Links, and Read Message History.", color=discord.Color.orange())
    await respond(interaction, '', embed=embed, ephemeral=True)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    original = error
    if isinstance(error, app_commands.CommandInvokeError):
        original = error.original
    if isinstance(original, app_commands.MissingPermissions):
        message = 'You need the Manage Server permission to use that command.'
    elif isinstance(original, app_commands.CheckFailure):
        message = str(original) or 'You do not have permission to use that command.'
    else:
        logger.exception('Unhandled slash command error', exc_info=original)
        message = 'Something went wrong while handling that command.'
    if interaction.response.is_done():
        try:
            await interaction.followup.send(message, ephemeral=True)
        except discord.NotFound:
            logger.warning('Could not send error response for interaction %s; token expired.', interaction.id)
    else:
        await respond(interaction, message, ephemeral=True)
if __name__ == '__main__':
    token = os.getenv(TOKEN_ENV)
    if not token:
        raise RuntimeError(f'{TOKEN_ENV} is not configured. Add it to your Railway service Variables before starting the bot.')
    bot.run(token, log_handler=None)