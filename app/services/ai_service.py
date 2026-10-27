"""Provider-independent AI service with limits, bounded history and deterministic fallbacks."""
from __future__ import annotations

import re
from collections import Counter

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database.models import AIMessage, AIUsage
from app.services.ollama_service import (AIError, AIQuotaExceeded, AIUnavailable, OllamaClient,
                                         OpenAICompatClient)
from app.utils.dates import utcnow

SYSTEM = ("You are Uday's executive Personal Assistant (PA). "
          "You are highly intelligent, organized, proactive, polite, and practical. "
          "Always address Uday respectfully. Provide clear, direct, structured, and actionable assistance. "
          "You assist with daily schedule management, tasks, reminders, software engineering, client management, and freelance work. "
          "Never claim to run code or destructive commands directly. Keep answers concise, high-quality, and useful.")

MODES = {
    "ask": SYSTEM,
    "explain": SYSTEM + " Explain the topic simply with a short example; use code only for illustration.",
    "summarize": SYSTEM + " Summarize the user's text in at most 6 bullet points.",
    "rewrite": SYSTEM + " Rewrite the user's text to be clearer and professional, keeping the meaning. Output only the rewrite.",
    "devhelp": SYSTEM + " You are helping with a programming question. Explain errors and code clearly; never execute anything.",
}

OFFLINE_NOTE = ("🤖 The AI model is offline or not configured, so I used a built-in helper instead.\n"
                "To enable AI: install Ollama, run `ollama pull <model>`, set OLLAMA_MODEL in .env and restart.")


def build_client(settings: Settings):
    if settings.ai_provider == "ollama" and settings.ollama_model:
        return OllamaClient(settings.ollama_base_url, settings.ollama_model, settings.ai_timeout_seconds, settings.ai_max_tokens)
    if settings.ai_provider == "openai_compatible" and settings.ai_enabled:
        return OpenAICompatClient(settings.hosted_ai_base_url, settings.hosted_ai_api_key, settings.hosted_ai_model,
                                  settings.ai_timeout_seconds, settings.ai_max_tokens)
    return None


def _today() -> str:
    return utcnow().strftime("%Y-%m-%d")


def usage_today(session: Session, user_id: int) -> int:
    row = session.scalar(select(AIUsage).where(AIUsage.user_id == user_id, AIUsage.day == _today()))
    return row.count if row else 0


def _bump_usage(session: Session, user_id: int) -> None:
    row = session.scalar(select(AIUsage).where(AIUsage.user_id == user_id, AIUsage.day == _today()))
    if row is None:
        session.add(AIUsage(user_id=user_id, day=_today(), count=1))
    else:
        row.count += 1


def clear_history(session: Session, user_id: int) -> int:
    return session.execute(delete(AIMessage).where(AIMessage.user_id == user_id)).rowcount or 0


class AIService:
    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        self.client = client if client is not None else build_client(settings)

    async def status(self) -> str:
        if self.client is None:
            return "disabled (no model configured)"
        return "online" if await self.client.is_available() else "offline"

    async def respond(self, session: Session, user_id: int, text: str, mode: str = "ask", *,
                      use_history: bool = True) -> str:
        """Return model text. Raises AIError subclasses (message is user-safe) when unavailable."""
        if self.client is None:
            raise AIUnavailable("AI is not configured (set OLLAMA_MODEL).")
        text = (text or "").strip()
        if not text:
            raise AIError("Please provide some text.")
        if len(text) > self.settings.ai_max_input_chars:
            raise AIError(f"Input too long ({len(text)} chars). Limit is {self.settings.ai_max_input_chars}.")
        if usage_today(session, user_id) >= self.settings.ai_daily_request_limit:
            raise AIQuotaExceeded(f"Daily AI limit reached ({self.settings.ai_daily_request_limit}). Try again tomorrow.")
        messages = [{"role": "system", "content": MODES.get(mode, SYSTEM)}]
        if use_history and self.settings.ai_history_messages:
            hist = list(session.scalars(select(AIMessage).where(AIMessage.user_id == user_id)
                                        .order_by(AIMessage.id.desc()).limit(self.settings.ai_history_messages)))
            messages += [{"role": m.role, "content": m.content} for m in reversed(hist)]
        messages.append({"role": "user", "content": text})
        reply = await self.client.chat(messages)  # may raise AIError
        reply = reply[: self.settings.ai_max_tokens * 6] or "(the model returned an empty answer)"
        _bump_usage(session, user_id)
        if use_history:
            session.add(AIMessage(user_id=user_id, role="user", content=text))
            session.add(AIMessage(user_id=user_id, role="assistant", content=reply))
            session.flush()
            keep = self.settings.ai_history_messages * 2
            old = list(session.scalars(select(AIMessage.id).where(AIMessage.user_id == user_id)
                                       .order_by(AIMessage.id.desc()).offset(keep)))
            if old:
                session.execute(delete(AIMessage).where(AIMessage.id.in_(old)))
        return reply


# ---- deterministic fallbacks (no AI needed) ------------------------------------------------
_STOP = set("the a an and or but if of to in on for with is are was were be been it this that as at by from "
            "i you he she we they my your our their so not no can will would should could do does did".split())


def fallback_summary(text: str, max_sentences: int = 3) -> str:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if len(s.strip()) > 15]
    if len(sentences) <= max_sentences:
        return " ".join(sentences) or text[:300]
    freq = Counter(w for w in re.findall(r"[a-z']+", text.lower()) if w not in _STOP and len(w) > 2)
    scored = [(sum(freq[w] for w in re.findall(r"[a-z']+", s.lower())) / (len(s.split()) ** 0.7), i, s)
              for i, s in enumerate(sentences)]
    top = sorted(sorted(scored, reverse=True)[:max_sentences], key=lambda t: t[1])
    return "\n".join(f"• {s}" for _, _, s in top)


def fallback_rewrite(text: str) -> str:
    t = re.sub(r"[ \t]+", " ", text.strip())
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    t = re.sub(r"([,;:])(?=[A-Za-z])", r"\1 ", t)
    t = re.sub(r"\bi\b", "I", t)
    t = re.sub(r"([.!?]\s+|^)([a-z])", lambda m: m.group(1) + m.group(2).upper(), t)
    if t and t[-1] not in ".!?":
        t += "."
    return t
