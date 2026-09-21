import asyncio
from datetime import datetime, timezone
import logging

from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models.habit import Habit
from core.models.routine import Deletion, Routine
from db.database import AsyncSessionLocal
from db.rdb import get_rdb

logger = logging.getLogger(__name__)


async def run_hard_delete_sweep(db: AsyncSession) -> int:
    """Find expired deletions (deadline < NOW()) and bulk hard-delete soft-deleted routines/habits."""
    now_utc = datetime.now(timezone.utc)
    result = await db.execute(
        select(Deletion.id).where(Deletion.deadline < now_utc)
    )
    expired_ids = result.scalars().all()

    if not expired_ids:
        return 0
    
    await db.execute(delete(Routine).where(Routine.deletion_id.in_(expired_ids)))
    await db.execute(delete(Habit).where(Habit.deletion_id.in_(expired_ids)))
    await db.execute(delete(Deletion).where(Deletion.id.in_(expired_ids)))

    await db.commit()
    return len(expired_ids)


async def get_next_sleep_interval(db: AsyncSession, default_idle: float = 30.0, active_interval: float = 10.0) -> float:
    """Determine adaptive sleep duration based on the earliest upcoming deletion deadline."""
    now_utc = datetime.now(timezone.utc)
    result = await db.execute(
        select(func.min(Deletion.deadline)).where(Deletion.deadline >= now_utc)
    )
    next_deadline = result.scalar()

    if not next_deadline:
        return default_idle

    seconds_until_next = (next_deadline - now_utc).total_seconds()
    # Sleep until next deadline, clamped between 1.0s and active_interval (10s)
    return max(1.0, min(seconds_until_next, active_interval))


async def _execute_sweep_step(default_idle: float, active_interval: float) -> float:
    rdb: Redis = await get_rdb()
    acquired = await rdb.set("lock:hard_delete_sweep", "1", nx=True, ex=15)
    if not acquired:
        return active_interval

    try:
        async with AsyncSessionLocal() as db:
            cleaned_count = await run_hard_delete_sweep(db)
            if cleaned_count > 0:
                logger.info(f"Hard delete sweep removed {cleaned_count} expired deletion(s)")

            return await get_next_sleep_interval(
                db, default_idle=default_idle, active_interval=active_interval
            )
    finally:
        await rdb.delete("lock:hard_delete_sweep")


async def start_sweep_scheduler(default_idle_seconds: float = 30.0, active_interval_seconds: float = 10.0) -> None:
    """Background loop with adaptive sleeping, bulk deletes, and safe distributed Redis locking."""
    logger.info("Starting optimized hard-delete sweep worker")
    while True:
        try:
            sleep_seconds = await _execute_sweep_step(default_idle_seconds, active_interval_seconds)
        except Exception as e:
            logger.error(f"Error in hard-delete sweep scheduler: {e}", exc_info=True)
            sleep_seconds = active_interval_seconds

        await asyncio.sleep(sleep_seconds)

