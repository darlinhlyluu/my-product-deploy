import time
import redis
import logging
from fastapi import HTTPException
from .config import settings

logger = logging.getLogger(__name__)

try:
    r = redis.from_url(settings.redis_url, decode_responses=True)
    r.ping()
except Exception as e:
    logger.warning(f"Failed to connect to Redis for Cost Guard: {e}. Using in-memory fallback.")
    r = None

PRICE_PER_1K_INPUT_TOKENS = 0.00015
PRICE_PER_1K_OUTPUT_TOKENS = 0.0006

_in_memory_budget = {}

def get_budget_key(user_id: str) -> str:
    month_key = time.strftime("%Y-%m")
    return f"budget:{user_id}:{month_key}"

def _calculate_cost(input_tokens: int, output_tokens: int) -> float:
    input_cost = (input_tokens / 1000) * PRICE_PER_1K_INPUT_TOKENS
    output_cost = (output_tokens / 1000) * PRICE_PER_1K_OUTPUT_TOKENS
    return input_cost + output_cost

def check_budget(user_id: str):
    """Raise 402 Payment Required if budget exceeded."""
    if r:
        key = get_budget_key(user_id)
        current = r.get(key)
        current_cost = float(current) if current else 0.0
        
        if current_cost >= settings.daily_budget_usd:
            raise HTTPException(
                status_code=402,
                detail=f"Monthly budget exceeded: User has used ${current_cost:.4f} / ${settings.daily_budget_usd}",
            )
    else:
        key = get_budget_key(user_id)
        current_cost = _in_memory_budget.get(key, 0.0)
        if current_cost >= settings.daily_budget_usd:
            raise HTTPException(
                status_code=402,
                detail=f"Monthly budget exceeded: User has used ${current_cost:.4f} / ${settings.daily_budget_usd}",
            )

def record_usage(user_id: str, input_tokens: int, output_tokens: int):
    cost = _calculate_cost(input_tokens, output_tokens)
    if cost <= 0:
        return
        
    key = get_budget_key(user_id)
    if r:
        r.incrbyfloat(key, cost)
        r.expire(key, 32 * 24 * 3600)  # Expire after ~1 month
    else:
        current = _in_memory_budget.get(key, 0.0)
        _in_memory_budget[key] = current + cost
