"""Three-stage brain: triage → retrieval → composition."""

import json
from pathlib import Path

import anthropic

from src import retriever
from src import safety

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "config" / "prompts"

TRIAGE_MODEL = "claude-haiku-4-5-20251001"
COMPOSITION_MODEL = "claude-sonnet-4-5-20250929"


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text().strip()


def triage(client: anthropic.Anthropic, stimulus: str) -> dict:
    """Stage 1: decide whether to engage.

    Returns {"engage": bool, "reason": str} and when engaging also
    {"search_queries": [...], "the_problem": "..."}.
    """
    system = _load_prompt("triage.md")
    response = client.messages.create(
        model=TRIAGE_MODEL,
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": stimulus}],
    )
    text = response.content[0].text.strip()
    # Strip markdown fences if the model wraps its response
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        result = {"engage": False, "reason": f"Triage returned unparseable response: {text[:100]}"}
    result["_usage"] = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return result


def retrieve(queries: list[str], n_results: int = 5, exclude_ids: set[str] | None = None) -> list[dict]:
    """Stage 2: multi-query semantic search over the corpus.

    Returns list of passage dicts.
    """
    return retriever.search_multi(queries, n_results=n_results, exclude_ids=exclude_ids)


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

        if context_parts:
            user_msg += "\n\n" + "\n\n".join(context_parts)

    response = client.messages.create(
        model=COMPOSITION_MODEL,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
        tools=[_COMPOSE_TOOL],
        tool_choice={"type": "tool", "name": "compose_response"},
    )

    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }

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
            result["_usage"] = usage
            return result

    # Fallback — shouldn't happen with tool_choice forced
    return {"decision": "skip", "mode": "thought_only", "posts": [], "skip_reason": "No tool call in response."}


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

    response = client.messages.create(
        model=REFLECT_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": user_msg}],
        tools=[_REFLECT_TOOL],
        tool_choice={"type": "tool", "name": "log_reflection"},
    )

    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }

    for block in response.content:
        if block.type == "tool_use":
            result = block.input
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
                        "id": {"type": "integer", "description": "Interaction ID to publish."},
                        "reason": {"type": "string", "description": "Brief reason for selecting this entry (1 sentence)."},
                    },
                    "required": ["id", "reason"],
                },
                "description": "3-7 interaction IDs to publish, with reasoning.",
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
                    "Free-form notes to your future self (the composition model). "
                    "Tonal corrections, poets to seek out, patterns to break, "
                    "things you noticed about your own writing today."
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

    response = client.messages.create(
        model=DAILY_REVIEW_MODEL,
        max_tokens=2048,
        system=soul,
        messages=[{"role": "user", "content": user_msg}],
        tools=[_DAILY_REVIEW_TOOL],
        tool_choice={"type": "tool", "name": "daily_review"},
    )

    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }

    for block in response.content:
        if block.type == "tool_use":
            result = block.input
            result["_usage"] = usage
            return result

    return {
        "selected_ids": [], "summary": "", "preoccupations": [],
        "recommendations": [], "self_notes": "", "_usage": usage,
    }


def run(
    client: anthropic.Anthropic,
    stimulus: str,
    exclude_ids: set[str] | None = None,
    reflection_context: dict | None = None,
) -> dict:
    """Run the full triage → retrieval → composition pipeline.

    reflection_context is passed through to compose() for preference injection.

    Returns:
        {
            "stimulus": str,
            "triage": {"engage": bool, "reason": str, "search_queries": [...], "the_problem": "..."},
            "passages": [...] or None,
            "composition": {...} or None,
        }
    """
    result = {"stimulus": stimulus, "triage": None, "passages": None, "composition": None,
              "tokens_in": 0, "tokens_out": 0}

    # Stage 1
    result["triage"] = triage(client, stimulus)
    triage_usage = result["triage"].pop("_usage", {})
    result["tokens_in"] += triage_usage.get("input_tokens", 0)
    result["tokens_out"] += triage_usage.get("output_tokens", 0)

    if not result["triage"].get("engage"):
        return result

    # Stage 2: use triage's search queries instead of raw stimulus
    search_queries = result["triage"].get("search_queries", [stimulus])
    raw_passages = retrieve(search_queries, exclude_ids=exclude_ids)
    result["passages"] = safety.filter_passages(raw_passages)

    if not result["passages"]:
        result["composition"] = {
            "decision": "skip",
            "mode": "thought_only",
            "posts": [],
            "skip_reason": "All retrieved passages filtered by safety check.",
        }
        return result

    # Stage 3: pass the_problem and reflection context to composition
    the_problem = result["triage"].get("the_problem", "")
    result["composition"] = compose(
        client, stimulus, result["passages"], the_problem,
        reflection_context=reflection_context,
    )
    comp_usage = result["composition"].pop("_usage", {})
    result["tokens_in"] += comp_usage.get("input_tokens", 0)
    result["tokens_out"] += comp_usage.get("output_tokens", 0)

    return result
