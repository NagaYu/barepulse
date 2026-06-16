"""
BarePulse — FastAPI collector + dashboard.

A cookieless, privacy-first web analytics engine that runs entirely on your own
infrastructure. No third parties, no tracking cookies, no data leaving your box.

Routes
------
GET  /                 -> redirect to /dashboard
GET  /dashboard        -> the analytics UI (self-contained, client-rendered)
GET  /tracker.js       -> the embeddable 1KB tracker
POST /api/collect      -> ingest a pageview (called by tracker.js)
GET  /api/stats        -> aggregated stats consumed by the dashboard
GET  /api/health       -> liveness probe
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from user_agents import parse as parse_ua

from .database import get_db, init_db
from .models import PageView

# ---------------------------------------------------------------------------
# Paths & configuration
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

log = logging.getLogger("barepulse")


def _as_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


DEFAULT_SALT = "barepulse-dev-salt-change-me-in-production"

# development | production. In production we refuse to start with insecure
# defaults (see _security_audit) so a server is never accidentally exposed.
ENV = os.getenv("BAREPULSE_ENV", "development").strip().lower()

# The salt is the keystone of our anonymization. Set BAREPULSE_SALT in
# production so hashes cannot be recomputed by an attacker who knows the scheme.
SALT = os.getenv("BAREPULSE_SALT", DEFAULT_SALT)

MAX_BODY_BYTES = 8 * 1024  # reject oversized payloads early

# --- Dashboard auth ---------------------------------------------------------
# The collector (/api/collect) is intentionally public. The dashboard and its
# stats API are private: set BAREPULSE_PASSWORD to protect them with HTTP Basic
# auth. If it is left empty (local dev), the dashboard stays open.
DASHBOARD_USER = os.getenv("BAREPULSE_USER", "admin")
DASHBOARD_PASSWORD = os.getenv("BAREPULSE_PASSWORD", "")

# --- Rate limiting (per-IP, in-memory) --------------------------------------
RATE_LIMIT = int(os.getenv("BAREPULSE_RATE_LIMIT", "120"))   # requests / window
RATE_WINDOW = int(os.getenv("BAREPULSE_RATE_WINDOW", "60"))  # window in seconds

# --- Proxy / host trust -----------------------------------------------------
# Only trust X-Forwarded-For when explicitly behind a reverse proxy (Render,
# Nginx, …). Otherwise a client could spoof the header to evade rate limiting
# or poison visitor hashes. Set BAREPULSE_TRUST_PROXY=true on PaaS/VPS.
TRUST_PROXY = _as_bool(os.getenv("BAREPULSE_TRUST_PROXY", "false"))

# Optional Host allow-list (defends against Host-header attacks).
ALLOWED_HOSTS = [
    h.strip() for h in os.getenv("BAREPULSE_ALLOWED_HOSTS", "").split(",") if h.strip()
]


def _security_audit() -> list[str]:
    problems = []
    if SALT == DEFAULT_SALT:
        problems.append("BAREPULSE_SALT must be set to a secret value")
    if not DASHBOARD_PASSWORD:
        problems.append("BAREPULSE_PASSWORD must be set to protect the dashboard")
    return problems


# ---------------------------------------------------------------------------
# Lifespan: create tables on startup + security audit
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    problems = _security_audit()
    if problems:
        if ENV == "production":
            # Fail fast: never expose an insecure instance to the public.
            raise RuntimeError(
                "Refusing to start in production with insecure configuration: "
                + "; ".join(problems)
            )
        for p in problems:
            log.warning("INSECURE: %s (ok for local dev, NOT for public).", p)
    yield


app = FastAPI(
    title="BarePulse",
    description="Cookieless, self-hosted, lightning-fast web analytics.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS: the tracker lives on customer sites across arbitrary origins, so any
# origin may POST a pageview. We never use cookies or auth on /api/collect, so
# credentials stay disabled — the safe way to combine with allow_origins=["*"].
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    max_age=86400,
)

# Host-header allow-list (only when configured).
if ALLOWED_HOSTS:
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


# Content-Security-Policy: lock the dashboard down to self + the Chart.js CDN.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = _CSP
    # HSTS only once we know the connection is HTTPS (direct or via proxy).
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    if proto == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ---------------------------------------------------------------------------
# Auth (HTTP Basic) — protects the dashboard + stats API
# ---------------------------------------------------------------------------
_basic = HTTPBasic(auto_error=False)


def require_auth(credentials: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    # No password configured => open (local/dev mode).
    if not DASHBOARD_PASSWORD:
        return
    ok = credentials is not None and (
        secrets.compare_digest(credentials.username, DASHBOARD_USER)
        and secrets.compare_digest(credentials.password, DASHBOARD_PASSWORD)
    )
    if not ok:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": 'Basic realm="BarePulse"'},
        )


# ---------------------------------------------------------------------------
# Rate limiter — per-IP fixed window, in-memory, zero dependencies
# ---------------------------------------------------------------------------
class RateLimiter:
    def __init__(self, limit: int, window: int) -> None:
        self.limit = max(1, limit)
        self.window = max(1, window)
        self._buckets: dict[str, tuple[int, float]] = {}
        self._last_prune = time.monotonic()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        count, start = self._buckets.get(key, (0, now))
        if now - start >= self.window:
            count, start = 0, now
        count += 1
        self._buckets[key] = (count, start)
        if now - self._last_prune > self.window:
            self._prune(now)
        return count <= self.limit

    def _prune(self, now: float) -> None:
        self._last_prune = now
        stale = [k for k, (_, s) in self._buckets.items() if now - s >= self.window]
        for k in stale:
            self._buckets.pop(k, None)


limiter = RateLimiter(RATE_LIMIT, RATE_WINDOW)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class CollectPayload(BaseModel):
    domain: str | None = Field(default=None, max_length=255)
    path: str = Field(default="/", max_length=2048)
    referrer: str | None = Field(default=None, max_length=2048)
    screen: str | None = Field(default=None, max_length=32)
    language: str | None = Field(default=None, max_length=32)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def client_ip(request: Request) -> str:
    """
    Best-effort client IP.

    Proxy headers (X-Forwarded-For / X-Real-IP) are honored ONLY when
    BAREPULSE_TRUST_PROXY is enabled, since they are trivially spoofable when
    the server is reachable directly.
    """
    if TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
        real = request.headers.get("x-real-ip")
        if real:
            return real.strip()
    return request.client.host if request.client else "0.0.0.0"


def compute_user_hash(ip: str, user_agent: str) -> str:
    """
    Cookieless unique-visitor token.

    digest = SHA256( SALT | IP | User-Agent | UTC-date )

    The UTC date component rotates the hash every 24h, so the same visitor maps
    to one stable token per day and is uncountable across days — and the raw IP
    is never stored or recoverable.
    """
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw = f"{SALT}|{ip}|{user_agent}|{day}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def referrer_host(referrer: str | None) -> str:
    """Reduce a referrer URL to its hostname; empty/self => 'Direct'."""
    if not referrer:
        return "Direct"
    try:
        host = urlparse(referrer).hostname
    except (ValueError, AttributeError):
        return "Direct"
    return host or "Direct"


def device_type_of(ua) -> str:  # noqa: ANN001
    if ua.is_bot:
        return "Bot"
    if ua.is_tablet:
        return "Tablet"
    if ua.is_mobile:
        return "Mobile"
    if ua.is_pc:
        return "Desktop"
    return "Other"


# ---------------------------------------------------------------------------
# Static / page routes
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False,
         dependencies=[Depends(require_auth)])
async def dashboard() -> HTMLResponse:
    html = (TEMPLATES_DIR / "dashboard.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@app.get("/tracker.js", include_in_schema=False)
async def tracker_js() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "tracker.js",
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "barepulse", "time": datetime.now(timezone.utc).isoformat()}


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------
@app.post("/api/collect")
async def collect(request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """
    Ingest a single pageview.

    The body is read raw (not via automatic Pydantic binding) because the
    tracker uses ``navigator.sendBeacon`` / a ``text/plain`` fetch to stay a
    CORS "simple request" (no preflight). We accept both JSON and text/plain.
    """
    ip = client_ip(request)
    if not limiter.allow(ip):
        return JSONResponse(
            {"ok": False, "error": "rate limited"},
            status_code=429,
            headers={"Retry-After": str(RATE_WINDOW)},
        )

    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return JSONResponse({"ok": False, "error": "payload too large"}, status_code=413)

    try:
        payload = CollectPayload.model_validate_json(raw or b"{}")
    except ValueError:
        return JSONResponse({"ok": False, "error": "invalid payload"}, status_code=422)

    ua_string = request.headers.get("user-agent", "")
    ua = parse_ua(ua_string)

    # Drop bots so analytics reflect real humans.
    if ua.is_bot:
        return JSONResponse({"ok": True, "ignored": "bot"}, status_code=202)

    # Prefer the Origin/Referer-derived host as the authoritative domain when the
    # client omits it; fall back to the reported value.
    origin = request.headers.get("origin") or request.headers.get("referer")
    domain = payload.domain or (urlparse(origin).hostname if origin else None) or "unknown"

    pv = PageView(
        domain=domain[:255],
        path=(payload.path or "/")[:2048],
        referrer=referrer_host(payload.referrer)[:255],
        browser=(ua.browser.family or "Unknown")[:64],
        os=(ua.os.family or "Unknown")[:64],
        device_type=device_type_of(ua),
        user_hash=compute_user_hash(ip, ua_string),
    )

    db.add(pv)
    await db.commit()

    # 204-style ACK kept tiny; tracker ignores the response anyway.
    return JSONResponse({"ok": True}, status_code=201)


# ---------------------------------------------------------------------------
# Stats (consumed by the dashboard)
# ---------------------------------------------------------------------------
async def _top(db, column, conditions, limit=5):
    stmt = (
        select(column.label("label"), func.count().label("count"))
        .where(*conditions)
        .group_by(column)
        .order_by(func.count().desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [{"label": (r.label or "Unknown"), "count": r.count} for r in rows]


@app.get("/api/stats", dependencies=[Depends(require_auth)])
async def stats(
    domain: str | None = None,
    days: int = 7,
    db: AsyncSession = Depends(get_db),
) -> dict:
    days = max(1, min(days, 90))

    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

    base = [PageView.created_at >= start]
    if domain:
        base.append(PageView.domain == domain)

    # ---- list of known domains (for the picker) ----
    domains = [
        r[0]
        for r in (await db.execute(select(PageView.domain).distinct().order_by(PageView.domain))).all()
    ]

    # ---- timeline: bucket PV + distinct UV per day, in Python (DB-agnostic) ----
    tl_rows = (
        await db.execute(select(PageView.created_at, PageView.user_hash).where(*base))
    ).all()

    buckets: dict[str, dict] = {}
    for i in range(days):
        key = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        buckets[key] = {"date": key, "pv": 0, "uv": set()}

    for created_at, user_hash in tl_rows:
        # SQLite may return naive UTC datetimes; treat them as UTC.
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        key = created_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
        if key in buckets:
            buckets[key]["pv"] += 1
            buckets[key]["uv"].add(user_hash)

    timeline = [
        {"date": b["date"], "pv": b["pv"], "uv": len(b["uv"])}
        for b in buckets.values()
    ]
    total_pv = sum(b["pv"] for b in timeline)
    total_uv = len({h for r in tl_rows for h in [r[1]]})

    # ---- top-N breakdowns ----
    top_paths = await _top(db, PageView.path, base)
    top_referrers = await _top(db, PageView.referrer, base)
    top_browsers = await _top(db, PageView.browser, base)
    top_os = await _top(db, PageView.os, base)
    top_devices = await _top(db, PageView.device_type, base)

    return {
        "range_days": days,
        "domain": domain,
        "domains": domains,
        "totals": {"pageviews": total_pv, "visitors": total_uv},
        "timeline": timeline,
        "top_paths": top_paths,
        "top_referrers": top_referrers,
        "top_browsers": top_browsers,
        "top_os": top_os,
        "top_devices": top_devices,
    }
