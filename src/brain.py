"""Three-stage brain: triage → retrieval → composition."""

import json
import logging
from pathlib import Path

import anthropic

from src import retriever
from src import safety

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "config" / "prompts"

TRIAGE_MODEL = "claude-haiku-4-5-20251001"
COMPOSITION_MODEL = "claude-opus-4-6"

_EMPTY_USAGE = {"input_tokens": 0, "output_tokens": 0}


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text().strip()


def _safe_api_call(client, caller_name: str, **kwargs) -> tuple:
    """Wrap client.messages.create() with error handling.

    Returns (response, usage) on success, raises on unrecoverable error.
    Catches transient API errors and returns None.
    """
    try:
        response = client.messages.create(**kwargs)
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        return response, usage
    except (anthropic.APIError, anthropic.APIConnectionError) as e:
        log.error("API error in %s: %s", caller_name, e)
        return None, _EMPTY_USAGE


def triage(client: anthropic.Anthropic, stimulus: str) -> dict:
    """Stage 1: decide whether to engage.

    Returns {"engage": bool, "reason": str} and when engaging also
    {"search_queries": [...], "the_problem": "..."}.
    """
    system = _load_prompt("triage.md")
    response, usage = _safe_api_call(
        client, "triage",
        model=TRIAGE_MODEL,
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": stimulus}],
    )
    if response is None:
        return {"engage": False, "reason": "API error during triage", "_usage": usage}
    text = response.content[0].text.strip()
    # Strip markdown fences if the model wraps its response
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        result = {"engage": False, "reason": f"Triage returned unparseable response: {text[:100]}"}
    result["_usage"] = usage
    return result


def retrieve(
    queries: list[str],
    n_results: int = 5,
    exclude_ids: set[str] | None = None,
    exclude_poets: set[str] | None = None,
    content_type: str | None = None,
    max_prose: int | None = None,
) -> list[dict]:
    """Stage 2: multi-query semantic search over the corpus.

    Returns list of passage dicts, or [] if ChromaDB is unavailable.
    """
    try:
        return retriever.search_multi(
            queries, n_results=n_results, exclude_ids=exclude_ids,
            exclude_poets=exclude_poets, content_type=content_type,
            max_prose=max_prose,
        )
    except Exception as e:
        log.error("Retrieval failed (ChromaDB unavailable?): %s", e)
        return []


# Tool schema for structured composition output
_COMPOSE_TOOL = {
    "name": "compose_response",
    "description": "Produce the bot's response to a stimulus post.",
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["post", "skip"],
                "description": "Whether to post or skip.",
            },
            "mode": {
                "type": "string",
                "enum": ["quote_only", "thought_quote", "thought_only", "quote_timeline"],
                "description": (
                    "Response mode. quote_only: just the passage. "
                    "thought_quote: your observation (post 1) + passage (post 2). "
                    "thought_only: your thought, no explicit quote. "
                    "quote_timeline: post passage to own timeline, not as reply."
                ),
            },
            "posts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "1–2 post texts, each max 300 characters. Empty if decision is skip.",
            },
            "passage_used": {
                "type": "object",
                "properties": {
                    "chunk_id": {
                        "type": "string",
                        "description": "Copy the chunk_id EXACTLY as shown in the [chunk_id: ...] header of the passage you used.",
                    },
                    "poet": {
                        "type": "string",
                        "description": "The poet's name.",
                    },
                    "poem_title": {
                        "type": "string",
                        "description": "Title of the poem.",
                    },
                },
                "required": ["chunk_id", "poet", "poem_title"],
                "description": "The passage that anchors the response. Null if skip or thought_only.",
            },
            "skip_reason": {
                "type": "string",
                "description": "Why we're skipping. Only if decision is skip.",
            },
        },
        "required": ["decision", "mode", "posts"],
    },
}


