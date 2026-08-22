"""
db.py — Persistence layer.

Everything the app ever computes that has future value (recommendations,
holdings, manual entries) is written to a local SQLite database
(data/portfolio.db) via SQLAlchemy. This is what makes the recommendation
engine "remember" across runs instead of regenerating from a blank slate
(requirement: Persistent memory of recommendations).

DECISION (see DECISIONS.md #1): SQLite chosen over a server DB for
zero-config local persistence, per the brief. A single file is created on
first run.
"""
from __future__ import annotations

import datetime as dt
import os
from typing import Optional, List

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime, Text, Boolean
)
from sqlalchemy.orm import declarative_base, sessionmaker

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "portfolio.db"
)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class Recommendation(Base):
    """
    One row per recommendation ever generated. Rows are never overwritten —
    a new call generates a new row with a new timestamp, so the full history
    of the thesis on a symbol is preserved and can be re-examined later
    (did the "buy" play out? does the thesis still hold?).
    """
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, index=True, nullable=False)
    name = Column(String)
    market = Column(String)          # "IN" or "US"
    asset_type = Column(String)      # "stock" | "etf" | "mutual_fund"
    verdict = Column(String)         # Strong Buy / Buy / Hold / Trim / Exit
    fundamental_score = Column(Float)
    technical_score = Column(Float)
    composite_score = Column(Float)
    price_at_reco = Column(Float)
    reasoning = Column(Text)
    benchmark_index = Column(String)
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class PortfolioHolding(Base):
    """
    Unified holdings table. Every position the app knows about — whether it
    came from a parsed CAS PDF, was typed in manually as a US trade, or is an
    off-CAS asset like unlisted shares or gold — lands here with a `source`
    tag. This is what lets the portfolio evaluator, benchmark comparison and
    charts treat the whole net worth as one portfolio regardless of where the
    data originated.
    """
    __tablename__ = "portfolio_holdings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String, nullable=False)   # "cas" | "manual_us" | "unlisted" | "gold" | "manual_other"
    symbol = Column(String)
    name = Column(String)
    isin = Column(String)
    asset_type = Column(String)   # "stock" | "etf" | "mutual_fund" | "unlisted_equity" | "gold" | "other"
    market = Column(String)       # "IN" | "US" | "OTHER"
    quantity = Column(Float)
    avg_cost = Column(Float)
    unit = Column(String, default="units")  # "units" or "grams" (for gold)
    current_price = Column(Float)
    current_value = Column(Float)
    currency = Column(String, default="INR")
    as_of_date = Column(DateTime, default=dt.datetime.utcnow)
    batch_id = Column(String, index=True)   # groups holdings from the same upload/entry so old batches can be replaced
    notes = Column(Text)


class ImportBatch(Base):
    """Metadata about each CAS upload / manual entry batch, for auditability."""
    __tablename__ = "import_batches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String, unique=True)
    source = Column(String)
    label = Column(String)
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class TrendPoint(Base):
    """Historical consolidated-portfolio-value points, sourced from the CAS's
    own 'Monthly movement' table on upload. Kept distinct per batch so a
    newer CAS upload's trend can supersede an older one."""
    __tablename__ = "trend_points"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String, index=True)
    label = Column(String)      # e.g. "Jul25"
    month = Column(String)
    year = Column(Integer)
    value = Column(Float)


class NPSSnapshot(Base):
    """NPS Tier I summary, sourced from the CAS's own NPS account table."""
    __tablename__ = "nps_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String, index=True)
    tier = Column(String)
    contribution = Column(Float)
    withdrawal = Column(Float)
    value = Column(Float)
    gain = Column(Float)
    xirr = Column(Float)
    created_at = Column(DateTime, default=dt.datetime.utcnow)


def init_db():
    Base.metadata.create_all(engine)


def get_session():
    return SessionLocal()


# --------------------------------------------------------------------------- #
# Recommendation helpers
# --------------------------------------------------------------------------- #

def save_recommendation(rec: dict) -> None:
    session = get_session()
    try:
        row = Recommendation(**rec)
        session.add(row)
        session.commit()
    finally:
        session.close()


def get_recommendation_history(symbol: str) -> List[Recommendation]:
    session = get_session()
    try:
        return (
            session.query(Recommendation)
            .filter(Recommendation.symbol == symbol)
            .order_by(Recommendation.created_at.desc())
            .all()
        )
    finally:
        session.close()


