# EventNow

A multi-tenant event-management platform built as a university web-systems capstone (UQ INFS7202) and kept live afterwards as a portfolio piece.

Organisers create and run events with scheduled sessions; attendees register, receive capacity-aware confirmations, and track upcoming events on a personal calendar.

## Live demo

**https://eventnow-kq6z.onrender.com/**

> Hosted on Render's free tier with a cron-job.org keep-alive ping every 10 minutes, so requests are usually instant. Rare cold-start delays of ~50 s can still happen after outages or redeploys.

### Organiser test accounts

Sign in: https://eventnow-kq6z.onrender.com/accounts/login/

| Tenant | Email | Password |
|---|---|---|
| UQ Goat Patters | `uqgoatpatters@proton.me` | `2LrcRm\|0<gT2` |
| UQ Dog Patters | `uqdogpatters@proton.me` | `sx£78C\|v8%3O` |
| UQ Random Events | `uqrandomevents@proton.me` | `8R9y*R\|M8£2g` |

### Attendee test account

Attendees sign in via **Google** — no password path. Use any Google account
to try the attendee flow (registering for events, personal calendar).

## What this project demonstrates

- **Multi-tenant SaaS data model** — every query is scoped to the organiser's tenant; four independent enforcement points (models, views, forms, ownership stamping) mean cross-tenant reads/writes fail even if one layer is misused.
- **Soft-delete pattern** — no destructive deletes; records transition through status flags, keeping history for audit and undo.
- **HTMX for progressive enhancement** — the AI-polish button, dynamic signup form, and speaker management update inline without a full page reload and without hand-rolled JavaScript.
- **Provider-agnostic LLM integration** — the "Polish with AI" feature talks to an OpenAI-compatible endpoint; swapping providers is a base-URL and API-key change, no client code touched.
- **Accessibility built in, not bolted on** — WCAG 2.1 AA contrast, keyboard navigable, skip-links, semantic landmarks.

## Tech stack

| Layer | Tech |
|---|---|
| Framework | Django 6.0, Python 3.14 |
| Database | PostgreSQL (Neon, pooled connection) |
| App server | gunicorn |
| Static files | WhiteNoise with finders fallback |
| Media storage | Cloudinary (CDN + on-the-fly image optimisation) |
| Frontend | Bootstrap 5.3, Bootstrap Icons, HTMX |
| Auth | django-allauth (email + password, Google OAuth) |
| LLM | LangChain against Groq's `openai/gpt-oss-20b` |
| Charts / calendar | FullCalendar, Google Charts |
| Hosting | Render (Singapore region, free tier) |
| Observability | Grafana Cloud — synthetic monitoring, Prometheus, alerting |

## Key features

- **Event & session CRUD** — full lifecycle including soft delete via status
- **Google OAuth** for organisers and attendees, plus password auth
- **Role-differentiated signup** — attendee vs. organiser flows swapped via HTMX
- **Personal attendee calendar** + a public calendar (FullCalendar reads a JSON feed exposed by the API)
- **Session capacity tracking** with check-in; doughnut chart for utilisation
- **Google Maps embeds** on event pages
- **"Polish with AI"** — rewrites rough event/session descriptions into marketing copy in one click (LangChain + Groq, HTMX partial update)
- **Django admin** with custom soft-delete actions for tenant management
- **Health probes and alerting** — `/healthz/` and `/readyz/` polled by Grafana Cloud; email alert on outage
- **WCAG 2.1 AA** contrast across authored content

## AI Usage Statement

**In the app.** The "Polish with AI" feature (rubric item 5.1) sends an organiser's rough event or session description to a large language model via LangChain — currently Groq's `openai/gpt-oss-20b` through Groq's OpenAI-compatible endpoint — and returns polished marketing copy. The provider is configured via the `LLM_API_KEY` env var and a `base_url` in `ai.py`, so it can be swapped for OpenAI, OpenRouter, or another compatible provider without touching any other code.

**In development.** Anthropic's Claude (via Claude Code) was used as a coding assistant during development for scaffolding views/models/forms, debugging deployment issues (Google OAuth, CSRF, database connections), generating Bootstrap markup, and writing code documentation. All architectural decisions were my own.

