"""SQLite cache for parsed guideline chunks and document embeddings."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from oe_ai_agent.guidelines.corpus import corpus_fingerprint
from oe_ai_agent.guidelines.models import GuidelineChunk


class GuidelineIndexStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def sync_chunks(self, chunks: list[GuidelineChunk]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fingerprint = corpus_fingerprint(chunks)
        with self._connect() as connection:
            _ensure_schema(connection)
            current = _metadata_value(connection, "corpus_fingerprint")
            if current == fingerprint:
                return
            connection.execute("DELETE FROM embeddings")
            connection.execute("DELETE FROM chunks")
            connection.executemany(
                """
                INSERT INTO chunks (
                    chunk_id,
                    file_path,
                    section_path,
                    title,
                    source_organization,
                    publication_date,
                    grade,
                    population,
                    source_url,
                    license,
                    topic_tags_json,
                    category,
                    status,
                    note,
                    authors,
                    secondary_url,
                    content_hash,
                    text,
                    search_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        chunk.chunk_id,
                        chunk.metadata.file_path,
                        chunk.section_path,
                        chunk.metadata.title,
                        chunk.metadata.source_organization,
                        chunk.metadata.publication_date,
                        chunk.metadata.grade,
                        chunk.metadata.population,
                        chunk.metadata.source_url,
                        chunk.metadata.license,
                        json.dumps(list(chunk.metadata.topic_tags)),
                        chunk.metadata.category,
                        chunk.metadata.status,
                        chunk.metadata.note,
                        chunk.metadata.authors,
                        chunk.metadata.secondary_url,
                        chunk.content_hash,
                        chunk.text,
                        chunk.search_text,
                    )
                    for chunk in chunks
                ],
            )
            _set_metadata_value(connection, "corpus_fingerprint", fingerprint)

    def load_embeddings(
        self,
        *,
        model: str,
        dimension: int,
        chunk_ids: set[str],
    ) -> dict[str, list[float]]:
        if not chunk_ids or not self._path.exists():
            return {}
        with self._connect() as connection:
            _ensure_schema(connection)
            rows = connection.execute(
                """
                SELECT chunk_id, vector_json
                FROM embeddings
                WHERE model = ? AND dimension = ?
                """,
                (model, dimension),
            ).fetchall()
        vectors: dict[str, list[float]] = {}
        for row in rows:
            chunk_id = str(row["chunk_id"])
            if chunk_id not in chunk_ids:
                continue
            raw_vector = json.loads(str(row["vector_json"]))
            if isinstance(raw_vector, list):
                vector: list[float] = []
                for value in raw_vector:
                    if isinstance(value, int | float):
                        vector.append(float(value))
                if vector:
                    vectors[chunk_id] = vector
        return vectors

    def store_embeddings(
        self,
        *,
        model: str,
        dimension: int,
        embeddings: dict[str, list[float]],
    ) -> None:
        if not embeddings:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            _ensure_schema(connection)
            connection.executemany(
                """
                INSERT OR REPLACE INTO embeddings (
                    chunk_id,
                    model,
                    dimension,
                    vector_json
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        chunk_id,
                        model,
                        dimension,
                        json.dumps(vector, separators=(",", ":")),
                    )
                    for chunk_id, vector in embeddings.items()
                ],
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            section_path TEXT NOT NULL,
            title TEXT NOT NULL,
            source_organization TEXT NOT NULL,
            publication_date TEXT NOT NULL,
            grade TEXT,
            population TEXT,
            source_url TEXT,
            license TEXT,
            topic_tags_json TEXT NOT NULL,
            category TEXT NOT NULL,
            status TEXT,
            note TEXT,
            authors TEXT,
            secondary_url TEXT,
            content_hash TEXT NOT NULL,
            text TEXT NOT NULL,
            search_text TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS embeddings (
            chunk_id TEXT NOT NULL,
            model TEXT NOT NULL,
            dimension INTEGER NOT NULL,
            vector_json TEXT NOT NULL,
            PRIMARY KEY (chunk_id, model, dimension),
            FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE
        )
        """
    )


def _metadata_value(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return str(row["value"])


def _set_metadata_value(
    connection: sqlite3.Connection,
    key: str,
    value: str,
) -> None:
    connection.execute(
        """
        INSERT INTO metadata (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
