"""SQLite access for jobs, verifications and idempotency keys."""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from ..contract.result import VerificationResult

SCHEMA_VERSION = 1
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass
class JobRecord:
    id: str
    status: str
    input_kind: str
    locale: str
    created_at: datetime
    updated_at: datetime
    progress: int = 0
    input_url: str | None = None
    canonical_url: str | None = None
    input: dict[str, Any] = field(default_factory=dict)
    preview: dict[str, Any] | None = None
    verification_id: str | None = None
    from_cache: bool = False
    error_code: str | None = None
    error_message: str | None = None
    attempts: int = 0


def _job_from_row(row: aiosqlite.Row) -> JobRecord:
    return JobRecord(
        id=row["id"],
        status=row["status"],
        progress=row["progress"],
        input_kind=row["input_kind"],
        input_url=row["input_url"],
        canonical_url=row["canonical_url"],
        input=json.loads(row["input_json"]),
        locale=row["locale"],
        preview=json.loads(row["preview_json"]) if row["preview_json"] else None,
        verification_id=row["verification_id"],
        from_cache=bool(row["from_cache"]),
        error_code=row["error_code"],
        error_message=row["error_message"],
        attempts=row["attempts"],
        created_at=_dt(row["created_at"]),
        updated_at=_dt(row["updated_at"]),
    )