## Running locally

Requires Python 3.12+.

```
python -m venv venv
# Windows: .\venv\Scripts\Activate.ps1
source venv/bin/activate
pip install -r requirements.txt

cd eventnow
python manage.py migrate
python manage.py runserver
```

Then browse to http://127.0.0.1:8000/.

Copy `.env.example` to `.env` in the repository root and fill it in. The
minimum for local dev (SQLite fallback, no cloud services) is:

```
SECRET_KEY=any-random-string-for-dev
DEBUG=True
LLM_API_KEY=your-groq-or-openai-key
```

For the full production experience locally, also set `DATABASE_URL`, `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`.

## Deployment

Hosted on **Render** with a Neon Postgres pooled endpoint and Cloudinary for uploaded media.

- **Build command:** `pip install -r ../requirements.txt && python manage.py collectstatic --no-input && python manage.py migrate`
- **Start command:** `gunicorn eventnow.wsgi:application`
- **Root directory:** `eventnow`
- **HTTPS termination** happens at Render's load balancer; `SECURE_PROXY_SSL_HEADER` picks up the forwarded scheme.

Static files are served by WhiteNoise middleware with `WHITENOISE_USE_FINDERS = True` so the app is resilient to a build that skips `collectstatic`. User uploads route through Cloudinary because Render's free tier has no persistent disk.

## Monitoring

The live deployment is watched from outside by [Grafana Cloud](https://grafana.com/products/cloud/) synthetic monitoring, with email alerting on outage. Full runbook, dashboard queries and the free-tier arithmetic behind the design: [`monitoring/README.md`](monitoring/README.md).

The app exposes two probes, split along the Kubernetes liveness/readiness line:

| Endpoint | Checks | Poll interval | Cost |
|---|---|---|---|
| `/healthz/` | process up, credentials present | 2 min | no I/O at all |
| `/readyz/` | the above plus `SELECT 1` | 30 min | wakes the Neon compute |

The split is a budget decision. Neon's free tier suspends Postgres after ~5 minutes idle and bills compute hours, so a probe running `SELECT 1` every minute would hold the database awake around the clock and spend roughly twice the monthly allowance on health checks alone.

Neither probe ever calls the LLM — that would cost money and rate limit on every execution. The config check asserts the credentials are present and reports which model the app would ask for:

```json
{
  "status": "ok",
  "checks": { "db": "ok", "llm_config": "ok" },
  "version": "1743d71",
  "llm_model": "openai/gpt-oss-20b"
}
```

`status` distinguishes `ok` from `degraded` (misconfigured, but still serving) and `error` (a hard dependency is unreachable, HTTP 503). The synthetic check asserts on the response body as well as the status code, because the incident that prompted this work — a provider retiring the configured model — left the site returning a perfectly healthy 200 on every page.

*Planned (V2): OpenTelemetry instrumentation pushing RED metrics and per-request `polish_requests_total` / `polish_latency_seconds` to the same stack.*

## References

**Legislation cited** in the Privacy Policy and Terms of Service:

- *Privacy Act 1988* (Cth) — Privacy Policy §1; Terms of Service §§8–9 (APP-based handling of personal information).
- *Spam Act 2003* (Cth) — Terms of Service §5 (acceptable use).
- *Competition and Consumer Act 2010* (Cth) — Terms of Service §§9–10 (ACL non-exclusion of consumer guarantees, disclaimers, liability).

**Reference materials consulted:**

- Business Victoria — *Privacy policy template* (structure of the Privacy Policy page).
- Office of the Australian Information Commissioner — *Australian Privacy Principles guidelines*.
- W3C Web Accessibility Initiative — *Accessibility Principles* (POUR baseline).
- Django Software Foundation — *Security in Django* (settings hardening).
- Google Developers — *Visualization: Pie Chart* (capacity doughnuts).

## Image credits

All event and session images come from open-licence sources:

- [Pexels](https://www.pexels.com/) — Pexels licence (free to use, no attribution required).
- [Wikimedia Commons](https://commons.wikimedia.org/) — Creative Commons or public-domain licences.

## Author

Benjamin Lang — [GitHub](https://github.com/ben-lang-eng)