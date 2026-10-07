"""Heading-aware chunking: chunks never cross section boundaries; long sections are packed by
paragraph (then by sentence) up to `target_chars`, with a sentence-aligned overlap."""

import re
from dataclasses import dataclass

from brand_mcp.rag.parsing import Block

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


@dataclass(frozen=True)
class TextChunk:
    section: str
    text: str

    def embedding_text(self) -> str:
        """Section path is part of what we embed: «Тон голоса > Эмодзи» is a strong signal."""
        return f"{self.section}\n{self.text}" if self.section else self.text


def _pieces(text: str, target: int) -> list[tuple[int, str]]:
    """(paragraph index, text): whole paragraphs, or sentences of over-long paragraphs."""
    pieces: list[tuple[int, str]] = []
    for index, raw in enumerate(re.split(r"\n\s*\n", text)):
        paragraph = raw.strip()
        if not paragraph:
            continue
        if len(paragraph) <= target:
            pieces.append((index, paragraph))
        else:
            pieces.extend((index, s) for s in _SENTENCE_END.split(paragraph) if s.strip())
    return pieces


def _join(pieces: list[tuple[int, str]]) -> str:
    """Sentences of one paragraph join with a space, paragraphs with a blank line."""
    out = ""
    for position, (index, piece) in enumerate(pieces):
        if position:
            out += " " if pieces[position - 1][0] == index else "\n\n"
        out += piece
    return out


def _tail(text: str, overlap: int) -> str:
    if overlap <= 0 or len(text) <= overlap:
        return ""
    sentences = _SENTENCE_END.split(text)
    tail: list[str] = []
    for sentence in reversed(sentences):
        if sum(len(s) for s in tail) + len(sentence) > overlap:
            break
        tail.insert(0, sentence)
    return " ".join(tail)


def chunk_blocks(blocks: list[Block], *, target_chars: int, overlap_chars: int) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    for block in blocks:
        current: list[tuple[int, str]] = []
        size = 0
        for index, piece in _pieces(block.text, target_chars):
            if current and size + len(piece) > target_chars:
                text = _join(current)
                chunks.append(TextChunk(block.section, text))
                overlap = _tail(text, overlap_chars)
                # The overlap continues the paragraph it was cut from.
                current = [(current[-1][0], overlap)] if overlap else []
                size = len(overlap)
            current.append((index, piece))
            size += len(piece)
        if current:
            chunks.append(TextChunk(block.section, _join(current)))
    return chunks
