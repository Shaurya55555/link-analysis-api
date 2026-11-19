import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.schemas import AnalysisRequest, AnalysisResponse, ErrorResponse
from app.services.analysis_service import analyze
from app.services.llm_service import LLMError
from app.services.website_extractor import FetchError
from app.utils.validators import UnsafeURLError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("link-analysis")

app = FastAPI(title="AI Website Link Analysis API", version="1.0.0",
              description="Analyzes a website with an LLM: competitors, search queries, SEO, services, audience.")


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
                     502: {"model": ErrorResponse}})
def link_analysis(req: AnalysisRequest):
    # Plain def: FastAPI runs it in a threadpool, so blocking HTTP calls do not stall the event loop.
    log.info("analysis requested url=%s", req.url)
    try:
        resp = analyze(req.url)
        log.info("analysis ok url=%s provider=%s llm_s=%s cached=%s", resp.website,
                 resp.meta.provider, resp.meta.llm_seconds, resp.meta.cached)
        return resp
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