def compose(
    client: anthropic.Anthropic,
    stimulus: str,
    passages: list[dict],
    the_problem: str,
    reflection_context: dict | None = None,
) -> dict:
    """Stage 3: compose the bot's response using retrieved passages.

    reflection_context (optional): {
        "latest_reflection": str or None,
        "passage_notes": {chunk_id: {note, times_used, ...}},
        "poet_warnings": [str],
    }

    Returns structured dict with decision, mode, posts, passage_used, skip_reason.
    """
    soul = _load_prompt("soul.md")
    rules = _load_prompt("system.md")
    system = f"{soul}\n\n---\n\n{rules}"

    passages_text = "\n\n---\n\n".join(
        f"[chunk_id: {p['chunk_id']}]\n"
        f"[{p['poet']} — \"{p['poem_title']}\" ({p['date']}), {p['work']}]\n{p['text']}"
        for p in passages
    )

    user_msg = (
        f"ORIGINAL POST:\n{stimulus}\n\n"
        f"THE PROBLEM:\n{the_problem}\n\n"
        f"RETRIEVED PASSAGES:\n{passages_text}"
    )

    # Inject reflection context if available
    if reflection_context:
        context_parts = []

        if reflection_context.get("latest_reflection"):
            context_parts.append(
                f"YOUR RECENT PATTERNS:\n{reflection_context['latest_reflection']}"
            )

        notes = reflection_context.get("passage_notes") or {}
        if notes:
            history_lines = []
            for chunk_id, pn in notes.items():
                history_lines.append(
                    f"- \"{pn.get('poem_title', '?')}\" by {pn.get('poet', '?')}: "
                    f"used {pn.get('times_used', 0)} times. Your note: {pn.get('note', '(none)')}"
                )
            context_parts.append("PASSAGE HISTORY:\n" + "\n".join(history_lines))

        if reflection_context.get("self_notes"):
            context_parts.append(
                f"NOTES FROM YOUR REVIEWER:\n{reflection_context['self_notes']}"
            )

        for w in (reflection_context.get("poet_warnings") or []):
            context_parts.append(w)

        if reflection_context.get("seeds"):
            context_parts.append(reflection_context["seeds"])

        if reflection_context.get("oblique_strategy"):
            context_parts.append(
                f"OBLIQUE STRATEGY (draw one, follow it or argue with it — let it change the shape of what you write):\n"
                f"\"{reflection_context['oblique_strategy']}\""
            )

        if context_parts:
            user_msg += "\n\n" + "\n\n".join(context_parts)

    response, usage = _safe_api_call(
        client, "compose",
        model=COMPOSITION_MODEL,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
        tools=[_COMPOSE_TOOL],
        tool_choice={"type": "tool", "name": "compose_response"},
    )
    if response is None:
        return {"decision": "skip", "mode": "thought_only", "posts": [],
                "skip_reason": "API error during composition", "_usage": usage}

    # Extract the tool call input
    for block in response.content:
        if block.type == "tool_use":
            result = block.input
            # Normalize posts to plain list of strings
            posts = result.get("posts", [])
            if isinstance(posts, str):
                # Could be a JSON array literal stuffed into a string
                try:
                    parsed = json.loads(posts)
                    if isinstance(parsed, list):
                        posts = parsed
                    else:
                        posts = [posts]
                except (json.JSONDecodeError, TypeError):
                    posts = [posts]
            # Flatten: if model nested an array inside a single-element list
            flat = []
            for p in posts:
                if isinstance(p, str):
                    try:
                        inner = json.loads(p)
                        if isinstance(inner, list):
                            flat.extend(str(x) for x in inner)
                            continue
                    except (json.JSONDecodeError, TypeError):
                        pass
                    flat.append(p)
                else:
                    flat.append(str(p))
            result["posts"] = [p.replace("\n", " ").strip() for p in flat]
            # Normalize passage_used
            pu = result.get("passage_used")
            if isinstance(pu, str):
                result["passage_used"] = {"chunk_id": "", "poet": "", "poem_title": pu}
            elif isinstance(pu, dict):
                pu.setdefault("chunk_id", "")
                pu.setdefault("poet", "")
                pu.setdefault("poem_title", "")
            # Ensure required fields
            result.setdefault("decision", "skip")
            result.setdefault("posts", [])
            result.setdefault("mode", "thought_only")
            result["_usage"] = usage
            return result

    # Fallback — shouldn't happen with tool_choice forced
    return {"decision": "skip", "mode": "thought_only", "posts": [],
            "skip_reason": "No tool call in response.", "_usage": usage}


