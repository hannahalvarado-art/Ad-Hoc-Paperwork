from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class OverrideCreate(BaseModel):
    """A CSM confirming the price for an account Salesforce has no price for.

    `confirmed_by` is gone: it used to be a text box, so the audit trail
    recorded what someone typed rather than who they were. The identity now
    comes from the session and cannot be supplied by the caller.

    `confirm` must be true. Saving a price is signoff, so the two-step
    confirmation in the UI is mirrored by an explicit flag on the request
    rather than being purely cosmetic.
    """

    sf_account_id: str = Field(min_length=1)
    confirmed_unit_price: float = Field(ge=0)
    effective_date: str = Field(min_length=1)
    confirm: bool = False
    note: str = ""
    billing_customer: str = ""
    sf_account_name: str = ""
    period: str | None = None

    @field_validator("confirm")
    @classmethod
    def _require_confirmation(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Confirm the price before saving it.")
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


class RunPeriodRequest(BaseModel):
    """Run or re-run a month. Defaults to the prior calendar month — the same
    target the scheduled job picks — so testing exercises the real path."""

    year: int | None = None
    month: int | None = None
    source: str | None = None          # 'keboola' | 'upload'
    notify: bool = False               # off by default: a manual rerun should
                                       # not message @csms unless asked
    refresh_usage: bool = True


class ApprovalUpsert(BaseModel):
    """Good to Bill for one customer in one month.

    No approver field: the identity comes from the session. `period` is
    required rather than defaulted, because an approval silently landing on the
    wrong month is exactly the failure this design exists to prevent.
    """

    period: str
    billing_customer: str = Field(min_length=1)
    salesforce_account_id: str = ""
    good_to_bill: bool
    note: str = ""


class ClosePeriod(BaseModel):
    # The period label, typed back. A boolean could be sent by a stray click.
    confirm: str = Field(min_length=1)
    note: str = ""


class ReopenPeriod(BaseModel):
    reason: str = Field(min_length=1)
