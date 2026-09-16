"""SQLite-backed tournament engine for the eFootball Mobile Discord bot.

This module deliberately has no Discord dependency.  The scheduling, validation,
and standings rules can therefore be tested independently from the bot.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import combinations
import random
import re
from typing import Any, Iterable, Sequence

logger = logging.getLogger("efootball-tournament")

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # SQLite-only tests can run without PostgreSQL dependencies.
    psycopg = None
    dict_row = None


ROUND_ROBIN = "round_robin"  # Historical name; this is actually a single-elimination
                              # "KO Match" bracket (see close_and_generate). Kept as-is —
                              # it's an intentional, independently-used tournament type.
GROUP_KNOCKOUT = "group_knockout"
LEAGUE = "league"  # A genuine all-play-all round robin with a points table and no
                    # knockout stage. Added for the Premier League template, which
                    # was previously (incorrectly) mapped onto ROUND_ROBIN/"KO Match".
OPEN = "open"
DRAFT = "draft"
CLOSED = "closed"
COMPLETED = "completed"
# Supports up to 16 groups (World Cup 48/64's largest shape). GROUPS is kept
# as the historical alias for the first 4 letters, since it's still used
# wherever exactly the original 4-group shape is intended (e.g. the Discord
# bot's per-tournament Group A-D chat channels/roles).
GROUP_LETTERS = tuple(chr(ord("A") + i) for i in range(16))
GROUPS = GROUP_LETTERS[:4]
KNOCKOUT_STAGES = ("quarterfinal", "semifinal", "final")

# --- Tournament templates ---------------------------------------------------
# A template is a named, fixed configuration on top of the three underlying
# engine types (LEAGUE / ROUND_ROBIN / GROUP_KNOCKOUT). Selecting a template
# pins the tournament_type and player_limit so they can no longer be set to
# an inconsistent combination.
#
# For GROUP_KNOCKOUT templates, group_count/group_sizes/qualify_per_group/
# knockout_stages fully describe the shape (see _group_knockout_shape_for);
# the engine is generic over these, so any group count (even/uneven sizes)
# and any knockout depth works as long as qualify_per_group == 2 (the
# cross-group first-knockout-round pairing in _build_first_knockout_matchups
# assumes exactly 2 qualifiers per group and an even group count — true for
# every template below).
TEMPLATE_PREMIER_LEAGUE = "premier_league"
TEMPLATE_WEEKLY_CHAMPIONSHIP = "weekly_championship"
TEMPLATE_CHAMPIONS_LEAGUE = "champions_league"
TEMPLATE_WORLD_CUP_32 = "world_cup_32"
TEMPLATE_WORLD_CUP_48 = "world_cup_48"
TEMPLATE_WORLD_CUP_64 = "world_cup_64"

# National-team pool used by World Cup templates. Nations are assigned once
# registration closes and remain fixed for the lifetime of that tournament.
WORLD_CUP_NATIONS_48 = (
    "Argentina", "Australia", "Austria", "Belgium", "Brazil", "Canada",
    "Chile", "China", "Colombia", "Croatia", "Denmark", "Ecuador",
    "Egypt", "England", "France", "Germany", "Ghana", "Greece",
    "Iran", "Italy", "Ivory Coast", "Japan", "Mexico", "Morocco",
    "Netherlands", "New Zealand", "Nigeria", "Norway", "Panama",
    "Paraguay", "Peru", "Poland", "Portugal", "Qatar", "Saudi Arabia",
    "Scotland", "Senegal", "Serbia", "South Africa", "South Korea",
    "Spain", "Sweden", "Switzerland", "Tunisia", "Türkiye", "Ukraine",
    "Uruguay", "USA",
)
WORLD_CUP_NATIONS_64 = WORLD_CUP_NATIONS_48 + (
    "Algeria", "Cameroon", "Costa Rica", "Czech Republic", "Hungary",
    "Jamaica", "Mali", "Romania", "Slovakia", "Slovenia", "Venezuela",
    "Wales", "Finland", "Iceland", "Northern Ireland", "Bolivia",
)
WORLD_CUP_TEMPLATE_IDS = {TEMPLATE_WORLD_CUP_32, TEMPLATE_WORLD_CUP_48, TEMPLATE_WORLD_CUP_64}

# Competitive title-role mapping.  KO Match is deliberately absent because
# it is a practice/fun tournament and does not award competitive titles.
CHAMPION_TITLE_KEYS = {
    TEMPLATE_WEEKLY_CHAMPIONSHIP: "weekly_championship_winner",
    TEMPLATE_PREMIER_LEAGUE: "premier_league_champion",
    TEMPLATE_CHAMPIONS_LEAGUE: "champions_league_champion",
    TEMPLATE_WORLD_CUP_32: "world_cup_champion",
    TEMPLATE_WORLD_CUP_48: "world_cup_champion",
    TEMPLATE_WORLD_CUP_64: "world_cup_champion",
}


TEMPLATES: dict[str, dict[str, Any]] = {
    TEMPLATE_PREMIER_LEAGUE: {
        "display_name": "Premier League",
        "tournament_type": LEAGUE,
        "player_limit": 20,
        "description": "20 players, single round-robin (everyone plays everyone once), no knockout stage.",
    },
    TEMPLATE_WEEKLY_CHAMPIONSHIP: {
        "display_name": "Weekly Championship",
        "tournament_type": GROUP_KNOCKOUT,
        "player_limit": 16,
        "description": "16 players, 4 groups of 4 (single round-robin), top 2 per group advance to quarterfinal/semifinal/final.",
        "group_count": 4,
        "group_sizes": [4, 4, 4, 4],
        "qualify_per_group": 2,
        "knockout_stages": ("quarterfinal", "semifinal", "final"),
    },
    TEMPLATE_CHAMPIONS_LEAGUE: {
        "display_name": "Champions League",
        "tournament_type": GROUP_KNOCKOUT,
        "player_limit": 32,
        "description": "32 players, 8 groups of 4 (single round-robin), top 2 per group advance to Round of 16/quarterfinal/semifinal/final.",
        "group_count": 8,
        "group_sizes": [4] * 8,
        "qualify_per_group": 2,
        "knockout_stages": ("round_of_16", "quarterfinal", "semifinal", "final"),
    },
    TEMPLATE_WORLD_CUP_32: {
        "display_name": "World Cup (32 Players)",
        "tournament_type": GROUP_KNOCKOUT,
        "player_limit": 32,
        "description": "32 players, 8 groups of 4 (single round-robin), top 2 per group advance to Round of 16/quarterfinal/semifinal/final.",
        "group_count": 8,
        "group_sizes": [4] * 8,
        "qualify_per_group": 2,
        "knockout_stages": ("round_of_16", "quarterfinal", "semifinal", "final"),
    },
    TEMPLATE_WORLD_CUP_48: {
        "display_name": "World Cup (48 Players)",
        "tournament_type": GROUP_KNOCKOUT,
        "player_limit": 48,
        "description": "48 players, 16 groups of 3 (single round-robin), top 2 per group advance to Round of 32/16/quarterfinal/semifinal/final.",
        "group_count": 16,
        "group_sizes": [3] * 16,
        "qualify_per_group": 2,
        "knockout_stages": ("round_of_32", "round_of_16", "quarterfinal", "semifinal", "final"),
    },
    TEMPLATE_WORLD_CUP_64: {
        "display_name": "World Cup (64 Players)",
        "tournament_type": GROUP_KNOCKOUT,
        "player_limit": 64,
        "description": "64 players, 16 groups of 4 (single round-robin), top 2 per group advance to Round of 32/16/quarterfinal/semifinal/final.",
        "group_count": 16,
        "group_sizes": [4] * 16,
        "qualify_per_group": 2,
        "knockout_stages": ("round_of_32", "round_of_16", "quarterfinal", "semifinal", "final"),
    },
}
# Group-stage matches must be completed within this many hours of fixtures
# being generated. Stored on the tournament row so the deadline survives a
# bot restart and can be displayed/used for future reminder features.
GROUP_STAGE_DEADLINE_HOURS = 48
LEAGUE_DEADLINE_HOURS = 7 * 24  # A full round-robin has far more matches to fit in.
ROUND_OF_32_DEADLINE_HOURS = 24
ROUND_OF_16_DEADLINE_HOURS = 24
QUARTERFINAL_DEADLINE_HOURS = 24
SEMIFINAL_DEADLINE_HOURS = 24
FINAL_DEADLINE_HOURS = 12


class TournamentError(Exception):
    """A safe, user-facing tournament validation error."""


@dataclass(frozen=True)
class Standing:
    user_id: int
    display_name: str
    played: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int

    @property
    def points(self) -> int:
        return self.wins * 3 + self.draws

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against


class _ConnectionAdapter:
    """Keep repository queries portable between SQLite and PostgreSQL.

    PostgreSQL connections can be dropped by the provider/network while the
    process remains alive. Connection-loss recovery is centralized here.
    Reads may be replayed once after reconnect; writes are never blindly
    replayed because the server may already have accepted them.
    """

    def __init__(self, connection: Any, backend: str, database_url: str | None = None) -> None:
        self._connection = connection
        self.backend = backend
        self._database_url = database_url

    @staticmethod
    def _is_connection_loss(error: BaseException) -> bool:
        if psycopg is None:
            return False
        operational_error = getattr(psycopg, "OperationalError", None)
        if operational_error is None or not isinstance(error, operational_error):
            return False
        message = str(error).casefold()
        markers = (
            "ssl connection has been closed unexpectedly",
            "connection is closed",
            "connection closed",
            "server closed the connection unexpectedly",
            "connection not open",
            "could not receive data from server",
            "could not send data to server",
            "connection reset by peer",
            "broken pipe",
        )
        return any(marker in message for marker in markers)

    @staticmethod
    def _is_read_query(query: str) -> bool:
        statement = query.lstrip().casefold()
        # Do not classify WITH as read: a CTE may contain a write.
        return statement.startswith("select") or statement.startswith("values")

    def _reconnect(self) -> None:
        if self.backend != "postgres" or not self._database_url or psycopg is None or dict_row is None:
            raise RuntimeError("PostgreSQL connection cannot be recovered.")
        try:
            try:
                self._connection.rollback()
            except Exception:
                pass
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = psycopg.connect(
                self._database_url,
                autocommit=True,
                row_factory=dict_row,
                connect_timeout=10,
            )
            logger.info("PostgreSQL connection recovered successfully.")
        except Exception:
            logger.exception("PostgreSQL reconnection failed.")
            raise

    def execute(self, query: str, parameters: tuple[Any, ...] = ()) -> Any:
        if self.backend != "postgres":
            return self._connection.execute(query, parameters)
        db_query = query.replace("?", "%s")
        try:
            return self._connection.execute(db_query, parameters)
        except Exception as error:
            if not self._is_connection_loss(error):
                raise
            if self._is_read_query(db_query):
                logger.warning(
                    "PostgreSQL connection lost; reconnecting and retrying read operation."
                )
            else:
                logger.warning(
                    "PostgreSQL connection lost during a write; reconnecting without replaying the operation."
                )
            self._reconnect()
            if not self._is_read_query(db_query):
                # The connection is recovered for subsequent operations, but
                # the failed write is deliberately not replayed.
                raise
            try:
                return self._connection.execute(db_query, parameters)
            except Exception:
                raise

    def commit(self) -> None:
        """Commit for SQLite; PostgreSQL connections use autocommit."""
        if self.backend != "postgres":
            self._connection.commit()

    def execute_read(self, query: str, parameters: tuple[Any, ...] = (), *, fetch: str = "one") -> Any:
        """Execute and consume a read, retrying once if the connection dies.

        psycopg may raise a connection-loss OperationalError while consuming
        cursor input (e.g. ``fetchone``), not during ``connection.execute``.
        Keeping execute+fetch together here closes that recovery gap.
        """
        if self.backend != "postgres":
            cursor = self._connection.execute(query, parameters)
            return cursor.fetchone() if fetch == "one" else cursor.fetchall()
        db_query = query.replace("?", "%s")
        if not self._is_read_query(db_query):
            raise ValueError("execute_read() only accepts read queries")
        for attempt in range(2):
            try:
                cursor = self._connection.execute(db_query, parameters)
                return cursor.fetchone() if fetch == "one" else cursor.fetchall()
            except Exception as error:
                if attempt == 1 or not self._is_connection_loss(error):
                    raise
                logger.warning(
                    "PostgreSQL connection lost; reconnecting and retrying read operation."
                )
                self._reconnect()
        raise RuntimeError("Unreachable connection retry state.")

    def executescript(self, query: str) -> Any:
        if self.backend != "sqlite":
            raise RuntimeError("executescript is only available for SQLite.")
        return self._connection.executescript(query)

    def close(self) -> None:
        self._connection.close()


def generate_round_robin_fixtures(
    player_ids: Sequence[int],
) -> list[tuple[int, int]]:
    """Return one fixture for every pair, with no duplicate pair."""
    return list(combinations(player_ids, 2))


def generate_knockout_fixtures(
    player_ids: Sequence[int],
) -> list[tuple[int, int]]:
    """Create the first round of a single-elimination bracket.

    Players are randomized before pairing. If the player count is not a power
    of two, byes are represented by simply not creating a match for the lone
    player in that slot; that player advances to the next round automatically.
    """
    players = list(player_ids)
    random.shuffle(players)
    if len(players) < 2:
        return []
    return [(players[i], players[i + 1]) for i in range(0, len(players) - 1, 2)]


def calculate_standings(
    players: Iterable[tuple[int, str] | dict[str, Any]],
    matches: Iterable[dict[str, Any]],
) -> list[Standing]:
    """Calculate standings from completed matches in fixture orientation."""
    table: dict[int, dict[str, Any]] = {}
    for player in players:
        if isinstance(player, dict):
            user_id = int(player["user_id"])
            display_name = str(player.get("nation_name") or player["display_name"])
        else:
            user_id, display_name = player
        table[user_id] = {
            "user_id": user_id,
            "display_name": display_name,
            "played": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "goals_for": 0,
            "goals_against": 0,
        }

    for match in matches:
        if match.get("status") != "completed":
            continue
        player1 = int(match["player1_id"])
        player2 = int(match["player2_id"])
        if player1 not in table or player2 not in table:
            continue
        score1 = int(match["score1"])
        score2 = int(match["score2"])
        first, second = table[player1], table[player2]
        first["played"] += 1
        second["played"] += 1
        first["goals_for"] += score1
        first["goals_against"] += score2
        second["goals_for"] += score2
        second["goals_against"] += score1
        if score1 > score2:
            first["wins"] += 1
            second["losses"] += 1
        elif score2 > score1:
            second["wins"] += 1
            first["losses"] += 1
        else:
            first["draws"] += 1
            second["draws"] += 1

    standings = [Standing(**values) for values in table.values()]
    return sorted(
        standings,
        key=lambda row: (
            -row.points,
            -row.goal_difference,
            -row.goals_for,
            -row.wins,
            row.display_name.casefold(),
            row.user_id,
        ),
    )


class TournamentStore:
    """Serialized tournament repository and state machine.

    PostgreSQL is the production backend. A SQLite path remains supported only
    for the existing tests and the one-way migration utility.
    """

    def __init__(
        self,
        path: str = "tournament.db",
        *,
        database_url: str | None = None,
    ) -> None:
        configured_url = (database_url or "").strip() or (
            path.strip() if isinstance(path, str) and path.startswith(("postgres://", "postgresql://")) else None
        )
        if configured_url:
            if psycopg is None or dict_row is None:
                raise RuntimeError(
                    "PostgreSQL support requires psycopg. Install requirements.txt."
                )
            self.backend = "postgres"
            try:
                raw_connection = psycopg.connect(
                    configured_url,
                    autocommit=True,
                    row_factory=dict_row,
                    connect_timeout=10,
                )
            except Exception as error:
                raise RuntimeError(
                    "Could not connect to PostgreSQL from DATABASE_URL. "
                    "Check the Railway PostgreSQL service and DATABASE_URL variable."
                ) from error
        else:
            self.backend = "sqlite"
            raw_connection = sqlite3.connect(
                path, check_same_thread=False, isolation_level=None
            )
            raw_connection.row_factory = sqlite3.Row
            raw_connection.execute("PRAGMA foreign_keys = ON")
            raw_connection.execute("PRAGMA journal_mode = WAL")
            raw_connection.execute("PRAGMA busy_timeout = 5000")
        self.connection = _ConnectionAdapter(raw_connection, self.backend, configured_url)
        self._lock = threading.RLock()
        self._create_schema()

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def _create_schema(self) -> None:
        with self._lock:
            id_type = (
                "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"
                if self.backend == "postgres"
                else "INTEGER PRIMARY KEY AUTOINCREMENT"
            )
            schema = f"""
                CREATE TABLE IF NOT EXISTS tournaments (
                    id {id_type},
                    guild_id BIGINT NOT NULL,
                    name TEXT NOT NULL,
                    tournament_type TEXT NOT NULL CHECK (
                        tournament_type IN ('round_robin', 'group_knockout', 'league')
                    ),
                    player_limit INTEGER NOT NULL CHECK (player_limit > 1),
                    status TEXT NOT NULL DEFAULT 'draft' CHECK (
                        status IN ('draft', 'open', 'closed', 'completed')
                    ),
                    created_by BIGINT NOT NULL,
                    created_at TEXT NOT NULL,
                    registration_closed_at TEXT,
                    group_stage_deadline_at TEXT,
                    league_deadline_at TEXT,
                    round_of_32_deadline_at TEXT,
                    round_of_16_deadline_at TEXT,
                    quarterfinal_deadline_at TEXT,
                    semifinal_deadline_at TEXT,
                    final_deadline_at TEXT,
                    template_id TEXT
                );

                CREATE TABLE IF NOT EXISTS tournament_players (
                    tournament_id BIGINT NOT NULL REFERENCES tournaments(id)
                        ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    display_name TEXT NOT NULL,
                    nation_name TEXT,
                    group_name TEXT,
                    joined_at TEXT NOT NULL,
                    PRIMARY KEY (tournament_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS matches (
                    id {id_type},
                    tournament_id BIGINT NOT NULL REFERENCES tournaments(id)
                        ON DELETE CASCADE,
                    stage TEXT NOT NULL,
                    group_name TEXT,
                    round_number INTEGER NOT NULL DEFAULT 1,
                    player1_id BIGINT NOT NULL,
                    player2_id BIGINT NOT NULL,
                    score1 INTEGER,
                    score2 INTEGER,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (
                        status IN ('pending', 'completed')
                    ),
                    reported_by BIGINT,
                    dispute_channel_id BIGINT,
                    disputed_at TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    schedule_status TEXT NOT NULL DEFAULT 'unscheduled' CHECK (schedule_status IN ('unscheduled', 'proposed', 'confirmed')),
                    schedule_proposer_id BIGINT,
                    schedule_proposed_at TEXT,
                    schedule_proposed_for TEXT,
                    schedule_confirmed_at TEXT,
                    schedule_prompted_player1_at TEXT,
                    schedule_prompted_player2_at TEXT,
                    schedule_message_id BIGINT,
                    CHECK (player1_id <> player2_id),
                    CHECK (score1 IS NULL OR score1 >= 0),
                    CHECK (score2 IS NULL OR score2 >= 0)
                );

            """
            if self.backend == "sqlite":
                self.connection.executescript(schema)
            else:
                self.connection.execute(schema)
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS champions_totals (
                    discord_user_id BIGINT PRIMARY KEY,
                    total_points INTEGER NOT NULL DEFAULT 0,
                    championships INTEGER NOT NULL DEFAULT 0,
                    runner_ups INTEGER NOT NULL DEFAULT 0,
                    semifinal_appearances INTEGER NOT NULL DEFAULT 0
                )
            """)
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS champion_awards (
                    tournament_id BIGINT NOT NULL,
                    discord_user_id BIGINT NOT NULL,
                    placement TEXT NOT NULL,
                    points INTEGER NOT NULL,
                    awarded_at TEXT NOT NULL,
                    PRIMARY KEY (tournament_id, discord_user_id, placement)
                )
            """)
            # Current competitive title holders are separate from the
            # historical Hall-of-Fame/Champions records.  Each guild has at
            # most one holder per title, so a new Weekly/PL/CL/World Cup
            # champion replaces the previous holder instead of accumulating
            # the title on multiple members.
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS champion_title_holders (
                    guild_id BIGINT NOT NULL,
                    title_key TEXT NOT NULL,
                    role_id BIGINT,
                    user_id BIGINT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, title_key)
                )
            """)
            # Competitive goal records.  KO Match (the historical
            # ``round_robin`` engine type) is intentionally excluded because
            # it is a practice/fun tournament and must never contribute goal
            # statistics.  Records are stored separately from match rows so
            # goal totals remain available as a dedicated tournament statistic.
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS goal_records (
                    tournament_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    display_name TEXT NOT NULL,
                    nation_name TEXT,
                    goals INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (tournament_id, user_id)
                )
            """)
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS ballon_dor_awards (
                    guild_id BIGINT NOT NULL,
                    month TEXT NOT NULL,
                    user_id BIGINT NOT NULL,
                    role_id BIGINT NOT NULL,
                    score INTEGER NOT NULL,
                    awarded_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, month)
                )
            """)
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS golden_boot_awards (
                    guild_id BIGINT NOT NULL,
                    award_key TEXT NOT NULL,
                    month TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    user_id BIGINT NOT NULL,
                    role_id BIGINT NOT NULL,
                    goals INTEGER NOT NULL,
                    tournament_ids TEXT NOT NULL DEFAULT '',
                    awarded_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, award_key)
                )
            """)
            # Per-tournament group-stage chat channel/role, one row per
            # group. Replaces the old fixed group_a_role_id..group_d_role_id
            # / group_a_channel_id..group_d_channel_id columns on
            # ``tournaments``, which only had schema support for exactly 4
            # groups and silently had nowhere to put groups E onward for
            # Champions League (8 groups) or World Cup 48/64 (16 groups).
            # Those old columns are left in place (unread by any new code
            # path) rather than dropped, so no destructive migration is
            # needed; ``_migrate_group_channel_columns`` below copies any
            # data already sitting in them into this table exactly once.
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS tournament_group_channels (
                    tournament_id BIGINT NOT NULL REFERENCES tournaments(id)
                        ON DELETE CASCADE,
                    group_name TEXT NOT NULL,
                    role_id BIGINT,
                    channel_id BIGINT,
                    PRIMARY KEY (tournament_id, group_name)
                )
            """)
            # A player can have only one placement in a completed tournament.
            # This is intentionally additive and does not alter existing results.
            try:
                duplicate_rows = self._all(
                    """
                    SELECT tournament_id, discord_user_id, COUNT(*) AS duplicate_count
                    FROM champion_awards
                    GROUP BY tournament_id, discord_user_id
                    HAVING COUNT(*) > 1
                    """
                )
                if duplicate_rows:
                    logger.warning(
                        "Champions legacy data contains %d conflicting "
                        "(tournament_id, discord_user_id) award group(s). "
                        "The unique index was NOT created; no records were deleted. "
                        "Manual resolution is required.",
                        len(duplicate_rows),
                    )
                else:
                    self.connection.execute(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS
                        ux_champion_awards_tournament_player
                        ON champion_awards(tournament_id, discord_user_id)
                        """
                    )
            except Exception:
                logger.exception(
                    "Unable to validate/create the Champions award uniqueness index. "
                    "No legacy Champions records were deleted."
                )

            # ``CREATE TABLE IF NOT EXISTS`` only creates missing tables; it
            # does not update tables that already exist.  Railway keeps the
            # PostgreSQL database across deploys, so an older database can
            # otherwise be missing columns added by a newer bot release.
            # Detect those columns on every startup and add only what is
            # missing.  The migration definitions below intentionally use
            # nullable columns or safe defaults so existing rows remain valid.
            self._migrate_missing_columns()
            self._migrate_column_types()
            self._migrate_tournament_type_check()
            self._migrate_group_channel_columns()
            self._create_indexes()
            self._backfill_goal_records()

    def _create_indexes(self) -> None:
        """Create indexes after migrations so older schemas are safe."""
        indexes = (
            """CREATE INDEX IF NOT EXISTS idx_tournaments_guild_status
               ON tournaments(guild_id, status)""",
            """CREATE INDEX IF NOT EXISTS idx_players_tournament
               ON tournament_players(tournament_id)""",
            """CREATE INDEX IF NOT EXISTS idx_matches_tournament_stage
               ON matches(tournament_id, stage, group_name)""",
            """CREATE INDEX IF NOT EXISTS idx_group_channels_tournament
               ON tournament_group_channels(tournament_id)""",
            """CREATE INDEX IF NOT EXISTS idx_goal_records_user
               ON goal_records(user_id)""",
            """CREATE INDEX IF NOT EXISTS idx_goal_records_tournament_goals
               ON goal_records(tournament_id, goals DESC)""",
        )
        for statement in indexes:
            self.connection.execute(statement)

    def _table_columns(self, table_name: str) -> set[str]:
        """Return the existing column names for a table."""
        if self.backend == "sqlite":
            rows = self.connection.execute(
                f"PRAGMA table_info({table_name})"
            ).fetchall()
            return {str(row[1]) for row in rows}

        rows = self._all(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = ?
            """,
            (table_name,),
        )
        return {str(row["column_name"]) for row in rows}

    def _migrate_missing_columns(self) -> None:
        """Add columns introduced by newer bot versions to existing tables.

        This is deliberately idempotent: every definition is checked against
        the live database before an ALTER TABLE is issued.  That means a
        redeploy is safe and only genuinely missing columns are added.
        """
        migrations: dict[str, dict[str, str]] = {
            "tournament_players": {
                "nation_name": "TEXT",
                "group_name": "TEXT",
            },
            "matches": {
                "schedule_status": "TEXT NOT NULL DEFAULT 'unscheduled'",
                "schedule_proposer_id": "BIGINT",
                "schedule_proposed_at": "TEXT",
                "schedule_proposed_for": "TEXT",
                "schedule_confirmed_at": "TEXT",
                "schedule_prompted_player1_at": "TEXT",
                "schedule_prompted_player2_at": "TEXT",
                "schedule_message_id": "BIGINT",
                "group_name": "TEXT",
                "round_number": "INTEGER DEFAULT 1",
                "score1": "INTEGER",
                "score2": "INTEGER",
                "status": "TEXT DEFAULT 'pending'",
                "reported_by": "BIGINT",
                "completed_at": "TEXT",
                "dispute_channel_id": "BIGINT",
                "disputed_at": "TEXT",
            },
            "tournaments": {
                "registration_closed_at": "TEXT",
                # When the group stage's 48-hour completion window ends.
                # Nullable/absent for round-robin (KO Match) tournaments and
                # for any tournament that hasn't reached the group stage yet.
                "group_stage_deadline_at": "TEXT",
                # This tournament's single completion-window deadline for
                # LEAGUE-type (e.g. Premier League) tournaments — one flat
                # round robin, so there's no separate knockout stage.
                "league_deadline_at": "TEXT",
                # Added when Champions League / World Cup templates
                # introduced knockout stages deeper than quarterfinal (up to
                # Round of 32 for the 48/64-player World Cup variants).
                "round_of_32_deadline_at": "TEXT",
                "round_of_16_deadline_at": "TEXT",
                "quarterfinal_deadline_at": "TEXT",
                "semifinal_deadline_at": "TEXT",
                "final_deadline_at": "TEXT",
                # Per-tournament Discord resources, so multiple tournaments
                # can run concurrently in the same guild without sharing
                # channels/roles. Populated the first time a tournament is
                # opened; nullable for tournaments created before this existed.
                "category_id": "BIGINT",
                "registration_channel_id": "BIGINT",
                "fixtures_channel_id": "BIGINT",
                "results_channel_id": "BIGINT",
                "participant_role_id": "BIGINT",
                # Which named template (if any) this tournament was created
                # from. Nullable/absent for tournaments created before the
                # template system existed, and for any tournament created
                # directly with a raw tournament_type/player_limit instead of
                # a template — those keep working exactly as before.
                "template_id": "TEXT",
                # This tournament's own isolated chat channel (unlike the
                # shared legacy #tournament-chat). Provisioned alongside the
                # registration/fixtures/results channels in
                # provision_tournament_resources(); nullable for tournaments
                # created before this existed.
                "tournament_chat_channel_id": "BIGINT",
                # This tournament's own isolated announcements channel,
                # separate from the permanent guild-wide #tournament-
                # announcements channel. Stage-deadline/registration/
                # fixtures-generated/qualification/playoff announcements for
                # THIS tournament are posted here so two tournaments running
                # concurrently never interleave their announcements in one
                # shared channel. Provisioned alongside the registration/
                # fixtures/results/chat channels in
                # provision_tournament_resources(); nullable for tournaments
                # created before this existed (those fall back to the
                # permanent shared channel until next provisioned).
                "announcements_channel_id": "BIGINT",
                # This tournament's own isolated standings/playoffs channels.
                # Previously these were resolved by looking up a single
                # shared #standings/#playoffs-fixtures/#playoffs-standings
                # channel by NAME, which silently collided whenever more
                # than one tournament was running at once. Storing IDs here
                # (mirroring registration/fixtures/results/chat above) lets
                # every tournament get its own, correctly isolated channels.
                # Nullable for tournaments created before this existed;
                # those are lazily backfilled the next time they're needed.
                "standings_channel_id": "BIGINT",
                "playoffs_fixtures_channel_id": "BIGINT",
                "playoffs_standings_channel_id": "BIGINT",
                "match_schedule_channel_id": "BIGINT",
                # Per-tournament group-stage chat channels/roles and
                # playoffs-chat channel/role. Previously these all reused
                # global, guild-wide roles ("Group A", "Playoff Qualified")
                # and a single shared #group-a-chat/#playoffs-chat channel
                # by name — so two group/knockout tournaments active at the
                # same time (one "closed"/in-progress while another opens)
                # would incorrectly share group and playoffs chat access
                # across tournaments. Nullable for tournaments created
                # before this existed; lazily backfilled when next needed.
                "group_a_role_id": "BIGINT",
                "group_b_role_id": "BIGINT",
                "group_c_role_id": "BIGINT",
                "group_d_role_id": "BIGINT",
                "group_a_channel_id": "BIGINT",
                "group_b_channel_id": "BIGINT",
                "group_c_channel_id": "BIGINT",
                "group_d_channel_id": "BIGINT",
                "playoffs_chat_channel_id": "BIGINT",
                "playoff_qualified_role_id": "BIGINT",
                # Archive-then-delete pod lifecycle: when a completed
                # tournament's category/channels were locked to read-only
                # ("archived"), and the timestamp after which the pod (its
                # category/channels/role — never the tournament's database
                # row, matches, or Champions data) becomes eligible for
                # automatic deletion. Both nullable/absent for tournaments
                # created before this existed, and for any tournament that
                # hasn't completed yet.
                "archived_at": "TEXT",
                "delete_after": "TEXT",
                # When the pod's Discord resources were actually deleted by
                # the automatic sweep (or a manual /tournament_archive_now).
                # The tournament row and all of its historical data are kept
                # forever; only this timestamp and the now-defunct channel/
                # category/role ID columns change.
                "resources_deleted_at": "TEXT",
            },

        }

        for table_name, columns in migrations.items():
            existing = self._table_columns(table_name)
            for column_name, definition in columns.items():
                if column_name in existing:
                    continue
                self.connection.execute(
                    f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
                )
                existing.add(column_name)
                logger.info(
                    "Database migration: added %s.%s",
                    table_name,
                    column_name,
                )

    def _migrate_group_channel_columns(self) -> None:
        """One-time backfill from the old fixed group_a..group_d columns
        into ``tournament_group_channels``.

        Idempotent: only inserts a row when neither the source column nor
        an existing destination row is empty, and uses ``INSERT ... WHERE
        NOT EXISTS`` (or the SQLite equivalent) so re-running on every
        startup after the first is a no-op. The old columns are left
        untouched (not NULLed) — they're just no longer read by any code
        path after this migration exists.
        """
        existing = self._table_columns("tournaments")
        legacy_columns = (
            ("A", "group_a_role_id", "group_a_channel_id"),
            ("B", "group_b_role_id", "group_b_channel_id"),
            ("C", "group_c_role_id", "group_c_channel_id"),
            ("D", "group_d_role_id", "group_d_channel_id"),
        )
        for group_name, role_col, channel_col in legacy_columns:
            if role_col not in existing or channel_col not in existing:
                # Fresh database created after this migration existed —
                # the legacy columns were never added, nothing to backfill.
                continue
            if self.backend == "sqlite":
                query = f"""
                    INSERT OR IGNORE INTO tournament_group_channels
                        (tournament_id, group_name, role_id, channel_id)
                    SELECT id, ?, {role_col}, {channel_col}
                    FROM tournaments
                    WHERE {role_col} IS NOT NULL OR {channel_col} IS NOT NULL
                """
                self.connection.execute(query, (group_name,))
            else:
                query = f"""
                    INSERT INTO tournament_group_channels
                        (tournament_id, group_name, role_id, channel_id)
                    SELECT id, ?, {role_col}, {channel_col}
                    FROM tournaments
                    WHERE ({role_col} IS NOT NULL OR {channel_col} IS NOT NULL)
                    ON CONFLICT (tournament_id, group_name) DO NOTHING
                """
                self.connection.execute(query, (group_name,))

    def _migrate_column_types(self) -> None:
        """Widen columns that were created with the wrong integer type.

        ``matches.reported_by`` stores a Discord user ID (a snowflake, up to
        ~19 digits), but earlier releases created it as a 32-bit ``INTEGER``.
        On PostgreSQL that overflows as soon as anyone reports or sets a
        result, raising ``NumericValueOutOfRange``.  SQLite is unaffected
        (its INTEGER storage class is always 64-bit regardless of the
        declared type), so this only needs to run for the postgres backend.

        ``ALTER COLUMN ... TYPE BIGINT`` only widens the column - it cannot
        lose data, and every existing row (including NULLs and any values
        that already fit) is preserved unchanged.
        """
        if self.backend != "postgres":
            return
        widenings: dict[str, tuple[str, str]] = {
            "matches": ("reported_by", "BIGINT"),
        }
        for table_name, (column_name, target_type) in widenings.items():
            row = self._one(
                """
                SELECT data_type FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = ? AND column_name = ?
                """,
                (table_name, column_name),
            )
            if not row:
                continue
            current_type = str(row["data_type"]).lower()
            if current_type in ("bigint", "int8"):
                continue
            self.connection.execute(
                f"ALTER TABLE {table_name} "
                f"ALTER COLUMN {column_name} TYPE {target_type}"
            )
            logger.info(
                "Database migration: widened %s.%s to %s",
                table_name,
                column_name,
                target_type,
            )

    def _migrate_tournament_type_check(self) -> None:
        """Allow the new 'league' tournament_type through the CHECK
        constraint on databases created before it existed.

        LEAGUE was added for the Premier League template (a genuine
        all-play-all round robin) as a third tournament_type distinct from
        the pre-existing 'round_robin' (which is actually a single-
        elimination "KO Match" bracket, kept as-is) and 'group_knockout'.
        New databases already get the updated constraint from
        _create_schema; this widens it for existing ones. SQLite doesn't
        need this — its CREATE TABLE IF NOT EXISTS only ever runs once, and
        the test suite always starts from a fresh database.
        """
        if self.backend != "postgres":
            return
        rows = self._all(
            """
            SELECT conname, pg_get_constraintdef(oid) AS definition
            FROM pg_constraint
            WHERE conrelid = 'tournaments'::regclass AND contype = 'c'
            """
        )
        for row in rows:
            definition = str(row["definition"])
            if "tournament_type" in definition and "'league'" not in definition:
                self.connection.execute(
                    f'ALTER TABLE tournaments DROP CONSTRAINT "{row["conname"]}"'
                )
                self.connection.execute(
                    "ALTER TABLE tournaments ADD CONSTRAINT tournaments_tournament_type_check "
                    "CHECK (tournament_type IN ('round_robin', 'group_knockout', 'league'))"
                )
                logger.info(
                    "Database migration: widened tournaments.tournament_type CHECK to allow 'league'."
                )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _one(self, query: str, parameters: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        return self.connection.execute_read(query, parameters, fetch="one")

    def _all(self, query: str, parameters: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return self.connection.execute_read(query, parameters, fetch="all")

    def create_tournament(
        self,
        guild_id: int,
        name: str,
        tournament_type: str,
        player_limit: int,
        created_by: int,
        template_id: str | None = None,
    ) -> int:
        name = name.strip()
        if not name:
            raise TournamentError("Tournament name cannot be empty.")
        if tournament_type not in (ROUND_ROBIN, GROUP_KNOCKOUT, LEAGUE):
            raise TournamentError("Unsupported tournament type.")

        if template_id is not None:
            template = TEMPLATES.get(template_id)
            if template is None:
                raise TournamentError(f"Unknown tournament template: {template_id}")
            # The template pins both values. Rather than silently overriding
            # whatever the caller passed in, require them to match so a
            # mismatched template+type/limit combination is rejected with a
            # clear error instead of quietly doing something unexpected.
            if tournament_type != template["tournament_type"] or player_limit != template["player_limit"]:
                raise TournamentError(
                    f"{template['display_name']} requires tournament_type="
                    f"'{template['tournament_type']}' and player_limit={template['player_limit']}."
                )
        else:
            # No template: fall back to the original, fixed manual-creation
            # rules (unchanged from before templates existed). A templated
            # GROUP_KNOCKOUT tournament (Champions League, World Cup, ...)
            # is already fully validated above against its own player_limit,
            # so this fixed "exactly 16" rule only applies to the untemplated/
            # legacy manual path.
            if tournament_type == GROUP_KNOCKOUT and player_limit != 16:
                raise TournamentError("Group + knockout tournaments require exactly 16 players.")
            if tournament_type == ROUND_ROBIN and not 2 <= player_limit <= 64:
                raise TournamentError("Round robin player limit must be between 2 and 64.")
            if tournament_type == LEAGUE and not 2 <= player_limit <= 64:
                raise TournamentError("League player limit must be between 2 and 64.")
        with self._lock:
            existing = self._one(
                "SELECT id FROM tournaments WHERE guild_id = ? AND status IN ('draft', 'open')",
                (guild_id,),
            )
            if existing:
                raise TournamentError("This server already has a tournament being prepared.")
            insert_query = """
                INSERT INTO tournaments
                    (guild_id, name, tournament_type, player_limit, created_by, created_at, template_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """
            parameters = (
                guild_id, name, tournament_type, player_limit, created_by, self._now(), template_id
            )
            if self.backend == "postgres":
                cursor = self.connection.execute(insert_query + " RETURNING id", parameters)
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("PostgreSQL did not return the new tournament ID.")
                return int(row["id"] if isinstance(row, dict) else row[0])
            cursor = self.connection.execute(insert_query, parameters)
            return int(cursor.lastrowid)

    def list_tournaments(
        self,
        guild_id: int,
        statuses: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return tournaments for a server, optionally filtered by status.

        This method is intentionally separate from ``current_tournament``:
        moderators need to be able to list old/completed tournaments so that
        they can obtain an ID for commands such as ``/tournament_delete``.
        """
        with self._lock:
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                rows = self._all(
                    f"""
                    SELECT * FROM tournaments
                    WHERE guild_id = ? AND status IN ({placeholders})
                    ORDER BY id DESC
                    """,
                    (guild_id, *tuple(statuses)),
                )
            else:
                rows = self._all(
                    """
                    SELECT * FROM tournaments
                    WHERE guild_id = ?
                    ORDER BY id DESC
                    """,
                    (guild_id,),
                )
            return [dict(row) for row in rows]

    def get_tournament(self, tournament_id: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM tournaments WHERE id = ?", (tournament_id,))
        return dict(row) if row else None

    def get_guild_tournament(
        self, guild_id: int, tournament_id: int
    ) -> dict[str, Any] | None:
        """Fetch a tournament by both server ID and tournament ID.

        Using both keys avoids accidentally operating on a tournament from a
        different server and makes the create/open/list flow use the same
        guild-scoped lookup everywhere.
        """
        row = self._one(
            "SELECT * FROM tournaments WHERE guild_id = ? AND id = ?",
            (guild_id, tournament_id),
        )
        return dict(row) if row else None

    def delete_tournament(self, guild_id: int, tournament_id: int | None = None) -> dict[str, Any]:
        """Delete a tournament and its players/matches.

        Discord should expose this only to moderators. Players and matches are
        removed automatically by the schema's ON DELETE CASCADE rules.
        """
        with self._lock:
            tournament = (
                self.get_tournament(tournament_id)
                if tournament_id is not None
                else self.current_tournament(guild_id, (DRAFT, OPEN, CLOSED, COMPLETED))
            )
            if not tournament or int(tournament["guild_id"]) != guild_id:
                raise TournamentError("No matching tournament was found.")

            # A default delete is allowed for a draft. Active/in-progress
            # tournaments require the explicit tournament ID as a safeguard.
            if tournament_id is None and tournament["status"] in (OPEN, CLOSED):
                raise TournamentError(
                    "The current tournament is active/in progress. "
                    "Provide its tournament ID to explicitly delete it."
                )

            tournament_id_value = int(tournament["id"])
            cursor = self.connection.execute(
                "DELETE FROM tournaments WHERE id = ? AND guild_id = ?",
                (tournament_id_value, guild_id),
            )
            if self.backend == "postgres":
                # PostgreSQL cursor.rowcount is available for DELETE statements.
                if cursor.rowcount == 0:
                    raise TournamentError("Tournament could not be deleted.")
            elif cursor.rowcount == 0:
                raise TournamentError("Tournament could not be deleted.")

            return tournament

    def current_tournament(
        self, guild_id: int, statuses: tuple[str, ...] = (OPEN, CLOSED)
    ) -> dict[str, Any] | None:
        placeholders = ",".join("?" for _ in statuses)
        row = self._one(
            f"""
            SELECT * FROM tournaments
            WHERE guild_id = ? AND status IN ({placeholders})
            ORDER BY id DESC LIMIT 1
            """,
            (guild_id, *statuses),
        )
        return dict(row) if row else None

    def open_tournament(self, guild_id: int, tournament_id: int | None = None) -> dict[str, Any]:
        with self._lock:
            tournament = self._find_manageable(guild_id, tournament_id)
            if tournament["status"] != DRAFT:
                raise TournamentError("Only a draft tournament can be opened.")
            # Multiple tournaments may be open concurrently in the same
            # guild, each with its own dedicated channels/role (see
            # set_tournament_channels). There is no longer a single-active
            # -tournament restriction here.
            self.connection.execute(
                "UPDATE tournaments SET status = 'open' WHERE id = ?", (tournament["id"],)
            )
            return self.get_tournament(int(tournament["id"])) or tournament

    def set_tournament_channels(
        self,
        tournament_id: int,
        *,
        category_id: int | None = None,
        registration_channel_id: int | None = None,
        fixtures_channel_id: int | None = None,
        results_channel_id: int | None = None,
        participant_role_id: int | None = None,
        tournament_chat_channel_id: int | None = None,
        announcements_channel_id: int | None = None,
        standings_channel_id: int | None = None,
        playoffs_fixtures_channel_id: int | None = None,
        playoffs_standings_channel_id: int | None = None,
        match_schedule_channel_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Persist the Discord resources auto-provisioned for a tournament.

        Only fields explicitly passed (non-None) are updated; omitted fields
        keep their existing stored value, so this can be called incrementally
        (e.g. lazily creating just the role later) without clobbering the rest.
        """
        updates: dict[str, int] = {
            key: value
            for key, value in (
                ("category_id", category_id),
                ("registration_channel_id", registration_channel_id),
                ("fixtures_channel_id", fixtures_channel_id),
                ("results_channel_id", results_channel_id),
                ("participant_role_id", participant_role_id),
                ("tournament_chat_channel_id", tournament_chat_channel_id),
                ("announcements_channel_id", announcements_channel_id),
                ("standings_channel_id", standings_channel_id),
                ("playoffs_fixtures_channel_id", playoffs_fixtures_channel_id),
                ("playoffs_standings_channel_id", playoffs_standings_channel_id),
                ("match_schedule_channel_id", match_schedule_channel_id),
            )
            if value is not None
        }
        if not updates:
            return self.get_tournament(tournament_id)
        with self._lock:
            set_clause = ", ".join(f"{key} = ?" for key in updates)
            self.connection.execute(
                f"UPDATE tournaments SET {set_clause} WHERE id = ?",
                (*updates.values(), tournament_id),
            )
            return self.get_tournament(tournament_id)

    def set_tournament_group_resources(
        self,
        tournament_id: int,
        *,
        group_role_ids: dict[str, int] | None = None,
        group_channel_ids: dict[str, int] | None = None,
        playoffs_chat_channel_id: int | None = None,
        playoff_qualified_role_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Persist this tournament's own group-stage/playoffs-chat resources.

        Per-group role/channel IDs are stored one row per group in
        ``tournament_group_channels`` (any number of groups — Champions
        League's 8 or World Cup's 16, not just the original fixed A-D),
        upserted so a group's role and channel can be set in separate calls
        without clobbering each other. ``playoffs_chat_channel_id`` and
        ``playoff_qualified_role_id`` are tournament-wide (one per
        tournament regardless of group count) and stay as columns on
        ``tournaments``. Only fields explicitly passed are updated.
        """
        group_roles = {g: v for g, v in (group_role_ids or {}).items() if v is not None}
        group_channels = {g: v for g, v in (group_channel_ids or {}).items() if v is not None}
        groups = set(group_roles) | set(group_channels)

        with self._lock:
            for group in groups:
                role_id = group_roles.get(group)
                channel_id = group_channels.get(group)
                if self.backend == "sqlite":
                    self.connection.execute(
                        """
                        INSERT INTO tournament_group_channels
                            (tournament_id, group_name, role_id, channel_id)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT (tournament_id, group_name) DO UPDATE SET
                            role_id = COALESCE(excluded.role_id, tournament_group_channels.role_id),
                            channel_id = COALESCE(excluded.channel_id, tournament_group_channels.channel_id)
                        """,
                        (tournament_id, group, role_id, channel_id),
                    )
                else:
                    self.connection.execute(
                        """
                        INSERT INTO tournament_group_channels
                            (tournament_id, group_name, role_id, channel_id)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT (tournament_id, group_name) DO UPDATE SET
                            role_id = COALESCE(EXCLUDED.role_id, tournament_group_channels.role_id),
                            channel_id = COALESCE(EXCLUDED.channel_id, tournament_group_channels.channel_id)
                        """,
                        (tournament_id, group, role_id, channel_id),
                    )

            updates: dict[str, int] = {}
            if playoffs_chat_channel_id is not None:
                updates["playoffs_chat_channel_id"] = playoffs_chat_channel_id
            if playoff_qualified_role_id is not None:
                updates["playoff_qualified_role_id"] = playoff_qualified_role_id
            if updates:
                set_clause = ", ".join(f"{key} = ?" for key in updates)
                self.connection.execute(
                    f"UPDATE tournaments SET {set_clause} WHERE id = ?",
                    (*updates.values(), tournament_id),
                )
            return self.get_tournament(tournament_id)

    def get_tournament_group_resources(self, tournament_id: int) -> dict[str, dict[str, int | None]]:
        """Return ``{group_name: {"role_id": ..., "channel_id": ...}}`` for
        every group this tournament has provisioned resources for.

        Only returns groups that actually have a row (i.e. have had a role
        or channel provisioned at least once) — callers should treat a
        missing group the same as "not yet created", same as the old
        fixed-column ``tournament.get(f"group_{group.lower()}_role_id")``
        pattern did for a group that was never provisioned.
        """
        rows = self._all(
            """
            SELECT group_name, role_id, channel_id
            FROM tournament_group_channels
            WHERE tournament_id = ?
            """,
            (tournament_id,),
        )
        return {
            str(row["group_name"]): {
                "role_id": row["role_id"],
                "channel_id": row["channel_id"],
            }
            for row in rows
        }

    def clear_tournament_group_resources(self, tournament_id: int) -> None:
        """Delete all group chat rows for a tournament.

        Called when an archived tournament's Discord pod is actually
        deleted, mirroring what ``mark_tournament_resources_deleted`` does
        for the fixed columns — those Discord objects no longer exist.
        """
        with self._lock:
            self.connection.execute(
                "DELETE FROM tournament_group_channels WHERE tournament_id = ?",
                (tournament_id,),
            )


    def get_tournament_by_channel(self, guild_id: int, channel_id: int) -> dict[str, Any] | None:
        """Find the tournament whose registration, fixtures, results, chat,
        standings, or playoffs channel matches.

        Used to route /register, /admin_register, /report, fixture posting,
        standings posting, and tournament-chat access to the correct
        tournament when several are running at once in one guild.
        """
        row = self._one(
            """
            SELECT * FROM tournaments
            WHERE guild_id = ?
              AND (registration_channel_id = ? OR fixtures_channel_id = ?
                   OR results_channel_id = ? OR tournament_chat_channel_id = ?
                   OR announcements_channel_id = ?
                   OR standings_channel_id = ? OR playoffs_fixtures_channel_id = ?
                   OR playoffs_standings_channel_id = ? OR playoffs_chat_channel_id = ?
                   OR id IN (
                       SELECT tournament_id FROM tournament_group_channels
                       WHERE channel_id = ?
                   ))
            ORDER BY id DESC LIMIT 1
            """,
            (
                guild_id,
                channel_id,
                channel_id,
                channel_id,
                channel_id,
                channel_id,
                channel_id,
                channel_id,
                channel_id,
                channel_id,
                channel_id,
            ),
        )
        return dict(row) if row else None

    def count_tournaments_referencing_resource(
        self, guild_id: int, resource_id: int, exclude_tournament_id: int
    ) -> int:
        """Count other tournaments that also point at this same channel/role id.

        Used before deleting a tournament's auto-provisioned category/channel
        /role, so cleanup never deletes something still in use elsewhere.
        """
        row = self._one(
            """
            SELECT COUNT(*) AS cnt FROM tournaments
            WHERE guild_id = ? AND id != ?
              AND (category_id = ? OR registration_channel_id = ? OR fixtures_channel_id = ?
                   OR results_channel_id = ? OR participant_role_id = ?
                   OR tournament_chat_channel_id = ? OR announcements_channel_id = ?
                   OR standings_channel_id = ?
                   OR playoffs_fixtures_channel_id = ? OR playoffs_standings_channel_id = ?
                   OR playoffs_chat_channel_id = ? OR playoff_qualified_role_id = ?
                   OR id IN (
                       SELECT tournament_id FROM tournament_group_channels
                       WHERE role_id = ? OR channel_id = ?
                   ))
            """,
            (
                guild_id,
                exclude_tournament_id,
                resource_id,
                resource_id,
                resource_id,
                resource_id,
                resource_id,
                resource_id,
                resource_id,
                resource_id,
                resource_id,
                resource_id,
                resource_id,
                resource_id,
                resource_id,
                resource_id,
            ),
        )
        return int(row["cnt"]) if row else 0

    # -- Archive-then-delete pod lifecycle --------------------------------
    #
    # "Archiving" only ever locks a completed tournament's Discord channels
    # to read-only and later deletes that Discord pod (category, channels,
    # role). It never touches the tournaments/matches/tournament_players
    # rows or Champions data — those are kept forever for history, exactly
    # like a manually-run /tournament_delete does NOT apply here.

    def tournaments_pending_archive(self) -> list[dict[str, Any]]:
        """Completed tournaments that still have live (non-archived) channels.

        Scans across every guild — the periodic sweep resolves each row's
        guild via the Discord client itself.
        """
        rows = self._all(
            """
            SELECT * FROM tournaments
            WHERE status = ? AND archived_at IS NULL
            """,
            (COMPLETED,),
        )
        return [dict(row) for row in rows]

    def tournaments_pending_resource_deletion(self) -> list[dict[str, Any]]:
        """Archived tournaments whose retention window has elapsed.

        Only tournaments that still have an un-deleted pod (resources_deleted_at
        IS NULL) and a due ``delete_after`` are returned.
        """
        rows = self._all(
            """
            SELECT * FROM tournaments
            WHERE archived_at IS NOT NULL
              AND resources_deleted_at IS NULL
              AND delete_after IS NOT NULL
              AND delete_after <= ?
            """,
            (self._now(),),
        )
        return [dict(row) for row in rows]

    def mark_tournament_archived(
        self, tournament_id: int, *, retention_days: int
    ) -> dict[str, Any] | None:
        """Record that a tournament's channels were just locked to read-only.

        ``delete_after`` is computed here (now + retention_days) so callers
        never have to do their own date math. Safe to call only once per
        tournament in practice, but re-calling simply recomputes the same
        kind of schedule from "now" rather than duplicating anything.
        """
        now = datetime.now(UTC)
        delete_after = (now + timedelta(days=retention_days)).isoformat()
        with self._lock:
            self.connection.execute(
                "UPDATE tournaments SET archived_at = ?, delete_after = ? WHERE id = ?",
                (now.isoformat(), delete_after, tournament_id),
            )
            return self.get_tournament(tournament_id)

    def mark_tournament_resources_deleted(self, tournament_id: int) -> dict[str, Any] | None:
        """Record that an archived tournament's Discord pod was deleted.

        Clears the now-defunct category/channel/role ID columns (those
        Discord objects no longer exist) but leaves every other column —
        including the tournament's name, status, players, matches, and any
        Champions awards — untouched.
        """
        with self._lock:
            self.connection.execute(
                """
                UPDATE tournaments
                SET resources_deleted_at = ?,
                    category_id = NULL,
                    registration_channel_id = NULL,
                    fixtures_channel_id = NULL,
                    results_channel_id = NULL,
                    tournament_chat_channel_id = NULL,
                    announcements_channel_id = NULL,
                    standings_channel_id = NULL,
                    playoffs_fixtures_channel_id = NULL,
                    playoffs_standings_channel_id = NULL,
                    playoffs_chat_channel_id = NULL,
                    playoff_qualified_role_id = NULL,
                    participant_role_id = NULL
                WHERE id = ?
                """,
                (self._now(), tournament_id),
            )
            self.connection.execute(
                "DELETE FROM tournament_group_channels WHERE tournament_id = ?",
                (tournament_id,),
            )
            return self.get_tournament(tournament_id)

    def _assign_world_cup_nations(self, tournament_id: int, template_id: str | None, players: list[dict[str, Any]]) -> None:
        """Assign unique national teams after registration closes.

        The mapping is stored in the tournament_players row, so restarts and
        later fixture/result generation always use the same nation. Existing
        assignments are preserved if this method is ever called again.
        """
        if template_id not in WORLD_CUP_TEMPLATE_IDS:
            return
        if any(player.get("nation_name") for player in players):
            return
        pool = WORLD_CUP_NATIONS_64 if template_id == TEMPLATE_WORLD_CUP_64 else WORLD_CUP_NATIONS_48
        if len(players) > len(pool):
            raise TournamentError(f"Not enough national teams are available for {len(players)} players.")
        nations = random.sample(pool, len(players))
        for player, nation in zip(players, nations):
            self.connection.execute(
                "UPDATE tournament_players SET nation_name = ? WHERE tournament_id = ? AND user_id = ?",
                (nation, tournament_id, int(player["user_id"])),
            )

    def assign_world_cup_nation(self, tournament_id: int, user_id: int, nation_name: str) -> dict[str, Any]:
        """Manually assign an unused national team to a World Cup participant."""
        with self._lock:
            tournament = self.get_tournament(tournament_id)
            if not tournament or tournament.get("template_id") not in WORLD_CUP_TEMPLATE_IDS:
                raise TournamentError("This tournament is not a World Cup.")
            nation = str(nation_name or "").strip()
            pool = WORLD_CUP_NATIONS_64 if tournament.get("template_id") == TEMPLATE_WORLD_CUP_64 else WORLD_CUP_NATIONS_48
            if nation not in pool:
                raise TournamentError("That country is not available in the World Cup country pool.")
            player = next((row for row in self.players(tournament_id) if int(row["user_id"]) == int(user_id)), None)
            if player is None:
                raise TournamentError("That user is not registered in this World Cup.")
            owner = next((row for row in self.players(tournament_id) if str(row.get("nation_name") or "") == nation and int(row["user_id"]) != int(user_id)), None)
            if owner is not None:
                raise TournamentError(f"{nation} is already assigned to another player in this World Cup.")
            self.connection.execute(
                "UPDATE tournament_players SET nation_name = ? WHERE tournament_id = ? AND user_id = ?",
                (nation, tournament_id, int(user_id)),
            )
            return self.get_tournament(tournament_id) or tournament

    def close_and_generate(
        self, guild_id: int, tournament_id: int | None = None
    ) -> dict[str, Any]:
        with self._lock:
            tournament = self.current_tournament(guild_id, (OPEN,))
            if tournament_id is not None:
                tournament = self.get_tournament(tournament_id)
            if not tournament or tournament["guild_id"] != guild_id:
                raise TournamentError("No open tournament was found.")
            if tournament["status"] != OPEN:
                raise TournamentError("Only an open tournament can be closed.")
            players = self.players(int(tournament["id"]))
            required = int(tournament["player_limit"])
            if len(players) < 2:
                raise TournamentError("At least two players are required.")
            if tournament["tournament_type"] == GROUP_KNOCKOUT and len(players) != required:
                raise TournamentError(
                    f"This tournament requires exactly {required} players; currently there are {len(players)}."
                )
            if len(players) > required:
                raise TournamentError("The tournament has more players than its limit.")
            tournament_id_value = int(tournament["id"])
            self._assign_world_cup_nations(
                tournament_id_value, str(tournament.get("template_id") or ""), players
            )
            players = self.players(tournament_id_value)
            now = self._now()
            self.connection.execute(
                """
                UPDATE tournaments
                SET status = 'closed', registration_closed_at = ?
                WHERE id = ?
                """,
                (now, tournament_id_value),
            )
            if tournament["tournament_type"] == ROUND_ROBIN:
                # Despite the historical option name "round_robin", this
                # tournament type is a single-elimination knockout: one match
                # per player per round, loser eliminated, winner advances.
                # (The LEAGUE type below is the genuine all-play-all format.)
                fixture_count = self._insert_knockout_round(
                    tournament_id_value,
                    1,
                    [int(row["user_id"]) for row in players],
                )
            elif tournament["tournament_type"] == LEAGUE:
                fixture_count = self._insert_fixtures(
                    tournament_id_value,
                    "league",
                    None,
                    [int(row["user_id"]) for row in players],
                )
                self._set_stage_deadline(tournament_id_value, "league", LEAGUE_DEADLINE_HOURS)
            else:
                fixture_count = self._generate_group_stage(tournament_id_value, players)
            return {
                "tournament": self.get_tournament(tournament_id_value),
                "players": len(players),
                "fixtures": fixture_count,
            }

    def register(
        self, tournament_id: int, user_id: int, display_name: str
    ) -> dict[str, Any]:
        """Register a player and automatically close a full tournament.

        The return value includes ``auto_closed`` and the generated fixture
        count so the Discord layer can assign group roles immediately after
        the final registration.
        """
        with self._lock:
            tournament = self.get_tournament(tournament_id)
            if not tournament or tournament["status"] != OPEN:
                raise TournamentError("Registration is not currently open.")
            count = self._one(
                "SELECT COUNT(*) AS count FROM tournament_players WHERE tournament_id = ?",
                (tournament_id,),
            )
            if int(count["count"]) >= int(tournament["player_limit"]):
                raise TournamentError("This tournament is full.")
            duplicate = self._one(
                """
                SELECT 1 FROM tournament_players
                WHERE tournament_id = ? AND user_id = ?
                """,
                (tournament_id, user_id),
            )
            if duplicate:
                raise TournamentError("You are already registered for this tournament.")
            self.connection.execute(
                """
                INSERT INTO tournament_players
                    (tournament_id, user_id, display_name, joined_at)
                VALUES (?, ?, ?, ?)
                """,
                (tournament_id, user_id, display_name[:100], self._now()),
            )

            new_count = int(count["count"]) + 1
            result: dict[str, Any] = {
                "registered": True,
                "auto_closed": False,
                "players": new_count,
                "fixtures": 0,
                "tournament": self.get_tournament(tournament_id),
            }
            if new_count == int(tournament["player_limit"]):
                closed = self.close_and_generate(
                    int(tournament["guild_id"]), tournament_id
                )
                result.update(
                    {
                        "auto_closed": True,
                        "players": closed["players"],
                        "fixtures": closed["fixtures"],
                        "tournament": closed["tournament"],
                    }
                )
            return result

    def replace_participant(
        self, tournament_id: int, removed_user_id: int, replacement_user_id: int,
        replacement_display_name: str,
    ) -> dict[str, Any]:
        """Atomically replace an unplayed participant in one tournament.

        This is intentionally scoped to ``tournament_id``. The removed
        participant's global history and registrations in every other
        tournament are untouched. Existing pending fixtures are updated in
        place, preserving their database IDs, visible ordering, opponents,
        and group assignment.
        """
        tournament_id = int(tournament_id)
        removed_user_id = int(removed_user_id)
        replacement_user_id = int(replacement_user_id)
        if removed_user_id == replacement_user_id:
            raise TournamentError("The removed participant and replacement participant must be different.")
        display_name = str(replacement_display_name or "").strip()[:100]
        if not display_name:
            raise TournamentError("The replacement participant has no valid display name.")

        with self._lock:
            transaction_started = False
            postgres_transaction = None
            try:
                if self.backend == "postgres":
                    # psycopg3 supports an explicit transaction even though
                    # the production connection is configured with
                    # autocommit=True. Do not route transaction statements
                    # through the reconnecting write adapter: reconnecting in
                    # the middle of a transaction could invalidate atomicity.
                    postgres_transaction = self.connection._connection.transaction()
                    postgres_transaction.__enter__()
                else:
                    self.connection.execute("BEGIN")
                    transaction_started = True

                tournament = self.get_tournament(tournament_id)
                if not tournament:
                    raise TournamentError("No matching tournament was found.")
                if tournament["status"] not in (DRAFT, OPEN, CLOSED):
                    raise TournamentError("This tournament is no longer in its starting stage.")

                removed = self._one(
                    """
                    SELECT * FROM tournament_players
                    WHERE tournament_id = ? AND user_id = ?
                    """,
                    (tournament_id, removed_user_id),
                )
                if not removed:
                    raise TournamentError("The removed participant is not registered in this tournament.")

                replacement = self._one(
                    """
                    SELECT 1 FROM tournament_players
                    WHERE tournament_id = ? AND user_id = ?
                    """,
                    (tournament_id, replacement_user_id),
                )
                if replacement:
                    raise TournamentError("The replacement participant is already registered in this tournament.")

                all_matches = self.matches(tournament_id, user_id=removed_user_id)
                completed = [row for row in all_matches if str(row.get("status")) == "completed"]
                if completed:
                    raise TournamentError("The removed participant has already played a match in this tournament.")

                # A participant may only be replaced while the tournament is
                # still in its initial competition stage. This prevents the
                # command from touching an already-created playoff bracket or
                # later KO round even if the removed player has no result in
                # that later data.
                tournament_type = str(tournament["tournament_type"])
                for row in all_matches:
                    stage = str(row.get("stage") or "")
                    round_number = int(row.get("round_number") or 1)
                    if tournament_type == GROUP_KNOCKOUT:
                        if stage != "group":
                            raise TournamentError("This tournament is no longer in its starting stage.")
                    elif tournament_type == LEAGUE:
                        if stage != "league":
                            raise TournamentError("This tournament is no longer in its starting stage.")
                    else:
                        if stage != "round_robin" or round_number != 1:
                            raise TournamentError("This tournament is no longer in its starting stage.")

                group_name = removed["group_name"]
                pending_fixture_ids = [int(row["id"]) for row in all_matches]

                # Preserve the registration row's joined_at/group/position;
                # only the Discord identity and display name change.
                self.connection.execute(
                    """
                    UPDATE tournament_players
                    SET user_id = ?, display_name = ?
                    WHERE tournament_id = ? AND user_id = ?
                    """,
                    (replacement_user_id, display_name, tournament_id, removed_user_id),
                )

                # Only pending fixtures are mutable. The completed-match check
                # above is intentionally repeated defensively in the UPDATE
                # predicates so a finalized result can never be changed by
                # this feature.
                for match_id in pending_fixture_ids:
                    self.connection.execute(
                        """
                        UPDATE matches
                        SET player1_id = ?
                        WHERE id = ? AND tournament_id = ?
                          AND player1_id = ? AND status = 'pending'
                        """,
                        (replacement_user_id, match_id, tournament_id, removed_user_id),
                    )
                    self.connection.execute(
                        """
                        UPDATE matches
                        SET player2_id = ?
                        WHERE id = ? AND tournament_id = ?
                          AND player2_id = ? AND status = 'pending'
                        """,
                        (replacement_user_id, match_id, tournament_id, removed_user_id),
                    )

                # Verify the transaction did not leave any stale pending
                # fixture reference behind. If anything is inconsistent,
                # raising here rolls back the participant update as well.
                stale = self._one(
                    """
                    SELECT 1 FROM matches
                    WHERE tournament_id = ? AND status = 'pending'
                      AND (player1_id = ? OR player2_id = ?)
                    """,
                    (tournament_id, removed_user_id, removed_user_id),
                )
                if stale:
                    raise TournamentError("The tournament data is inconsistent. No changes were made.")

                result = {
                    "tournament": self.get_tournament(tournament_id),
                    "removed_user_id": removed_user_id,
                    "replacement_user_id": replacement_user_id,
                    "group_name": group_name,
                    "fixtures_updated": len(pending_fixture_ids),
                    "fixtures_generated": bool(all_matches),
                    "played_matches": 0,
                }

                if self.backend == "postgres":
                    postgres_transaction.__exit__(None, None, None)
                    postgres_transaction = None
                else:
                    self.connection.execute("COMMIT")
                    transaction_started = False
                return result
            except Exception:
                if postgres_transaction is not None:
                    try:
                        postgres_transaction.__exit__(*__import__('sys').exc_info())
                    except Exception:
                        logger.exception("Failed to roll back participant replacement transaction.")
                    postgres_transaction = None
                elif transaction_started:
                    try:
                        self.connection.execute("ROLLBACK")
                    except Exception:
                        logger.exception("Failed to roll back participant replacement transaction.")
                raise

    def unregister(self, tournament_id: int, user_id: int) -> None:
        with self._lock:
            tournament = self.get_tournament(tournament_id)
            if not tournament or tournament["status"] != OPEN:
                raise TournamentError("Registration is closed.")
            cursor = self.connection.execute(
                """
                DELETE FROM tournament_players
                WHERE tournament_id = ? AND user_id = ?
                """,
                (tournament_id, user_id),
            )
            if cursor.rowcount == 0:
                raise TournamentError("You are not registered for this tournament.")

    def replaceable_players(self, tournament_id: int) -> list[dict[str, Any]]:
        """Return registered players with no finalized match in this tournament."""
        rows = self._all(
            """
            SELECT tp.*
            FROM tournament_players tp
            WHERE tp.tournament_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM matches m
                  WHERE m.tournament_id = tp.tournament_id
                    AND m.status = 'completed'
                    AND (m.player1_id = tp.user_id OR m.player2_id = tp.user_id)
              )
            ORDER BY tp.joined_at, tp.user_id
            """,
            (int(tournament_id),),
        )
        return [dict(row) for row in rows]

    def player_has_other_tournament_registration(self, user_id: int, tournament_id: int) -> bool:
        row = self._one(
            """
            SELECT 1 FROM tournament_players
            WHERE user_id = ? AND tournament_id <> ?
            LIMIT 1
            """,
            (int(user_id), int(tournament_id)),
        )
        return row is not None

    def players(self, tournament_id: int) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self._all(
                """
                SELECT * FROM tournament_players
                WHERE tournament_id = ?
                ORDER BY joined_at, user_id
                """,
                (tournament_id,),
            )
        ]

    def matches(
        self,
        tournament_id: int,
        *,
        stage: str | None = None,
        group_name: str | None = None,
        user_id: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["tournament_id = ?"]
        parameters: list[Any] = [tournament_id]
        if stage:
            clauses.append("stage = ?")
            parameters.append(stage)
        if group_name:
            clauses.append("group_name = ?")
            parameters.append(group_name)
        if user_id is not None:
            clauses.append("(player1_id = ? OR player2_id = ?)")
            parameters.extend([user_id, user_id])
        rows = self._all(
            f"SELECT * FROM matches WHERE {' AND '.join(clauses)} ORDER BY id",
            tuple(parameters),
        )
        return [dict(row) for row in rows]

    def get_match_schedule(self, match_id: int) -> dict[str, Any] | None:
        row = self._one(
            """
            SELECT m.*, t.guild_id, t.name AS tournament_name,
                   CASE m.stage
                       WHEN 'group' THEN t.group_stage_deadline_at
                       WHEN 'league' THEN t.league_deadline_at
                       WHEN 'round_of_32' THEN t.round_of_32_deadline_at
                       WHEN 'round_of_16' THEN t.round_of_16_deadline_at
                       WHEN 'quarterfinal' THEN t.quarterfinal_deadline_at
                       WHEN 'semifinal' THEN t.semifinal_deadline_at
                       WHEN 'final' THEN t.final_deadline_at
                       ELSE NULL
                   END AS stage_deadline_at
            FROM matches m JOIN tournaments t ON t.id = m.tournament_id
            WHERE m.id = ?
            """, (int(match_id),))
        return dict(row) if row else None

    def propose_match_schedule(self, match_id: int, proposer_id: int, proposed_for: str) -> dict[str, Any]:
        proposed_for = str(proposed_for or '').strip()
        if not proposed_for:
            raise TournamentError("Please provide a valid date and time.")
        with self._lock:
            match = self.get_match_schedule(match_id)
            if not match:
                raise TournamentError("Match not found.")
            if match['status'] == 'completed':
                raise TournamentError("This match has already been completed.")
            if int(proposer_id) not in (int(match['player1_id']), int(match['player2_id'])):
                raise TournamentError("Only the players in this fixture can schedule it.")
            deadline = match.get('stage_deadline_at')
            now = datetime.now(UTC)
            if deadline and now >= datetime.fromisoformat(str(deadline)):
                raise TournamentError("The scheduling deadline has already passed. Contact tournament staff.")
            self.connection.execute(
                "UPDATE matches SET schedule_status = 'proposed', schedule_proposer_id = ?, schedule_proposed_at = ?, schedule_proposed_for = ?, schedule_confirmed_at = NULL WHERE id = ?",
                (int(proposer_id), now.isoformat(), proposed_for, int(match_id)),
            )
            return self.get_match_schedule(match_id) or match

    def respond_match_schedule(self, match_id: int, responder_id: int, action: str) -> dict[str, Any]:
        action = str(action).strip().lower()
        if action not in ('accept', 'decline'):
            raise TournamentError("Invalid schedule response.")
        with self._lock:
            match = self.get_match_schedule(match_id)
            if not match:
                raise TournamentError("Match not found.")
            if match['status'] == 'completed':
                raise TournamentError("This match has already been completed.")
            players = (int(match['player1_id']), int(match['player2_id']))
            if int(responder_id) not in players:
                raise TournamentError("Only the players in this fixture can respond.")
            proposer = match.get('schedule_proposer_id')
            if not proposer:
                raise TournamentError("There is no pending schedule proposal.")
            if int(proposer) == int(responder_id):
                raise TournamentError("You cannot respond to your own proposal.")
            deadline = match.get('stage_deadline_at')
            now = datetime.now(UTC)
            if deadline and now >= datetime.fromisoformat(str(deadline)):
                raise TournamentError("The scheduling deadline has already passed. Contact tournament staff.")
            if action == 'accept':
                self.connection.execute("UPDATE matches SET schedule_status = 'confirmed', schedule_confirmed_at = ? WHERE id = ?", (now.isoformat(), int(match_id)))
            else:
                self.connection.execute("UPDATE matches SET schedule_status = 'unscheduled', schedule_proposer_id = NULL, schedule_proposed_at = NULL, schedule_proposed_for = NULL, schedule_confirmed_at = NULL WHERE id = ?", (int(match_id),))
            return self.get_match_schedule(match_id) or match

    def mark_schedule_message_posted(self, match_id: int, message_id: int) -> None:
        """Remember the Discord message used for a confirmed match schedule.

        This makes startup recovery idempotent: a deploy/restart can safely
        scan confirmed schedules without reposting messages that were already
        published.
        """
        with self._lock:
            self.connection.execute(
                "UPDATE matches SET schedule_message_id = ? WHERE id = ?",
                (int(message_id), int(match_id)),
            )

    def mark_schedule_prompted(self, match_id: int, player_id: int) -> None:
        with self._lock:
            match = self.get_match_schedule(match_id)
            if not match:
                return
            if int(match['player1_id']) == int(player_id):
                column = 'schedule_prompted_player1_at'
            elif int(match['player2_id']) == int(player_id):
                column = 'schedule_prompted_player2_at'
            else:
                return
            self.connection.execute(f"UPDATE matches SET {column} = ? WHERE id = ?", (self._now(), int(match_id)))

    def get_match(self, match_id: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM matches WHERE id = ?", (match_id,))
        return dict(row) if row else None

    def mark_disputed(
        self, match_id: int, user_id: int, channel_id: int
    ) -> dict[str, Any]:
        """Record that a submitted result is being disputed.

        Only the opponent of whoever reported the pending score may raise a
        dispute, and only while the match is still awaiting confirmation.
        This does not change the score or status - it just flags the match
        and links the private thread where evidence is being discussed, so
        a moderator can review it and resolve it with ``/set_result``.
        """
        with self._lock:
            match = self._one("SELECT * FROM matches WHERE id = ?", (match_id,))
            if not match:
                raise TournamentError("Match not found.")
            if match["status"] == "completed":
                raise TournamentError("This match has already been completed.")
            if match["reported_by"] is None:
                raise TournamentError("There is no submitted result to dispute yet.")
            if user_id not in (int(match["player1_id"]), int(match["player2_id"])):
                raise TournamentError("You are not one of the players in this match.")
            if int(match["reported_by"]) == user_id:
                raise TournamentError("You cannot dispute the result you submitted yourself.")
            self.connection.execute(
                """
                UPDATE matches
                SET dispute_channel_id = ?, disputed_at = ?
                WHERE id = ?
                """,
                (channel_id, self._now(), match_id),
            )
            return dict(self._one("SELECT * FROM matches WHERE id = ?", (match_id,)))

    def standings(
        self, tournament_id: int, group_name: str | None = None
    ) -> list[Standing]:
        players = self.players(tournament_id)
        if group_name:
            players = [row for row in players if row["group_name"] == group_name]
        matches = self.matches(
            tournament_id,
            stage="group" if group_name else None,
            group_name=group_name,
        )
        if not group_name:
            tournament = self.get_tournament(tournament_id)
            if tournament and tournament["tournament_type"] == ROUND_ROBIN:
                matches = self.matches(tournament_id, stage="round_robin")
            elif tournament and tournament["tournament_type"] == LEAGUE:
                matches = self.matches(tournament_id, stage="league")
            else:
                matches = []
        return calculate_standings(players, matches)

    def _backfill_goal_records(self) -> None:
        """Rebuild competitive goal totals from official completed results.

        This is idempotent and also makes V18 immediately populate statistics
        for existing Weekly Championship, Champions League, Premier League, and
        World Cup results.  KO Match tournaments (``round_robin``) are excluded
        completely.
        """
        with self._lock:
            self.connection.execute("DELETE FROM goal_records")
            self.connection.execute(
                """
                INSERT INTO goal_records
                    (tournament_id, user_id, display_name, nation_name, goals)
                SELECT
                    x.tournament_id,
                    x.user_id,
                    COALESCE(tp.display_name, 'Unknown Player'),
                    tp.nation_name,
                    SUM(x.goals) AS goals
                FROM (
                    SELECT tournament_id, player1_id AS user_id, score1 AS goals
                    FROM matches
                    WHERE status = 'completed' AND score1 IS NOT NULL
                    UNION ALL
                    SELECT tournament_id, player2_id AS user_id, score2 AS goals
                    FROM matches
                    WHERE status = 'completed' AND score2 IS NOT NULL
                ) AS x
                JOIN tournaments t ON t.id = x.tournament_id
                LEFT JOIN tournament_players tp
                    ON tp.tournament_id = x.tournament_id
                   AND tp.user_id = x.user_id
                WHERE t.tournament_type <> ?
                GROUP BY x.tournament_id, x.user_id, tp.display_name, tp.nation_name
                """,
                (ROUND_ROBIN,),
            )

    def goal_records_for_tournament(self, tournament_id: int) -> list[dict[str, Any]]:
        """Return top scorers for one competitive tournament.

        KO Match is deliberately treated as a non-competitive practice format
        and therefore returns no goal records.
        """
        tournament = self.get_tournament(tournament_id)
        if not tournament or tournament.get("tournament_type") == ROUND_ROBIN:
            return []
        rows = self._all(
            """
            SELECT user_id, display_name, nation_name, goals
            FROM goal_records
            WHERE tournament_id = ?
            ORDER BY goals DESC, display_name ASC, user_id ASC
            """,
            (int(tournament_id),),
        )
        return [dict(row) for row in rows]

    def goal_records_all(self) -> list[dict[str, Any]]:
        """Return competitive goal totals grouped across all tournaments."""
        rows = self._all(
            """
            SELECT user_id,
                   MAX(display_name) AS display_name,
                   SUM(goals) AS goals,
                   COUNT(DISTINCT tournament_id) AS tournaments_played
            FROM goal_records
            GROUP BY user_id
            ORDER BY SUM(goals) DESC, MAX(display_name) ASC, user_id ASC
            """
        )
        return [dict(row) for row in rows]

    def golden_boot_candidates_for_guild(self, guild_id: int, month: str, scope: str) -> list[dict[str, Any]]:
        """Return monthly Golden Boot standings for one guild.

        Uses completed competitive tournaments whose recorded start month is
        ``month``. KO Match is never included.
        """
        month = str(month).strip()
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise ValueError("Month must use YYYY-MM format, for example 2026-09.")
        scope = str(scope).strip().lower()
        groups = {
            "pl": (TEMPLATE_PREMIER_LEAGUE,),
            "wc": tuple(sorted(WORLD_CUP_TEMPLATE_IDS)),
            "cl": (TEMPLATE_CHAMPIONS_LEAGUE,),
            "combined": (
                TEMPLATE_PREMIER_LEAGUE, TEMPLATE_WEEKLY_CHAMPIONSHIP,
                TEMPLATE_CHAMPIONS_LEAGUE, *tuple(sorted(WORLD_CUP_TEMPLATE_IDS))
            ),
        }
        if scope not in groups:
            raise ValueError("Golden Boot scope must be pl, wc, cl, or combined.")
        placeholders = ",".join("?" for _ in groups[scope])
        rows = self._all(
            f"""SELECT gr.user_id, MAX(gr.display_name) AS display_name,
                       MAX(gr.nation_name) AS nation_name, SUM(gr.goals) AS goals,
                       COUNT(DISTINCT gr.tournament_id) AS tournaments_count
                  FROM goal_records gr
                  JOIN tournaments t ON t.id = gr.tournament_id
                 WHERE t.guild_id = ?
                   AND t.status = 'completed'
                   AND substr(COALESCE(t.registration_closed_at, t.created_at), 1, 7) = ?
                   AND t.template_id IN ({placeholders})
                 GROUP BY gr.user_id
                 ORDER BY SUM(gr.goals) DESC, MAX(gr.display_name) ASC, gr.user_id ASC""",
            (int(guild_id), month, *groups[scope]),
        )
        return [dict(row) for row in rows]

    def golden_boot_candidates(self, month: str, scope: str) -> list[dict[str, Any]]:
        """Return completed competitive goal totals for a calendar month.

        Scope is ``pl``, ``wc``, ``cl`` or ``combined``. KO Match is never
        included. The tournament start month defines the competition month. The start timestamp is registration_closed_at (with created_at as a legacy fallback).
        """
        month = str(month).strip()
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise ValueError("Month must use YYYY-MM format, for example 2026-09.")
        scope = str(scope).strip().lower()
        groups = {
            "pl": (TEMPLATE_PREMIER_LEAGUE,),
            "wc": tuple(sorted(WORLD_CUP_TEMPLATE_IDS)),
            "cl": (TEMPLATE_CHAMPIONS_LEAGUE,),
            "combined": (TEMPLATE_PREMIER_LEAGUE, TEMPLATE_WEEKLY_CHAMPIONSHIP, TEMPLATE_CHAMPIONS_LEAGUE, *tuple(sorted(WORLD_CUP_TEMPLATE_IDS))),
        }
        if scope not in groups:
            raise ValueError("Golden Boot scope must be pl, wc, cl, or combined.")
        placeholders = ",".join("?" for _ in groups[scope])
        rows = self._all(
            f"""SELECT gr.user_id, MAX(gr.display_name) AS display_name,
                       MAX(gr.nation_name) AS nation_name, SUM(gr.goals) AS goals,
                       COUNT(DISTINCT gr.tournament_id) AS tournaments_count
                  FROM goal_records gr
                  JOIN tournaments t ON t.id = gr.tournament_id
                 WHERE t.status = 'completed'
                   AND substr(COALESCE(t.registration_closed_at, t.created_at), 1, 7) = ?
                   AND t.template_id IN ({placeholders})
                 GROUP BY gr.user_id
                 ORDER BY SUM(gr.goals) DESC, MAX(gr.display_name) ASC, gr.user_id ASC""",
            (month, *groups[scope]),
        )
        return [dict(row) for row in rows]

    def golden_boot_award(self, guild_id: int, award_key: str, month: str, scope: str,
                          user_id: int, role_id: int, goals: int, tournament_ids: list[int]) -> dict[str, Any]:
        """Persist an idempotent monthly Golden Boot award."""
        with self._lock:
            now = datetime.now(UTC).isoformat()
            self.connection.execute(
                """INSERT INTO golden_boot_awards
                   (guild_id, award_key, month, scope, user_id, role_id, goals, tournament_ids, awarded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(guild_id, award_key) DO UPDATE SET
                     user_id=excluded.user_id, role_id=excluded.role_id, goals=excluded.goals,
                     tournament_ids=excluded.tournament_ids, awarded_at=excluded.awarded_at""",
                (int(guild_id), award_key, month, scope, int(user_id), int(role_id), int(goals), ",".join(map(str, tournament_ids)), now),
            )
            self.connection.commit()
            return {"guild_id": guild_id, "award_key": award_key, "month": month, "scope": scope, "user_id": user_id, "role_id": role_id, "goals": goals, "tournament_ids": ",".join(map(str, tournament_ids)), "awarded_at": now}

    def golden_boot_award_record(self, guild_id: int, award_key: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM golden_boot_awards WHERE guild_id = ? AND award_key = ?", (int(guild_id), award_key))
        return dict(row) if row else None

    def ballon_dor_candidates_for_guild(self, guild_id: int, month: str) -> list[dict[str, Any]]:
        """Return an automatic monthly Ballon d'Or ranking for one guild."""
        month = str(month).strip()
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise ValueError("Month must use YYYY-MM format, for example 2026-09.")
        with self._lock:
            tournaments = [dict(row) for row in self._all(
                """SELECT * FROM tournaments
                   WHERE guild_id = ? AND status = 'completed'
                     AND substr(COALESCE(registration_closed_at, created_at), 1, 7) = ?
                     AND tournament_type <> ?
                   ORDER BY id ASC""",
                (int(guild_id), month, ROUND_ROBIN),
            )]
            stats: dict[int, dict[str, Any]] = {}

            def ensure_player(uid: int, name: str, nation: str | None) -> dict[str, Any]:
                row = stats.setdefault(uid, {
                    'user_id': uid, 'display_name': name or f'Player {uid}',
                    'nation_name': nation, 'goals': 0, 'wins': 0,
                    'championships': 0, 'runner_ups': 0, 'semifinals': 0,
                    'score': 0, 'tournaments_count': 0,
                })
                if nation:
                    row['nation_name'] = nation
                return row

            for tournament in tournaments:
                tid = int(tournament['id'])
                players = {int(p['user_id']): p for p in self.players(tid)}
                completed = [m for m in self.matches(tid) if m.get('status') == 'completed']
                involved: set[int] = set()
                for match in completed:
                    for side, score_key in ((1, 'score1'), (2, 'score2')):
                        uid = match.get(f'player{side}_id')
                        if uid is None:
                            continue
                        uid = int(uid); involved.add(uid)
                        p = players.get(uid, {})
                        row = ensure_player(uid, str(p.get('display_name') or f'Player {uid}'), p.get('nation_name'))
                        row['goals'] += int(match.get(score_key) or 0)
                        if match.get('score1') is not None and match.get('score2') is not None:
                            other = 'score2' if side == 1 else 'score1'
                            if int(match[score_key]) > int(match[other]):
                                row['wins'] += 1
                for uid in involved:
                    ensure_player(uid, str(players.get(uid, {}).get('display_name') or f'Player {uid}'), players.get(uid, {}).get('nation_name'))['tournaments_count'] += 1

                placements: dict[int, tuple[str, int]] = {}
                if tournament.get('tournament_type') == GROUP_KNOCKOUT:
                    try:
                        result = self._validate_completed_champions_result(tid)
                        placements = {int(uid): (place, int(points)) for uid, (place, points) in result['placements'].items()}
                    except ValueError:
                        # A completed legacy tournament may not have a fully
                        # shaped bracket. Goals/wins remain valid; don't make
                        # the whole monthly ranking fail because placement
                        # metadata cannot be safely inferred.
                        placements = {}
                elif tournament.get('tournament_type') == LEAGUE:
                    try:
                        table = self.standings(tid)
                        if table:
                            if len(table) >= 1: placements[int(table[0].user_id)] = ('champion', 10)
                            if len(table) >= 2: placements[int(table[1].user_id)] = ('runner_up', 6)
                            if len(table) >= 3: placements[int(table[2].user_id)] = ('third_place', 3)
                    except Exception:
                        placements = {}
                for uid, (place, bonus) in placements.items():
                    if uid not in players:
                        continue
                    row = ensure_player(uid, str(players[uid].get('display_name') or f'Player {uid}'), players[uid].get('nation_name'))
                    row['score'] += int(bonus)
                    if place == 'champion': row['championships'] += 1
                    elif place == 'runner_up': row['runner_ups'] += 1
                    elif place == 'semifinalist': row['semifinals'] += 1

            for row in stats.values():
                row['score'] += int(row['goals']) + int(row['wins'])
                row['placement_bonus'] = int(row['score']) - int(row['goals']) - int(row['wins'])
            rows = list(stats.values())
            rows.sort(key=lambda r: (-int(r['score']), -int(r['goals']), -int(r['wins']), str(r['display_name']).casefold(), int(r['user_id'])))
            for index, row in enumerate(rows, 1):
                row['rank'] = index
            return rows

    def ballon_dor_award(self, guild_id: int, month: str, user_id: int, role_id: int, score: int) -> dict[str, Any]:
        month = str(month).strip()
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise ValueError("Month must use YYYY-MM format, for example 2026-09.")
        with self._lock:
            now = datetime.now(UTC).isoformat()
            self.connection.execute(
                """INSERT INTO ballon_dor_awards(guild_id, month, user_id, role_id, score, awarded_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(guild_id, month) DO UPDATE SET
                     user_id=excluded.user_id, role_id=excluded.role_id, score=excluded.score, awarded_at=excluded.awarded_at""",
                (int(guild_id), month, int(user_id), int(role_id), int(score), now),
            )
            self.connection.commit()
            return {'guild_id': int(guild_id), 'month': month, 'user_id': int(user_id), 'role_id': int(role_id), 'score': int(score), 'awarded_at': now}

    def ballon_dor_award_record(self, guild_id: int, month: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM ballon_dor_awards WHERE guild_id = ? AND month = ?", (int(guild_id), str(month)))
        return dict(row) if row else None

    def report_result(
        self,
        match_id: int,
        reporter_id: int,
        score1: int,
        score2: int,
    ) -> dict[str, Any]:
        return self._record_result(match_id, reporter_id, score1, score2, moderator=False)

    def set_result(
        self,
        guild_id: int,
        match_id: int,
        moderator_id: int,
        score1: int,
        score2: int,
    ) -> dict[str, Any]:
        with self._lock:
            match = self._one(
                """
                SELECT matches.*, tournaments.guild_id, tournaments.tournament_type
                FROM matches JOIN tournaments ON tournaments.id = matches.tournament_id
                WHERE matches.id = ?
                """,
                (match_id,),
            )
            if not match or int(match["guild_id"]) != guild_id:
                raise TournamentError("That match does not belong to this server.")
            return self._record_result(
                match_id, moderator_id, score1, score2, moderator=True
            )

    def _record_result(
        self,
        match_id: int,
        reporter_id: int,
        score1: int,
        score2: int,
        *,
        moderator: bool,
    ) -> dict[str, Any]:
        self._validate_score(score1, score2, allow_draw=True)
        with self._lock:
            match = self._one("SELECT * FROM matches WHERE id = ?", (match_id,))
            if not match:
                raise TournamentError("Match not found.")
            if not moderator and reporter_id not in (
                int(match["player1_id"]),
                int(match["player2_id"]),
            ):
                raise TournamentError("You can only report matches you are playing.")
            tournament = self.get_tournament(int(match["tournament_id"]))
            if not tournament or tournament["status"] not in (CLOSED, COMPLETED):
                raise TournamentError("This tournament is not accepting results.")
            is_knockout_stage = match["stage"] == "round_robin" or (
                tournament["tournament_type"] == GROUP_KNOCKOUT
                and match["stage"]
                in self._group_knockout_shape_for(int(match["tournament_id"]))[
                    "knockout_stages"
                ]
            )
            if is_knockout_stage:
                if score1 == score2:
                    raise TournamentError("Knockout matches cannot end in a draw.")

            # Moderators can set/correct a result immediately. Player reports
            # use a two-party confirmation flow: the first player submits the
            # proposed score, and the opponent must submit exactly the same
            # score before the fixture is completed and the next round starts.
            if not moderator:
                existing_reporter = match["reported_by"]

                if match["status"] == "completed":
                    raise TournamentError("This match has already been confirmed and completed.")

                if existing_reporter is None:
                    self.connection.execute(
                        """
                        UPDATE matches
                        SET score1 = ?, score2 = ?, reported_by = ?, completed_at = NULL,
                            dispute_channel_id = NULL, disputed_at = NULL
                        WHERE id = ?
                        """,
                        (score1, score2, reporter_id, match_id),
                    )
                    result = dict(self._one("SELECT * FROM matches WHERE id = ?", (match_id,)))
                    result["event"] = {
                        "type": "awaiting_confirmation",
                        "reporter_id": reporter_id,
                    }
                    return result

                if int(existing_reporter) == reporter_id:
                    raise TournamentError(
                        "Your score is already submitted. Waiting for your opponent to confirm it."
                    )

                # The opponent is confirming the first player's submitted score.
                if match["score1"] is None or match["score2"] is None:
                    raise TournamentError("No score is waiting for confirmation.")

                if int(match["score1"]) != score1 or int(match["score2"]) != score2:
                    raise TournamentError(
                        "Your score does not match the submitted result. "
                        "Both players must agree on the exact score. Ask a moderator to resolve a dispute."
                    )

            was_completed = match["status"] == "completed"
            self.connection.execute(
                """
                UPDATE matches
                SET score1 = ?, score2 = ?, status = 'completed',
                    reported_by = ?, completed_at = ?,
                    dispute_channel_id = NULL, disputed_at = NULL
                WHERE id = ?
                """,
                (score1, score2, reporter_id, self._now(), match_id),
            )
            # Goal records are derived from official completed results, so a
            # moderator correction automatically replaces the affected
            # tournament's totals.  KO Match is ignored by the rebuild.
            self._rebuild_goal_records_for_tournament(int(match["tournament_id"]))
            if was_completed and (
                tournament["tournament_type"] == GROUP_KNOCKOUT
                or match["stage"] == "round_robin"
            ):
                self._delete_downstream_matches(
                    int(match["tournament_id"]), str(match["stage"])
                )
                # Correcting a completed match anywhere before the final stage
                # deletes at least one downstream match above (see
                # _delete_downstream_matches), so the tournament can no longer
                # be considered complete -- reset it to 'closed' so results
                # can keep being reported. Correcting the final itself needs
                # no explicit reset: nothing is downstream of it, and
                # _advance_if_ready() below will simply recompute the (still
                # complete) final stage and set status back to 'completed'
                # with the corrected champion.
                non_final_stages = ("group",) + tuple(
                    self._group_knockout_shape_for(int(match["tournament_id"]))[
                        "knockout_stages"
                    ][:-1]
                )
                if match["stage"] in non_final_stages:
                    self.connection.execute(
                        "UPDATE tournaments SET status = 'closed' WHERE id = ?",
                        (int(match["tournament_id"]),),
                    )
            event = self._advance_if_ready(int(match["tournament_id"]))
            result = dict(self._one("SELECT * FROM matches WHERE id = ?", (match_id,)))
            result["event"] = event
            return result

    @staticmethod
    def _validate_score(score1: int, score2: int, *, allow_draw: bool) -> None:
        if not isinstance(score1, int) or not isinstance(score2, int):
            raise TournamentError("Scores must be whole numbers.")
        if score1 < 0 or score2 < 0 or score1 > 99 or score2 > 99:
            raise TournamentError("Scores must be whole numbers from 0 to 99.")
        if not allow_draw and score1 == score2:
            raise TournamentError("This match cannot end in a draw.")

    def _find_manageable(
        self, guild_id: int, tournament_id: int | None
    ) -> dict[str, Any]:
        tournament = (
            self.get_tournament(tournament_id)
            if tournament_id is not None
            else self.current_tournament(guild_id, (DRAFT,))
        )
        if not tournament or int(tournament["guild_id"]) != guild_id:
            raise TournamentError("No matching tournament was found.")
        return tournament

    def _insert_knockout_round(
        self, tournament_id: int, round_number: int, player_ids: list[int]
    ) -> int:
        """Insert one single-elimination round and return actual match count."""
        players = list(player_ids)
        random.shuffle(players)
        created = 0
        for index in range(0, len(players) - 1, 2):
            player1, player2 = players[index], players[index + 1]
            self.connection.execute(
                """
                INSERT INTO matches
                    (tournament_id, stage, group_name, round_number,
                     player1_id, player2_id, created_at)
                VALUES (?, 'round_robin', NULL, ?, ?, ?, ?)
                """,
                (tournament_id, round_number, player1, player2, self._now()),
            )
            created += 1
        return created

    def _knockout_round_matches(
        self, tournament_id: int, round_number: int
    ) -> list[dict[str, Any]]:
        return self.matches(
            tournament_id, stage="round_robin"
        ) and [
            row
            for row in self.matches(tournament_id, stage="round_robin")
            if int(row["round_number"]) == round_number
        ] or []

    def _round_candidates(self, tournament_id: int, round_number: int) -> list[int]:
        """Return all players entering a knockout round, including byes."""
        if round_number <= 1:
            return [int(row["user_id"]) for row in self.players(tournament_id)]

        previous = self._round_candidates(tournament_id, round_number - 1)
        previous_matches = [
            row for row in self.matches(tournament_id, stage="round_robin")
            if int(row["round_number"]) == round_number - 1
        ]
        played = {
            int(row["player1_id"]) for row in previous_matches
        } | {
            int(row["player2_id"]) for row in previous_matches
        }
        # Players who entered the previous round but had no opponent received
        # a bye and therefore advance automatically.
        byes = [player_id for player_id in previous if player_id not in played]
        winners = [self._winner(row) for row in previous_matches if row["status"] == "completed"]
        return winners + byes

    def _advance_single_elimination(self, tournament_id: int) -> dict[str, Any] | None:
        rows = self.matches(tournament_id, stage="round_robin")
        if not rows:
            return None
        current_round = max(int(row["round_number"]) for row in rows)
        current = [row for row in rows if int(row["round_number"]) == current_round]
        if any(row["status"] != "completed" for row in current):
            return None

        candidates = self._round_candidates(tournament_id, current_round)
        played = {
            int(row["player1_id"]) for row in current
        } | {
            int(row["player2_id"]) for row in current
        }
        winners = [self._winner(row) for row in current]
        winners.extend(player_id for player_id in candidates if player_id not in played)

        if len(winners) == 1:
            champion_id = winners[0]
            champion = next(
                str(row.get("nation_name") or row["display_name"])
                for row in self.players(tournament_id)
                if int(row["user_id"]) == champion_id
            )
            self.connection.execute(
                "UPDATE tournaments SET status = 'completed' WHERE id = ?",
                (tournament_id,),
            )
            return {"type": "champion", "name": champion, "user_id": champion_id}

        next_round = current_round + 1
        existing = [
            row for row in rows if int(row["round_number"]) == next_round
        ]
        if existing:
            return None
        created = self._insert_knockout_round(
            tournament_id, next_round, winners
        )
        return {
            "type": "stage",
            "name": f"Round {next_round}",
            "round": next_round,
            "fixtures": created,
            "players": len(winners),
        }

    def _insert_fixtures(
        self,
        tournament_id: int,
        stage: str,
        group_name: str | None,
        player_ids: list[int],
    ) -> int:
        fixtures = generate_round_robin_fixtures(player_ids)
        created = 0
        for player1, player2 in fixtures:
            duplicate = self._one(
                """
                SELECT 1 FROM matches
                WHERE tournament_id = ? AND stage = ?
                  AND COALESCE(group_name, '') = COALESCE(?, '')
                  AND player1_id = ? AND player2_id = ?
                """,
                (tournament_id, stage, group_name, player1, player2),
            )
            if not duplicate:
                self.connection.execute(
                    """
                    INSERT INTO matches
                        (tournament_id, stage, group_name, player1_id, player2_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (tournament_id, stage, group_name, player1, player2, self._now()),
                )
                created += 1
        return created

    def _group_knockout_shape_for(self, tournament_id: int) -> dict[str, Any]:
        """Return this tournament's group/knockout shape.

        Derived from its template when one is set (Weekly Championship,
        Champions League, World Cup 32/48/64 all provide group_count/
        group_sizes/qualify_per_group/knockout_stages). Falls back to the
        original fixed 4-groups-of-4 / quarterfinal-semifinal-final shape
        for any GROUP_KNOCKOUT tournament created without a template
        (the only shape that existed before templates did, and still the
        only shape reachable via manual/no-template creation today).
        """
        tournament = self.get_tournament(tournament_id)
        template = TEMPLATES.get(tournament.get("template_id")) if tournament else None
        if template and template.get("tournament_type") == GROUP_KNOCKOUT and "group_count" in template:
            return {
                "groups": list(GROUP_LETTERS[: template["group_count"]]),
                "group_sizes": list(template["group_sizes"]),
                "qualify_per_group": template["qualify_per_group"],
                "knockout_stages": tuple(template["knockout_stages"]),
            }
        return {
            "groups": list(GROUP_LETTERS[:4]),
            "group_sizes": [4, 4, 4, 4],
            "qualify_per_group": 2,
            "knockout_stages": ("quarterfinal", "semifinal", "final"),
        }

    def knockout_shape_for(self, tournament_id: int) -> dict[str, Any]:
        """Public alias for _group_knockout_shape_for.

        bot.py needs the tournament's actual group/knockout shape (groups,
        knockout_stages) so it stops assuming every GROUP_KNOCKOUT
        tournament is the original fixed 4-group/quarterfinal-semifinal-
        final shape. Exposed as a real public method rather than having
        bot.py reach across the module boundary to the underscore-prefixed
        one.
        """
        return self._group_knockout_shape_for(tournament_id)

    def _generate_group_stage(self, tournament_id: int, players: list[dict[str, Any]]) -> int:
        # Randomize group assignment only after registration has closed.
        shape = self._group_knockout_shape_for(tournament_id)
        randomized = list(players)
        random.shuffle(randomized)
        count = 0
        offset = 0
        for group, size in zip(shape["groups"], shape["group_sizes"]):
            group_players = randomized[offset : offset + size]
            offset += size
            for player in group_players:
                self.connection.execute(
                    """
                    UPDATE tournament_players SET group_name = ?
                    WHERE tournament_id = ? AND user_id = ?
                    """,
                    (group, tournament_id, player["user_id"]),
                )
            count += self._insert_fixtures(
                tournament_id,
                "group",
                group,
                [int(player["user_id"]) for player in group_players],
            )
        # Record the 48-hour group-stage deadline on the tournament itself so
        # it survives restarts/redeploys and can back future reminders. This
        # is set once, when fixtures are generated; correcting a result later
        # does not push the deadline back.
        deadline = self._set_stage_deadline(
            tournament_id, "group", GROUP_STAGE_DEADLINE_HOURS
        )
        return count

    def _set_stage_deadline(self, tournament_id: int, stage: str, hours: int) -> str:
        """Set a stage deadline when that stage's fixtures are created."""
        column_by_stage = {
            "group": "group_stage_deadline_at",
            "league": "league_deadline_at",
            "round_of_32": "round_of_32_deadline_at",
            "round_of_16": "round_of_16_deadline_at",
            "quarterfinal": "quarterfinal_deadline_at",
            "semifinal": "semifinal_deadline_at",
            "final": "final_deadline_at",
        }
        column = column_by_stage.get(stage)
        if column is None:
            raise ValueError(f"Unsupported deadline stage: {stage}")
        deadline = (datetime.now(UTC) + timedelta(hours=hours)).isoformat()
        self.connection.execute(
            f"UPDATE tournaments SET {column} = ? WHERE id = ?",
            (deadline, tournament_id),
        )
        return deadline

    def stage_deadline(self, tournament_id: int, stage: str) -> str | None:
        """Return the stored deadline for a tournament stage."""
        column_by_stage = {
            "group": "group_stage_deadline_at",
            "league": "league_deadline_at",
            "round_of_32": "round_of_32_deadline_at",
            "round_of_16": "round_of_16_deadline_at",
            "quarterfinal": "quarterfinal_deadline_at",
            "semifinal": "semifinal_deadline_at",
            "final": "final_deadline_at",
        }
        column = column_by_stage.get(stage)
        if column is None:
            return None
        row = self._one(
            f"SELECT {column} AS deadline_at FROM tournaments WHERE id = ?",
            (tournament_id,),
        )
        return str(row["deadline_at"]) if row and row["deadline_at"] else None

    def _stage_has_matches(self, tournament_id: int, stage: str) -> bool:
        return bool(
            self._one(
                "SELECT 1 FROM matches WHERE tournament_id = ? AND stage = ? LIMIT 1",
                (tournament_id, stage),
            )
        )

    def _stage_complete(self, tournament_id: int, stage: str) -> bool:
        rows = self._all(
            "SELECT status FROM matches WHERE tournament_id = ? AND stage = ?",
            (tournament_id, stage),
        )
        return bool(rows) and all(row["status"] == "completed" for row in rows)

    def _advance_if_ready(self, tournament_id: int) -> dict[str, Any] | None:
        tournament = self.get_tournament(tournament_id)
        if not tournament:
            return None
        ttype = tournament["tournament_type"]
        if ttype == ROUND_ROBIN:
            return self._advance_single_elimination(tournament_id)
        if ttype == LEAGUE:
            return self._advance_league(tournament_id)
        if ttype != GROUP_KNOCKOUT:
            return None

        shape = self._group_knockout_shape_for(tournament_id)
        groups = shape["groups"]
        qualify_n = shape["qualify_per_group"]
        stages = shape["knockout_stages"]
        first_stage = stages[0]

        if self._stage_complete(tournament_id, "group") and not self._stage_has_matches(
            tournament_id, first_stage
        ):
            qualifiers: dict[str, list[Standing]] = {
                group: self.standings(tournament_id, group)[:qualify_n] for group in groups
            }
            matchups = self._build_first_knockout_matchups(groups, qualifiers, qualify_n)
            self._insert_knockout_matches(tournament_id, first_stage, matchups)
            deadline = self._set_stage_deadline(
                tournament_id, first_stage, self._knockout_deadline_hours(first_stage)
            )
            return {
                "type": "qualified",
                "stage": first_stage,
                "deadline_at": deadline,
                "qualifiers": [
                    row.display_name for group in groups for row in qualifiers[group]
                ],
                "qualifier_ids": [
                    row.user_id for group in groups for row in qualifiers[group]
                ],
            }

        for index in range(len(stages) - 1):
            stage = stages[index]
            next_stage = stages[index + 1]
            if not self._stage_complete(tournament_id, stage):
                continue
            if self._stage_has_matches(tournament_id, next_stage):
                continue
            # Winners advance in the stable order their matches were created
            # in, so e.g. Round of 16 match 1's winner and match 2's winner
            # meet in the next quarterfinal slot, and so on down the bracket.
            current_matches = sorted(
                self.matches(tournament_id, stage=stage), key=lambda row: int(row["id"])
            )
            winners = [self._winner(row) for row in current_matches]
            matchups = [
                (winners[i], winners[i + 1]) for i in range(0, len(winners) - 1, 2)
            ]
            self._insert_knockout_matches(tournament_id, next_stage, matchups)
            deadline = self._set_stage_deadline(
                tournament_id, next_stage, self._knockout_deadline_hours(next_stage)
            )
            display_names = {
                int(player["user_id"]): str(player.get("nation_name") or player["display_name"])
                for player in self.players(tournament_id)
            }
            return {
                "type": "stage",
                "stage": next_stage,
                "name": next_stage.replace("_", " ").title(),
                "deadline_at": deadline,
                "qualifier_ids": winners,
                "qualifiers": [display_names.get(winner_id, str(winner_id)) for winner_id in winners],
            }

        last_stage = stages[-1]
        if self._stage_complete(tournament_id, last_stage):
            final = self.matches(tournament_id, stage=last_stage)[0]
            champion_id = self._winner(final)
            champion = next(
                row["display_name"]
                for row in self.players(tournament_id)
                if int(row["user_id"]) == champion_id
            )
            self.connection.execute(
                "UPDATE tournaments SET status = 'completed' WHERE id = ?",
                (tournament_id,),
            )
            return {
                "type": "champion",
                "name": champion,
                "user_id": champion_id,
            }
        return None

    def _advance_league(self, tournament_id: int) -> dict[str, Any] | None:
        """Complete a LEAGUE (e.g. Premier League) tournament once every
        fixture has been played, crowning whoever tops the final table."""
        if not self._stage_complete(tournament_id, "league"):
            return None
        table = self.standings(tournament_id)
        if not table:
            return None
        champion = table[0]
        self.connection.execute(
            "UPDATE tournaments SET status = 'completed' WHERE id = ?",
            (tournament_id,),
        )
        return {
            "type": "champion",
            "name": champion.display_name,
            "user_id": champion.user_id,
        }

    @staticmethod
    def _build_first_knockout_matchups(
        groups: list[str], qualifiers: dict[str, list[Standing]], qualify_n: int
    ) -> list[tuple[int, int]]:
        """Pair group-stage qualifiers for the first knockout round.

        For the standard case (exactly 2 qualifiers per group, an even
        number of groups), consecutive groups are paired and crossed —
        group i's winner faces group (i+1)'s runner-up and vice versa —
        so nobody meets a group-stage opponent again in the very next round.
        This is the same pairing Weekly Championship always used, just
        generalized from exactly 4 groups to any even count (8 for
        Champions League/World Cup 32, 16 for World Cup 48/64).
        """
        if qualify_n != 2 or len(groups) % 2 != 0:
            # No defined convention for this shape (e.g. an odd group count,
            # or more than 2 qualifiers per group) — fall back to a simple
            # sequential pairing rather than guessing a bracket structure.
            flat = [row.user_id for group in groups for row in qualifiers[group]]
            return [(flat[i], flat[i + 1]) for i in range(0, len(flat) - 1, 2)]
        matchups: list[tuple[int, int]] = []
        for i in range(0, len(groups), 2):
            group_a, group_b = groups[i], groups[i + 1]
            winner_a, runner_up_a = qualifiers[group_a][0].user_id, qualifiers[group_a][1].user_id
            winner_b, runner_up_b = qualifiers[group_b][0].user_id, qualifiers[group_b][1].user_id
            matchups.append((winner_a, runner_up_b))
            matchups.append((winner_b, runner_up_a))
        return matchups

    @staticmethod
    def _knockout_deadline_hours(stage: str) -> int:
        return {
            "round_of_32": ROUND_OF_32_DEADLINE_HOURS,
            "round_of_16": ROUND_OF_16_DEADLINE_HOURS,
            "quarterfinal": QUARTERFINAL_DEADLINE_HOURS,
            "semifinal": SEMIFINAL_DEADLINE_HOURS,
            "final": FINAL_DEADLINE_HOURS,
        }.get(stage, QUARTERFINAL_DEADLINE_HOURS)

    def _insert_knockout_matches(
        self, tournament_id: int, stage: str, matchups: list[tuple[int, int]]
    ) -> None:
        for player1, player2 in matchups:
            self.connection.execute(
                """
                INSERT INTO matches
                    (tournament_id, stage, player1_id, player2_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (tournament_id, stage, player1, player2, self._now()),
            )

    def _delete_downstream_matches(self, tournament_id: int, stage: str) -> None:
        if stage == "group":
            stages = self._group_knockout_shape_for(tournament_id)["knockout_stages"]
        elif stage in self._group_knockout_shape_for(tournament_id)["knockout_stages"]:
            all_stages = self._group_knockout_shape_for(tournament_id)["knockout_stages"]
            stages = all_stages[all_stages.index(stage) + 1 :]
        elif stage == "round_robin":
            # For single elimination, remove rounds after the corrected round.
            # The current round is preserved.
            current = self._one(
                "SELECT MAX(round_number) AS round_number FROM matches WHERE tournament_id = ? AND stage = 'round_robin' AND status = 'completed'",
                (tournament_id,),
            )
            # The caller has already identified the corrected stage; deleting
            # all later rounds is safer than leaving stale advancement matches.
            # The current round number is inferred from the most recent completed
            # match and any later pending rounds are removed.
            if current and current["round_number"] is not None:
                round_number = int(current["round_number"])
                self.connection.execute(
                    "DELETE FROM matches WHERE tournament_id = ? AND stage = 'round_robin' AND round_number > ?",
                    (tournament_id, round_number),
                )
            return
        elif stage == "league":
            # A flat all-play-all league has no downstream stage to cascade to.
            return
        else:
            stages = ()
        if stages:
            placeholders = ",".join("?" for _ in stages)
            self.connection.execute(
                f"DELETE FROM matches WHERE tournament_id = ? AND stage IN ({placeholders})",
                (tournament_id, *stages),
            )

    def _validate_completed_flat_champion(
        self, tournament_id: int, tournament_type: str
    ) -> dict[str, Any]:
        """Identify the champion of a LEAGUE or ROUND_ROBIN (KO Match)
        tournament for Champions-leaderboard crediting.

        Unlike GROUP_KNOCKOUT's fixed semifinal/final bracket, neither of
        these has a stable stage structure to validate against (a league is
        one flat table; a KO Match bracket's depth varies with player
        count), so only the champion is credited — no runner-up or
        semifinalist placements.
        """
        if tournament_type == LEAGUE:
            table = self.standings(tournament_id)
            if not table:
                raise ValueError(
                    f"Tournament {tournament_id}: no completed league standings to award."
                )
            champion_id = table[0].user_id
        else:
            completed = [
                row for row in self.matches(tournament_id, stage="round_robin")
                if row.get("status") == "completed"
            ]
            if not completed:
                raise ValueError(
                    f"Tournament {tournament_id}: no completed matches to award."
                )
            final_round = max(int(row["round_number"]) for row in completed)
            finals = [row for row in completed if int(row["round_number"]) == final_round]
            if len(finals) != 1:
                raise ValueError(
                    f"Tournament {tournament_id}: expected exactly 1 final-round match, found {len(finals)}."
                )
            champion_id = self._winner(finals[0])
        return {
            "champion_id": champion_id,
            "runner_up_id": None,
            "semifinalist_ids": [],
            "placements": {champion_id: ("champion", 3)},
        }

    def _rebuild_goal_records_for_tournament(self, tournament_id: int) -> None:
        """Rebuild one tournament's goal totals from its official results."""
        tournament = self.get_tournament(tournament_id)
        self.connection.execute(
            "DELETE FROM goal_records WHERE tournament_id = ?",
            (int(tournament_id),),
        )
        if not tournament or tournament.get("tournament_type") == ROUND_ROBIN:
            return
        self.connection.execute(
            """
            INSERT INTO goal_records
                (tournament_id, user_id, display_name, nation_name, goals)
            SELECT
                x.tournament_id,
                x.user_id,
                COALESCE(tp.display_name, 'Unknown Player'),
                tp.nation_name,
                SUM(x.goals) AS goals
            FROM (
                SELECT tournament_id, player1_id AS user_id, score1 AS goals
                FROM matches
                WHERE tournament_id = ? AND status = 'completed' AND score1 IS NOT NULL
                UNION ALL
                SELECT tournament_id, player2_id AS user_id, score2 AS goals
                FROM matches
                WHERE tournament_id = ? AND status = 'completed' AND score2 IS NOT NULL
            ) AS x
            LEFT JOIN tournament_players tp
                ON tp.tournament_id = x.tournament_id
               AND tp.user_id = x.user_id
            GROUP BY x.tournament_id, x.user_id, tp.display_name, tp.nation_name
            """,
            (int(tournament_id), int(tournament_id)),
        )

    def _validate_completed_champions_result(
        self, tournament_id: int
    ) -> dict[str, Any]:
        """
        Validate the completed playoff tree before awarding Champions points.

        This deliberately refuses to guess when the existing playoff data is
        incomplete, duplicated, contradictory, or otherwise unsafe.
        """
        finals = self.matches(tournament_id, stage="final")
        semifinals = self.matches(tournament_id, stage="semifinal")

        if len(finals) != 1:
            raise ValueError(
                f"Tournament {tournament_id}: expected exactly 1 final, found {len(finals)}."
            )
        if len(semifinals) != 2:
            raise ValueError(
                f"Tournament {tournament_id}: expected exactly 2 semifinals, found {len(semifinals)}."
            )

        final = finals[0]
        if final.get("status") != "completed":
            raise ValueError(
                f"Tournament {tournament_id}: final is not completed."
            )

        final_p1 = final.get("player1_id")
        final_p2 = final.get("player2_id")
        if final_p1 is None or final_p2 is None or int(final_p1) == int(final_p2):
            raise ValueError(
                f"Tournament {tournament_id}: final has invalid participants."
            )

        champion_id = self._winner(final)
        if champion_id not in {int(final_p1), int(final_p2)}:
            raise ValueError(
                f"Tournament {tournament_id}: final winner is not one of the finalists."
            )

        runner_up_id = int(final_p2) if int(final_p1) == champion_id else int(final_p1)

        semifinalists: list[int] = []
        semifinal_winners: list[int] = []

        for index, match in enumerate(semifinals, 1):
            if match.get("status") != "completed":
                raise ValueError(
                    f"Tournament {tournament_id}: semifinal {index} is not completed."
                )

            p1 = match.get("player1_id")
            p2 = match.get("player2_id")
            if p1 is None or p2 is None or int(p1) == int(p2):
                raise ValueError(
                    f"Tournament {tournament_id}: semifinal {index} has invalid participants."
                )

            p1, p2 = int(p1), int(p2)
            semifinalists.extend([p1, p2])
            winner = self._winner(match)
            if winner not in {p1, p2}:
                raise ValueError(
                    f"Tournament {tournament_id}: semifinal {index} has invalid winner."
                )
            semifinal_winners.append(winner)

        if len(set(semifinalists)) != 4:
            raise ValueError(
                f"Tournament {tournament_id}: semifinals do not contain exactly four unique players."
            )

        # Both finalists must come from the semifinal winners.
        if set([champion_id, runner_up_id]) != set(semifinal_winners):
            raise ValueError(
                f"Tournament {tournament_id}: final participants do not match the semifinal winners."
            )

        losing_semifinalists = [
            uid for uid in semifinalists
            if uid not in {champion_id, runner_up_id}
        ]

        if len(losing_semifinalists) != 2 or len(set(losing_semifinalists)) != 2:
            raise ValueError(
                f"Tournament {tournament_id}: could not identify exactly two losing semifinalists."
            )

        placements = {
            champion_id: ("champion", 3),
            runner_up_id: ("runner_up", 2),
            losing_semifinalists[0]: ("semifinalist", 1),
            losing_semifinalists[1]: ("semifinalist", 1),
        }

        if len(placements) != 4:
            raise ValueError(
                f"Tournament {tournament_id}: placement identities are not unique."
            )

        return {
            "champion_id": champion_id,
            "runner_up_id": runner_up_id,
            "semifinalist_ids": losing_semifinalists,
            "placements": placements,
        }

    def award_champions_for_completed_tournament(
        self, tournament_id: int
    ) -> dict[str, Any] | None:
        """Award Champions leaderboard points for Weekly Championship only.

        The Champions leaderboard is intentionally exclusive to the Weekly
        Championship. Premier League, KO Match, World Cup, and other
        tournaments can still announce their champions / Hall of Fame result,
        but they must never create Champions leaderboard awards.
        """
        tournament = self.get_tournament(tournament_id)
        if not tournament:
            raise ValueError(f"Tournament {tournament_id} not found.")
        if tournament.get("template_id") != TEMPLATE_WEEKLY_CHAMPIONSHIP:
            return None
        if tournament["tournament_type"] != GROUP_KNOCKOUT:
            return None
        if tournament["tournament_type"] == GROUP_KNOCKOUT:
            # Every GROUP_KNOCKOUT template's knockout_stages ends in
            # ("...", "semifinal", "final"), so this bracket-shaped
            # validator (which awards champion/runner-up/semifinalist
            # placements) applies unchanged regardless of how many earlier
            # knockout rounds (Round of 32/16) came before the semifinal.
            result = self._validate_completed_champions_result(tournament_id)
        else:
            # LEAGUE (Premier League) and ROUND_ROBIN ("KO Match") have no
            # fixed semifinal/final structure to validate against — a
            # league is one flat table, and a KO Match bracket's depth
            # varies with player count. Both simply award the champion.
            result = self._validate_completed_flat_champion(tournament_id, tournament["tournament_type"])

        expected = {
            int(uid): (placement, points)
            for uid, (placement, points) in result["placements"].items()
        }

        with self._lock:
            try:
                self.connection.execute("BEGIN")

                existing = self._all(
                    """
                    SELECT discord_user_id, placement, points
                    FROM champion_awards
                    WHERE tournament_id = ?
                    """,
                    (int(tournament_id),),
                )
                existing_rows = [dict(row) for row in existing]

                if existing_rows:
                    actual = {
                        int(row["discord_user_id"]): (
                            row["placement"], int(row["points"])
                        )
                        for row in existing_rows
                    }
                    if actual != expected:
                        raise ValueError(
                            f"Tournament {tournament_id}: existing Champions awards "
                            "do not exactly match the completed playoff result."
                        )
                    self.connection.execute("ROLLBACK")
                    return result

                now = datetime.now(UTC).isoformat()

                for user_id, (placement, points) in expected.items():
                    self.connection.execute(
                        """
                        INSERT INTO champion_awards(
                            tournament_id, discord_user_id, placement, points, awarded_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (int(tournament_id), int(user_id), placement, points, now),
                    )

                for user_id, (placement, points) in expected.items():
                    self.connection.execute(
                        """
                        INSERT INTO champions_totals(
                            discord_user_id, total_points, championships,
                            runner_ups, semifinal_appearances
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(discord_user_id) DO UPDATE SET
                            total_points = champions_totals.total_points + excluded.total_points,
                            championships = champions_totals.championships + excluded.championships,
                            runner_ups = champions_totals.runner_ups + excluded.runner_ups,
                            semifinal_appearances =
                                champions_totals.semifinal_appearances + excluded.semifinal_appearances
                        """,
                        (
                            int(user_id),
                            int(points),
                            1 if placement == "champion" else 0,
                            1 if placement == "runner_up" else 0,
                            1 if placement == "semifinalist" else 0,
                        ),
                    )

                self.connection.execute("COMMIT")
                return result

            except Exception:
                try:
                    self.connection.execute("ROLLBACK")
                except Exception:
                    pass
                raise

    def backfill_champions_for_completed_tournament(
        self, tournament_id: int
    ) -> dict[str, Any]:
        """
        One-time, idempotent backfill for a tournament whose final was already
        completed before the Champions feature was installed.

        This reads existing playoff results only and never changes match results.
        """
        with self._lock:
            tournament = self.get_tournament(tournament_id)
            if not tournament:
                raise ValueError(f"Tournament {tournament_id} not found.")
            if tournament.get("template_id") != TEMPLATE_WEEKLY_CHAMPIONSHIP:
                raise ValueError(
                    f"Tournament {tournament_id} is not a Weekly Championship; "
                    "Champions leaderboard backfill is only available for Weekly Championship tournaments."
                )

            result = self._validate_completed_champions_result(tournament_id)

            existing = self._all(
                """
                SELECT discord_user_id, placement, points
                FROM champion_awards
                WHERE tournament_id = ?
                """,
                (int(tournament_id),),
            )
            existing_rows = [dict(row) for row in existing]
            expected = {
                int(uid): (placement, points)
                for uid, (placement, points) in result["placements"].items()
            }

            if existing_rows:
                actual = {
                    int(row["discord_user_id"]): (
                        row["placement"], int(row["points"])
                    )
                    for row in existing_rows
                }
                if actual != expected:
                    raise ValueError(
                        f"Tournament {tournament_id}: existing Champions awards "
                        "conflict with the actual playoff result. No changes made."
                    )
                return {
                    **result,
                    "already_awarded": True,
                    "points_awarded": 0,
                }

            # Reuse the same safe awarding path. This does not touch match data.
            self.award_champions_for_completed_tournament(tournament_id)
            return {
                **result,
                "already_awarded": False,
                "points_awarded": 7,
            }

    def current_champion_title_holder(self, guild_id: int, title_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._one(
                """SELECT guild_id, title_key, role_id, user_id, updated_at
                   FROM champion_title_holders
                  WHERE guild_id = ? AND title_key = ?""",
                (int(guild_id), str(title_key)),
            )
            return dict(row) if row else None

    def set_current_champion_title_holder(self, guild_id: int, title_key: str, role_id: int, user_id: int) -> None:
        with self._lock:
            self.connection.execute(
                """INSERT INTO champion_title_holders(guild_id, title_key, role_id, user_id, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(guild_id, title_key) DO UPDATE SET
                       role_id = excluded.role_id,
                       user_id = excluded.user_id,
                       updated_at = excluded.updated_at""",
                (int(guild_id), str(title_key), int(role_id), int(user_id), datetime.now(UTC).isoformat()),
            )
            self.connection.commit()

    def champions_leaderboard(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._all(
                """
                SELECT discord_user_id, total_points, championships,
                       runner_ups, semifinal_appearances
                FROM champions_totals
                WHERE total_points > 0
                ORDER BY total_points DESC,
                         championships DESC,
                         runner_ups DESC,
                         semifinal_appearances DESC,
                         discord_user_id ASC
                LIMIT 20
                """
            )
            return [dict(row) for row in rows]

    @staticmethod
    def _winner(match: dict[str, Any]) -> int:
        if match["score1"] is None or match["score2"] is None:
            raise TournamentError("A knockout match is missing a result.")
        if int(match["score1"]) == int(match["score2"]):
            raise TournamentError("A knockout match cannot have a tied result.")
        return int(match["player1_id"] if match["score1"] > match["score2"] else match["player2_id"])