def get_latest_recommendation(symbol: str) -> Optional[Recommendation]:
    history = get_recommendation_history(symbol)
    return history[0] if history else None


def get_all_latest_recommendations() -> List[Recommendation]:
    """Returns the most recent recommendation per distinct symbol."""
    session = get_session()
    try:
        all_rows = (
            session.query(Recommendation)
            .order_by(Recommendation.created_at.desc())
            .all()
        )
        seen = set()
        latest = []
        for row in all_rows:
            if row.symbol not in seen:
                latest.append(row)
                seen.add(row.symbol)
        return latest
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Holdings helpers
# --------------------------------------------------------------------------- #

def save_holdings(holdings: List[dict], source: str, batch_id: str, label: str = "") -> None:
    """Persists a batch of holdings, tagging them with a batch_id so a later
    re-upload/re-entry can replace just that batch without touching others."""
    session = get_session()
    try:
        session.add(ImportBatch(batch_id=batch_id, source=source, label=label))
        for h in holdings:
            h = dict(h)
            h["source"] = source
            h["batch_id"] = batch_id
            session.add(PortfolioHolding(**h))
        session.commit()
    finally:
        session.close()


def replace_batch(batch_id: str, holdings: List[dict], source: str, label: str = "") -> None:
    session = get_session()
    try:
        session.query(PortfolioHolding).filter(PortfolioHolding.batch_id == batch_id).delete()
        session.query(ImportBatch).filter(ImportBatch.batch_id == batch_id).delete()
        session.commit()
    finally:
        session.close()
    save_holdings(holdings, source=source, batch_id=batch_id, label=label)


def get_all_holdings() -> List[PortfolioHolding]:
    session = get_session()
    try:
        return session.query(PortfolioHolding).all()
    finally:
        session.close()


def get_holdings_by_source(source: str) -> List[PortfolioHolding]:
    session = get_session()
    try:
        return session.query(PortfolioHolding).filter(PortfolioHolding.source == source).all()
    finally:
        session.close()


def delete_batch(batch_id: str) -> None:
    session = get_session()
    try:
        session.query(PortfolioHolding).filter(PortfolioHolding.batch_id == batch_id).delete()
        session.query(ImportBatch).filter(ImportBatch.batch_id == batch_id).delete()
        session.commit()
    finally:
        session.close()


def list_batches() -> List[ImportBatch]:
    session = get_session()
    try:
        return session.query(ImportBatch).order_by(ImportBatch.created_at.desc()).all()
    finally:
        session.close()


def clear_all_holdings() -> None:
    session = get_session()
    try:
        session.query(PortfolioHolding).delete()
        session.query(ImportBatch).delete()
        session.commit()
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Trend helpers
# --------------------------------------------------------------------------- #

def save_trend_points(points: list, batch_id: str) -> None:
    session = get_session()
    try:
        for p in points:
            session.add(TrendPoint(batch_id=batch_id, label=p["label"], month=p["month"],
                                    year=p["year"], value=p["value"]))
        session.commit()
    finally:
        session.close()


def get_latest_trend() -> list:
    """Returns the trend points from the most recently uploaded CAS batch."""
    session = get_session()
    try:
        latest_batch = (
            session.query(ImportBatch)
            .filter(ImportBatch.source == "cas")
            .order_by(ImportBatch.created_at.desc())
            .first()
        )
        if not latest_batch:
            return []
        points = (
            session.query(TrendPoint)
            .filter(TrendPoint.batch_id == latest_batch.batch_id)
            .all()
        )
        return [{"label": p.label, "month": p.month, "year": p.year, "value": p.value} for p in points]
    finally:
        session.close()


def save_nps_snapshot(nps: dict, batch_id: str) -> None:
    session = get_session()
    try:
        session.add(NPSSnapshot(batch_id=batch_id, **nps))
        session.commit()
    finally:
        session.close()


def get_latest_nps() -> Optional[dict]:
    session = get_session()
    try:
        row = session.query(NPSSnapshot).order_by(NPSSnapshot.created_at.desc()).first()
        if not row:
            return None
        return {"tier": row.tier, "contribution": row.contribution, "withdrawal": row.withdrawal,
                "value": row.value, "gain": row.gain, "xirr": row.xirr}
    finally:
        session.close()
