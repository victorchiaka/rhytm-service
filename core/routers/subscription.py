from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.subscription import BenefitsResponse, InitiateSubscriptionRequest
from core.security import get_current_user
from core.services.subscription_service import SubscriptionService
from db.database import get_db

subscriptions_router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])
subscription_service = SubscriptionService()


@subscriptions_router.get("/benefits", response_model=BenefitsResponse, status_code=status.HTTP_200_OK)
async def get_benefits():
    return await subscription_service.populate_benefits()


@subscriptions_router.post("/initiate", status_code=status.HTTP_200_OK)
async def initiate_subscription(
    payload: InitiateSubscriptionRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await subscription_service.initiate_subscription(
        user_id=current_user["user_id"], plan=payload.plan, db=db
    )


@subscriptions_router.post("/webhook", status_code=status.HTTP_200_OK)
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    return await subscription_service.handle_webhook(
        payload=await request.body(),
        sig_header=request.headers.get("stripe-signature", ""),
        db=db,
    )
