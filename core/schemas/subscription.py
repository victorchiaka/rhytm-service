from typing import Annotated, Literal

from pydantic import BaseModel


class TextValue(BaseModel):
    type: Literal["text"] = "text"
    value: str


class IncludedValue(BaseModel):
    type: Literal["included"] = "included"


class NotIncludedValue(BaseModel):
    type: Literal["not_included"] = "not_included"


FeatureValue = Annotated[
    TextValue | IncludedValue | NotIncludedValue, "discriminated by 'type'"
]


class PlanFeature(BaseModel):
    name: str
    basic: FeatureValue
    pro: FeatureValue


class PlanPricing(BaseModel):
    price: str
    period: str


class BenefitsResponse(BaseModel):
    features: list[PlanFeature]
    plans: dict[str, PlanPricing]


class InitiateSubscriptionRequest(BaseModel):
    plan: Literal["monthly", "yearly"]
