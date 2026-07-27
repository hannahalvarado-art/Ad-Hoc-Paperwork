from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class OverrideCreate(BaseModel):
    """A CSM confirming the price for an account Salesforce has no price for."""

    sf_account_id: str = Field(min_length=1)
    confirmed_unit_price: float = Field(ge=0)
    confirmed_by: str = Field(min_length=1)
    effective_date: str = Field(min_length=1)
    note: str = ""
    billing_customer: str = ""
    sf_account_name: str = ""

    @field_validator("confirmed_by")
    @classmethod
    def _trim(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Enter who is confirming this price.")
        return v


class OverrideImport(BaseModel):
    schema_name: str = Field(default="adhoc_csm_pricing_overrides", alias="schema")
    version: int = 1
    overrides: list[dict[str, Any]] = []

    model_config = {"populate_by_name": True}


class RawIngest(BaseModel):
    period: str
    records: list[dict[str, Any]]
    replace: bool = True


class ContractLookupIngest(BaseModel):
    csv_text: str | None = None
    pairs: list[tuple[str, str]] | None = None


class PeriodCreate(BaseModel):
    label: str
    name: str
    basis: str = "sent_date"


class CustomerMapUpsert(BaseModel):
    source_customer: str
    billing_customer: str
    sf_account_id: str
    reason: str
    active: bool = True


class ExclusionUpsert(BaseModel):
    source_customer: str
    reason: str
    active: bool = True


class AccountUpsert(BaseModel):
    account_id: str
    name: str
    csm: str | None = None
    adhoc_price: float | None = None


class SettingUpsert(BaseModel):
    value: str
    note: str | None = None
