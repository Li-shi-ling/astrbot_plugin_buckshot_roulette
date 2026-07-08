from __future__ import annotations

import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

try:
    from .roulette_game import short_user_id
except ImportError:
    from core.roulette_game import short_user_id


CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
FORBIDDEN_NAME_PARTS = ("@全体", "@全员", "全体成员", "全员成员")
MAX_DISPLAY_NAME_LEN = 12


@dataclass(frozen=True)
class RouletteUserProfile:
    id: int
    platform_user_id: str
    group_openid: str
    display_name: str
    created_at: str
    updated_at: str


class RouletteDBManager:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.db_path}",
            echo=False,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with self.engine.begin() as conn:
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA synchronous=NORMAL"))
            await conn.execute(text("PRAGMA cache_size=-20000"))
            await conn.execute(text("PRAGMA temp_store=MEMORY"))
            await conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS roulette_user_profile (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        platform_user_id VARCHAR NOT NULL,
                        group_openid VARCHAR NOT NULL,
                        display_name VARCHAR NOT NULL,
                        created_at TIMESTAMP NOT NULL,
                        updated_at TIMESTAMP NOT NULL,
                        UNIQUE (group_openid, platform_user_id)
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_roulette_user_group "
                    "ON roulette_user_profile (group_openid)"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_roulette_user_display_name "
                    "ON roulette_user_profile (display_name)"
                )
            )
            await conn.execute(text("PRAGMA optimize"))

    @asynccontextmanager
    async def get_session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            async with session.begin():
                yield session

    async def close(self) -> None:
        await self.engine.dispose()


class RouletteUserRepo:
    def __init__(self, db: RouletteDBManager):
        self.db = db

    async def get_profile(
        self, group_openid: str, platform_user_id: str
    ) -> RouletteUserProfile | None:
        async with self.db.get_session() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id, platform_user_id, group_openid, display_name, created_at, updated_at
                    FROM roulette_user_profile
                    WHERE group_openid = :group_openid
                      AND platform_user_id = :platform_user_id
                    """
                ),
                {
                    "group_openid": group_openid,
                    "platform_user_id": platform_user_id,
                },
            )
            row = result.mappings().one_or_none()
            return _profile_from_row(row) if row else None

    async def is_name_taken_in_group(
        self,
        group_openid: str,
        display_name: str,
        *,
        exclude_user_id: str | None = None,
    ) -> bool:
        params: dict[str, str] = {
            "group_openid": group_openid,
            "display_name": display_name,
        }
        condition = ""
        if exclude_user_id is not None:
            condition = "AND platform_user_id != :exclude_user_id"
            params["exclude_user_id"] = exclude_user_id
        async with self.db.get_session() as session:
            result = await session.execute(
                text(
                    f"""
                    SELECT 1
                    FROM roulette_user_profile
                    WHERE group_openid = :group_openid
                      AND display_name = :display_name
                      {condition}
                    LIMIT 1
                    """
                ),
                params,
            )
            return result.scalar_one_or_none() is not None

    async def upsert_profile(
        self,
        group_openid: str,
        platform_user_id: str,
        display_name: str,
    ) -> RouletteUserProfile:
        cleaned = validate_display_name(display_name)
        if await self.is_name_taken_in_group(
            group_openid, cleaned, exclude_user_id=platform_user_id
        ):
            raise ValueError("这个昵称在本群已经被使用，请换一个。")

        now = datetime.now().isoformat(timespec="seconds")
        async with self.db.get_session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO roulette_user_profile (
                        platform_user_id, group_openid, display_name, created_at, updated_at
                    )
                    VALUES (:platform_user_id, :group_openid, :display_name, :created_at, :updated_at)
                    ON CONFLICT(group_openid, platform_user_id) DO UPDATE SET
                        display_name = excluded.display_name,
                        updated_at = excluded.updated_at
                    """
                ),
                {
                    "platform_user_id": platform_user_id,
                    "group_openid": group_openid,
                    "display_name": cleaned,
                    "created_at": now,
                    "updated_at": now,
                },
            )

        profile = await self.get_profile(group_openid, platform_user_id)
        if profile is None:
            raise RuntimeError("昵称资料写入后读取失败。")
        return profile

    async def resolve_display_name(self, group_openid: str, platform_user_id: str) -> str:
        profile = await self.get_profile(group_openid, platform_user_id)
        if profile:
            return profile.display_name
        return f"玩家_{short_user_id(platform_user_id)}"


def validate_display_name(display_name: str) -> str:
    cleaned = str(display_name or "").strip()
    if not cleaned:
        raise ValueError("昵称不能为空。")
    if "\n" in cleaned or "\r" in cleaned or CONTROL_CHAR_RE.search(cleaned):
        raise ValueError("昵称不能包含换行或控制字符。")
    if len(cleaned) > MAX_DISPLAY_NAME_LEN:
        raise ValueError("昵称最多 12 个字符。")
    lowered = cleaned.lower()
    if any(part.lower() in lowered for part in FORBIDDEN_NAME_PARTS):
        raise ValueError("昵称不能包含 @全体 类文本。")
    return cleaned


def _profile_from_row(row) -> RouletteUserProfile:
    return RouletteUserProfile(
        id=int(row["id"]),
        platform_user_id=str(row["platform_user_id"]),
        group_openid=str(row["group_openid"]),
        display_name=str(row["display_name"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
