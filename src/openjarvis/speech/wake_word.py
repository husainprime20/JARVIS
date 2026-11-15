"""Wake-word spotting on speech-to-text transcripts.

JARVIS listens continuously, transcribes each utterance locally and reacts
only when it is addressed by name ("Jarvis, open Spotify"). Matching the
transcript instead of running a separate keyword-spotting model means no
extra dependencies or model downloads, and it works with every STT backend.

Speech recognisers often mishear names, so common misspellings of the wake
word are accepted too.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple

# How speech recognisers commonly spell a spoken "Jarvis".
_KNOWN_VARIANTS: Dict[str, Tuple[str, ...]] = {
    "jarvis": (
        "jarvis",
        "jarviss",
        "jervis",
        "jarves",
        "jarvus",
        "javis",
        "jarbis",
        "garvis",
        "charvis",
        "service",  # "hey jarvis" -> "hey service" with small Whisper models
    ),
}
# Ordinary words that only count after a greeting ("hey service").
_GREETING_ONLY = frozenset({"service"})

_GREETINGS = ("hey", "hi", "hello", "ok", "okay", "yo", "oi", "so")
_FILLERS = frozenset({"um", "uh", "er", "ah", "oh", "hmm", "please", "and", "now"})
_SKIPPABLE = _FILLERS | frozenset(_GREETINGS)
_EDGE_CHARS = " \t\r\n,.!?;:-\u2013\u2014"

_STOP_PHRASES = frozenset(
    {
        "goodbye",
        "good bye",
        "bye bye",
        "stop listening",
        "go to sleep",
        "shut down",
        "exit",
        "quit",
    }
)
_CANCEL_PHRASES = frozenset(
    {"never mind", "nevermind", "cancel", "forget it", "nothing", "thats all"}
)
# Whisper's favourite hallucinations on silence or background noise.
_NOISE_TRANSCRIPTS = frozenset(
    {
        "you",
        "thank you",
        "thanks for watching",
        "thank you for watching",
        "please subscribe",
        "bye",
        "music",
        "applause",
        "silence",
        "laughter",
    }
)


def _normalise(text: str) -> str:
    """Lower-case, strip accents and punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("\u2019", "").replace("'", "")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_command(raw: str) -> str:
    """Trim punctuation plus leading greetings/fillers, keeping the wording."""
    text = raw.strip(_EDGE_CHARS)
    while True:
        lead = re.match(r"(\w+)\b[\s,.!?;:]*", text)
        if not lead or lead.group(1).lower() not in _SKIPPABLE:
            break
        text = text[lead.end() :]
    while True:
        tail = re.search(r"[\s,.!?;:]*\b(\w+)$", text)
        if not tail or tail.group(1).lower() not in _FILLERS:
            break
        text = text[: tail.start()]
    return text.strip(_EDGE_CHARS)


@dataclass(frozen=True)
class WakeMatch:
    """A transcript that addressed JARVIS."""

    command: str  # the request without the wake word ("" = just the name)
    heard: str  # the full transcript


class WakeWordDetector:
    """Find the wake word in a transcript and extract the command."""

    def __init__(
        self, wake_word: str = "jarvis", extra_variants: Iterable[str] = ()
    ) -> None:
        word = _normalise(wake_word) or "jarvis"
        variants = set(_KNOWN_VARIANTS.get(word, (word,)))
        variants.add(word)
        variants.update(n for n in (_normalise(v) for v in extra_variants) if n)
        self.wake_word = word
        self.variants = frozenset(variants)
        names = "|".join(
            r"\s*".join(re.escape(part) for part in v.split())
            for v in sorted(self.variants, key=len, reverse=True)
        )
        greetings = "|".join(_GREETINGS)
        self._pattern = re.compile(
            rf"(?:\b(?P<greeting>{greetings})\b[\s,.!]*)?\b(?P<name>{names})\b",
            re.IGNORECASE,
        )

    def find(self, transcript: str) -> Optional[WakeMatch]:
        """Return the request if *transcript* addresses JARVIS, else ``None``.

        "Jarvis, what's the time?" and "What's the time, Jarvis?" both match.
        The name must open or close the sentence (after at most one lead-in
        word, as in "Okay so Jarvis ..."), so merely mentioning Jarvis
        mid-sentence does not wake it.
        """
        original = (transcript or "").strip()
        if not original:
            return None
        for match in self._pattern.finditer(original):
            name = _normalise(match.group("name"))
            if name in _GREETING_ONLY and not match.group("greeting"):
                continue
            before_raw = original[: match.start()]
            after_raw = original[match.end() :]
            leading = [w for w in _normalise(before_raw).split() if w not in _SKIPPABLE]
            after = _normalise(after_raw).split()
            if not leading:
                return WakeMatch(_strip_command(after_raw), original)
            if all(w in _FILLERS for w in after):
                return WakeMatch(_strip_command(before_raw), original)
            if len(leading) == 1 and len(after) >= 2:
                return WakeMatch(_strip_command(after_raw), original)
        return None


def is_stop_command(text: str) -> bool:
    """True for "goodbye", "stop listening" and similar."""
    clean = _normalise(text)
    for prefix in ("jarvis ", "ok ", "okay "):
        clean = clean.removeprefix(prefix)
    return clean.removesuffix(" jarvis") in _STOP_PHRASES


def is_cancel_command(text: str) -> bool:
    """True for "never mind", "cancel" and similar."""
    return _normalise(text) in _CANCEL_PHRASES


def is_noise_transcript(text: str) -> bool:
    """True for empty or junk transcripts produced from background noise."""
    clean = _normalise(text)
    return len(clean) < 2 or clean in _NOISE_TRANSCRIPTS


__all__ = [
    "WakeMatch",
    "WakeWordDetector",
    "is_cancel_command",
    "is_noise_transcript",
    "is_stop_command",
]
