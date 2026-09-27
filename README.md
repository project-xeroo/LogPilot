# LogPilot

An autonomous agent that watches your production logs around the clock, explains what's going wrong in plain
language, and forecasts failures **before** they become incidents. It supervises; people decide. Every proposal
it makes is recorded and needs a human sign-off before anyone acts on it.

## Contents

- [Built with IBM BOB](#built-with-ibm-bob)
- [What LogPilot does](#what-logpilot-does)
  - [See problems coming, not just after they land](#see-problems-coming-not-just-after-they-land)
  - [Talk to your logs](#talk-to-your-logs)
  - [Make sense of the noise automatically](#make-sense-of-the-noise-automatically)
  - [Explain the why, not just the what](#explain-the-why-not-just-the-what)
  - [Nothing happens without a person saying yes](#nothing-happens-without-a-person-saying-yes)
  - [Learn from what already happened](#learn-from-what-already-happened)
  - [Watch deployments, not just services](#watch-deployments-not-just-services)
  - [Built for how real teams actually work](#built-for-how-real-teams-actually-work)
- [Getting started](#getting-started)
  - [What you need first](#what-you-need-first)
  - [1. Configure](#1-configure)
  - [2. Start everything](#2-start-everything)
  - [3. Open it](#3-open-it)
  - [4. A port is already taken](#4-a-port-is-already-taken)
  - [5. Run the tests](#5-run-the-tests)
  - [6. Point it at a real hosted model](#6-point-it-at-a-real-hosted-model)
  - [7. Work on one part without the full stack](#7-work-on-one-part-without-the-full-stack)
  - [Stopping it](#stopping-it)
- [Honest boundaries](#honest-boundaries)

## Built with IBM BOB

We used IBM BOB to generate the entire structure and architecture of this software. It was dramatically faster
than every other model we tried at that job, which meant that instead of spending our hackathon window laying
scaffolding, we got to spend nearly all of it finishing features, fixing rough edges, and polishing the product
in our own editor. BOB gave us a running start; everything past that point — the workflows, the UI, the tuning,
the fixes — is us.

## What LogPilot does

LogPilot sits in front of your applications' log streams and turns raw noise into an early-warning system with
a memory. It doesn't just tell you something broke — it tells you *why*, whether it's happened before, whether
it's about to happen again, and what it thinks you should do about it.

### See problems coming, not just after they land

The centerpiece of the product is forward-looking risk scoring. Every service being watched gets a live health
score built from how fast its error rate is accelerating, not just how high it currently is — the same event
looks very different depending on whether it happened once or is compounding. When a service's trajectory
crosses into dangerous territory, LogPilot raises a **pre-incident alert** ahead of the outage instead of after
it, gives it a plain-language explanation, and keeps tracking it until someone acts.

### Talk to your logs

A conversational agent sits on top of the whole system. Ask it things like *"why is the checkout service
flagged?"* or *"what changed after the last deploy?"* and it answers using the actual data — root cause
analysis, log search, incident history, deployment comparisons — instead of generic advice. It can also just be
watched: a live feed shows what the agent is noticing in real time, so nobody has to go ask.

### Make sense of the noise automatically

Incoming logs — in whatever shape they arrive in — get parsed, sensitive information gets automatically
stripped out before it's stored or ever shown to a model, and near-identical errors get grouped into single
clusters instead of flooding a dashboard with thousands of duplicate rows. On top of that, an anomaly detector
watches for statistically unusual spikes or silences that a simple threshold would never catch.

### Explain the why, not just the what

For any incident or cluster of errors, the agent can put together a root-cause writeup: what broke, what likely
caused it, and what else was happening at the same time — including whether it lines up with a recent
deployment. It can also generate a full incident or health report on demand, which a human can edit and sign off
on before it goes out.

### Nothing happens without a person saying yes

This is the part we care about most. The agent only ever *proposes* — it never takes action on its own. Every
suggested fix or response goes through a strict pipeline we designed specifically for how real teams are
structured:

1. **Flagged** — the agent notices something and drafts a recommendation.
2. **Needs a decision** — visible only to senior roles (admins, SREs, developers), who approve, edit, or dismiss it.
3. **Open** — once a decision has been made, it becomes visible to the whole team, including junior engineers, as something safe to actually go work on.
4. **History** — the full resolved record, kept for good.

Junior team members never see an unapproved proposal sitting in their queue; they only ever see work that's
already been vetted. Every decision — who approved what, when, and why — is written to a tamper-evident audit
log.

### Learn from what already happened

LogPilot remembers past incidents. When something resembling a prior outage starts building again, it can
connect the dots and say so, instead of treating every incident as if it's never seen anything like it before.

### Watch deployments, not just services

Every release gets compared against the error behavior right before and after it shipped, so a bad deploy shows
up as a bad deploy — not as an unexplained spike that someone has to manually trace back.

### Built for how real teams actually work

- **Role-based access** — Viewers can look but not touch; junior engineers work in a guided mode and can't
  approve anything themselves; seniors and admins have full authority; every permission boundary is enforced
  server-side, not just hidden in the interface.
- **Full audit trail** — every decision, approval, and dismissal is logged and attributable, with built-in
  success metrics so a team can see whether the agent is actually helping.
- **Configurable autonomy policy** — thresholds, notification routing, and webhook destinations are all
  controlled from settings, with guardrails so a webhook can't be pointed at an internal address by mistake.
- **Programmatic access** — API keys let other systems integrate with the platform the same way the console
  does.
- **Works offline or with a real hosted model** — a fully deterministic offline mode means the whole product
  works with nothing external at all; point it at a hosted model instead and every prompt and embedding is
  scrubbed of sensitive data first, and outbound calls are restricted to an explicit allow-list.

## Getting started

### What you need first

A machine that can run containers, with Compose support — that's the only real requirement. The default setup
needs no external accounts, keys, or network access; it runs fully self-contained out of the box.

### 1. Configure

```bash
cp .env.example .env
```

The defaults work as-is for trying it out. Every value in `.env.example` — including the demo account
passwords — is a placeholder; change them before running this anywhere other than your own machine.

### 2. Start everything

```bash
make up
```

The first run builds every part of the system from scratch, which takes a few minutes; later runs are fast.
This one command also brings up the infrastructure the application depends on (database, cache, vector search,
object storage) — it's pulled in from a nested setup folder included in this repo, so leave that folder where
it is.

### 3. Open it

- Console (the app itself): **http://localhost:8080**
- API, with interactive docs: **http://localhost:8000/docs**

Sign in with one of the built-in demo accounts (email/password are in `.env.example`) — they cover every role,
from an admin down to a read-only viewer. Upload logs from the console, or send them straight to the ingestion
API, and the agent starts working immediately: parsing, redacting, clustering, and scoring risk in real time.
Ask it about anything it flags.

### 4. A port is already taken

Every port this stack exposes has a matching `*_PORT` override — open `.env.example` to see them all, set the
ones you need in `.env`, and re-run `make up`.

### 5. Run the tests

```bash
make test      # backend + frontend unit tests
make e2e        # end-to-end checks against the running stack (needs `make up` first)
```

### 6. Point it at a real hosted model

By default, every piece of intelligence in the system runs on a deterministic offline mode — no key, no outside
network call, fully reproducible, good enough to see the whole product work end to end. To use a real hosted
model instead, set the `AI_*` variables in `.env` (endpoint, key, and the model names for its three roles) and
restart. Every prompt and embedding sent out is scrubbed of sensitive data first, and outbound calls are
restricted to an explicit allow-list you control.

### 7. Work on one part without the full stack

Start just the infrastructure on its own, then run any single part of the system directly against it for a
fast local loop instead of rebuilding containers on every change. The console has its own hot-reload mode via
`make console-dev`.

### Stopping it

```bash
make down       # stop everything, keep your data
make clean      # stop everything and wipe data + build output
```

## Honest boundaries

This release proposes and records; it does not execute remediations on its own — that's a deliberate choice,
not a missing feature. Things like chat-app notifications, autonomous execution, cascading-failure prediction,
single sign-on, and multi-tenant support are intentionally left for later so the core loop — notice, explain,
decide, act — could be built properly first.
