from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.models import ErrorDetail, ErrorResponse, HealthResponse

app = FastAPI(title="AutoDev App")


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(ok=True)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    err = ErrorResponse(error=ErrorDetail(code="BAD_REQUEST", message=str(exc)))
    payload = err.model_dump() if hasattr(err, "model_dump") else err.dict()
    return JSONResponse(status_code=400, content=payload)