REFLECT_MODEL = TRIAGE_MODEL  # Haiku — cheap per-interaction reflection


# Tool schema for structured reflection output
_REFLECT_TOOL = {
    "name": "log_reflection",
    "description": "Record your observation about what a passage did in this context.",
    "input_schema": {
        "type": "object",
        "properties": {
            "collision_note": {
                "type": "string",
                "description": "What the passage accomplished in this context (1-2 sentences).",
            },
            "themes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "1-3 theme tags.",
            },
            "updated_note": {
                "type": "string",
                "description": "Cumulative note on this passage across all uses. Update the previous note if one exists.",
            },
        },
        "required": ["collision_note", "themes", "updated_note"],
    },
}


def reflect(
    client: anthropic.Anthropic,
    *,
    stimulus: str,
    the_problem: str,
    passage_text: str,
    post_text: str,
    existing_note: str | None = None,
) -> dict:
    """Post-composition reflection. Uses Haiku. Returns collision_note, themes, updated_note."""
    prompt_template = _load_prompt("reflect.md")
    user_msg = prompt_template.format(
        existing_note=existing_note or "First encounter.",
        stimulus=stimulus,
        the_problem=the_problem,
        passage_text=passage_text,
        post_text=post_text,
    )

    response, usage = _safe_api_call(
        client, "reflect",
        model=REFLECT_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": user_msg}],
        tools=[_REFLECT_TOOL],
        tool_choice={"type": "tool", "name": "log_reflection"},
    )
    if response is None:
        return {"collision_note": "", "themes": [], "updated_note": existing_note or "", "_usage": usage}

    for block in response.content:
        if block.type == "tool_use":
            result = block.input
            result.setdefault("collision_note", "")
            result.setdefault("themes", [])
            result.setdefault("updated_note", existing_note or "")
            result["_usage"] = usage
            return result

    return {"collision_note": "", "themes": [], "updated_note": existing_note or "", "_usage": usage}


DAILY_REVIEW_MODEL = "claude-opus-4-6"


# Tool schema for Opus daily review — selection + reflection
_DAILY_REVIEW_TOOL = {
    "name": "daily_review",
    "description": "Select the day's best entries for publication and write a critical reflection.",
    "input_schema": {
        "type": "object",
        "properties": {
            "selected_ids": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "Interaction ID."},
                        "tier": {
                            "type": "string",
                            "enum": ["publish", "notebook"],
                            "description": "publish: long-form entry for full publication with editorial revision. notebook: short-form entry for the daily notebook page.",
                        },
                        "reason": {"type": "string", "description": "Brief reason for selecting this entry (1 sentence)."},
                    },
                    "required": ["id", "tier", "reason"],
                },
                "description": "Select 3-5 long-form entries (tier: publish) and 5-10 short-form entries (tier: notebook).",
            },
            "summary": {
                "type": "string",
                "description": "2-3 paragraph critical reflection on the day's work.",
            },
            "preoccupations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 3 current intellectual preoccupations.",
            },
            "recommendations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 3 concrete adjustments for tomorrow.",
            },
            "self_notes": {
                "type": "string",
                "description": (
                    "Seeds for tomorrow's composition — these get injected into your writing context. "
                    "Be specific and generative: poets to explore, angles to try, what worked well today "
                    "and should continue. Invitations, not corrections. Must not be empty."
                ),
            },
        },
        "required": ["selected_ids", "summary", "preoccupations", "recommendations", "self_notes"],
    },
}


