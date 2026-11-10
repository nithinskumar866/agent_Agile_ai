"""Retrieval core: split documents into passages and rank them against a question (BM25).

Pure functions and one small class, no I/O and no model, so every behaviour is unit-testable.
This is the part that is swapped for Vertex AI RAG Engine on Google Cloud (which adds embeddings,
PDF parsing and managed storage); chunking by heading, citing a source, and refusing to answer when
nothing relevant is found stay the same.

Why BM25 and not embeddings here: it needs no model or download, it is explainable (you can see
which words matched), and it exposes the failure that matters most for RAG: a question the documents
do not answer must return nothing, not the least-bad passage.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

MAX_CHUNK_CHARS = 900
K1, B = 1.5, 0.75
DEFAULT_MIN_COVERAGE = 0.5
COMMON_TERM_SHARE = 0.6  # a query word found in at least this share of passages does not discriminate

_STOPWORDS = frozenset("""a an and are as at be been but by can could did do does for from had has have how i if in into is it its
me my of on or our so than that the their them then there these they this to us was we were what when where which who whom
why will with would you your yours about any also not no yes please tell""".split())
_WORD = re.compile(r"[a-z0-9]+(?:[.:][0-9]+)*")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def stem(word: str) -> str:
    """A deliberately light stemmer: enough for plurals and common endings, easy to reason about."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 4 and re.search(r"(ss|x|z|ch|sh)es$", word):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    return [stem(w) for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]


@dataclass(frozen=True)
class Chunk:
    doc: str       # file name, e.g. faq.md
    section: str   # heading path, e.g. "Delivery" or "Policies > Refunds"; "" before the first heading
    text: str
    index: int     # position within the document, for stable ordering
    title: str = ""  # the document's own title (its H1): searched, but kept out of the citation

    def searchable(self) -> str:
        return f"{self.title} {self.section} {self.text}"


def chunk_markdown(text: str, doc: str, max_chars: int = MAX_CHUNK_CHARS) -> list[Chunk]:
    """Split by heading, then by paragraph so no passage exceeds `max_chars` (one oversized
    paragraph becomes its own passage). The heading path travels with every passage."""
    chunks: list[Chunk] = []
    path: list[tuple[int, str]] = []
    buffer: list[str] = []

    def flush() -> None:
        body = "\n\n".join(buffer).strip()
        buffer.clear()
        if body:
            title = path[0][1] if path and path[0][0] == 1 else ""
            chunks.append(Chunk(doc, " > ".join(t for level, t in path if level > 1), body, len(chunks), title))

    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        block = block.strip()
        if not block:
            continue
        heading = _HEADING.match(block.splitlines()[0])
        if heading:
            flush()
            level = len(heading.group(1))
            while path and path[-1][0] >= level:
                path.pop()
            path.append((level, heading.group(2)))
            block = "\n".join(block.splitlines()[1:]).strip()
            if not block:
                continue
        if buffer and sum(len(b) for b in buffer) + len(block) > max_chars:
            flush()
        buffer.append(block)
    flush()
    return chunks


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float
    coverage: float            # share of the question's distinct terms found in the passage
    matched: tuple[str, ...]   # which terms matched, to explain the ranking


@dataclass
class Index:
    chunks: list[Chunk]
    _tf: list[Counter] = field(default_factory=list, repr=False)
    _df: Counter = field(default_factory=Counter, repr=False)
    _avg_len: float = 0.0

    def __post_init__(self) -> None:
        self._tf = [Counter(tokenize(c.searchable())) for c in self.chunks]
        for tf in self._tf:
            self._df.update(tf.keys())
        self._avg_len = (sum(sum(tf.values()) for tf in self._tf) / len(self._tf)) if self._tf else 0.0

    def search(self, query: str, k: int = 4, min_coverage: float = DEFAULT_MIN_COVERAGE) -> list[Hit]:
        """The best `k` passages, or [] when none covers at least `min_coverage` of the question's
        terms. Returning nothing is the correct answer to a question the documents do not cover."""
        terms = list(dict.fromkeys(tokenize(query)))
        if not terms or not self.chunks:
            return []
        n, hits = len(self.chunks), []
        # A word in nearly every passage (the client's own name, say) says nothing about which passage
        # answers the question, so it must not count towards coverage: "how many employees does Acme
        # Bakery have" would otherwise match every document on "Acme Bakery" alone.
        distinctive = [t for t in terms if self._df[t] / n < COMMON_TERM_SHARE]
        terms = distinctive or terms
        for chunk, tf in zip(self.chunks, self._tf):
            matched = [t for t in terms if tf[t]]
            coverage = len(matched) / len(terms)
            if not matched or coverage < min_coverage:
                continue
            length = sum(tf.values())
            score = sum(
                math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                * tf[t] * (K1 + 1) / (tf[t] + K1 * (1 - B + B * length / (self._avg_len or 1)))
                for t in matched)
            hits.append(Hit(chunk, round(score, 3), round(coverage, 2), tuple(matched)))
        hits.sort(key=lambda h: (-h.coverage, -h.score, h.chunk.doc, h.chunk.index))
        return hits[:k]
