import logging
import os
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.schemas import AnalysisRequest, AnalysisResponse, ErrorResponse
from app.services.analysis_service import analyze
from app.services.llm_service import LLMError
from app.services.website_extractor import FetchError
from app.utils.validators import UnresolvableHostError, UnsafeURLError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("link-analysis")

app = FastAPI(title="AI Website Link Analysis API", version="1.0.0",
              description="Analyzes a website with an LLM: competitors, search queries, SEO, services, audience.")


_hits = defaultdict(deque)


def _rate_limited(ip: str) -> bool:
    """Sliding 1-hour window per client IP. In-memory: per instance, which is enough to stop casual abuse."""
    limit = int(os.getenv("RATE_LIMIT_PER_HOUR", "20"))
    now, q = time.time(), _hits[ip]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    request.state.rid = rid
    t0 = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    log.info("rid=%s %s %s -> %s in %.2fs", rid, request.method, request.url.path, response.status_code,
             time.perf_counter() - t0)
    return response


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content=ErrorResponse(error=message, code=code).model_dump())


@app.exception_handler(RequestValidationError)
async def bad_request(_: Request, exc: RequestValidationError):
    return _err(422, "invalid_request", 'Body must be JSON like {"url": "https://example.com"}')


@app.get("/")
def root():
    return {"service": "link-analysis", "docs": "/docs", "endpoint": "POST /link-analysis"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/link-analysis", response_model=AnalysisResponse,
          responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
                     429: {"model": ErrorResponse}, 502: {"model": ErrorResponse}})
def link_analysis(req: AnalysisRequest, request: Request):
    # Plain def: FastAPI runs it in a threadpool, so blocking HTTP calls do not stall the event loop.
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "unknown")).split(",")[0].strip()
    if _rate_limited(ip):
        return _err(429, "rate_limited", "Too many analyses from this address. Please try again later.")
    log.info("rid=%s analysis requested url=%s", request.state.rid, req.url)
    try:
        resp = analyze(req.url)
        resp.meta.request_id = request.state.rid
        log.info("analysis ok url=%s provider=%s llm_s=%s cached=%s", resp.website,
                 resp.meta.provider, resp.meta.llm_seconds, resp.meta.cached)
        return resp
    except UnresolvableHostError as exc:
        return _err(502, "website_unreachable", str(exc))
    except UnsafeURLError as exc:
        return _err(400, "invalid_or_blocked_url", str(exc))
    except FetchError as exc:
        return _err(502, "website_unreachable", str(exc))
    except LLMError as exc:
        log.warning("llm failure: %s", exc)
        return _err(502, "llm_failure", "The analysis service failed to produce a valid result. Try again shortly.")
    except Exception:  # noqa: BLE001
        log.exception("unexpected error")
        return _err(500, "internal_error", "Unexpected server error")