def daily_review(
    client: anthropic.Anthropic,
    *,
    interactions: list[dict],
    poet_usage: dict[str, int],
    theme_usage: dict[str, int],
    recent_reflections: list[dict] | None = None,
) -> dict:
    """Daily Opus review: select best entries + write reflection.

    Returns selected_ids, summary, preoccupations, recommendations, self_notes.
    """
    prompt_template = _load_prompt("daily_reflect.md")

    # Format full interaction data
    interaction_blocks = []
    for ix in interactions:
        posts = ix.get("posts") or []
        post_text = " | ".join(posts) if isinstance(posts, list) else str(posts)

        passage = ix.get("passage_used") or {}
        if isinstance(passage, dict):
            passage_info = (
                f"  Passage: {passage.get('poet', '?')} — \"{passage.get('poem_title', '?')}\"\n"
                f"  Verse: {(passage.get('text') or '(no text)')[:300]}"
            )
        else:
            passage_info = f"  Passage: {passage}"

        block = (
            f"[ID {ix['id']}]\n"
            f"  Stimulus: \"{(ix.get('stimulus_text') or '')[:200]}\"\n"
            f"  Triage reason: {ix.get('triage_reason', '?')}\n"
            f"  The problem: {ix.get('the_problem', '?')}\n"
            f"{passage_info}\n"
            f"  Bot post: {post_text}\n"
            f"  Mode: {ix.get('composition_mode', '?')}"
        )
        interaction_blocks.append(block)

    interactions_block = "\n\n".join(interaction_blocks) if interaction_blocks else "(No posts today.)"

    poet_dist = ", ".join(f"{p}: {n}" for p, n in poet_usage.items()) or "(none)"
    theme_dist = ", ".join(f"{t}: {n}" for t, n in theme_usage.items()) or "(none)"

    # Format reflection history (up to 3, newest first)
    reflections = recent_reflections or []
    if reflections:
        history_parts = []
        for i, ref in enumerate(reflections):
            date = ref.get("timestamp", "")[:10]
            label = "Yesterday" if i == 0 else f"{date}"
            summary = ref.get("summary", "(none)")
            self_notes = ref.get("self_notes") or "(none)"
            preoccs = ref.get("preoccupations") or []
            recs = ref.get("recommendations") or []
            part = (
                f"--- {label} ({date}) ---\n"
                f"Reflection: {summary}\n"
                f"Self-notes: {self_notes}\n"
                f"Preoccupations: {', '.join(preoccs) if preoccs else '(none)'}\n"
                f"Recommendations: {', '.join(recs) if recs else '(none)'}"
            )
            history_parts.append(part)
        reflection_history = "\n\n".join(history_parts)
    else:
        reflection_history = "None yet."

    user_msg = prompt_template.format(
        n_posts=len(interactions),
        interactions_block=interactions_block,
        poet_distribution=poet_dist,
        theme_distribution=theme_dist,
        reflection_history=reflection_history,
    )

    soul = _load_prompt("soul.md")

    response, usage = _safe_api_call(
        client, "daily_review",
        model=DAILY_REVIEW_MODEL,
        max_tokens=4096,
        system=soul,
        messages=[{"role": "user", "content": user_msg}],
        tools=[_DAILY_REVIEW_TOOL],
        tool_choice={"type": "tool", "name": "daily_review"},
    )
    if response is None:
        return {"selected_ids": [], "summary": "", "preoccupations": [],
                "recommendations": [], "self_notes": "", "_usage": usage}

    for block in response.content:
        if block.type == "tool_use":
            result = block.input
            result.setdefault("selected_ids", [])
            result.setdefault("summary", "")
            result.setdefault("preoccupations", [])
            result.setdefault("recommendations", [])
            result.setdefault("self_notes", "")
            result["_usage"] = usage
            return result

    return {
        "selected_ids": [], "summary": "", "preoccupations": [],
        "recommendations": [], "self_notes": "", "_usage": usage,
    }


# Tool schema for editorial revision
_REVISE_TOOL = {
    "name": "revise_entry",
    "description": "Return the revised text of a selected entry.",
    "input_schema": {
        "type": "object",
        "properties": {
            "revised_posts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The revised paragraphs. Return the full text, not a diff. If no changes needed, return the original unchanged.",
            },
            "changes_made": {
                "type": "string",
                "description": "Brief note on what was changed, or 'no changes' if the entry was clean.",
            },
        },
        "required": ["revised_posts", "changes_made"],
    },
}


