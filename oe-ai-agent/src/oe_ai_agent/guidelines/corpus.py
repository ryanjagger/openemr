"""Parser and chunker for the markdown guideline corpus."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from pathlib import Path

from oe_ai_agent.guidelines.models import (
    GuidelineChunk,
    GuidelineDocument,
    GuidelineMetadata,
)

CHUNKER_VERSION = "clinical-guidelines-v1"
MAX_CHUNK_CHARS = 1800
MIN_SPLIT_CHARS = 350
MIN_QUOTED_SCALAR_LENGTH = 2
SECTION_HEADING_LEVEL = 2
SUBSECTION_HEADING_LEVEL = 3

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def load_guideline_documents(corpus_dir: Path) -> list[GuidelineDocument]:
    if not corpus_dir.exists():
        return []
    documents: list[GuidelineDocument] = []
    for path in sorted(corpus_dir.rglob("*.md")):
        parsed = _parse_document(corpus_dir, path)
        if parsed is not None:
            documents.append(parsed)
    return documents


def chunk_guideline_documents(documents: Iterable[GuidelineDocument]) -> list[GuidelineChunk]:
    chunks: list[GuidelineChunk] = []
    for document in documents:
        chunks.extend(_chunk_document(document))
    return chunks


def corpus_fingerprint(chunks: list[GuidelineChunk]) -> str:
    hasher = hashlib.sha256()
    hasher.update(CHUNKER_VERSION.encode())
    for chunk in sorted(chunks, key=lambda item: item.chunk_id):
        hasher.update(chunk.chunk_id.encode())
        hasher.update(chunk.content_hash.encode())
    return hasher.hexdigest()


def _parse_document(corpus_dir: Path, path: Path) -> GuidelineDocument | None:
    raw = path.read_text(encoding="utf-8")
    front_matter, body = _split_front_matter(raw)
    if front_matter is None:
        return None
    relative_path = path.relative_to(corpus_dir).as_posix()
    metadata = _metadata_from_front_matter(
        front_matter,
        relative_path=relative_path,
        category=relative_path.split("/", 1)[0],
    )
    if metadata is None:
        return None
    return GuidelineDocument(
        metadata=metadata,
        body=body.strip(),
        content_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )


def _split_front_matter(raw: str) -> tuple[dict[str, str] | None, str]:
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, raw
    values: dict[str, str] = {}
    body_start = 0
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body_start = index + 1
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if separator != ":":
            continue
        values[key.strip()] = _clean_scalar(value.strip())
    if body_start == 0:
        return None, raw
    return values, "\n".join(lines[body_start:])


def _metadata_from_front_matter(
    values: dict[str, str],
    *,
    relative_path: str,
    category: str,
) -> GuidelineMetadata | None:
    title = values.get("title", "").strip()
    source_organization = values.get("source_organization", "").strip()
    if not title or not source_organization:
        return None
    topic_tags = tuple(
        tag.strip()
        for tag in values.get("topic_tags", "").split(",")
        if tag.strip()
    )
    return GuidelineMetadata(
        source_organization=source_organization,
        title=title,
        publication_date=values.get("publication_date", "").strip(),
        grade=_optional(values.get("grade")),
        population=_optional(values.get("population")),
        source_url=_optional(values.get("source_url")),
        license=_optional(values.get("license")),
        topic_tags=topic_tags,
        file_path=relative_path,
        category=category,
        status=_optional(values.get("status")),
        note=_optional(values.get("note")),
        authors=_optional(values.get("authors")),
        secondary_url=_optional(values.get("secondary_url")),
    )


def _chunk_document(document: GuidelineDocument) -> list[GuidelineChunk]:
    sections = _sections(document.body)
    chunks: list[GuidelineChunk] = []
    ordinal = 0
    for section_path, section_text in sections:
        for text in _split_large_section(section_text):
            clean_text = text.strip()
            if not clean_text:
                continue
            ordinal += 1
            chunks.append(
                GuidelineChunk(
                    chunk_id=_chunk_id(document, section_path, ordinal, clean_text),
                    metadata=document.metadata,
                    section_path=section_path,
                    text=clean_text,
                    search_text=_search_text(document.metadata, section_path, clean_text),
                    content_hash=hashlib.sha256(clean_text.encode("utf-8")).hexdigest(),
                )
            )
    return chunks


def _sections(body: str) -> list[tuple[str, str]]:
    title = ""
    current_path: list[str] = []
    current_lines: list[str] = []
    sections: list[tuple[str, str]] = []

    for line in body.splitlines():
        match = _HEADING_RE.match(line)
        if match is None:
            current_lines.append(line)
            continue

        level = len(match.group(1))
        heading = match.group(2).strip()
        if level == 1:
            title = heading
            continue
        if level in {SECTION_HEADING_LEVEL, SUBSECTION_HEADING_LEVEL}:
            _append_section(sections, title, current_path, current_lines)
            if level == SECTION_HEADING_LEVEL or not current_path:
                current_path = [heading]
            elif len(current_path) == 1:
                current_path = [current_path[0], heading]
            else:
                current_path = [current_path[0], heading]
            current_lines = [line]
            continue

        current_lines.append(line)

    _append_section(sections, title, current_path, current_lines)
    return sections


def _append_section(
    sections: list[tuple[str, str]],
    title: str,
    section_path: list[str],
    lines: list[str],
) -> None:
    text = "\n".join(lines).strip()
    if not text:
        return
    parts = [title, *section_path] if title else section_path
    path = " > ".join(part for part in parts if part) or "Document"
    sections.append((path, text))


def _split_large_section(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]

    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for raw_paragraph in paragraphs:
        paragraph = raw_paragraph.strip()
        if not paragraph:
            continue
        projected = current_len + len(paragraph) + 2
        if current and projected > MAX_CHUNK_CHARS and current_len >= MIN_SPLIT_CHARS:
            chunks.append("\n\n".join(current))
            current = [paragraph]
            current_len = len(paragraph)
        else:
            current.append(paragraph)
            current_len = projected
    if current:
        chunks.append("\n\n".join(current))
    return chunks or [text]


def _chunk_id(
    document: GuidelineDocument,
    section_path: str,
    ordinal: int,
    text: str,
) -> str:
    digest = hashlib.sha256(
        "|".join(
            [
                document.metadata.file_path,
                section_path,
                str(ordinal),
                text,
            ]
        ).encode("utf-8")
    ).hexdigest()[:16]
    stem = document.metadata.file_path.rsplit("/", 1)[-1].removesuffix(".md")
    return f"{stem}:{ordinal}:{digest}"


def _search_text(metadata: GuidelineMetadata, section_path: str, text: str) -> str:
    parts = [
        metadata.title,
        metadata.source_organization,
        metadata.publication_date,
        metadata.grade or "",
        metadata.population or "",
        " ".join(metadata.topic_tags),
        metadata.category,
        section_path,
        text,
    ]
    return "\n".join(part for part in parts if part)


def _optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _clean_scalar(value: str) -> str:
    if (
        len(value) >= MIN_QUOTED_SCALAR_LENGTH
        and value[0] == value[-1]
        and value[0] in {"'", '"'}
    ):
        return value[1:-1]
    return value
