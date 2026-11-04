import re

_BREAK = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9\"'(])")
_BULLET = re.compile(r"^\s*(?:[-*•]+|\d+[.)])\s*")


def split(text):
    sentences = []
    for block in str(text or "").replace("\r", "\n").split("\n"):
        block = _BULLET.sub("", block).strip()
        if not block:
            continue
        for part in _BREAK.split(block):
            part = " ".join(part.split())
            if len(part) >= 2:
                sentences.append(part)
    return sentences


def normalise(text):
    return " ".join(re.sub(r"[\u2018\u2019]", "'", re.sub(r"[\u201c\u201d]", '"', str(text or ""))).lower().split())


def quote_in(quote, sentences, numbers):
    q = normalise(quote).strip(" .\"'")
    if len(q) < 3:
        return False
    joined = " ".join(normalise(sentences[n - 1]) for n in numbers if 1 <= n <= len(sentences))
    return q in joined