def revise_entry(
    client: anthropic.Anthropic,
    entry_text: str,
    stimulus_context: str,
    passage_context: str,
) -> dict:
    """Editorially revise a selected entry, catching tics and formulaic habits.

    Returns dict with revised_posts (list of paragraphs) and changes_made.
    """
    soul = _load_prompt("soul.md")
    prompt_template = _load_prompt("revise.md")

    user_msg = prompt_template.format(
        entry_text=entry_text,
        stimulus_context=stimulus_context,
        passage_context=passage_context,
    )

    response, usage = _safe_api_call(
        client, "revise_entry",
        model=DAILY_REVIEW_MODEL,
        max_tokens=2048,
        system=soul,
        messages=[{"role": "user", "content": user_msg}],
        tools=[_REVISE_TOOL],
        tool_choice={"type": "tool", "name": "revise_entry"},
    )
    if response is None:
        return {"revised_posts": [], "changes_made": "API error", "_usage": usage}

    for block in response.content:
        if block.type == "tool_use":
            result = block.input
            result.setdefault("revised_posts", [])
            result.setdefault("changes_made", "")
            result["_usage"] = usage
            return result

    return {"revised_posts": [], "changes_made": "revision failed", "_usage": usage}


def _generate_search_direction(
    client: anthropic.Anthropic,
    self_notes: str | None,
    recent_themes: list[str] | None,
    recent_poets: list[str] | None,
) -> dict:
    """Ask Haiku to generate a search query based on self-notes and recent patterns.

    Returns {"query": str, "reason": str} — the query for ChromaDB and why.
    """
    parts = []
    if self_notes:
        parts.append(f"Your recent self-notes:\n{self_notes}")
    if recent_themes:
        parts.append(f"Themes you've been working with: {', '.join(recent_themes)}")
    if recent_poets:
        parts.append(f"Poets you've used recently: {', '.join(recent_poets)}")

    if not parts:
        # No context — go random
        return {"query": "", "reason": "No self-notes or recent context; exploring freely."}

    user_msg = (
        "You are deciding what to read next from your poetry corpus. "
        "Based on the context below, generate a short search query (a phrase or sentence) "
        "that would find an interesting passage — something you haven't explored enough, "
        "or a direction your self-notes suggest. Drift slightly from your recent themes; "
        "don't just repeat them.\n\n"
        + "\n\n".join(parts)
        + "\n\nRespond with JSON: {\"query\": \"...\", \"reason\": \"...\"}"
    )

    response, usage = _safe_api_call(
        client, "search_direction",
        model=TRIAGE_MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": user_msg}],
    )
    if response is None:
        return {"query": "", "reason": "API error during search direction", "_usage": usage}
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        result = {"query": text[:200], "reason": "Unparseable response, using as raw query."}
    result["_usage"] = usage
    return result


# Tool schema for long-form engage responses
_ENGAGE_TOOL = {
    "name": "engage_response",
    "description": "Produce a long-form response to a bespoke stimulus.",
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["post", "skip"],
                "description": "Whether to post or skip.",
            },
            "paragraphs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-3 paragraphs of critical prose. Each can be as long as the thought requires.",
            },
            "passage_used": {
                "type": "object",
                "properties": {
                    "chunk_id": {
                        "type": "string",
                        "description": "Copy the chunk_id EXACTLY as shown in the [chunk_id: ...] header of the primary passage.",
                    },
                    "poet": {"type": "string"},
                    "poem_title": {"type": "string"},
                },
                "required": ["chunk_id", "poet", "poem_title"],
            },
            "skip_reason": {
                "type": "string",
                "description": "Why we're skipping. Only if decision is skip.",
            },
        },
        "required": ["decision", "paragraphs"],
    },
}


