import os
import time
import signal
import logging
import json
from datetime import datetime, timezone
from contextlib import asynccontextmanager
import redis

from fastapi import FastAPI, HTTPException, Security, Depends, Request, Response
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

from .config import settings
from .auth import verify_api_key
from .rate_limiter import check_rate_limit
from .cost_guard import check_budget, record_usage

# Logging setup
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format='{"ts":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}',
)
logger = logging.getLogger(__name__)

START_TIME = time.time()
_is_ready = False
_request_count = 0

# Try to connect to Redis for Stateless history
try:
    r = redis.from_url(settings.redis_url, decode_responses=True)
    r.ping()
except Exception as e:
    logger.warning(f"Failed to connect to Redis for History: {e}. State will be lost on restart.")
    r = None
    _in_memory_history = {}

# Mock LLM fallback
def mock_llm_ask(question: str) -> str:
    time.sleep(0.5) # Simulate latency
    return f"This is a mock response to: '{question}'"

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _is_ready
    logger.info(json.dumps({
        "event": "startup",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
    }))
    time.sleep(0.1)
    _is_ready = True
    logger.info(json.dumps({"event": "ready"}))
    yield
    _is_ready = False
    logger.info(json.dumps({"event": "shutdown"}))

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_allowed_origins(),
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)

@app.middleware("http")
async def request_middleware(request: Request, call_next):
    global _request_count
    start = time.time()
    _request_count += 1
    try:
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if "server" in response.headers:
            del response.headers["server"]
        duration = round((time.time() - start) * 1000, 1)
        logger.info(json.dumps({
            "event": "request",
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "ms": duration,
        }))
        return response
    except Exception as e:
        logger.error(f"Error handling request: {e}", exc_info=True)
        raise

class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)

class AskResponse(BaseModel):
    question: str
    answer: str
    model: str
    history_count: int
    timestamp: str

@app.get("/health", tags=["Operations"])
def health():
    return {
        "status": "ok",
        "version": settings.app_version,
        "environment": settings.environment,
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "total_requests": _request_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

@app.get("/ready", tags=["Operations"])
def ready():
    if not _is_ready:
        raise HTTPException(503, "Not ready")
    if r:
        try:
            r.ping()
        except redis.RedisError:
            raise HTTPException(503, "Redis disconnected")
    return {"ready": True}

@app.post("/ask", response_model=AskResponse, tags=["Agent"])
async def ask_agent(
    body: AskRequest,
    request: Request,
    api_key: str = Depends(verify_api_key),
):
    # Using the API key conceptually as a user_id
    user_id = api_key[:8]
    
    check_rate_limit(user_id)
    check_budget(user_id)

    input_tokens = len(body.question.split()) * 2
    
    # Process Stateless History
    history_count = 0
    if r:
        history_key = f"history:{user_id}"
        r.rpush(history_key, body.question)
        history_count = r.llen(history_key)
        r.expire(history_key, 3600) # History expires in 1 hour
    else:
        if user_id not in _in_memory_history:
            _in_memory_history[user_id] = []
        _in_memory_history[user_id].append(body.question)
        history_count = len(_in_memory_history[user_id])

    # Call LLM
    answer = mock_llm_ask(body.question)
    output_tokens = len(answer.split()) * 2

    # Record Cost
    record_usage(user_id, input_tokens, output_tokens)

    return AskResponse(
        question=body.question,
        answer=answer,
        model=settings.llm_model,
        history_count=history_count,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

def _handle_signal(signum, _frame):
    logger.info(json.dumps({"event": "signal", "signum": signum}))

signal.signal(signal.SIGTERM, _handle_signal)

if __name__ == "__main__":
    logger.info(f"Starting API on {settings.host}:{settings.port}")
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        timeout_graceful_shutdown=30,
    )
