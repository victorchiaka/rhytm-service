# type: ignore
import os

from dotenv import load_dotenv
from redis.asyncio import Redis

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

redis_client = Redis.from_url(REDIS_URL)


async def get_rdb() -> Redis:
    try:
        yield redis_client
    finally:
        await redis_client.close()
