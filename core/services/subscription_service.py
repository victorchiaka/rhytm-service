import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import stripe
from dotenv import load_dotenv
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from stripe import SignatureVerificationError, StripeClient

from core.messages import SUBSCRIPTION_MESSAGES
from core.models.user import User
from core.schemas.subscription import (
    BenefitsResponse,
    PlanFeature,
    PlanPricing,
)

logger = logging.getLogger(__name__)

PLAN_INTERVALS = {"month": "monthly", "year": "yearly"}
PLAN_PRICES = lambda: {
    "monthly": os.getenv("STRIPE_PRICE_ID_MONTHLY"),
    "yearly": os.getenv("STRIPE_PRICE_ID_YEARLY"),
}


class SubscriptionService:
    load_dotenv()

    ENVIRONMENT = os.getenv("ENVIRONMENT")

    @staticmethod
    def _resolve_api_key() -> str:
        prod = os.getenv("PROD_STRIPE_API_KEY")
        dev = os.getenv("DEV_STRIPE_API_KEY")
        return prod if SubscriptionService.ENVIRONMENT == "prod" else dev

    @staticmethod
    def _resolve_webhook_key() -> str:
        prod = os.getenv("PROD_STRIPE_WEBHOOK_KEY")
        dev = os.getenv("DEV_STRIPE_WEBHOOK_KEY")
        return prod if SubscriptionService.ENVIRONMENT == "prod" else dev

    @staticmethod
    def _resolve_plan_id(plan: str) -> str:
        price_id = PLAN_PRICES().get(plan)
        if not price_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=SUBSCRIPTION_MESSAGES.INVALID_PLAN,
            )
        return price_id

    @staticmethod
    def _client() -> StripeClient:
        return StripeClient(SubscriptionService._resolve_api_key())

    @staticmethod
    async def populate_benefits() -> BenefitsResponse:
        benefits_path = Path(__file__).parent.parent / "data" / "benefits.json"
        raw = json.loads(benefits_path.read_text())
        features = [PlanFeature.model_validate(f) for f in raw["features"]]

        client = SubscriptionService._client()
        plans: dict[str, PlanPricing] = {}
        for plan_name, price_id in PLAN_PRICES().items():
            if not price_id:
                continue
            price = client.v1.prices.retrieve(price_id)
            amount = f"${price.unit_amount / 100:.2f}"
            plans[plan_name] = PlanPricing(
                price=amount, period=price.recurring.interval
            )

        return BenefitsResponse(features=features, plans=plans)

    async def initiate_subscription(
        self, user_id: str, plan: str, db: AsyncSession
    ) -> dict:
        user = await db.scalar(select(User).where(User.id == user_id))
        if not user:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, SUBSCRIPTION_MESSAGES.USER_NOT_FOUND
            )

        client = self._client()
        price_id = self._resolve_plan_id(plan)

        if not user.stripe_customer_id:
            customer = client.v1.customers.create(
                params={"email": user.email, "name": user.full_name}
            )
            user.stripe_customer_id = customer.id
            await db.commit()

        session = client.v1.checkout.sessions.create(
            params={
                "customer": user.stripe_customer_id,
                "line_items": [{"price": price_id, "quantity": 1}],
                "mode": "subscription",
                "success_url": "https://rhytm.top", # Not functional url, TODO: will swap for something
            }
        )

        return {
            "checkout_url": session.url,
            "message": "Subscription initiated.",
        }

    async def handle_webhook(
        self, payload: bytes, sig_header: str, db: AsyncSession
    ) -> dict:
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, self._resolve_webhook_key()
            )
        except SignatureVerificationError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                SUBSCRIPTION_MESSAGES.INVALID_WEBHOOK_SIGNATURE,
            )

        handlers = {
            "invoice.payment_succeeded": self._on_payment_succeeded,
            "customer.subscription.deleted": self._on_subscription_deleted,
        }
        handler = handlers.get(event["type"])
        if handler:
            await handler(event["data"]["object"], db)
        else:
            logger.info(f"[WEBHOOK] Unhandled event: {event['type']}")

        return {"received": True}

    async def _on_payment_succeeded(self, invoice, db: AsyncSession) -> None:
        customer = getattr(invoice, "customer", None)
        customer_id = customer if isinstance(customer, str) else getattr(customer, "id", None)

        subscription = getattr(invoice, "subscription", None)
        sub_details = getattr(getattr(invoice, "parent", None), "subscription_details", None)
        sub_from_parent = getattr(sub_details, "subscription", None) if sub_details else None

        subscription = subscription or sub_from_parent
        subscription_id = subscription if isinstance(subscription, str) else getattr(subscription, "id", None)

        if not (customer_id and subscription_id):
            logger.warning(
                f"[WEBHOOK] Missing customer or subscription on invoice. customer={customer_id} subscription={subscription_id}"
            )
            return

        user = await db.scalar(
            select(User).where(User.stripe_customer_id == customer_id)
        )
        if not user:
            logger.warning(f"[WEBHOOK] No user for Stripe customer {customer_id}")
            return

        sub = self._client().v1.subscriptions.retrieve(subscription_id)
        item = sub.items.data[0]
        interval = item.price.recurring.interval

        period_end = getattr(item, "current_period_end", None) or getattr(sub, "current_period_end", None) or getattr(invoice, "period_end", None)

        user.plan = PLAN_INTERVALS.get(interval, "monthly")
        user.subscription_status = "pro"
        user.plan_expires_at = datetime.fromtimestamp(period_end, tz=UTC) if period_end else None
        await db.commit()
        logger.info(
            f"[WEBHOOK] Activated '{user.plan}' (status: {user.subscription_status}) for user {user.id} until {user.plan_expires_at}"
        )

    async def _on_subscription_deleted(self, subscription, db: AsyncSession) -> None:
        customer_id = subscription.customer
        if not customer_id:
            return

        user = await db.scalar(
            select(User).where(User.stripe_customer_id == customer_id)
        )
        if not user:
            logger.warning(f"[WEBHOOK] No user for Stripe customer {customer_id}")
            return

        user.plan = None
        user.subscription_status = "basic"
        user.plan_expires_at = None
        await db.commit()
        logger.info(f"[WEBHOOK] Downgraded user {user.id} to basic")