def engage(
    client: anthropic.Anthropic,
    stimulus_text: str,
    passages: list[dict],
    reflection_context: dict | None = None,
) -> dict:
    """Long-form response to a bespoke stimulus (article, provocation, clip).

    Returns dict with decision, paragraphs, passage_used, skip_reason.
    """
    soul = _load_prompt("soul.md")
    prompt_template = _load_prompt("engage.md")

    passages_text = "\n\n".join(
        f"[chunk_id: {p['chunk_id']}]\n"
        f"[{p['poet']} — \"{p.get('poem_title', '')}\" ({p.get('date', '')}), {p.get('work', '')}]\n"
        f"{p['text']}"
        for p in passages
    )

    user_msg = prompt_template.format(
        stimulus_text=stimulus_text,
        passages_text=passages_text,
    )

    # Inject reflection context if available
    if reflection_context:
        parts = []
        if reflection_context.get("self_notes"):
            parts.append(f"NOTES FROM YOUR REVIEWER:\n{reflection_context['self_notes']}")
        if reflection_context.get("seeds"):
            parts.append(reflection_context["seeds"])
        if reflection_context.get("oblique_strategy"):
            parts.append(
                f"OBLIQUE STRATEGY (draw one, follow it or argue with it — let it change the shape of what you write):\n"
                f"\"{reflection_context['oblique_strategy']}\""
            )
        if parts:
            user_msg += "\n\n" + "\n\n".join(parts)

    response, usage = _safe_api_call(
        client, "engage",
        model=COMPOSITION_MODEL,
        max_tokens=2048,
        system=soul,
        messages=[{"role": "user", "content": user_msg}],
        tools=[_ENGAGE_TOOL],
        tool_choice={"type": "tool", "name": "engage_response"},
    )
    if response is None:
        return {"decision": "skip", "mode": "engage", "posts": [], "paragraphs": [],
                "skip_reason": "API error during engage", "_usage": usage}

    for block in response.content:
        if block.type == "tool_use":
            result = block.input
            # Normalise paragraphs
            paragraphs = result.get("paragraphs", [])
            if isinstance(paragraphs, str):
                paragraphs = [paragraphs]
            result["paragraphs"] = [p.strip() for p in paragraphs if isinstance(p, str) and p.strip()]
            # Store as posts for compatibility with the rest of the pipeline
            result["posts"] = result["paragraphs"]
            result["mode"] = "engage"
            pu = result.get("passage_used")
            if isinstance(pu, str):
                result["passage_used"] = {"chunk_id": "", "poet": "", "poem_title": pu}
            elif isinstance(pu, dict):
                pu.setdefault("chunk_id", "")
                pu.setdefault("poet", "")
                pu.setdefault("poem_title", "")
            result.setdefault("decision", "skip")
            result.setdefault("posts", [])
            result.setdefault("paragraphs", [])
            result["_usage"] = usage
            return result

    return {"decision": "skip", "mode": "engage", "posts": [], "paragraphs": [], "skip_reason": "No tool call.", "_usage": usage}


def _inject_self_gen_context(user_msg: str, reflection_context: dict | None) -> str:
    """Append self_notes, seeds, and oblique strategy to a self-gen prompt if available."""
    if not reflection_context:
        return user_msg
    parts = []
    if reflection_context.get("self_notes"):
        parts.append(f"NOTES FROM YOUR REVIEWER:\n{reflection_context['self_notes']}")
    if reflection_context.get("seeds"):
        parts.append(reflection_context["seeds"])
    if reflection_context.get("oblique_strategy"):
        parts.append(
            f"OBLIQUE STRATEGY (draw one, follow it or argue with it — let it change the shape of what you write):\n"
            f"\"{reflection_context['oblique_strategy']}\""
        )
    if parts:
        user_msg += "\n\n" + "\n\n".join(parts)
    return user_msg


