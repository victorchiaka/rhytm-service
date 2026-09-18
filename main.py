import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

load_dotenv()

version = os.getenv("API_VERSION", "0.0.1")

app = FastAPI(
    title="Rhytm API",
    description="API for the Rhytm Service",
    version=version,
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

from core.routers.routine import routines_router
from core.routers.user import auth_router, users_router

api_router = APIRouter(prefix="/api")
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(routines_router)

app.include_router(api_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    for error in errors:
        if error["msg"].startswith("Value error, "):
            error["msg"] = error["msg"].replace("Value error, ", "", 1)
        elif error["msg"].startswith("Assertion failed, "):
            error["msg"] = error["msg"].replace("Assertion failed, ", "", 1)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": errors},
    )


@app.get("/health")
async def health_check():
    logger.info("Health check endpoint was hit")
    return {"status": "ok", "message": "Service is healthy"}


if __name__ == "__main__":
    logger.info("Starting FastAPI application...")
    import uvicorn

    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
