import time
import redis
import logging
from fastapi import HTTPException
from .config import settings

logger = logging.getLogger(__name__)

# Try to connect to Redis
try:
    r = redis.from_url(settings.redis_url, decode_responses=True)
    r.ping()
except Exception as e:
    logger.warning(f"Failed to connect to Redis for Rate Limiting: {e}. Using in-memory fallback.")
    r = None

# In-memory fallback
_in_memory_rate_windows = {}

def check_rate_limit(user_id: str):
    now = time.time()
    limit = settings.rate_limit_per_minute
    
    if r:
        key = f"rate_limit:{user_id}"
        # Sliding window using Redis Sorted Set
        pipeline = r.pipeline()
        # Remove old requests (older than 60s)
        pipeline.zremrangebyscore(key, 0, now - 60)
        # Add current request
        pipeline.zadd(key, {str(now): now})
        # Count requests in the last 60s
        pipeline.zcard(key)
        # Set expire on the key to avoid memory leaks
        pipeline.expire(key, 60)
        results = pipeline.execute()
        
        request_count = results[2]
        if request_count > limit:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: {limit} req/min",
                headers={"Retry-After": "60"},
            )
    else:
        # Fallback in-memory
        if user_id not in _in_memory_rate_windows:
            _in_memory_rate_windows[user_id] = []
        
        window = _in_memory_rate_windows[user_id]
        while window and window[0] < now - 60:
            window.pop(0)
            
        if len(window) >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: {limit} req/min",
                headers={"Retry-After": "60"},
            )
        window.append(now)
