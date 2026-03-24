"""SQLite interaction store — platform-agnostic logging for the brain pipeline."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "interactions.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    source TEXT,
    stimulus_uri TEXT,
    stimulus_text TEXT,
    stimulus_author TEXT,
    triage_engage INTEGER,
    triage_reason TEXT,
    triage_queries TEXT,
    the_problem TEXT,
    passages_retrieved TEXT,
    passage_used TEXT,
    composition_decision TEXT,
    composition_mode TEXT,
    posts TEXT,
    response_uris TEXT,
    skip_reason TEXT,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    dry_run INTEGER DEFAULT 0,
    published INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date TEXT PRIMARY KEY,
    total_triaged INTEGER DEFAULT 0,
    total_engaged INTEGER DEFAULT 0,
    total_posted INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY,
    chunk_id TEXT NOT NULL,
    poet TEXT,
    poem_title TEXT,
    interaction_id INTEGER REFERENCES interactions(id),
    timestamp TEXT NOT NULL,
    stimulus_text TEXT,
    collision_note TEXT,
    themes TEXT
);
CREATE INDEX IF NOT EXISTS idx_readings_chunk ON readings(chunk_id);

CREATE TABLE IF NOT EXISTS reflections (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    period TEXT NOT NULL,
    summary TEXT NOT NULL,
    poets_used TEXT,
    themes_used TEXT,
    preoccupations TEXT,
    recommendations TEXT,
    self_notes TEXT
);

CREATE TABLE IF NOT EXISTS passage_notes (
    chunk_id TEXT PRIMARY KEY,
    poet TEXT,
    poem_title TEXT,
    times_used INTEGER DEFAULT 0,
    last_used TEXT,
    note TEXT,
    themes TEXT
);
"""