class Repository:
    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn

    @classmethod
    async def open(cls, path: Path) -> "Repository":
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        for pragma in (
            "PRAGMA journal_mode=WAL",
            "PRAGMA busy_timeout=5000",
            "PRAGMA synchronous=NORMAL",
            "PRAGMA foreign_keys=ON",
        ):
            await conn.execute(pragma)
        await conn.executescript(SCHEMA_PATH.read_text())
        await conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        await conn.commit()
        return cls(conn)

    async def close(self) -> None:
        await self.conn.close()

    # ---- jobs ----

    async def insert_job(self, job: JobRecord) -> None:
        await self.conn.execute(
            """INSERT INTO verification_jobs (id, status, progress, input_kind, input_url, canonical_url,
                 input_json, locale, attempts, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job.id,
                job.status,
                job.progress,
                job.input_kind,
                job.input_url,
                job.canonical_url,
                json.dumps(job.input),
                job.locale,
                job.attempts,
                _ts(job.created_at),
                _ts(job.updated_at),
            ),
        )
        await self.conn.commit()

    async def get_job(self, job_id: str) -> JobRecord | None:
        async with self.conn.execute("SELECT * FROM verification_jobs WHERE id = ?", (job_id,)) as cur:
            row = await cur.fetchone()
        return _job_from_row(row) if row else None

    async def update_job(self, job_id: str, **changes: Any) -> None:
        columns = {
            "status": None,
            "progress": None,
            "canonical_url": None,
            "verification_id": None,
            "from_cache": int,
            "error_code": None,
            "error_message": None,
            "attempts": None,
            "preview": lambda v: json.dumps(v) if v is not None else None,
        }
        sets, values = [], []
        for key, value in changes.items():
            if key not in columns:
                raise KeyError(f"unknown job column: {key}")
            convert = columns[key]
            sets.append(f"{'preview_json' if key == 'preview' else key} = ?")
            values.append(convert(value) if convert else value)
        sets.append("updated_at = ?")
        values.append(_ts(utc_now()))
        await self.conn.execute(f"UPDATE verification_jobs SET {', '.join(sets)} WHERE id = ?", (*values, job_id))
        await self.conn.commit()

    async def delete_job(self, job_id: str) -> None:
        await self.conn.execute("DELETE FROM verification_jobs WHERE id = ?", (job_id,))
        await self.conn.commit()

    async def unfinished_jobs(self) -> list[JobRecord]:
        async with self.conn.execute(
            "SELECT * FROM verification_jobs WHERE status IN ('pending', 'processing') ORDER BY created_at"
        ) as cur:
            rows = await cur.fetchall()
        return [_job_from_row(r) for r in rows]

    # ---- verifications ----

    async def insert_verification(self, result: VerificationResult) -> None:
        await self.conn.execute(
            """INSERT INTO verifications (id, source_kind, canonical_url, name, price, store_name, trust_score,
                 risk_level, finding_count, result_json, checked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(result.id),
                result.source.kind,
                str(result.source.canonical_url) if result.source.canonical_url else None,
                result.displayName,
                result.product.price,
                result.metadata.store_name,
                result.trust_score,
                result.risk_level.value,
                sum(1 for c in result.checks if c.severity != "pass"),
                result.model_dump_json(),
                _ts(result.checked_at),
            ),
        )
        await self.conn.commit()

    async def get_verification(self, verification_id: str) -> VerificationResult | None:
        async with self.conn.execute(
            "SELECT result_json FROM verifications WHERE id = ?", (verification_id,)
        ) as cur:
            row = await cur.fetchone()
        return VerificationResult.model_validate_json(row["result_json"]) if row else None

    async def list_verifications(
        self,
        *,
        limit: int,
        cursor: tuple[str, str] | None = None,
        risk_level: str | None = None,
        from_: str | None = None,
        to: str | None = None,
    ) -> list[dict[str, Any]]:
        where, values = [], []
        if risk_level:
            where.append("risk_level = ?")
            values.append(risk_level)
        if from_:
            where.append("checked_at >= ?")
            values.append(from_)
        if to:
            where.append("checked_at < ?")
            values.append(to)
        if cursor:
            where.append("(checked_at < ? OR (checked_at = ? AND id < ?))")
            values.extend((cursor[0], cursor[0], cursor[1]))
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        async with self.conn.execute(
            f"""SELECT id, source_kind, name, price, store_name, trust_score, risk_level,
                       finding_count, result_json, checked_at
                FROM verifications {clause}
                ORDER BY checked_at DESC, id DESC LIMIT ?""",
            (*values, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "id": row["id"],
                "source_kind": row["source_kind"],
                "source_url": json.loads(row["result_json"])["source"].get("url"),
                "name": row["name"],
                "price": row["price"],
                "store_name": row["store_name"],
                "trust_score": row["trust_score"],
                "risk_level": row["risk_level"],
                "finding_count": row["finding_count"],
                "checked_at": row["checked_at"],
            }
            for row in rows
        ]

    async def find_fresh_verification(self, canonical_url: str, newer_than: datetime) -> str | None:
        async with self.conn.execute(
            """SELECT id FROM verifications WHERE canonical_url = ? AND checked_at > ?
               ORDER BY checked_at DESC LIMIT 1""",
            (canonical_url, _ts(newer_than)),
        ) as cur:
            row = await cur.fetchone()
        return row["id"] if row else None

    # ---- idempotency ----

    async def get_idempotency(self, key: str) -> tuple[str, str, datetime] | None:
        async with self.conn.execute(
            "SELECT request_hash, job_id, created_at FROM idempotency_keys WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        return (row["request_hash"], row["job_id"], _dt(row["created_at"])) if row else None

    async def insert_idempotency(self, key: str, request_hash: str, job_id: str) -> bool:
        """False when another request stored the same key first."""
        try:
            await self.conn.execute(
                "INSERT INTO idempotency_keys (key, request_hash, job_id, created_at) VALUES (?, ?, ?, ?)",
                (key, request_hash, job_id, _ts(utc_now())),
            )
            await self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            await self.conn.rollback()
            return False

    async def delete_idempotency(self, key: str) -> None:
        await self.conn.execute("DELETE FROM idempotency_keys WHERE key = ?", (key,))
        await self.conn.commit()

    async def purge_idempotency(self, older_than: datetime) -> None:
        await self.conn.execute("DELETE FROM idempotency_keys WHERE created_at < ?", (_ts(older_than),))
        await self.conn.commit()
