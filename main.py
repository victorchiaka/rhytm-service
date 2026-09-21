import asyncio
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.jobs.sweep import start_sweep_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

load_dotenv()

version = os.getenv("API_VERSION", "0.0.1")


@asynccontextmanager
async def lifespan(app: FastAPI):
    sweep_task = asyncio.create_task(
        start_sweep_scheduler(default_idle_seconds=30.0, active_interval_seconds=10.0)
    )
    yield
    sweep_task.cancel()


app = FastAPI(
    title="Rhytm API",
    description="API for the Rhytm Service",
    version=version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi import APIRouter, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from core.routers.habit import habits_router
from core.routers.routine import routines_router
from core.routers.user import auth_router, users_router

api_router = APIRouter(prefix="/api")
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(habits_router)
api_router.include_router(routines_router)

app.include_router(api_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    error_messages = []
    for error in exc.errors():
        msg = error["msg"]
        if msg.startswith("Value error, "):
            msg = msg.replace("Value error, ", "", 1)
        elif msg.startswith("Assertion failed, "):
            msg = msg.replace("Assertion failed, ", "", 1)

        error_messages.append(msg)

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "".join(error_messages)},
    )


@app.get("/health")
async def health_check():
    logger.info("Health check endpoint was hit")
    return {"status": "ok", "message": "Service is healthy"}


if __name__ == "__main__":
    logger.info("Starting FastAPI application...")
    import uvicorn

    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
