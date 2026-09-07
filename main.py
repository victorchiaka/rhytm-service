import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

# TODO: Include your routers here once you create them
# from src.users.router import router as users_router
# app.include_router(users_router, prefix="/users", tags=["Users"])


@app.get("/health")
async def health_check():
    logger.info("Health check endpoint was hit")
    return {"status": "ok", "message": "Service is healthy"}


if __name__ == "__main__":
    logger.info("Starting FastAPI application...")
    import uvicorn

    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
