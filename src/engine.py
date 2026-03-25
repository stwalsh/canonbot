"""Platform-agnostic orchestrator: brain + rate limiting + anti-repetition + logging + reflection."""

import json
import logging
import time
from pathlib import Path

import anthropic
import yaml

from src import brain
from src import safety
from src.store import Store

log = logging.getLogger(__name__)

_DEFAULT_CONFIG = {
    "max_responses_per_hour": 10,
    "max_responses_per_day": 30,
    "anti_repetition_hours": 48,
    "cooldown_after_post_seconds": 60,
    "poet_warning_threshold": 3,  # warn if poet used >N times in 48h
    "poet_cooling_threshold": 5,  # hard filter: exclude poet from retrieval if used >N times in 48h
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

    def _get_cooled_poets(self) -> set[str]:
        """Return poets that have been used too often recently and should be excluded from retrieval."""
        poet_usage = self.store.get_poet_usage(hours=48)
        threshold = self.config.get("poet_cooling_threshold", 5)
        return {poet for poet, count in poet_usage.items() if count >= threshold}

    def _build_reflection_context(
        self, passages: list[dict] | None, seeds: str | None = None,
    ) -> dict | None:
        """Assemble reflection context for composition: latest reflection, self_notes, passage notes, poet warnings, seeds."""
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

        # External seeds from source system
        if seeds:
            context["seeds"] = seeds

        return context if any(context.values()) else None

    def _build_self_gen_context(self, seeds: str | None = None) -> dict | None:
        """Build a lightweight reflection context for self-generated compositions."""
        context = {}
        latest = self.store.get_latest_reflection(period="daily")
        if latest and latest.get("self_notes"):
            context["self_notes"] = latest["self_notes"]
        if seeds:
            context["seeds"] = seeds
        return context if context else None

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
        seeds: str | None = None,
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

        # Stimulus dedup: cap responses per stimulus at 2
        stimulus_count = self.store.count_stimulus_responses(stimulus)
        if stimulus_count >= 2:
            return {"stimulus": stimulus, "rate_limited": True,
                    "reason": f"stimulus dedup ({stimulus_count} responses already)"}

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

        # Stage 2: Retrieval (with poet cooling)
        search_queries = result["triage"].get("search_queries", [stimulus])
        cooled_poets = self._get_cooled_poets()
        raw_passages = brain.retrieve(search_queries, exclude_ids=exclude_ids, exclude_poets=cooled_poets)
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

        # Build reflection context with actual retrieved passages + seeds
        reflection_context = self._build_reflection_context(result["passages"], seeds=seeds)

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

    def engage_stimulus(self, stimulus_text: str, source_name: str = "stimulus",
                        seeds: str | None = None, dry_run: bool = False) -> dict:
        """Long-form response to a bespoke stimulus (clip, article, provocation).

        Bypasses triage. Retrieves passages based on the stimulus, then runs
        the engage composition mode (2-3 paragraphs, no post-length constraint).
        """
        result = {"stimulus": stimulus_text, "tokens_in": 0, "tokens_out": 0}

        # Generate search queries from the stimulus (use first 500 chars as query)
        queries = [stimulus_text[:500]]
        exclude_ids = self.store.get_used_chunk_ids(hours=self.config["anti_repetition_hours"])
        cooled_poets = self._get_cooled_poets()

        raw_passages = brain.retrieve(queries, n_results=8, exclude_ids=exclude_ids, exclude_poets=cooled_poets)
        passages = safety.filter_passages(raw_passages)

        if not passages:
            return {"decision": "skip", "skip_reason": "No passages found.", **result}

        # Build reflection context with seeds
        self_gen_context = self._build_self_gen_context(seeds)

        comp = brain.engage(self.client, stimulus_text, passages, reflection_context=self_gen_context)
        comp_usage = comp.pop("_usage", {})
        result["tokens_in"] += comp_usage.get("input_tokens", 0)
        result["tokens_out"] += comp_usage.get("output_tokens", 0)
        result["composition"] = comp
        result["passages"] = passages

        # Enrich passage_used
        pu = comp.get("passage_used")
        if pu and pu.get("chunk_id"):
            for p in passages:
                if p["chunk_id"] == pu["chunk_id"]:
                    pu["text"] = p.get("text", "")
                    break

        if comp.get("decision") == "post":
            self._last_post_time = time.time()

        # Log as interaction
        triage_data = {
            "engage": comp.get("decision") == "post",
            "reason": "Bespoke stimulus — long-form engage mode",
            "search_queries": queries,
            "the_problem": stimulus_text[:200],
        }
        interaction_id = self.store.log_interaction(
            source=source_name,
            stimulus_text=stimulus_text,
            stimulus_uri=None,
            stimulus_author="interlocutor",
            triage=triage_data,
            passages=passages,
            composition=comp,
            tokens_in=result.get("tokens_in", 0),
            tokens_out=result.get("tokens_out", 0),
            dry_run=dry_run,
        )
        result["interaction_id"] = interaction_id
        result["mode"] = "engage"
        result["rate_limited"] = False

        # Post-composition reflection
        if comp.get("decision") == "post":
            self._run_post_reflection(result, interaction_id, stimulus_text[:200], dry_run)

        return result

    def self_generate(self, mode: str = "contemplate", seeds: str | None = None, dry_run: bool = False) -> dict:
        """Run a self-generated meditation or comparison.

        mode: "contemplate" (single passage) or "compare" (two passages).
        Returns the result dict with interaction_id.
        """
        import random

        result = {"tokens_in": 0, "tokens_out": 0}

        # Gather context for search direction
        latest = self.store.get_latest_reflection(period="daily")
        self_notes = latest.get("self_notes") if latest else None
        theme_usage = self.store.get_theme_usage(hours=168)
        poet_usage = self.store.get_poet_usage(hours=48)
        recent_themes = sorted(theme_usage, key=theme_usage.get, reverse=True)[:5]
        recent_poets = sorted(poet_usage, key=poet_usage.get, reverse=True)[:5]

        # Get recently used chunk_ids for anti-repetition
        exclude_ids = self.store.get_used_chunk_ids(hours=self.config["anti_repetition_hours"])

        # Generate search direction
        direction = brain._generate_search_direction(
            self.client, self_notes, recent_themes, recent_poets,
        )
        dir_usage = direction.pop("_usage", {})
        result["tokens_in"] += dir_usage.get("input_tokens", 0)
        result["tokens_out"] += dir_usage.get("output_tokens", 0)

        query = direction.get("query", "")
        search_reason = direction.get("reason", "")

        # Poet cooling for self-gen too
        cooled_poets = self._get_cooled_poets()

        # If no query generated, pick a random passage
        if not query:
            raw_passages = brain.retrieve(
                ["mortality", "desire", "power", "devotion", "loss"][random.randrange(5)],
                n_results=10, exclude_ids=exclude_ids, exclude_poets=cooled_poets,
            )
        else:
            raw_passages = brain.retrieve([query], n_results=10, exclude_ids=exclude_ids, exclude_poets=cooled_poets)

        passages = safety.filter_passages(raw_passages)
        if not passages:
            return {"decision": "skip", "skip_reason": "No passages found.", **result}

        # Build reflection context for self-gen (seeds + self_notes)
        self_gen_context = self._build_self_gen_context(seeds)

        if mode == "compare":
            # Pick passage 1, then find a contrasting passage 2
            passage_1 = passages[0]
            # Use passage 1's text as query but exclude same poet
            second_query = passage_1["text"][:200]
            raw_second = brain.retrieve(
                [second_query], n_results=10,
                exclude_ids=exclude_ids | {passage_1["chunk_id"]},
                exclude_poets=cooled_poets,
            )
            second_passages = [
                p for p in safety.filter_passages(raw_second)
                if p["poet"] != passage_1["poet"]
            ]
            if not second_passages:
                # Fall back to contemplate
                mode = "contemplate"
            else:
                passage_2 = second_passages[0]
                comp = brain.compare(self.client, passage_1, passage_2, search_reason, reflection_context=self_gen_context)
                comp_usage = comp.pop("_usage", {})
                result["tokens_in"] += comp_usage.get("input_tokens", 0)
                result["tokens_out"] += comp_usage.get("output_tokens", 0)
                result["composition"] = comp
                result["passages"] = [passage_1, passage_2]

                # Enrich passage_used
                pu = comp.get("passage_used")
                if pu and pu.get("chunk_id"):
                    for p in [passage_1, passage_2]:
                        if p["chunk_id"] == pu["chunk_id"]:
                            pu["text"] = p.get("text", "")
                            break

        if mode == "engage_self":
            # Long-form self-generated essay — uses engage() with search_reason as stimulus
            comp = brain.engage(
                self.client,
                stimulus_text=f"[Self-directed exploration] {search_reason}",
                passages=passages[:5],
                reflection_context=self_gen_context,
            )
            comp_usage = comp.pop("_usage", {})
            result["tokens_in"] += comp_usage.get("input_tokens", 0)
            result["tokens_out"] += comp_usage.get("output_tokens", 0)
            result["composition"] = comp
            result["passages"] = passages[:5]

            # Enrich passage_used
            pu = comp.get("passage_used")
            if pu and pu.get("chunk_id"):
                for p in passages:
                    if p["chunk_id"] == pu["chunk_id"]:
                        pu["text"] = p.get("text", "")
                        break

        if mode == "contemplate":
            passage = passages[0]
            comp = brain.contemplate(self.client, passage, search_reason, reflection_context=self_gen_context)
            comp_usage = comp.pop("_usage", {})
            result["tokens_in"] += comp_usage.get("input_tokens", 0)
            result["tokens_out"] += comp_usage.get("output_tokens", 0)
            result["composition"] = comp
            result["passages"] = [passage]

            # Enrich passage_used
            pu = comp.get("passage_used")
            if pu and not pu.get("text"):
                pu["text"] = passage.get("text", "")
                if not pu.get("chunk_id"):
                    pu["chunk_id"] = passage["chunk_id"]

        comp = result.get("composition", {})
        source = f"self_{mode}"

        # Track post time
        if comp.get("decision") == "post":
            self._last_post_time = time.time()

        # Build a triage-like dict for logging
        triage_data = {
            "engage": comp.get("decision") == "post",
            "reason": search_reason,
            "search_queries": [query] if query else [],
            "the_problem": search_reason,
        }

        interaction_id = self.store.log_interaction(
            source=source,
            stimulus_text=f"[{mode}] {search_reason}",
            stimulus_uri=None,
            stimulus_author="self",
            triage=triage_data,
            passages=result.get("passages"),
            composition=comp,
            tokens_in=result.get("tokens_in", 0),
            tokens_out=result.get("tokens_out", 0),
            dry_run=dry_run,
        )
        result["interaction_id"] = interaction_id
        result["mode"] = mode
        result["search_reason"] = search_reason
        result["rate_limited"] = False

        # Post-composition reflection
        if comp.get("decision") == "post":
            self._run_post_reflection(
                result, interaction_id, f"[{mode}] {search_reason}", dry_run,
            )

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

        # Mark selected entries as published (tiered)
        selected = result.get("selected_ids", [])
        valid_ids = {ix["id"] for ix in interactions}
        ix_by_id = {ix["id"]: ix for ix in interactions}

        publish_ids = [s["id"] for s in selected if s.get("tier") == "publish" and s.get("id") in valid_ids]
        notebook_ids = [s["id"] for s in selected if s.get("tier") == "notebook" and s.get("id") in valid_ids]

        # Backward compat: if no tier field, treat all as publish
        if not publish_ids and not notebook_ids:
            publish_ids = [s["id"] for s in selected if s.get("id") in valid_ids]

        if publish_ids:
            self.store.mark_published(publish_ids, tier=1)
            log.info("Marked %d entries for publication: %s", len(publish_ids), publish_ids)
        if notebook_ids:
            self.store.mark_published(notebook_ids, tier=2)
            log.info("Marked %d entries for notebook: %s", len(notebook_ids), notebook_ids)

        # Pass 2: Editorial revision of published entries
        for entry_id in publish_ids:
            ix = ix_by_id.get(entry_id)
            if not ix:
                continue
            posts = ix.get("posts") or []
            entry_text = "\n\n".join(posts) if isinstance(posts, list) else str(posts)
            stimulus = ix.get("stimulus_text", "")[:500]
            pu = ix.get("passage_used") or {}
            passage_ctx = ""
            if isinstance(pu, dict) and pu.get("text"):
                passage_ctx = f"{pu.get('poet', '')} — \"{pu.get('poem_title', '')}\"\n{pu['text']}"

            try:
                revision = brain.revise_entry(self.client, entry_text, stimulus, passage_ctx)
                rev_usage = revision.pop("_usage", {})
                revised = revision.get("revised_posts", [])
                changes = revision.get("changes_made", "")
                if revised:
                    self.store.store_edited_posts(entry_id, revised)
                    log.info("Revised entry %d: %s", entry_id, changes)
            except Exception:
                log.exception("Revision failed for entry %d (non-fatal)", entry_id)

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
            "Daily review stored. Published: %d, Notebook: %d, Total: %d. Preoccupations: %s",
            len(publish_ids), len(notebook_ids), len(interactions), result.get("preoccupations"),
        )
        return result
