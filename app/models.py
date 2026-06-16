"""
BarePulse — ORM models.

A single, lean ``pageviews`` table. Privacy by design: we never persist raw
IP addresses. The only per-visitor token is ``user_hash``, a salted SHA-256
digest that rotates every 24h and is irreversible.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class PageView(Base):
    __tablename__ = "pageviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    path: Mapped[str] = mapped_column(String(2048), nullable=False)
    referrer: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    browser: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    os: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    device_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    # Salted, date-rotating SHA-256 digest. NOT reversible to an IP.
    user_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    # Composite index: the dashboard always filters by domain + time window.
    __table_args__ = (
        Index("ix_pageviews_domain_created", "domain", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PageView id={self.id} domain={self.domain!r} path={self.path!r}>"
