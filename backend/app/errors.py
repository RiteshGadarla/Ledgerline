import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

logger = logging.getLogger("ledgerline")


class ProblemDetailError(Exception):
    """Base for typed domain exceptions mapped to RFC 9457 problem-detail responses."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    title: str = "Bad request"

    def __init__(self, detail: str, *, status_code: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if status_code is not None:
            self.status_code = status_code


class NotFoundError(ProblemDetailError):
    status_code = status.HTTP_404_NOT_FOUND
    title = "Not found"


class ValidationFailedError(ProblemDetailError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    title = "Validation failed"


def _problem_response(request: Request, status_code: int, title: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "type": "about:blank",
            "title": title,
            "status": status_code,
            "detail": detail,
            "instance": str(request.url.path),
        },
        media_type="application/problem+json",
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemDetailError)
    async def _handle_problem(request: Request, exc: ProblemDetailError) -> JSONResponse:
        return _problem_response(request, exc.status_code, exc.title, exc.detail)

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", extra={"path": request.url.path})
        return _problem_response(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Internal server error",
            "An unexpected error occurred.",
        )
