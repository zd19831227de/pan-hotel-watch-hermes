from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_rag_schema(connection: AsyncConnection) -> None:
    """V5 Step 4 — create RAG tables (defensive IF NOT EXISTS).

    New tables are also created by ``Base.metadata.create_all``; this migration
    ensures an existing production DB that ran an older ``create_all`` still
    receives them.
    """

    dialect = connection.dialect.name
    autoincrement = (
        "INTEGER PRIMARY KEY AUTO_INCREMENT"
        if dialect in {"mysql", "mariadb"}
        else "INTEGER PRIMARY KEY AUTOINCREMENT"
    )
    datetime_type = "DATETIME"
    for statement in _create_table_statements(autoincrement, datetime_type):
        await connection.execute(text(statement))
    await _ensure_rag_documents_folder_column(connection)
    await _ensure_rag_documents_folder_id_column(connection)
    await _migrate_legacy_folder_strings(connection)
    for statement in _create_index_statements():
        await connection.execute(text(statement))


async def _ensure_rag_documents_folder_column(connection: AsyncConnection) -> None:
    """Defensive column migration: older DBs created ``rag_documents`` without
    ``folder``; SQLite/MySQL ``ADD COLUMN`` patches them in place."""

    def _inspect_columns(sync_connection) -> list[str]:
        return [
            column["name"]
            for column in inspect(sync_connection).get_columns("rag_documents")
        ]

    columns = await connection.run_sync(_inspect_columns)
    if "folder" in columns:
        return
    null_keyword = "" if connection.dialect.name in {"sqlite"} else "NULL"
    statement = f"ALTER TABLE rag_documents ADD COLUMN folder VARCHAR(128) {null_keyword}".strip()
    await connection.execute(text(statement))


async def _ensure_rag_documents_folder_id_column(connection: AsyncConnection) -> None:
    """Add the ``folder_id`` foreign key column for DBs created before folders
    became real entities."""

    def _inspect_columns(sync_connection) -> list[str]:
        return [
            column["name"]
            for column in inspect(sync_connection).get_columns("rag_documents")
        ]

    columns = await connection.run_sync(_inspect_columns)
    if "folder_id" in columns:
        return
    column_type = "INTEGER" if connection.dialect.name in {"sqlite"} else "BIGINT"
    await connection.execute(
        text(
            f"ALTER TABLE rag_documents ADD COLUMN folder_id {column_type} NULL"
        )
    )


async def _migrate_legacy_folder_strings(connection: AsyncConnection) -> None:
    """One-shot migration of pre-entity folder strings: each distinct non-empty
    ``rag_documents.folder`` value becomes a ``rag_folders`` row, and the
    matching documents get ``folder_id`` backfilled. Idempotent."""

    def _inspect_columns(sync_connection) -> list[str]:
        return [
            column["name"]
            for column in inspect(sync_connection).get_columns("rag_documents")
        ]

    columns = await connection.run_sync(_inspect_columns)
    if "folder" not in columns:
        return
    rows = list(
        await connection.execute(
            text("SELECT DISTINCT folder FROM rag_documents WHERE folder IS NOT NULL")
        )
    )
    for (folder_name,) in rows:
        if not folder_name:
            continue
        await connection.execute(
            text(
                "INSERT INTO rag_folders (name, sort_order) "
                "SELECT :name, COALESCE(MAX(sort_order), 0) + 1 FROM rag_folders "
                "WHERE NOT EXISTS (SELECT 1 FROM rag_folders WHERE name = :name)"
            ),
            {"name": folder_name},
        )
        await connection.execute(
            text(
                "UPDATE rag_documents SET folder_id = "
                "(SELECT id FROM rag_folders WHERE name = :name) "
                "WHERE folder = :name AND folder_id IS NULL"
            ),
            {"name": folder_name},
        )


def _create_table_statements(autoincrement: str, datetime_type: str) -> list[str]:
    return [
        f"""
        CREATE TABLE IF NOT EXISTS runtime_embedding_configs (
            id {autoincrement},
            config_key VARCHAR(64) NOT NULL UNIQUE,
            provider VARCHAR(64) NOT NULL DEFAULT 'openai',
            model_name VARCHAR(128) NOT NULL DEFAULT 'text-embedding-3-small',
            base_url TEXT NOT NULL,
            api_key TEXT NULL,
            dimensions INTEGER NULL,
            created_at {datetime_type} NULL,
            updated_at {datetime_type} NULL
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS rag_folders (
            id {autoincrement},
            name VARCHAR(128) NOT NULL UNIQUE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at {datetime_type} NULL,
            updated_at {datetime_type} NULL
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS rag_documents (
            id {autoincrement},
            title VARCHAR(256) NOT NULL,
            source_type VARCHAR(32) NOT NULL DEFAULT 'text',
            folder VARCHAR(128) NULL,
            folder_id INTEGER NULL,
            content_hash VARCHAR(64) NOT NULL,
            raw_content TEXT NOT NULL,
            chunk_count INTEGER NOT NULL DEFAULT 0,
            created_at {datetime_type} NULL,
            updated_at {datetime_type} NULL
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS rag_chunks (
            id {autoincrement},
            document_id INTEGER NOT NULL,
            ordinal INTEGER NOT NULL DEFAULT 0,
            text TEXT NOT NULL,
            embedding_json TEXT NOT NULL,
            created_at {datetime_type} NULL,
            FOREIGN KEY (document_id) REFERENCES rag_documents(id) ON DELETE CASCADE
        )
        """.strip(),
    ]


def _create_index_statements() -> list[str]:
    return [
        "CREATE INDEX IF NOT EXISTS ix_rag_chunks_document_id ON rag_chunks (document_id)",
        "CREATE INDEX IF NOT EXISTS ix_rag_documents_folder_id ON rag_documents (folder_id)",
    ]


async def rag_table_exists(connection: AsyncConnection, table_name: str) -> bool:
    def _inspect(sync_connection) -> bool:
        return inspect(sync_connection).has_table(table_name)

    return await connection.run_sync(_inspect)
