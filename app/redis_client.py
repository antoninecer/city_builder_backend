from redis.asyncio import Redis
from app.config import settings

redis_client = Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    decode_responses=True
)

# Pro graceful shutdown
async def close_redis():
    await redis_client.close()
