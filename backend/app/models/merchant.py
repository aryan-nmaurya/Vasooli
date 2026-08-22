"""The owning business. Doc §8."""

import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.base import pk_column, timestamp_column, updated_at_column


class Merchant(SQLModel, table=True):
    __tablename__ = "merchants"

    id: uuid.UUID = Field(sa_column=pk_column())
    name: str
    # Where customer replies are routed and what appears in the reminder signature.
    contact_email: str
    reply_to_email: str | None = None

    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    updated_at: datetime = Field(sa_column=updated_at_column())
