# NOTES

Working notes on EventNow — decisions, incidents, and what I would do
differently. Kept because the reasoning is more interesting than the diff.

---

## Incident, 2026-08-26 — "Polish with AI" returns 404 mid-demo

**Symptom.** During a live interview demo, the Polish button returned the
generic "The AI service is temporarily unavailable" message. The site was
otherwise fine. Nothing had been deployed in days.

**Five steps to the fix:**

1. **Symptom.** The user-facing error is deliberately generic — `views.py`
   catches everything from `ai.py`, logs the real exception, and shows a safe
   message. So the message itself carried no information. Correct design;
   inconvenient moment.
2. **Render logs.** `logger.exception("AI polish failed")` had the real cause:
   an HTTP 404 from `api.groq.com`. A 404 from a chat-completions endpoint is
   not "endpoint missing" — it is "model not found".
3. **Docs comparison.** Groq's current model list no longer contained
   `llama-3.3-70b-versatile` on the developer tier; it had been moved to
   Enterprise. The key was valid, the code was valid, the model was gone.
4. **Two-line fix.** Swapped the model to `openai/gpt-oss-20b`, the fastest
   free-tier model still listed and more than adequate for rewriting a
   paragraph. Redeployed.
5. **End-to-end verify.** Clicked Polish on the live site, not just locally —
   the failure only existed in production, so only production could prove it
   fixed.

**The uncomfortable part.** The 404s had been in the logs for hours. The failure
was trivially detectable and completely unnoticed, because nothing was watching.
That is what the monitoring project exists to fix — see `monitoring/README.md`.

**Follow-up, made during the monitoring work.** The model name and base URL are
now env vars (`LLM_MODEL`, `LLM_BASE_URL`) with the current values as defaults.
The next time a provider retires a model, the fix is a dashboard edit and a
restart rather than a code change and a redeploy. `/healthz/` reports the model
the app would ask for, so "which model is production actually using?" is now a
curl rather than a source-code read.

---

## Monitoring, evening 1 — outside-in (completed 31 Aug 2026)

Built: `/healthz/` and `/readyz/` in Django; Grafana Cloud synthetic checks
(`eventnow-healthz` 2 min / `eventnow-readyz` 30 min), `eventnow-overview`
dashboard (6 panels), `eventnow-down` alert (email, 5-min pending period).
Fire drill run and passed: firing email at ~8 min, resolved email after
revert. Evidence in `monitoring/`. cron-job.org keep-alive retired — the
2-minute probe keeps Render warm now.

**Hardest decision.**
The handover specified one `/health/` endpoint including a database `SELECT 1`,
polled every 1-5 minutes. The arithmetic killed it: Neon's free tier gives 100
compute-hours per month and suspends after ~5 minutes idle, so any probe
querying the database more often than every 5 minutes holds Postgres awake
around the clock — roughly 182 CU-hours a month, nearly double the whole
allowance, spent entirely on health checks. Split it into liveness (no I/O,
every 2 minutes) and readiness (SELECT 1, every 30 minutes) instead. The cost
is up to 30 minutes to notice a database outage. For a portfolio site that is
the right trade; for something with users it would not be.

> _My version, in my words:_

**What I learned.**
- Liveness vs. readiness is not Kubernetes ceremony — it falls out of the
  question "what does this check cost to run?"
- Synthetic monitoring is outside-in and knows only what a stranger with a
  browser knows. It cannot see *why* something failed. RUM and application
  metrics answer different questions; V2 is the inside-out half.
- `histogram_quantile()` needs a histogram. Synthetic monitoring publishes a
  gauge, so p95 comes from `quantile_over_time()`. Picking the right one
  depends on the metric's shape, not on what you want to know.
- A pending period is not a detail. Without one, every transient blip pages you
  and you stop reading your own alerts.
- An alert nobody has watched fire is a hypothesis.
- **The first thing my monitoring ever alerted on was itself.** The body
  assertion went in with inverted polarity ("fail if the body matches"), so
  every healthy probe failed while the site was fine — visible only because I
  read the probe's execution log instead of guessing. The same polarity trap
  appeared again an hour later in the alert condition (`IS ABOVE 0` on a
  metric that is 1 when healthy), caught by the rule preview showing *Firing*
  on a healthy site. Twice in one evening: assertion direction is where
  monitoring bugs live, and previews/logs are how you catch them.
- Grafana's unit setting labels data, it does not convert it. A
  `probe_duration_seconds` panel set to "milliseconds" happily rendered
  0.5 s as 0.5 ms. Sanity-check panels against a number you already know.
- Every label combination is its own time series. Editing a check mints a new
  `config_version` label and splits the graph; `max by (probe) (...)` is the
  fix, and the reason the check page counts "active series" against the 10k
  free budget.

> _My version, in my words:_

**What I would do differently.**
- Check the free-tier arithmetic *before* designing the endpoints, not after
  writing them. The Neon limit rewrote the design.
- Retiring the cron-job.org keep-alive makes the monitor load-bearing: if
  Grafana's probes stop, the site cold-starts. Fine here, wrong in general, and
  I should have noticed the coupling before proposing it rather than after.

> _My version, in my words:_

---

## Monitoring, evening 2 — inside-out

_To fill in after V2._

**Hardest decision.**

**What I learned.** (candidates: push vs. pull for OTel on a PaaS with no
sidecar; cardinality — why `error_type` is a safe label and `user_id` is not;
why the probes must be excluded from the RED metrics)

**What I would do differently.**