class Store:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self):
        """Add columns that may be missing from older databases."""
        cursor = self._conn.execute("PRAGMA table_info(interactions)")
        ix_cols = {row[1] for row in cursor.fetchall()}
        if "published" not in ix_cols:
            self._conn.execute("ALTER TABLE interactions ADD COLUMN published INTEGER DEFAULT 0")
            self._conn.commit()

        cursor = self._conn.execute("PRAGMA table_info(reflections)")
        ref_cols = {row[1] for row in cursor.fetchall()}
        if "self_notes" not in ref_cols:
            self._conn.execute("ALTER TABLE reflections ADD COLUMN self_notes TEXT")
            self._conn.commit()

    def close(self):
        self._conn.close()

    def mark_published(self, interaction_ids: list[int]):
        """Set published=1 for the given interaction IDs."""
        if not interaction_ids:
            return
        placeholders = ",".join("?" * len(interaction_ids))
        self._conn.execute(
            f"UPDATE interactions SET published = 1 WHERE id IN ({placeholders})",
            interaction_ids,
        )
        self._conn.commit()

    def log_interaction(
        self,
        *,
        source: str = "unknown",
        stimulus_text: str = "",
        stimulus_uri: str | None = None,
        stimulus_author: str | None = None,
        triage: dict | None = None,
        passages: list[dict] | None = None,
        composition: dict | None = None,
        response_uris: list[str] | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        dry_run: bool = False,
    ) -> int:
        """Log a full brain pipeline result. Returns the row id."""
        now = datetime.now(timezone.utc).isoformat()
        triage = triage or {}
        composition = composition or {}

        engage = 1 if triage.get("engage") else 0
        decision = composition.get("decision")
        posted = 1 if decision == "post" else 0

        passage_used = composition.get("passage_used")
        # For self-compare, store full passage data so both poems are recoverable
        if source == "self_compare" and passages:
            passages_json = json.dumps([
                {k: v for k, v in p.items() if k in ("chunk_id", "poet", "poem_title", "text", "date", "work")}
                for p in passages
            ])
        else:
            chunk_ids = [p["chunk_id"] for p in (passages or []) if "chunk_id" in p]
            passages_json = json.dumps(chunk_ids) if chunk_ids else None

        cur = self._conn.execute(
            """INSERT INTO interactions (
                timestamp, source, stimulus_uri, stimulus_text, stimulus_author,
                triage_engage, triage_reason, triage_queries, the_problem,
                passages_retrieved, passage_used,
                composition_decision, composition_mode, posts, response_uris,
                skip_reason, tokens_in, tokens_out, dry_run
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                now,
                source,
                stimulus_uri,
                stimulus_text,
                stimulus_author,
                engage,
                triage.get("reason"),
                json.dumps(triage.get("search_queries")) if triage.get("search_queries") else None,
                triage.get("the_problem"),
                passages_json,
                json.dumps(passage_used) if passage_used else None,
                decision,
                composition.get("mode"),
                json.dumps(composition.get("posts")) if composition.get("posts") else None,
                json.dumps(response_uris) if response_uris else None,
                composition.get("skip_reason"),
                tokens_in,
                tokens_out,
                1 if dry_run else 0,
            ),
        )
        self._conn.commit()

        # Update daily stats
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._conn.execute(
            """INSERT INTO daily_stats (date, total_triaged, total_engaged, total_posted)
               VALUES (?, 1, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                   total_triaged = total_triaged + 1,
                   total_engaged = total_engaged + excluded.total_engaged,
                   total_posted = total_posted + excluded.total_posted""",
            (today, engage, posted),
        )
        self._conn.commit()

        return cur.lastrowid

    def get_used_chunk_ids(self, hours: int = 48) -> set[str]:
        """Return chunk_ids used in compositions within the last N hours."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = self._conn.execute(
            """SELECT passage_used FROM interactions
               WHERE timestamp > ? AND passage_used IS NOT NULL
                 AND composition_decision = 'post'""",
            (cutoff,),
        ).fetchall()
        ids = set()
        for row in rows:
            try:
                pu = json.loads(row["passage_used"])
                if isinstance(pu, dict) and pu.get("chunk_id"):
                    ids.add(pu["chunk_id"])
            except (json.JSONDecodeError, TypeError):
                pass
        return ids

    def count_responses_last_hour(self) -> int:
        """Count posts (not skips) in the last 60 minutes."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        row = self._conn.execute(
            """SELECT COUNT(*) as n FROM interactions
               WHERE timestamp > ? AND composition_decision = 'post'""",
            (cutoff,),
        ).fetchone()
        return row["n"]

    def count_responses_today(self) -> int:
        """Count posts today (UTC)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self._conn.execute(
            """SELECT total_posted FROM daily_stats WHERE date = ?""",
            (today,),
        ).fetchone()
        return row["total_posted"] if row else 0

    def count_stimulus_responses(self, stimulus_text: str, hours: int = 24) -> int:
        """Count how many times we've composed a response to this stimulus text recently."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        # Match on first 200 chars to catch near-duplicates
        prefix = stimulus_text[:200]
        row = self._conn.execute(
            """SELECT COUNT(*) as n FROM interactions
               WHERE timestamp > ? AND composition_decision = 'post'
                 AND substr(stimulus_text, 1, 200) = ?""",
            (cutoff, prefix),
        ).fetchone()
        return row["n"]

    def get_stats(self, date: str | None = None) -> dict:
        """Get stats for a given date (default: today UTC)."""
        date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self._conn.execute(
            "SELECT * FROM daily_stats WHERE date = ?", (date,)
        ).fetchone()
        if row:
            return dict(row)
        return {"date": date, "total_triaged": 0, "total_engaged": 0, "total_posted": 0}

    def get_token_totals(self, date: str | None = None) -> dict:
        """Get total tokens for a given date."""
        date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self._conn.execute(
            """SELECT COALESCE(SUM(tokens_in), 0) as tokens_in,
                      COALESCE(SUM(tokens_out), 0) as tokens_out
               FROM interactions WHERE timestamp LIKE ?""",
            (f"{date}%",),
        ).fetchone()
        return dict(row)

    # --- Readings (per-interaction annotation) ---

    def log_reading(
        self,
        *,
        chunk_id: str,
        poet: str | None = None,
        poem_title: str | None = None,
        interaction_id: int | None = None,
        stimulus_text: str | None = None,
        collision_note: str | None = None,
        themes: list[str] | None = None,
    ) -> int:
        """Log a reading journal entry for a passage used in composition."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            """INSERT INTO readings (chunk_id, poet, poem_title, interaction_id,
               timestamp, stimulus_text, collision_note, themes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (chunk_id, poet, poem_title, interaction_id, now,
             stimulus_text, collision_note, json.dumps(themes) if themes else None),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_readings_since(self, hours: int = 24) -> list[dict]:
        """Get readings from the last N hours."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = self._conn.execute(
            """SELECT r.*, i.stimulus_text as i_stimulus, i.the_problem,
                      i.posts as i_posts
               FROM readings r
               LEFT JOIN interactions i ON r.interaction_id = i.id
               WHERE r.timestamp > ?
               ORDER BY r.timestamp""",
            (cutoff,),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            for field in ("themes", "i_posts"):
                if d.get(field):
                    try:
                        d[field] = json.loads(d[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            results.append(d)
        return results

    # --- Passage notes (accumulated per-poem annotations) ---

    def get_passage_note(self, chunk_id: str) -> dict | None:
        """Get the accumulated note for a passage."""
        row = self._conn.execute(
            "SELECT * FROM passage_notes WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()
        if row:
            d = dict(row)
            if d.get("themes"):
                try:
                    d["themes"] = json.loads(d["themes"])
                except (json.JSONDecodeError, TypeError):
                    pass
            return d
        return None

    def upsert_passage_note(
        self,
        *,
        chunk_id: str,
        poet: str | None = None,
        poem_title: str | None = None,
        note: str | None = None,
        themes: list[str] | None = None,
    ):
        """Create or update a passage note. Increments times_used."""
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get_passage_note(chunk_id)
        if existing:
            # Merge themes
            old_themes = existing.get("themes") or []
            if isinstance(old_themes, str):
                try:
                    old_themes = json.loads(old_themes)
                except (json.JSONDecodeError, TypeError):
                    old_themes = []
            merged_themes = list(dict.fromkeys(old_themes + (themes or [])))
            self._conn.execute(
                """UPDATE passage_notes
                   SET times_used = times_used + 1, last_used = ?,
                       note = ?, themes = ?, poet = COALESCE(?, poet),
                       poem_title = COALESCE(?, poem_title)
                   WHERE chunk_id = ?""",
                (now, note, json.dumps(merged_themes), poet, poem_title, chunk_id),
            )
        else:
            self._conn.execute(
                """INSERT INTO passage_notes (chunk_id, poet, poem_title,
                   times_used, last_used, note, themes)
                   VALUES (?, ?, ?, 1, ?, ?, ?)""",
                (chunk_id, poet, poem_title, now, note,
                 json.dumps(themes) if themes else None),
            )
        self._conn.commit()

    def get_passage_notes_for_chunks(self, chunk_ids: list[str]) -> dict[str, dict]:
        """Get passage notes for a list of chunk_ids. Returns {chunk_id: note_dict}."""
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" * len(chunk_ids))
        rows = self._conn.execute(
            f"SELECT * FROM passage_notes WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
        result = {}
        for row in rows:
            d = dict(row)
            if d.get("themes"):
                try:
                    d["themes"] = json.loads(d["themes"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result[d["chunk_id"]] = d
        return result

    # --- Reflections (daily/periodic self-observation) ---

    def log_reflection(
        self,
        *,
        period: str,
        summary: str,
        poets_used: dict | None = None,
        themes_used: dict | None = None,
        preoccupations: list[str] | None = None,
        recommendations: list[str] | None = None,
        self_notes: str | None = None,
    ) -> int:
        """Log a reflection (daily, weekly, monthly)."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            """INSERT INTO reflections (timestamp, period, summary,
               poets_used, themes_used, preoccupations, recommendations, self_notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (now, period, summary,
             json.dumps(poets_used) if poets_used else None,
             json.dumps(themes_used) if themes_used else None,
             json.dumps(preoccupations) if preoccupations else None,
             json.dumps(recommendations) if recommendations else None,
             self_notes),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_recent_reflections(self, period: str = "daily", limit: int = 3) -> list[dict]:
        """Get the N most recent reflections of the given period, newest first."""
        rows = self._conn.execute(
            """SELECT * FROM reflections WHERE period = ?
               ORDER BY id DESC LIMIT ?""",
            (period, limit),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            for field in ("poets_used", "themes_used", "preoccupations", "recommendations"):
                if d.get(field):
                    try:
                        d[field] = json.loads(d[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            results.append(d)
        return results

    def get_latest_reflection(self, period: str = "daily") -> dict | None:
        """Get the most recent reflection of the given period."""
        row = self._conn.execute(
            """SELECT * FROM reflections WHERE period = ?
               ORDER BY id DESC LIMIT 1""",
            (period,),
        ).fetchone()
        if row:
            d = dict(row)
            for field in ("poets_used", "themes_used", "preoccupations", "recommendations"):
                if d.get(field):
                    try:
                        d[field] = json.loads(d[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            # self_notes is plain text, no JSON parsing needed
            return d
        return None

    # --- Usage statistics for reflection context ---

    def get_poet_usage(self, hours: int = 168) -> dict[str, int]:
        """Get poet frequency from readings over the last N hours (default 7 days)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = self._conn.execute(
            """SELECT poet, COUNT(*) as n FROM readings
               WHERE timestamp > ? AND poet IS NOT NULL
               GROUP BY poet ORDER BY n DESC""",
            (cutoff,),
        ).fetchall()
        return {row["poet"]: row["n"] for row in rows}

    def get_theme_usage(self, hours: int = 168) -> dict[str, int]:
        """Get theme frequency from readings over the last N hours (default 7 days)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = self._conn.execute(
            "SELECT themes FROM readings WHERE timestamp > ? AND themes IS NOT NULL",
            (cutoff,),
        ).fetchall()
        counts: dict[str, int] = {}
        for row in rows:
            try:
                themes = json.loads(row["themes"])
                for t in themes:
                    counts[t] = counts.get(t, 0) + 1
            except (json.JSONDecodeError, TypeError):
                pass
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    def get_posted_interactions(
        self, since: str | None = None, include_dry_run: bool = True
    ) -> list[dict]:
        """Get interactions where composition_decision='post', ordered by timestamp.

        Used by the site builder. Joins passage_used data inline.
        Args:
            since: ISO timestamp — only return interactions after this time.
            include_dry_run: if True, include dry-run interactions (default for blog).
        """
        conditions = ["composition_decision = 'post'"]
        params: list = []
        if since:
            conditions.append("timestamp > ?")
            params.append(since)
        if not include_dry_run:
            conditions.append("dry_run = 0")

        where = " AND ".join(conditions)
        rows = self._conn.execute(
            f"""SELECT * FROM interactions
                WHERE {where}
                ORDER BY timestamp ASC""",
            params,
        ).fetchall()

        results = []
        for row in rows:
            d = dict(row)
            for field in ("triage_queries", "passages_retrieved", "passage_used",
                          "posts", "response_uris"):
                if d.get(field):
                    try:
                        d[field] = json.loads(d[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            results.append(d)
        return results

    def get_todays_posted_interactions(self, date: str | None = None) -> list[dict]:
        """Get posted interactions for a specific date (default: today UTC).

        Returns full interaction dicts with JSON fields parsed.
        Used by the Opus daily review.
        """
        date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rows = self._conn.execute(
            """SELECT * FROM interactions
               WHERE timestamp LIKE ? AND composition_decision = 'post'
                 AND source != 'test'
               ORDER BY timestamp ASC""",
            (f"{date}%",),
        ).fetchall()

        results = []
        for row in rows:
            d = dict(row)
            for field in ("triage_queries", "passages_retrieved", "passage_used",
                          "posts", "response_uris"):
                if d.get(field):
                    try:
                        d[field] = json.loads(d[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            results.append(d)
        return results

    def get_all_reflections(self) -> list[dict]:
        """Get all reflections, ordered by timestamp. Used by site builder."""
        rows = self._conn.execute(
            "SELECT * FROM reflections ORDER BY timestamp ASC"
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            for field in ("poets_used", "themes_used", "preoccupations", "recommendations"):
                if d.get(field):
                    try:
                        d[field] = json.loads(d[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            results.append(d)
        return results

    def get_interactions(self, date: str | None = None, limit: int = 100) -> list[dict]:
        """Get interactions for a date, most recent first."""
        date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rows = self._conn.execute(
            """SELECT * FROM interactions
               WHERE timestamp LIKE ?
               ORDER BY id DESC LIMIT ?""",
            (f"{date}%", limit),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            # Parse JSON fields for the dashboard
            for field in ("triage_queries", "passages_retrieved", "passage_used", "posts", "response_uris"):
                if d.get(field):
                    try:
                        d[field] = json.loads(d[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            results.append(d)
        return results
