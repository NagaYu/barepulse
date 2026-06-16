<div align="center">

# 📡 BarePulse

**Cookieless, self-hosted, blazing-fast web analytics.**

No cookies. No third parties. No consent banners. No monthly bill.
Your data never leaves your server.

</div>

---

> 🇬🇧 English · [🇯🇵 日本語はこちら](#-日本語)

## What is BarePulse?

BarePulse is a complete, production-ready analytics platform you run yourself.
A **< 1KB** async tracker collects pageviews; a **FastAPI** collector anonymizes
and stores them; and a **premium dark-mode dashboard** visualizes traffic in
real time. It ships with **SQLite by default** (zero setup, zero server cost)
and switches to **PostgreSQL** with a single environment variable.

### Why it matters

| | Google Analytics / SaaS | **BarePulse** |
|---|---|---|
| Cookies | Yes (consent banner required) | **None** |
| Where your data lives | Vendor's cloud | **Your server** |
| GDPR / CCPA | Your problem | **Anonymized by design** |
| Page weight | 45–90 KB | **< 1 KB** |
| Cost at scale | $$$ / month | **$0** (your box) |

---

## ✨ Features

- **1KB async tracker** — Vanilla JS, no dependencies. Uses `navigator.sendBeacon`
  so the last pageview lands even as the tab closes, and never blocks rendering.
- **Cookieless unique visitors** — IPs are *never* stored. We compute a daily,
  salted `SHA-256(SALT | IP | User-Agent | UTC-date)` digest. Same person, same
  day = 1 visitor; the token rotates every 24h and is irreversible.
- **Low-latency collector** — Fully `async/await` FastAPI + async SQLAlchemy.
  SQLite runs in WAL mode, so reads and writes never deadlock under load.
- **Strict CORS** — Accepts beacons from any origin (your sites live everywhere)
  while keeping credentials off — the safe `allow_origins=["*"]` configuration.
- **Beautiful dashboard** — 7/14/30-day PV + UV timeline (Chart.js), plus top
  pages, referrers, browsers, OS, and devices with animated progress bars.
- **Bot filtering** — Crawlers are detected and excluded automatically.
- **One-flag database swap** — SQLite → PostgreSQL via `DATABASE_URL`.

---

## 🚀 3-Minute Quick Start

```bash
# 1. Install dependencies (Python 3.10+)
cd barepulse
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Set your secret salt (keeps visitor hashes private)
export BAREPULSE_SALT="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"

# 3. Run it
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000/dashboard** — that's it. The database is created
automatically on first launch.

> On Windows (PowerShell): `python -m venv .venv; .venv\Scripts\Activate.ps1`
> and `$env:BAREPULSE_SALT="..."`.

---

## 🔌 Embed on your site

Drop **one line** before `</body>` on any site you want to measure:

```html
<script async defer
        src="https://your-host.com/tracker.js"
        data-endpoint="https://your-host.com/api/collect"></script>
```

`data-endpoint` is optional — it defaults to `/api/collect` on the same origin
the script was served from. The tracker automatically reports pageviews,
referrer, screen resolution, and language, and follows SPA route changes
(`pushState` / `replaceState` / back-forward).

---

## ⚙️ Configuration

| Env var | Default | Description |
|---|---|---|
| `BAREPULSE_SALT` | dev placeholder | **Set this in production.** Secret salt for visitor hashing. |
| `BAREPULSE_USER` | `admin` | Dashboard login username (HTTP Basic). |
| `BAREPULSE_PASSWORD` | _(empty)_ | Dashboard password. **Empty = dashboard is PUBLIC.** Set it before going live. |
| `BAREPULSE_RATE_LIMIT` | `120` | Max `/api/collect` requests per IP per window. |
| `BAREPULSE_RATE_WINDOW` | `60` | Rate-limit window, in seconds. |
| `DATABASE_URL` | `sqlite+aiosqlite:///./barepulse.db` | Any async SQLAlchemy URL. |

### 🔒 Before you publish

The **collector** (`/tracker.js`, `/api/collect`) is meant to be public. The
**dashboard** and **stats API** are not. Run this checklist before exposing the
server to the internet:

1. **Set `BAREPULSE_PASSWORD`** (and optionally `BAREPULSE_USER`) — otherwise
   the dashboard and all your stats are world-readable. The dashboard is
   protected with HTTP Basic auth; `/api/collect` stays open as it must.
2. **Set a strong `BAREPULSE_SALT`** — `python3 -c 'import secrets; print(secrets.token_hex(32))'`.
3. **Serve over HTTPS** (Render does this automatically; on a VPS, terminate TLS
   at Nginx). Required for the tracker to load on `https://` sites.
4. Rate limiting is on by default (120 req/IP/min); tune via the env vars above.

### Switch to PostgreSQL

```bash
pip install asyncpg
export DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/barepulse"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

No code changes — the schema and queries are backend-agnostic.

---

## 🌐 Deploy (production-ready, batteries included)

This repo ships with everything needed for a safe public deployment. In
**production mode** (`BAREPULSE_ENV=production`) the server **refuses to start**
if the salt or dashboard password are missing — so you can't accidentally
expose an insecure instance.

### Option A — Render (one click)

A `render.yaml` Blueprint is included. Push to GitHub → Render → **New +** →
**Blueprint**. It provisions a persistent disk (so SQLite survives deploys),
auto-generates `BAREPULSE_SALT`, and turns on `BAREPULSE_TRUST_PROXY`. Just set
`BAREPULSE_PASSWORD` in the dashboard. HTTPS is automatic.

### Option B — Docker

```bash
docker build -t barepulse .
docker run -p 8000:8000 --env-file .env -v barepulse_data:/data \
  -e DATABASE_URL=sqlite+aiosqlite:////data/barepulse.db barepulse
```

The image runs as a non-root user and honors `$PORT`.

### Option C — VPS (systemd + Nginx)

Copy-paste configs live in [`deploy/`](deploy/):

- [`deploy/barepulse.service`](deploy/barepulse.service) — hardened systemd unit.
- [`deploy/nginx.conf.example`](deploy/nginx.conf.example) — reverse proxy that
  forwards the real client IP/scheme; pair with `certbot` for free TLS.

BarePulse honors `X-Forwarded-For` / `X-Real-IP` **only** when
`BAREPULSE_TRUST_PROXY=true`, so set that behind a proxy. For multiple workers
(use Postgres, not SQLite): `uvicorn app.main:app --workers 4`.

### Built-in hardening

- **Fail-fast** on insecure production config.
- **HTTP Basic auth** on the dashboard + stats API.
- **Per-IP rate limiting** on the collector.
- **Security headers** on every response: CSP, `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, and HSTS
  over HTTPS.
- **No raw IPs** ever stored; spoof-resistant client-IP handling.
- Optional **Host allow-list** via `BAREPULSE_ALLOWED_HOSTS`.

---

## 🗄️ Data model

A single lean table, `pageviews`:

| column | type | notes |
|---|---|---|
| `id` | int (PK) | |
| `domain` | str | site hostname |
| `path` | str | URL path + query |
| `referrer` | str | source **hostname** (`Direct` if none) |
| `browser` | str | parsed from User-Agent |
| `os` | str | parsed from User-Agent |
| `device_type` | str | Desktop / Mobile / Tablet |
| `user_hash` | str | **salted SHA-256 — never an IP** |
| `created_at` | datetime | server timestamp (UTC) |

---

## 📡 API

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/collect` | Ingest a pageview (called by the tracker). |
| `GET`  | `/api/stats?domain=&days=` | Aggregated stats (JSON). |
| `GET`  | `/api/health` | Liveness probe. |
| `GET`  | `/dashboard` | The analytics UI. |
| `GET`  | `/tracker.js` | The embeddable tracker. |

Interactive API docs are available at `/docs`.

---

<div align="center">

# 🇯🇵 日本語

</div>

## BarePulse とは

**Cookie を一切使わず**、完全にあなた自身のサーバー上で動作する、超高速な
アクセス解析プラットフォームです。**1KB 未満**の非同期トラッカーがアクセスを
収集し、**FastAPI** コレクターが匿名化して保存、**プレミアムなダークモード
ダッシュボード**がリアルタイムに可視化します。標準で **SQLite**（設定不要・
サーバー代0円）、環境変数1つで **PostgreSQL** に切り替え可能です。

## 🏆 ビジネス優位性（Acquire.com バイヤー様向け）

**「データ主権（Data Sovereignty）」こそが本製品の中核価値です。**

- **サーバー代 0 円・限界費用ゼロ**：標準の SQLite はファイル1つで完結。
  外部 DaaS も従量課金も不要で、トラフィックが増えても運用コストは増えません。
  これはそのまま **高い粗利率** を意味します。
- **Cookie 同意バナー不要**：IP アドレスを保存せず、日次ローテーションの
  ソルト付き SHA-256 ハッシュのみを保持するため、**GDPR / CCPA に設計段階で
  準拠**。導入企業の法務リスクとコンバージョン損失を同時に解消します。
- **ベンダーロックインなし**：すべて顧客自身のインフラ（Render / VPS）で完結。
  「自社データを他社クラウドに渡したくない」という強い市場ニーズに直接応えます。
- **圧倒的な軽量性**：トラッカーは 1KB 未満。Core Web Vitals を損なわず、
  既存の重い解析ツールからの乗り換え需要を取り込めます。
- **拡張性**：環境変数1つで PostgreSQL に移行でき、エンタープライズ規模まで
  同一コードベースでスケール。SaaS 化（マルチテナント課金）への発展も容易です。

> つまり BarePulse は、**運用コストほぼゼロ × 法規制リスクゼロ × 高い乗り換え
> 需要**を兼ね備えた、利益率の高い買収対象です。

## 🚀 3分で起動

```bash
# 1. 依存関係をインストール（Python 3.10 以上）
cd barepulse
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. 秘密のソルトを設定（訪問者ハッシュを保護します）
export BAREPULSE_SALT="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"

# 3. 起動
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

ブラウザで **http://localhost:8000/dashboard** を開くだけ。データベースは
初回起動時に自動生成されます。

## 🔌 自分のサイトへの埋め込み

計測したいサイトの `</body>` の直前に、**この1行**を貼るだけです。

```html
<script async defer
        src="https://your-host.com/tracker.js"
        data-endpoint="https://your-host.com/api/collect"></script>
```

URL（パス）、リファラー（流入元）、画面解像度、言語を自動収集し、SPA の
ページ遷移にも追従します。

## ⚙️ 設定

| 環境変数 | デフォルト | 説明 |
|---|---|---|
| `BAREPULSE_SALT` | 開発用の仮値 | **本番では必ず設定してください。** ハッシュ化用の秘密ソルト。 |
| `BAREPULSE_USER` | `admin` | ダッシュボードのログインユーザー名（Basic認証）。 |
| `BAREPULSE_PASSWORD` | _(空)_ | ダッシュボードのパスワード。**空＝ダッシュボードは公開状態**。公開前に必ず設定。 |
| `BAREPULSE_RATE_LIMIT` | `120` | IPごとの `/api/collect` 上限（ウィンドウあたり）。 |
| `BAREPULSE_RATE_WINDOW` | `60` | レート制限のウィンドウ（秒）。 |
| `DATABASE_URL` | `sqlite+aiosqlite:///./barepulse.db` | 任意の非同期 SQLAlchemy URL。 |

### 🔒 一般公開する前のチェックリスト

**収集側**（`/tracker.js`・`/api/collect`）は公開前提です。一方で
**ダッシュボードと統計API は非公開**であるべきです。インターネットに晒す前に：

1. **`BAREPULSE_PASSWORD` を設定**（任意で `BAREPULSE_USER` も）。未設定だと
   ダッシュボードと全統計が誰でも閲覧可能です。ダッシュボードは Basic 認証で
   保護され、`/api/collect` は仕様上そのまま公開されます。
2. **強力な `BAREPULSE_SALT` を設定** — `python3 -c 'import secrets; print(secrets.token_hex(32))'`。
3. **HTTPS で配信**（Render は自動。VPS では Nginx で TLS 終端）。`https://` の
   サイトにトラッカーを読み込ませるために必須です。
4. レート制限は標準で有効（120 req/IP/分）。上記の環境変数で調整できます。

### PostgreSQL へ切り替え

```bash
pip install asyncpg
export DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/barepulse"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

コードの変更は不要です。

---

<div align="center">

Built to be owned. **Your traffic. Your server. Your data.**

</div>
