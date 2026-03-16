"""Platform-agnostic orchestrator: brain + rate limiting + anti-repetition + logging + reflection."""

import json
import logging
import time

import anthropic
import yaml

from src import brain
from src import safety
from src.store import Store

log = logging.getLogger(__name__)

_DEFAULT_CONFIG = {
    "max_responses_per_hour": 5,
    "max_responses_per_day": 20,
    "anti_repetition_hours": 48,
    "cooldown_after_post_seconds": 60,
    "poet_warning_threshold": 3,  # warn if poet used >N times in 48h
}


def _load_engine_config() -> dict:
    try:
        with open("config/config.yaml") as f:
            cfg = yaml.safe_load(f)
        return {**_DEFAULT_CONFIG, **(cfg.get("engine") or {})}
    except FileNotFoundError:
        return _DEFAULT_CONFIG


class Engine:
    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        store: Store | None = None,
        config: dict | None = None,
    ):
        self.client = client or anthropic.Anthropic()
        self.store = store or Store()
        self.config = config or _load_engine_config()
        self._last_post_time: float = 0

    def _build_reflection_context(self, passages: list[dict] | None) -> dict | None:
        """Assemble reflection context for composition: latest reflection, self_notes, passage notes, poet warnings."""
        if not passages:
            return None

        context = {}

        # Latest daily reflection summary + self_notes from Opus
        latest = self.store.get_latest_reflection(period="daily")
        if latest:
            context["latest_reflection"] = latest.get("summary")
            if latest.get("self_notes"):
                context["self_notes"] = latest["self_notes"]

        # Passage notes for retrieved chunks
        chunk_ids = [p["chunk_id"] for p in passages if "chunk_id" in p]
        if chunk_ids:
            context["passage_notes"] = self.store.get_passage_notes_for_chunks(chunk_ids)

        # Poet distribution warnings (48h window)
        poet_usage_48h = self.store.get_poet_usage(hours=48)
        threshold = self.config.get("poet_warning_threshold", 3)
        warnings = []
        for poet, count in poet_usage_48h.items():
            if count >= threshold:
                warnings.append(
                    f"NOTE: You've used {poet} {count} times in the last 48 hours. "
                    f"Consider whether another voice might serve better here."
                )
        if warnings:
            context["poet_warnings"] = warnings

        return context if any(context.values()) else None

    def _run_post_reflection(
        self, result: dict, interaction_id: int, stimulus: str, dry_run: bool
    ):
        """After a successful post, run reflection and store reading + passage note."""

        comp = result.get("composition") or {}
        passage_used = comp.get("passage_used")
        if not passage_used or not passage_used.get("chunk_id"):
            return

        chunk_id = passage_used["chunk_id"]
        poet = passage_used.get("poet", "")
        poem_title = passage_used.get("poem_title", "")
        the_problem = (result.get("triage") or {}).get("the_problem", "")
        posts = comp.get("posts", [])
        post_text = " | ".join(posts)

        # Get passage text from retrieved passages
        passage_text = ""
        for p in (result.get("passages") or []):
            if p.get("chunk_id") == chunk_id:
                passage_text = p.get("text", "")
                break

        # Get existing note
        existing = self.store.get_passage_note(chunk_id)
        existing_note = existing["note"] if existing else None

        # Run reflection (Haiku)
        try:
            reflection = brain.reflect(
                self.client,
                stimulus=stimulus,
                the_problem=the_problem,
                passage_text=passage_text,
                post_text=post_text,
                existing_note=existing_note,
            )
            ref_usage = reflection.pop("_usage", {})
            result["tokens_in"] = result.get("tokens_in", 0) + ref_usage.get("input_tokens", 0)
            result["tokens_out"] = result.get("tokens_out", 0) + ref_usage.get("output_tokens", 0)
        except Exception:
            log.exception("Reflection failed (non-fatal)")
            reflection = {"collision_note": "", "themes": [], "updated_note": existing_note or ""}

        # Store reading
        self.store.log_reading(
            chunk_id=chunk_id,
            poet=poet,
            poem_title=poem_title,
            interaction_id=interaction_id,
            stimulus_text=stimulus,
            collision_note=reflection.get("collision_note"),
            themes=reflection.get("themes"),
        )

        # Update passage note
        self.store.upsert_passage_note(
            chunk_id=chunk_id,
            poet=poet,
            poem_title=poem_title,
            note=reflection.get("updated_note"),
            themes=reflection.get("themes"),
        )

    def process(
        self,
        stimulus: str,
        source: str = "unknown",
        stimulus_uri: str | None = None,
        stimulus_author: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Run brain pipeline with rate limiting, anti-repetition, reflection, and logging.

        Returns the brain result dict, augmented with:
          - "rate_limited": True if skipped due to rate limits
          - "interaction_id": row id in the store
        """
        # Rate limiting
        hourly = self.store.count_responses_last_hour()
        daily = self.store.count_responses_today()
        if hourly >= self.config["max_responses_per_hour"]:
            return {"stimulus": stimulus, "rate_limited": True,
                    "reason": f"hourly cap ({hourly}/{self.config['max_responses_per_hour']})"}
        if daily >= self.config["max_responses_per_day"]:
            return {"stimulus": stimulus, "rate_limited": True,
                    "reason": f"daily cap ({daily}/{self.config['max_responses_per_day']})"}

        # Cooldown
        elapsed = time.time() - self._last_post_time
        cooldown = self.config["cooldown_after_post_seconds"]
        if self._last_post_time > 0 and elapsed < cooldown:
            return {"stimulus": stimulus, "rate_limited": True,
                    "reason": f"cooldown ({cooldown - elapsed:.0f}s remaining)"}

        # Anti-repetition: get recently used chunk_ids
        exclude_ids = self.store.get_used_chunk_ids(
            hours=self.config["anti_repetition_hours"]
        )

        # Run brain pipeline with reflection context
        # We run triage + retrieval first, then build context, then compose
        result = {"stimulus": stimulus, "triage": None, "passages": None,
                  "composition": None, "tokens_in": 0, "tokens_out": 0}

        # Stage 1: Triage
        result["triage"] = brain.triage(self.client, stimulus)
        triage_usage = result["triage"].pop("_usage", {})
        result["tokens_in"] += triage_usage.get("input_tokens", 0)
        result["tokens_out"] += triage_usage.get("output_tokens", 0)

        if not result["triage"].get("engage"):
            # Log and return early
            interaction_id = self.store.log_interaction(
                source=source, stimulus_text=stimulus,
                stimulus_uri=stimulus_uri, stimulus_author=stimulus_author,
                triage=result["triage"], tokens_in=result["tokens_in"],
                tokens_out=result["tokens_out"], dry_run=dry_run,
            )
            result["interaction_id"] = interaction_id
            result["rate_limited"] = False
            return result

        # Stage 2: Retrieval
        search_queries = result["triage"].get("search_queries", [stimulus])
        raw_passages = brain.retrieve(search_queries, exclude_ids=exclude_ids)
        result["passages"] = safety.filter_passages(raw_passages)

        if not result["passages"]:
            result["composition"] = {
                "decision": "skip", "mode": "thought_only", "posts": [],
                "skip_reason": "All retrieved passages filtered by safety check.",
            }
            interaction_id = self.store.log_interaction(
                source=source, stimulus_text=stimulus,
                stimulus_uri=stimulus_uri, stimulus_author=stimulus_author,
                triage=result["triage"], passages=result["passages"],
                composition=result["composition"],
                tokens_in=result["tokens_in"], tokens_out=result["tokens_out"],
                dry_run=dry_run,
            )
            result["interaction_id"] = interaction_id
            result["rate_limited"] = False
            return result

        # Build reflection context with actual retrieved passages
        reflection_context = self._build_reflection_context(result["passages"])

        # Stage 3: Composition (with reflection context)
        the_problem = result["triage"].get("the_problem", "")
        result["composition"] = brain.compose(
            self.client, stimulus, result["passages"], the_problem,
            reflection_context=reflection_context,
        )
        comp_usage = result["composition"].pop("_usage", {})
        result["tokens_in"] += comp_usage.get("input_tokens", 0)
        result["tokens_out"] += comp_usage.get("output_tokens", 0)

        # Enrich passage_used with verse text from retrieved passages
        comp = result.get("composition") or {}
        pu = comp.get("passage_used")
        if pu and result.get("passages"):
            matched = None
            # Try exact chunk_id match first
            if pu.get("chunk_id"):
                for p in result["passages"]:
                    if p.get("chunk_id") == pu["chunk_id"]:
                        matched = p
                        break
            # Fall back to poet+title match
            if not matched and pu.get("poet"):
                poet_lower = pu["poet"].lower()
                for p in result["passages"]:
                    if p.get("poet", "").lower() == poet_lower:
                        matched = p
                        break
            if matched:
                pu["text"] = matched.get("text", "")
                pu["chunk_id"] = matched.get("chunk_id", pu.get("chunk_id", ""))

        # Track post time
        if comp.get("decision") == "post":
            self._last_post_time = time.time()

        # Log to store
        interaction_id = self.store.log_interaction(
            source=source,
            stimulus_text=stimulus,
            stimulus_uri=stimulus_uri,
            stimulus_author=stimulus_author,
            triage=result.get("triage"),
            passages=result.get("passages"),
            composition=comp,
            tokens_in=result.get("tokens_in", 0),
            tokens_out=result.get("tokens_out", 0),
            dry_run=dry_run,
        )

        result["interaction_id"] = interaction_id
        result["rate_limited"] = False

        # Post-composition reflection (only on successful posts)
        if comp.get("decision") == "post":
            self._run_post_reflection(result, interaction_id, stimulus, dry_run)

        return result

    def run_daily_reflection(self, date: str | None = None) -> dict | None:
        """Run the Opus daily review: select best entries + write reflection.

        Call once per day (or manually via CLI). Marks selected entries as published.
        Returns the review dict, or None if no posts today.
        """
        interactions = self.store.get_todays_posted_interactions(date=date)
        if not interactions:
            log.info("No posted interactions today — skipping daily review.")
            return None

        poet_usage = self.store.get_poet_usage(hours=168)  # 7 days
        theme_usage = self.store.get_theme_usage(hours=168)

        recent_reflections = self.store.get_recent_reflections(period="daily", limit=3)

        result = brain.daily_review(
            self.client,
            interactions=interactions,
            poet_usage=poet_usage,
            theme_usage=theme_usage,
            recent_reflections=recent_reflections,
        )

        usage = result.pop("_usage", {})

        # Mark selected entries as published
        selected = result.get("selected_ids", [])
        valid_ids = {ix["id"] for ix in interactions}
        publish_ids = [s["id"] for s in selected if s.get("id") in valid_ids]
        if publish_ids:
            self.store.mark_published(publish_ids)
            log.info("Marked %d entries as published: %s", len(publish_ids), publish_ids)

        # Store reflection
        self.store.log_reflection(
            period="daily",
            summary=result.get("summary", ""),
            poets_used=poet_usage,
            themes_used=theme_usage,
            preoccupations=result.get("preoccupations"),
            recommendations=result.get("recommendations"),
            self_notes=result.get("self_notes"),
        )

        log.info(
            "Daily Opus review stored. Selected %d/%d entries. Preoccupations: %s",
            len(publish_ids), len(interactions), result.get("preoccupations"),
        )
        return result
