import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.api import (
    attendance,
    audit_logs,
    attrition,
    auth,
    datasets,
    departments,
    employees,
    health,
    interventions,
    leave,
    notifications,
    payroll,
    performance,
    recommendations,
    reports,
    risk_monitoring,
    search,
    shifts,
    tasks,
    users,
    workforce_activity,
    workforce_analytics,
)
from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

MYSQL_CHECK_VIOLATION = 3819
MYSQL_TRANSACTION_CONFLICTS = frozenset({1213, 1205})  # deadlock, lock wait timeout
CONCURRENT_CONFLICT = "The request conflicted with a concurrent change. Please retry."


def mysql_error_code(exc: OperationalError) -> int | None:
    args = getattr(exc.orig, "args", ())
    return args[0] if args and isinstance(args[0], int) else None


def create_app(settings: Settings | None = None) -> FastAPI:
    # Settings are validated here, so the server refuses to start with missing or unsafe config.
    settings = settings or get_settings()

    # Interactive docs and the OpenAPI schema are off in staging/production unless API_DOCS_ENABLED=true.
    docs = settings.docs_enabled
    app = FastAPI(title=settings.app_name, docs_url="/docs" if docs else None, redoc_url="/redoc" if docs else None,
                  openapi_url="/openapi.json" if docs else None)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if settings.allowed_hosts != ["*"]:
        # Unknown Host headers get 400 before any route runs (required explicitly in staging and production).
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/"):
            # API responses carry personal data: never stored by browsers or shared caches.
            response.headers.setdefault("Cache-Control", "no-store")
            # JSON and file downloads only: nothing in an API response may run, load resources or be framed.
            response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        return response

    upload_limit = settings.dataset_max_upload_bytes + 64 * 1024  # file plus multipart overhead

    @app.middleware("http")
    async def reject_oversized_uploads(request: Request, call_next):
        # Refuse oversized dataset uploads before the body is parsed. Requests without Content-Length
        # (chunked) are still bounded by the limit enforced while reading the file.
        if request.method == "POST" and request.url.path.startswith("/api/datasets"):
            declared = request.headers.get("content-length")
            if declared is not None and (not declared.isdigit() or int(declared) > upload_limit):
                return JSONResponse(status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                                    content={"detail": "Upload exceeds the size limit"})
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default 422 body echoes submitted input, which would include passwords.
        errors = [{"type": e["type"], "loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": errors})

    @app.exception_handler(OperationalError)
    async def database_unavailable_handler(request: Request, exc: OperationalError) -> JSONResponse:
        # Driver messages include user and host names, so log the exception type and MySQL error number only.
        code = mysql_error_code(exc)
        if code in MYSQL_TRANSACTION_CONFLICTS:
            # InnoDB rolled the transaction back (deadlock or lock wait timeout); nothing was written, a retry is safe.
            logger.warning("Transaction conflict on %s %s: MySQL error %s", request.method, request.url.path, code)
            return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": CONCURRENT_CONFLICT})
        if code == MYSQL_CHECK_VIOLATION:
            # A CHECK constraint stopped invalid data: a server-side bug, not an outage (PyMySQL reports it as an
            # OperationalError; SQLite raises IntegrityError, which is also a 500).
            logger.error("Database constraint violated on %s %s", request.method, request.url.path)
            return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                content={"detail": "Internal Server Error"})
        logger.warning("Database error on %s %s: %s", request.method, request.url.path, type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"detail": "Database unavailable"}
        )

    @app.get("/api/health")
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(departments.router)
    app.include_router(employees.router)
    app.include_router(shifts.router)
    app.include_router(attendance.router)
    app.include_router(leave.router)
    app.include_router(notifications.router)
    app.include_router(payroll.router)
    app.include_router(attrition.router)
    app.include_router(datasets.router)
    app.include_router(risk_monitoring.router)
    app.include_router(workforce_analytics.router)
    app.include_router(interventions.router)
    app.include_router(recommendations.router)
    app.include_router(workforce_activity.router)
    app.include_router(reports.router)
    app.include_router(audit_logs.router)
    app.include_router(tasks.router)
    app.include_router(performance.router)
    app.include_router(search.router)
    app.include_router(users.router)

    return app


app = create_app()