def contemplate(
    client: anthropic.Anthropic,
    passage: dict,
    search_reason: str,
    reflection_context: dict | None = None,
) -> dict:
    """Self-generated meditation on a single passage.

    Returns structured dict with decision, mode, posts, passage_used, skip_reason.
    """
    soul = _load_prompt("soul.md")
    prompt_template = _load_prompt("contemplate.md")

    user_msg = prompt_template.format(
        search_reason=search_reason,
        chunk_id=passage["chunk_id"],
        poet=passage["poet"],
        poem_title=passage.get("poem_title", ""),
        date=passage.get("date", ""),
        work=passage.get("work", ""),
        text=passage["text"],
    )
    user_msg = _inject_self_gen_context(user_msg, reflection_context)

    response, usage = _safe_api_call(
        client, "contemplate",
        model=COMPOSITION_MODEL,
        max_tokens=1024,
        system=soul,
        messages=[{"role": "user", "content": user_msg}],
        tools=[_COMPOSE_TOOL],
        tool_choice={"type": "tool", "name": "compose_response"},
    )
    if response is None:
        return {"decision": "skip", "mode": "thought_only", "posts": [],
                "skip_reason": "API error during contemplate", "_usage": usage}

    for block in response.content:
        if block.type == "tool_use":
            result = block.input
            posts = result.get("posts", [])
            if isinstance(posts, str):
                try:
                    parsed = json.loads(posts)
                    posts = parsed if isinstance(parsed, list) else [posts]
                except (json.JSONDecodeError, TypeError):
                    posts = [posts]
            result["posts"] = [p.replace("\n", " ").strip() for p in posts if isinstance(p, str)]
            pu = result.get("passage_used")
            if isinstance(pu, str):
                result["passage_used"] = {"chunk_id": "", "poet": "", "poem_title": pu}
            elif isinstance(pu, dict):
                pu.setdefault("chunk_id", "")
                pu.setdefault("poet", "")
                pu.setdefault("poem_title", "")
            result.setdefault("decision", "skip")
            result.setdefault("mode", "thought_only")
            result["_usage"] = usage
            return result

    return {"decision": "skip", "mode": "thought_only", "posts": [], "skip_reason": "No tool call.", "_usage": usage}


def compare(
    client: anthropic.Anthropic,
    passage_1: dict,
    passage_2: dict,
    search_reason: str,
    reflection_context: dict | None = None,
) -> dict:
    """Self-generated comparison of two passages.

    Returns structured dict with decision, mode, posts, passage_used, skip_reason.
    """
    soul = _load_prompt("soul.md")
    prompt_template = _load_prompt("compare.md")

    user_msg = prompt_template.format(
        search_reason=search_reason,
        chunk_id_1=passage_1["chunk_id"],
        poet_1=passage_1["poet"],
        poem_title_1=passage_1.get("poem_title", ""),
        date_1=passage_1.get("date", ""),
        work_1=passage_1.get("work", ""),
        text_1=passage_1["text"],
        chunk_id_2=passage_2["chunk_id"],
        poet_2=passage_2["poet"],
        poem_title_2=passage_2.get("poem_title", ""),
        date_2=passage_2.get("date", ""),
        work_2=passage_2.get("work", ""),
        text_2=passage_2["text"],
    )
    user_msg = _inject_self_gen_context(user_msg, reflection_context)

    response, usage = _safe_api_call(
        client, "compare",
        model=COMPOSITION_MODEL,
        max_tokens=1024,
        system=soul,
        messages=[{"role": "user", "content": user_msg}],
        tools=[_COMPOSE_TOOL],
        tool_choice={"type": "tool", "name": "compose_response"},
    )
    if response is None:
        return {"decision": "skip", "mode": "thought_only", "posts": [],
                "skip_reason": "API error during compare", "_usage": usage}

    for block in response.content:
        if block.type == "tool_use":
            result = block.input
            posts = result.get("posts", [])
            if isinstance(posts, str):
                try:
                    parsed = json.loads(posts)
                    posts = parsed if isinstance(parsed, list) else [posts]
                except (json.JSONDecodeError, TypeError):
                    posts = [posts]
            result["posts"] = [p.replace("\n", " ").strip() for p in posts if isinstance(p, str)]
            pu = result.get("passage_used")
            if isinstance(pu, str):
                result["passage_used"] = {"chunk_id": "", "poet": "", "poem_title": pu}
            elif isinstance(pu, dict):
                pu.setdefault("chunk_id", "")
                pu.setdefault("poet", "")
                pu.setdefault("poem_title", "")
            result.setdefault("decision", "skip")
            result.setdefault("mode", "thought_only")
            result["_usage"] = usage
            return result

    return {"decision": "skip", "mode": "thought_only", "posts": [], "skip_reason": "No tool call.", "_usage": usage}


    # brain.run() was here — removed as dead code.
    # Engine.process() in engine.py handles the full pipeline with
    # rate limiting, anti-repetition, reflection context, and logging.
