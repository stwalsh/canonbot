"""Chat mode — conversational interface to Lucubrator.

ChatSession handles turn-taking, optional corpus retrieval, and model
escalation (Sonnet baseline, Opus when the conversation goes deep).
"""

import json
import logging
from pathlib import Path

import anthropic

from src import retriever, safety
from src.store import Store

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "config" / "prompts"

SONNET_MODEL = "claude-sonnet-4-6"
OPUS_MODEL = "claude-opus-4-6"

# ChromaDB similarity threshold — scores above this suggest a strong corpus match.
# ChromaDB distances are L2; lower = more similar. Threshold is inverted for "quality".
_RETRIEVAL_QUALITY_THRESHOLD = 0.85

# Sliding window: keep this many recent turns verbatim.
_WINDOW_SIZE = 12

# Tool definition for optional corpus search
_SEARCH_TOOL = {
    "name": "search_corpus",
    "description": (
        "Search the poetry corpus for passages relevant to the current conversation. "
        "Use when a specific poem, image, problem, or line of thought could be illuminated "
        "by the canon. Don't use every turn — only when the corpus might genuinely contribute."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A phrase or sentence to search for in the corpus.",
            },
        },
        "required": ["query"],
    },
}


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text().strip()


class ChatSession:
    def __init__(
        self,
        client: anthropic.Anthropic,
        store: Store,
        user_name: str,
        session_id: int | None = None,
    ):
        self.client = client
        self.store = store
        self.user_name = user_name

        if session_id:
            self.session_id = session_id
            session = store.get_chat_session(session_id)
            self.summary = session.get("summary", "") if session else ""
        else:
            self.session_id = store.create_chat_session(user_name)
            self.summary = ""

        self._soul = _load_prompt("soul.md")
        self._chat_prompt = _load_prompt("chat.md")
        self._turns_since_summary = 0

    def _build_system(self) -> str:
        """Build the system prompt with soul + chat instructions + context."""
        parts = [self._soul, "\n---\n", self._chat_prompt]

        # Inject self-notes if available
        latest = self.store.get_latest_reflection(period="daily")
        if latest and latest.get("self_notes"):
            parts.append(f"\n\nYOUR CURRENT PREOCCUPATIONS (from your most recent self-review):\n{latest['self_notes']}")

        return "\n".join(parts)

    def _build_messages(self, user_message: str) -> list[dict]:
        """Build the message list: summary + sliding window + new message."""
        messages = []

        # Prepend summary of older turns if we have one
        if self.summary:
            messages.append({
                "role": "user",
                "content": f"[Summary of our earlier conversation: {self.summary}]",
            })
            messages.append({
                "role": "assistant",
                "content": "I remember. Go on.",
            })

        # Sliding window of recent turns
        recent = self.store.get_recent_chat_turns(self.session_id, limit=_WINDOW_SIZE)
        for turn in recent:
            messages.append({"role": turn["role"], "content": turn["content"]})

        # New user message
        messages.append({"role": "user", "content": user_message})
        return messages

    def _do_retrieval(self, query: str) -> tuple[list[dict], float]:
        """Search the corpus. Returns (passages, best_score)."""
        raw = retriever.search_multi([query], n_results=5)
        passages = safety.filter_passages(raw)
        best_score = 0.0
        if passages and "distance" in passages[0]:
            # Convert distance to similarity (lower distance = higher similarity)
            best_score = 1.0 / (1.0 + passages[0]["distance"])
        return passages, best_score

    def _format_passages(self, passages: list[dict]) -> str:
        return "\n\n".join(
            f"[{p['poet']} — \"{p.get('poem_title', '')}\" ({p.get('date', '')})]"
            f"\n{p['text']}"
            for p in passages[:3]
        )

    def _should_escalate(self, retrieval_score: float, response_text: str) -> bool:
        """Decide whether to re-run this turn on Opus."""
        if retrieval_score >= _RETRIEVAL_QUALITY_THRESHOLD:
            return True
        # Check if Sonnet flagged escalation
        if "escalate" in response_text.lower()[:50]:
            return True
        return False

    def _compress_summary(self, turns: list[dict]) -> str:
        """Compress older turns into a summary using Haiku."""
        turn_text = "\n".join(
            f"{'User' if t['role'] == 'user' else 'Lucubrator'}: {t['content'][:200]}"
            for t in turns
        )
        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                messages=[{
                    "role": "user",
                    "content": (
                        "Compress this conversation into a 2-3 sentence summary. "
                        "Preserve key topics, any poems or poets discussed, and "
                        "the direction the conversation was heading.\n\n"
                        + turn_text
                    ),
                }],
            )
            return response.content[0].text.strip()
        except Exception as e:
            log.warning("Summary compression failed: %s", e)
            return self.summary  # keep old summary

    def _maybe_update_summary(self):
        """Every N turns beyond the window, compress older turns."""
        self._turns_since_summary += 1
        if self._turns_since_summary < _WINDOW_SIZE // 2:
            return

        all_turns = self.store.get_chat_turns(self.session_id)
        if len(all_turns) <= _WINDOW_SIZE:
            return

        # Turns outside the window need summarising
        older = all_turns[:-_WINDOW_SIZE]
        self.summary = self._compress_summary(older)
        self.store.update_chat_summary(self.session_id, self.summary)
        self._turns_since_summary = 0

    def greeting(self) -> str:
        """Generate the opening turn — bot is mid-thought, invites the interlocutor."""
        system = self._build_system()

        messages = [{"role": "user", "content": f"[{self.user_name} has just arrived.]"}]

        try:
            response = self.client.messages.create(
                model=SONNET_MODEL,
                max_tokens=300,
                system=system,
                messages=messages,
            )
            text = response.content[0].text.strip()
        except Exception as e:
            log.error("Greeting failed: %s", e)
            text = "I've been reading. What brings you?"

        # Log the greeting
        self.store.add_chat_turn(self.session_id, "assistant", text, model_used=SONNET_MODEL)
        return text

    def respond(self, user_message: str) -> dict:
        """Process a user message and return a response.

        Returns: {
            "text": str,
            "model": str,
            "passages": list[dict] | None,
            "escalated": bool,
        }
        """
        # Log user turn
        self.store.add_chat_turn(self.session_id, "user", user_message)

        system = self._build_system()
        messages = self._build_messages(user_message)

        # First pass: Sonnet with optional tool use
        try:
            response = self.client.messages.create(
                model=SONNET_MODEL,
                max_tokens=800,
                system=system,
                messages=messages,
                tools=[_SEARCH_TOOL],
            )
        except Exception as e:
            log.error("Chat response failed: %s", e)
            return {"text": "I've lost my thread. Say that again?", "model": SONNET_MODEL,
                    "passages": None, "escalated": False}

        # Handle tool use — Sonnet may want to search the corpus
        passages = None
        retrieval_score = 0.0

        if response.stop_reason == "tool_use":
            tool_block = next(
                (b for b in response.content if b.type == "tool_use"), None
            )
            if tool_block and tool_block.name == "search_corpus":
                query = tool_block.input.get("query", "")
                passages, retrieval_score = self._do_retrieval(query)

                # Feed results back and get final response
                tool_result_content = (
                    self._format_passages(passages) if passages
                    else "No relevant passages found."
                )
                messages.append({"role": "assistant", "content": response.content})
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": tool_block.id,
                         "content": tool_result_content},
                    ],
                })

                try:
                    response = self.client.messages.create(
                        model=SONNET_MODEL,
                        max_tokens=800,
                        system=system,
                        messages=messages,
                    )
                except Exception as e:
                    log.error("Chat follow-up failed: %s", e)
                    return {"text": "Something slipped. Go on?", "model": SONNET_MODEL,
                            "passages": passages, "escalated": False}

        # Extract text
        text = "".join(
            b.text for b in response.content if hasattr(b, "text")
        ).strip()

        # Check escalation
        escalated = False
        if passages and self._should_escalate(retrieval_score, text):
            opus_result = self._escalate_to_opus(system, messages, passages)
            if opus_result:
                text = opus_result
                escalated = True

        model_used = OPUS_MODEL if escalated else SONNET_MODEL

        # Log assistant turn
        self.store.add_chat_turn(
            self.session_id, "assistant", text,
            model_used=model_used,
            passages_used=[
                {"chunk_id": p.get("chunk_id"), "poet": p.get("poet"),
                 "poem_title": p.get("poem_title")}
                for p in passages
            ] if passages else None,
        )

        # Maybe update summary
        self._maybe_update_summary()

        return {
            "text": text,
            "model": model_used,
            "passages": passages,
            "escalated": escalated,
        }

    def _escalate_to_opus(
        self, system: str, messages: list[dict], passages: list[dict],
    ) -> str | None:
        """Re-run the turn on Opus for a deeper response."""
        # Inject a nudge that this is a moment to go deep
        escalation_system = (
            system + "\n\n"
            "The conversation has hit something real — a strong match between "
            "what's being discussed and what the corpus contains. Take the space "
            "you need. This is a moment for depth, not brevity."
        )

        try:
            response = self.client.messages.create(
                model=OPUS_MODEL,
                max_tokens=2048,
                system=escalation_system,
                messages=messages,
            )
            return "".join(
                b.text for b in response.content if hasattr(b, "text")
            ).strip()
        except Exception as e:
            log.error("Opus escalation failed: %s", e)
            return None
