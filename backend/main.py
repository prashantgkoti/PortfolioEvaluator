"""
main.py — FastAPI backend for the Portfolio Snapshot dashboard.

Serves two things from one process:
  1. A JSON API under /api/* backed by the same modules/*.py used by the
     Streamlit app (cas_parser, data_fetch, portfolio, recommendation,
     projection, benchmark, manual_assets, observations) — no logic is
     duplicated, only re-exposed over HTTP.
  2. The static dashboard (static/index.html + static/app.js) at "/".

Run with:  uvicorn backend.main:app --reload --port 8000
Then open: http://localhost:8000
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .modules import (
    db, cas_parser, portfolio, recommendation, projection,
    benchmark, manual_assets, observations, formatting,
)

app = FastAPI(title="Portfolio Snapshot API")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

db.init_db()


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #

class USTradeIn(BaseModel):
    symbol: str
    name: str = ""
    quantity: float
    avg_cost: float
    asset_type: str = "stock"


class UnlistedIn(BaseModel):
    name: str
    quantity: float
    avg_cost: float
    estimated_current_price: Optional[float] = None
    notes: str = ""


class GoldIn(BaseModel):
    grams: float
    avg_cost_per_gram: float
    current_price_per_gram: Optional[float] = None
    form: str = "Physical"


class OtherAssetIn(BaseModel):
    name: str
    asset_type: str = "other"
    quantity: float = 1.0
    avg_cost: float = 0.0
    current_value: Optional[float] = None
    notes: str = ""


class RecommendationIn(BaseModel):
    symbol: str
    market: str = "IN"
    asset_type: str = "stock"
    scheme_code: Optional[str] = None
    name: str = ""


class ProjectionIn(BaseModel):
    current_value: float
    sip_amount: float
    annual_return_pct: float
    years: int
    sip_growth_pct: float = 0.0


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _holdings_dataframe(refresh: bool = False):
    holdings = db.get_all_holdings()
    df = portfolio.holdings_to_dataframe(holdings)
    if refresh and not df.empty:
        df = portfolio.refresh_prices(df)
    usdinr = portfolio.get_usdinr_rate() if not df.empty else 87.0
    if not df.empty:
        df = portfolio.add_inr_columns(df, usdinr)
    return df, usdinr


# --------------------------------------------------------------------------- #
# CAS upload
# --------------------------------------------------------------------------- #

@app.post("/api/cas/upload")
async def upload_cas(file: UploadFile = File(...)):
    file_bytes = await file.read()
    result = cas_parser.parse_cas_bytes(file_bytes)
    if result["error"]:
        raise HTTPException(400, result["error"])

    batch_id = cas_parser.new_batch_id()
    if result["holdings"]:
        db.save_holdings(result["holdings"], source="cas", batch_id=batch_id, label=file.filename)

    trend_points = cas_parser.parse_trend_bytes(file_bytes)
    if trend_points:
        db.save_trend_points(trend_points, batch_id=batch_id)

    nps = cas_parser.parse_nps_bytes(file_bytes)
    if nps:
        db.save_nps_snapshot(nps, batch_id=batch_id)

    return {
        "batch_id": batch_id,
        "holdings_count": len(result["holdings"]),
        "warnings": result["warnings"],
        "trend_points_found": len(trend_points),
        "nps_found": nps is not None,
    }


# --------------------------------------------------------------------------- #
# Portfolio
# --------------------------------------------------------------------------- #

@app.get("/api/portfolio")
def get_portfolio(refresh: bool = Query(False)):
    df, usdinr = _holdings_dataframe(refresh=refresh)
    if df.empty:
        return {"total_value": 0, "total_cost": 0, "holdings": [], "asset_class": [],
                "rupee_buckets": [], "usdinr_rate": usdinr}

    total_value = float(df["value_inr"].fillna(0).sum())
    total_cost = float(df["cost_inr"].fillna(0).sum())

    asset_class = (
        df.groupby("asset_type")["value_inr"].sum().reset_index()
        .rename(columns={"value_inr": "value"}).to_dict("records")
    )
    rupee_buckets = (
        df.groupby("source")["value_inr"].sum().reset_index()
        .rename(columns={"value_inr": "value"}).to_dict("records")
    )

    holdings = df.astype(object).where(df.notnull(), None).to_dict("records")
    return {
        "total_value": round(total_value, 2),
        "total_cost": round(total_cost, 2),
        "total_gain": round(total_value - total_cost, 2),
        "usdinr_rate": round(usdinr, 2),
        "asset_class": asset_class,
        "rupee_buckets": rupee_buckets,
        "holdings": holdings,
    }


@app.post("/api/portfolio/refresh")
def refresh_portfolio():
    return get_portfolio(refresh=True)


@app.get("/api/portfolio/verdicts")
def get_verdicts():
    """Runs the fundamental+technical scoring engine against every holding
    that has a resolvable ticker — the same engine used by /api/recommendation,
    applied across the whole portfolio in one call."""
    df, _ = _holdings_dataframe(refresh=False)
    if df.empty:
        return {"verdicts": []}
    out = []
    for _, row in df.iterrows():
        v = portfolio.evaluate_holding(row)
        out.append({
            "name": row["name"], "symbol": row["symbol"] if portfolio.has_symbol(row["symbol"]) else None,
            "asset_type": row["asset_type"], "verdict": v["verdict"],
            "composite_score": v.get("composite_score"), "reasoning": v["reasoning"],
        })
    return {"verdicts": out}


@app.get("/api/trend")
def get_trend():
    return {"points": db.get_latest_trend()}


@app.get("/api/nps")
def get_nps():
    return db.get_latest_nps() or {}


@app.get("/api/observations")
def get_observations():
    df, _ = _holdings_dataframe(refresh=False)
    if df.empty:
        return {"equity": None, "mf_overlap": []}
    equity_rows = df[df["asset_type"] == "stock"][["symbol", "name", "current_value"]].to_dict("records")
    mf_rows = df[df["asset_type"] == "mutual_fund"][["name"]].to_dict("records")
    equity_obs = observations.compute_observations(equity_rows)
    overlap = observations.find_fund_overlap(mf_rows)
    return {"equity": equity_obs, "mf_overlap": overlap}


# --------------------------------------------------------------------------- #
# Batches
# --------------------------------------------------------------------------- #

@app.get("/api/batches")
def list_batches():
    batches = db.list_batches()
    return [{"batch_id": b.batch_id, "source": b.source, "label": b.label,
             "created_at": b.created_at.isoformat()} for b in batches]


@app.delete("/api/batches/{batch_id}")
def delete_batch(batch_id: str):
    db.delete_batch(batch_id)
    return {"deleted": batch_id}


# --------------------------------------------------------------------------- #
# Manual holdings
# --------------------------------------------------------------------------- #

@app.post("/api/manual-holdings/us")
def add_us_trade(body: USTradeIn):
    holding = manual_assets.build_us_trade_holding(body.symbol, body.name, body.quantity,
                                                     body.avg_cost, body.asset_type)
    batch_id = manual_assets.save_manual_batch([holding], "manual_us", f"US: {body.symbol.upper()}")
    return {"batch_id": batch_id, "holding": holding}


@app.post("/api/manual-holdings/unlisted")
def add_unlisted(body: UnlistedIn):
    holding = manual_assets.build_unlisted_holding(body.name, body.quantity, body.avg_cost,
                                                     body.estimated_current_price, body.notes)
    batch_id = manual_assets.save_manual_batch([holding], "unlisted", f"Unlisted: {body.name}")
    return {"batch_id": batch_id, "holding": holding}


@app.post("/api/manual-holdings/gold")
def add_gold(body: GoldIn):
    holding = manual_assets.build_gold_holding(body.grams, body.avg_cost_per_gram,
                                                 body.current_price_per_gram, body.form)
    batch_id = manual_assets.save_manual_batch([holding], "gold", f"Gold ({body.form})")
    return {"batch_id": batch_id, "holding": holding}


@app.post("/api/manual-holdings/other")
def add_other(body: OtherAssetIn):
    holding = manual_assets.build_other_holding(body.name, body.asset_type, body.quantity,
                                                  body.avg_cost, body.current_value, notes=body.notes)
    batch_id = manual_assets.save_manual_batch([holding], "manual_other", f"Other: {body.name}")
    return {"batch_id": batch_id, "holding": holding}


# --------------------------------------------------------------------------- #
# Recommendation engine
# --------------------------------------------------------------------------- #

@app.post("/api/recommendation")
def analyze(body: RecommendationIn):
    analysis = recommendation.analyze_symbol(
        body.symbol, body.market, body.asset_type, scheme_code=body.scheme_code, name=body.name
    )
    if "error" in analysis:
        raise HTTPException(422, analysis["error"])
    history = recommendation.compare_with_history(analysis["symbol"], analysis)
    analysis_out = {k: v for k, v in analysis.items() if k != "alpha"}
    analysis_out["alpha"] = analysis.get("alpha")
    return {"analysis": analysis_out, "history_comparison": history}


@app.post("/api/recommendation/save")
def save_recommendation(body: RecommendationIn):
    analysis = recommendation.analyze_symbol(
        body.symbol, body.market, body.asset_type, scheme_code=body.scheme_code, name=body.name
    )
    if "error" in analysis:
        raise HTTPException(422, analysis["error"])
    recommendation.save_analysis_as_recommendation(analysis)
    return {"saved": True, "symbol": analysis["symbol"]}


@app.get("/api/recommendation/history/{symbol}")
def recommendation_history(symbol: str):
    rows = db.get_recommendation_history(symbol)
    return [{"symbol": r.symbol, "verdict": r.verdict, "composite_score": r.composite_score,
             "price_at_reco": r.price_at_reco, "reasoning": r.reasoning,
             "created_at": r.created_at.isoformat()} for r in rows]


@app.get("/api/recommendation/latest")
def all_latest_recommendations():
    rows = db.get_all_latest_recommendations()
    return [{"symbol": r.symbol, "name": r.name, "market": r.market, "asset_type": r.asset_type,
             "verdict": r.verdict, "composite_score": r.composite_score,
             "created_at": r.created_at.isoformat()} for r in rows]


# --------------------------------------------------------------------------- #
# Projection
# --------------------------------------------------------------------------- #

@app.post("/api/projection")
def project(body: ProjectionIn):
    monthly_df = projection.project_corpus(
        body.current_value, body.sip_amount, body.annual_return_pct, body.years, body.sip_growth_pct
    )
    yearly_df = projection.yearly_summary(monthly_df)
    return {"yearly": yearly_df.to_dict("records"), "final": monthly_df.iloc[-1].to_dict()}


# --------------------------------------------------------------------------- #
# Benchmark comparison
# --------------------------------------------------------------------------- #

@app.get("/api/benchmark")
def get_benchmark(period: str = Query("1y")):
    df, _ = _holdings_dataframe(refresh=True)
    if df.empty:
        return {"holdings": [], "portfolio": None}

    from .modules import data_fetch
    results = []
    for _, row in df.iterrows():
        if row["asset_type"] not in ("stock", "etf") or not portfolio.has_symbol(row["symbol"]):
            continue
        hist = data_fetch.get_price_history(row["symbol"], row["market"], period=period)
        h_return = data_fetch.period_return(hist)
        idx_key = benchmark.determine_benchmark(row["asset_type"], row["market"], {}, row["name"] or "")
        info = benchmark.compute_alpha(h_return, idx_key, period=period)
        if info["alpha"] is not None:
            results.append({"name": row["name"] or row["symbol"], "symbol": row["symbol"], **info})

    agg = portfolio.portfolio_benchmark_alpha(df, period=period)
    return {"holdings": results, "portfolio": agg}


# --------------------------------------------------------------------------- #
# Static frontend
# --------------------------------------------------------------------------- #

@app.get("/")
def serve_index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
