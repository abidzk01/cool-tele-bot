"""Registry pekerjaan unduhan yang sedang berjalan.

Dipakai agar /cleanup bisa membatalkan unduhan yang masih berjalan (bukan hanya
menghapus file sisa). Setiap pekerjaan dibungkus asyncio.Task supaya bisa di-cancel.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass, field

log = logging.getLogger("appbot.jobs")


@dataclass
class JobRecord:
    """Satu pekerjaan unduhan."""

    id: str
    user_id: int | None
    chat_id: int | None
    url: str
    platform: str
    started_at: float = field(default_factory=time.time)
    task: asyncio.Task | None = None
    progress: object | None = None
    cancelled: bool = False

    @property
    def age(self) -> float:
        return max(0.0, time.time() - self.started_at)

    def label(self) -> str:
        return f"{self.platform} · {self.url[:60]}"


class JobRegistry:
    """Kumpulan pekerjaan aktif; aman dipakai dari event loop tunggal."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}

    def add(self, *, user_id: int | None, chat_id: int | None, url: str,
            platform: str, progress: object | None = None) -> JobRecord:
        record = JobRecord(
            id=uuid.uuid4().hex[:8],
            user_id=user_id,
            chat_id=chat_id,
            url=url,
            platform=platform,
            progress=progress,
        )
        self._jobs[record.id] = record
        return record

    def remove(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)

    def get(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    def all(self) -> list[JobRecord]:
        return list(self._jobs.values())

    def by_user(self, user_id: int | None) -> list[JobRecord]:
        return [job for job in self._jobs.values() if job.user_id == user_id]

    def cancel(self, record: JobRecord) -> bool:
        """Batalkan satu pekerjaan. True bila pekerjaan masih hidup saat dibatalkan."""
        if record.cancelled:
            return False
        record.cancelled = True
        # sinyal ke thread yt-dlp agar berhenti di kesempatan berikutnya
        with contextlib.suppress(Exception):
            record.progress.request_cancel()
        task = record.task
        if task is not None and not task.done():
            task.cancel()
        self.remove(record.id)
        return True

    async def cancel_many(self, records: list[JobRecord], timeout: float = 8.0) -> int:
        """Batalkan beberapa pekerjaan lalu tunggu sampai benar-benar berhenti."""
        stopped = 0
        pending: list[asyncio.Task] = []
        for record in list(records):
            if record.task is not None and not record.task.done():
                pending.append(record.task)
            if self.cancel(record):
                stopped += 1
        if pending:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True), timeout=timeout,
                )
            except asyncio.TimeoutError:
                log.warning("Sebagian pekerjaan belum berhenti setelah %.0fs", timeout)
        return stopped

    def __len__(self) -> int:
        return len(self._jobs)