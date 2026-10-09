from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import MetaData, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


def add_missing_columns(conn: Connection, metadata: MetaData) -> list[str]:
    """给已存在的表补上模型里新增的列。

    项目用 create_all 建表而没有迁移脚本，create_all 不会改动已有表，
    新加一列就得手工 ALTER；这里只处理"加可空列/带默认值的列"这种最简单的情况。
    """
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())
    added: list[str] = []
    for table in metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        present = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} " + column.type.compile(
                dialect=conn.dialect
            )
            if column.server_default is not None:
                default = column.server_default.arg
                default_sql = f"'{default}'" if isinstance(default, str) else str(default)
                ddl += f" DEFAULT {default_sql}"
            if not column.nullable and column.server_default is not None:
                ddl += " NOT NULL"
            conn.execute(text(ddl))
            added.append(f"{table.name}.{column.name}")
    return added

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """给非请求上下文(MQTT 回调、定时任务)使用的会话。"""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
