"""Текстовые утилиты: слова, упрощённый стемминг, предложения. Повторяют логику интерфейса (js/core/util.js),
поэтому оценки на сервере и в браузере совпадают."""
import math
import re
from decimal import ROUND_HALF_UP, Decimal

WORD_RE = re.compile(r"[a-zа-яёәғқңөұүһі0-9]+")
STOP = set(("и в во на по с со к ко о об от до для из у за над под при про без через как что это "
            "а но или ли же бы не ни то так также the of and to in for on with a an is are be by").split())
SENT_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+(?=[A-ZА-ЯЁ«\"(])")


def to_str(s) -> str:
    return "" if s is None else str(s)


def words(s) -> list[str]:
    return WORD_RE.findall(to_str(s).lower())


def stems(s, min_len: int = 4) -> list[str]:
    """Простейший стемминг: начало значимого слова (4–6 символов)."""
    out: list[str] = []
    for w in words(s):
        if len(w) < min_len or w in STOP:
            continue
        st = w[: min(6, max(4, len(w) - 2))]
        if st not in out:
            out.append(st)
    return out


def split_list(s) -> list[str]:
    parts = re.split(r"\n|;|•", to_str(s))
    out = [re.sub(r"^[\s\-–—*\d.)]+", "", p).strip() for p in parts]
    return [p for p in out if p]


def sentences(s) -> list[str]:
    text = re.sub(r"\s+", " ", to_str(s))
    return [x.strip() for x in SENT_SPLIT_RE.split(text) if len(x.strip()) > 2]


def js_round(x: float) -> int:
    """Math.round из JavaScript: половина округляется вверх."""
    return math.floor(x + 0.5)


def js_fixed1(x: float) -> str:
    """Number.prototype.toFixed(1) из JavaScript."""
    return str(Decimal(x).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
