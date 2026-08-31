from fastapi import FastAPI

from app.errors import register_error_handlers
from app.logging_config import configure_logging
from app.routers import health

configure_logging()

app = FastAPI(title="Ledgerline API")
register_error_handlers(app)
app.include_router(health.router)
