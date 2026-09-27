"""Rule-based composers used by the offline provider. Each takes the *same structured context* a
cloud model would be given in its prompt and returns the same output shape (plain text or JSON)."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Callable

# ---- small helpers ---------------------------------------------------------------------------------------


def _dt(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _ts(v: Any) -> str:
    d = _dt(v)
    return d.strftime("%b %d %H:%M UTC") if d else "unknown time"


def _hm(v: Any) -> str:
    d = _dt(v)
    return d.strftime("%H:%M") if d else "?"


def _when_list(incidents: list[dict]) -> str:
    """'November 14th and December 3rd'; adds the time when two incidents fall on the same date."""
    dates = [_ordinal_date(s.get("incident_start")) for s in incidents]
    if len(set(dates)) < len(dates):
        dates = [f"{d} at {_hm(s.get('incident_start'))} UTC" for d, s in zip(dates, incidents, strict=True)]
    return _join(dates)


def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:]


def _ordinal_date(v: Any) -> str:
    d = _dt(v)
    if not d:
        return "an earlier date"
    n = d.day
    suf = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{d.strftime('%B')} {n}{suf}"


def _join(items: list[str], conj: str = "and") -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {conj} " + items[-1]


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _top_msgs(errs: list[dict], n: int = 3) -> str:
    return "; ".join(f"\"{e.get('message', '')[:110]}\" (x{e.get('count', 0)})" for e in (errs or [])[:n])


def _sections(items: list[tuple[str, str, str]]) -> str:
    return json.dumps({"sections": [{"key": k, "title": t, "body": b.strip()} for k, t, b in items]})


def _sev_word(rank: str) -> str:
    return {"high": "serious", "medium": "notable", "low": "minor"}.get(rank, "notable")


# ---- error meaning catalogue (junior-engineer explanations) ------------------------------------------------
_MEANINGS: list[tuple[re.Pattern, dict[str, Any]]] = [
    (re.compile(r"timed? ?out|deadline exceeded", re.I), dict(
        meaning="An operation gave up waiting for a response. The service called something (a database, cache or another service) and it did not answer in time.",
        why="Timeouts usually mean the dependency is slow or overloaded, and they tend to cascade: callers pile up waiting, which slows their callers too.",
        check=["Is the dependency named in the message healthy right now?", "Did latency or error rate rise there just before this appeared?", "Was there a recent deployment or traffic spike?"])),
    (re.compile(r"connection (refused|reset|closed)|econnrefused|unreachable|no route", re.I), dict(
        meaning="The service tried to open a network connection and the other side refused or dropped it - typically because the target is down, restarting, or not accepting connections.",
        why="Nothing that needs that dependency can work until it is reachable again.",
        check=["Is the target process/pod running?", "Did it restart or get replaced?", "Are network rules or DNS changes involved?"])),
    (re.compile(r"pool|too many connections|max(imum)? connections|exhaust", re.I), dict(
        meaning="A limited set of reusable connections (a connection pool) has run out. New requests must wait or fail until a connection is freed.",
        why="Pool exhaustion is a classic leading indicator of an outage: it often starts small and snowballs as requests queue up.",
        check=["Are connections being leaked (opened but never released)?", "Did traffic rise, or did downstream calls get slower?", "Is the pool limit sized for current load?"])),
    (re.compile(r"out ?of ?memory|oom|heap|memory", re.I), dict(
        meaning="The process ran out of memory. The operating system or runtime may kill it.",
        why="Repeated OOM kills cause restarts and dropped requests.",
        check=["Is memory growing steadily (a leak) or spiking with load?", "Was a memory limit changed recently?", "Which request or job preceded the crash?"])),
    (re.compile(r"deadlock|lock wait|could not serialize", re.I), dict(
        meaning="Two database transactions were waiting on each other's locks, so the database aborted one.",
        why="Occasional deadlocks are normal; a rising rate means contention is building.",
        check=["Which queries/tables are involved?", "Did a code change alter transaction ordering?", "Is load higher than usual?"])),
    (re.compile(r"unauthori[sz]ed|forbidden|denied|invalid token|expired|401|403", re.I), dict(
        meaning="A request was rejected because credentials or permissions were missing, wrong or expired.",
        why="A burst of these can mean an expired secret or a misconfigured integration - or an attack.",
        check=["Did a credential or token expire or rotate?", "Is it one client or many?", "Any recent permission changes?"])),
    (re.compile(r"no space|disk (full|space)|enospc", re.I), dict(
        meaning="The disk is full, so the service cannot write files or logs.",
        why="Many services fail hard when they cannot write.",
        check=["Which volume is full and what grew?", "Can logs be rotated or old data cleared?", "Is auto-expansion configured?"])),
    (re.compile(r"null ?pointer|nil pointer|undefined|none ?type|attributeerror|keyerror", re.I), dict(
        meaning="The code used a value that was empty (null/None) or missing. This is a programming bug or unexpected input, not an infrastructure fault.",
        why="If it began right after a release it is a regression.",
        check=["Did it start with a deployment?", "What input triggered it (see request/trace ID)?", "Is a fix or rollback needed?"])),
    (re.compile(r"\b5\d\d\b|bad gateway|service unavailable|internal server error", re.I), dict(
        meaning="A server returned a 5xx status: the request was valid but the server failed to handle it.",
        why="5xx responses are directly user-visible failures.",
        check=["Which endpoint and service?", "Is an upstream dependency failing?", "Did it begin after a deployment?"])),
]
_DEFAULT_MEANING = dict(
    meaning="The service logged an error. Read the message for the failing operation and any identifiers (request ID, trace ID) to follow it through other services.",
    why="Repeated or accelerating errors are more important than a single occurrence.",
    check=["How often does it occur, and is that rate rising?", "Is it confined to one service or spread?", "Did anything change (deploy, config, traffic) beforehand?"])


def log_explanation(ctx: dict) -> str:
    msg = ctx.get("message", "")
    sev = (ctx.get("severity") or "ERROR").upper()
    info = next((v for pat, v in _MEANINGS if pat.search(msg)), _DEFAULT_MEANING)
    count = ctx.get("count")
    normal = sev in ("DEBUG", "INFO") or (sev == "WARN" and (count or 0) < 10)
    return json.dumps({
        "meaning": info["meaning"],
        "is_normal": bool(normal),
        "normality_note": ("This level of message is routine." if normal else
                           f"Errors like this are not normal{f' - it occurred {count} times' if count else ''}; worth understanding before it grows."),
        "why_it_matters": info["why"],
        "what_to_check": info["check"],
    })


# ---- chat --------------------------------------------------------------------------------------------------
def chat_answer(ctx: dict) -> str:
    tr = ctx.get("tool_results") or {}
    sources = ctx.get("sources") or []
    parts: list[str] = []

    risk = tr.get("risk")
    if risk:
        svcs = risk.get("services") or []
        focus = risk.get("focus")
        if focus and risk.get("detail"):
            d = risk["detail"]
            parts.append(_risk_detail_text(d))
        elif svcs:
            hot = [s for s in svcs if s.get("risk_score", 0) >= 60]
            top = sorted(svcs, key=lambda s: -s.get("risk_score", 0))[:5]
            if hot:
                parts.append("**Services at elevated failure risk right now:**\n" + "\n".join(
                    f"- **{s['service']}** - risk {s['risk_score']:.0f}/100 ({s.get('level', 'elevated')}, {s.get('trend', 'steady')})"
                    + (f", estimated time to impact {s['eta_minutes_low']:.0f}-{s['eta_minutes_high']:.0f} min" if s.get("eta_minutes_low") else "")
                    for s in sorted(hot, key=lambda s: -s["risk_score"])))
            else:
                parts.append("No service is above the warning threshold (60). Current highest risk: " + _join(
                    [f"{s['service']} ({s['risk_score']:.0f})" for s in top[:3]]) + ".")
    health = tr.get("health")
    if health:
        parts.append(_health_text(health))
    rca = tr.get("rca")
    if rca:
        parts.append(_rca_text(rca))
    dep = tr.get("deployment")
    if dep:
        parts.append(_deployment_text(dep))
    rep = tr.get("report")
    if rep:
        parts.append(f"I've drafted a **{rep.get('kind', 'incident').replace('_', ' ')} report**: \"{rep.get('title')}\". It is saved as a draft in Reports for review, editing and sign-off before export.")
    srch = tr.get("search")
    if srch is not None:
        n = srch.get("count", 0)
        if n:
            parts.append(f"I found **{n}** matching log record(s). The most recent:\n" + "\n".join(
                f"- `{r.get('timestamp', '')[:19]}` **{r.get('service')}** [{r.get('severity')}] {r.get('message', '')[:140]}" for r in (srch.get("results") or [])[:5]))
        else:
            parts.append("I didn't find any log records matching that search.")
    expl = tr.get("explain")
    if expl:
        parts.append(f"**What this means:** {expl.get('meaning')}\n\n**Why it matters:** {expl.get('why_it_matters')}\n\n**What to check:**\n" + "\n".join(f"- {c}" for c in expl.get("what_to_check", [])))

    if sources and not (risk or rca or dep or expl):
        lines = []
        for s in sources[:5]:
            occ = f", seen {s['count']:,} times" if s.get("count") else ""
            lines.append(f"- [{s['n']}] **{s.get('service')}** {s.get('severity', '')} at {_ts(s.get('timestamp'))}{occ}: {s.get('message', '')[:150]}")
        parts.append("Here is the most relevant evidence I found in the logs:\n" + "\n".join(lines))
    elif sources and (risk or rca or dep):
        parts.append("Supporting log evidence: " + _join([f"[{s['n']}] {s.get('service')}: {s.get('message', '')[:70]}" for s in sources[:3]], "and") + ".")

    mem = ctx.get("memory") or []
    if mem:
        m = mem[0]
        acts = _join(m.get("resolved_actions") or [])
        parts.append(f"**This resembles a past incident** - \"{m.get('label')}\" ({_ordinal_date(m.get('when'))})" + (f", which was resolved by: {acts}." if acts else "."))

    if not parts:
        parts.append("I couldn't find log evidence that answers this. Try naming a service, a time range (\"last hour\"), or paste an error message and I'll explain it.")

    if ctx.get("guided_mode"):
        parts.append(_guided_note(ctx))
    return "\n\n".join(parts)


def _guided_note(ctx: dict) -> str:
    tr = ctx.get("tool_results") or {}
    notes = []
    if tr.get("risk"):
        notes.append("**Risk score** - a 0-100 estimate of how likely a service is to fail soon; 60+ is a warning and 80+ is critical.")
    if tr.get("health"):
        notes.append("**Error rate** - the share of log lines that are errors; a rising rate matters more than a single error.")
    if tr.get("rca"):
        notes.append("**Root cause** - the original fault; the other services in the chain are usually just victims of it.")
    if tr.get("deployment"):
        notes.append("**Regression** - behaviour that got worse after a release.")
    if not notes:
        notes.append("**Why I answered this way** - I searched the logs for messages similar in meaning to your question and summarised the closest matches, citing each with [n].")
    return "> *In plain language:* " + " ".join(notes)


def _health_text(h: dict) -> str:
    svcs = h.get("services") or []
    bad = [s for s in svcs if s.get("status") in ("unhealthy", "degraded", "silent")]
    head = f"In the last {h.get('window_minutes', 60)} minutes I'm watching **{len(svcs)}** services; " + (
        f"**{len(bad)}** need attention." if bad else "all look healthy.")
    lines = [head]
    for s in svcs[:5]:
        if s["errors"]:
            lines.append(f"- **{s['service']}** - {s['status']}: {s['errors']} errors ({_pct(s['error_rate'])} of logs, {s['errors_per_min']}/min, {s['trend']})")
    tr = h.get("trending_issues") or []
    if tr:
        t = tr[0]
        lines.append(f"Fastest-growing error: \"{t['message'][:100]}\" ({t['count']} now vs {t['previous_count']} before).")
    return "\n".join(lines)


def _risk_detail_text(d: dict) -> str:
    s = d.get("service", "service")
    sig = d.get("signals") or {}
    v, sim, b = sig.get("velocity") or {}, sig.get("similarity") or {}, sig.get("baseline") or {}
    lines = [f"**{s}** failure risk is **{d.get('risk_score', 0):.0f}/100** ({d.get('trend', 'steady')})."]
    if d.get("eta_minutes_low"):
        lines.append(f"Estimated time to impact: {d['eta_minutes_low']:.0f}-{d['eta_minutes_high']:.0f} minutes.")
    lines.append(f"- Velocity signal {d.get('velocity_score', 0):.0f}: {v.get('errors_per_min', 0):.1f} errors/min, acceleration {v.get('acceleration', 0):+.2f}")
    lines.append(f"- Baseline deviation {d.get('baseline_score', 0):.0f}: {b.get('ratio', 0):.1f}x its typical rate (z={b.get('z', 0):.1f})")
    lines.append(f"- Pattern similarity {d.get('similarity_score', 0) * 1:.0f}: best match {sim.get('best_match', 0):.2f}" + (f" to \"{sim.get('best_label')}\"" if sim.get("best_label") else ""))
    if d.get("explanation"):
        lines.append("\n" + d["explanation"])
    return "\n".join(lines)


def _rca_text(r: dict) -> str:
    chain = r.get("causal_chain") or []
    conf = r.get("confidence", 0)
    if not chain:
        return "I couldn't establish a causal chain for that window - there wasn't enough correlated error activity."
    root = chain[0]
    path = " -> ".join(f"**{c['service']}**" for c in chain)
    return (f"**Root cause analysis** (confidence {conf * 100:.0f}%): {path}.\n\n{r.get('explanation', '')}\n\n"
            f"Root cause candidate: **{root['service']}** - {root.get('event', '')}")


def _deployment_text(d: dict) -> str:
    a, b = d["from"], d["to"]
    head = f"**{d['service']}** {a['version']} -> {b['version']}: error rate {_pct(a['error_rate'])} -> {_pct(b['error_rate'])}"
    if d.get("regression"):
        return head + f". **Regression detected:** {'; '.join(d.get('regression_reasons') or [])}."
    return head + ". No regression detected."


# ---- anomaly / labels / deployment narrative -------------------------------------------------------------------
def anomaly_explanation(ctx: dict) -> str:
    base = ctx.get("explanation", "")
    kind = ctx.get("kind", "")
    hint = {
        "error_spike": " This kind of jump often follows a deployment, a dependency slowing down, or a traffic surge.",
        "latency_spike": " Slower responses usually point at a saturated dependency or resource.",
        "traffic_spike": " Check whether this is expected (a campaign, a retry storm) or unexpected.",
        "new_error_type": " A brand-new error usually points at a recent code or configuration change.",
        "service_silence": " Check the process, its host, and the log shipper.",
    }.get(kind, "")
    return base + hint


def cluster_label(ctx: dict) -> str:
    from collections import Counter

    stop = set("the a an of to for in on at by with from and or is are was were be not no failed failure error errors exception unable could due while during after before when into over under via num id ip ts uuid hex request response".split())
    c: Counter = Counter()
    for t in (ctx.get("templates") or [])[:8]:
        for tok in set(re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", re.sub(r"<[a-z]+>", " ", t))):
            if tok.lower() not in stop:
                c[tok.lower()] += 1
    words = [w for w, _ in c.most_common(3)]
    return " ".join(w.replace("_", " ").title() for w in words) or "Unclassified Errors"


def narrate_deployment(ctx: dict) -> str:
    comp = ctx.get("comparison") or {}
    if not comp:
        return ctx.get("summary", "")
    return _deployment_text(comp) + (f" New error types: {_top_msgs([{'message': n.get('sample', ''), 'count': n.get('count', 0)} for n in comp.get('new_error_types', [])[:2]])}." if comp.get("new_error_types") else "")


# ---- RCA --------------------------------------------------------------------------------------------------------
def rca_reasoning(ctx: dict) -> str:
    chain = ctx.get("chain") or []
    edges = ctx.get("edges") or []
    out_chain = []
    for i, c in enumerate(chain):
        role = c.get("role") or ("root_cause" if i == 0 else "symptom")
        ev = _top_msgs(c.get("top_errors") or [], 2)
        onset = _hm(c.get("onset"))
        if role == "root_cause":
            event = f"Errors began at {onset}: {ev}" if ev else f"Anomalous activity began at {onset}"
        else:
            lag = c.get("lag_from_root_min")
            event = f"Errors began at {onset}" + (f" ({lag:.0f} min after the root cause)" if lag is not None else "") + (f": {ev}" if ev else "")
        out_chain.append({"service": c["service"], "role": role, "event": event, "onset": c.get("onset")})
    if not out_chain:
        expl = "There was not enough correlated error activity across services to establish a causal chain."
    else:
        root = out_chain[0]["service"]
        links = [e for e in edges if e.get("from") == root]
        tail = _join([c["service"] for c in out_chain[1:]])
        expl = (f"The earliest and strongest signal is in **{root}**: {out_chain[0]['event']}. "
                + (f"Errors then appeared downstream in {tail}, consistent with a cascade from {root}. " if tail else "")
                + (f"Timing correlation supports this (best lag {links[0].get('lag_min', 0):.0f} min, correlation {links[0].get('corr', 0):.2f}"
                   + (f", {links[0]['trace_count']} shared traces" if links[0].get("trace_count") else "") + "). " if links else "")
                + "Downstream errors are most likely secondary failures rather than independent faults.")
    return json.dumps({"causal_chain": out_chain, "explanation": expl, "confidence": ctx.get("algorithmic_confidence", 0.5)})


# ---- forecasting: risk explanation + recommended actions --------------------------------------------------------
_ACTION_RULES: list[tuple[re.Pattern, list[tuple[str, str, str]]]] = [
    (re.compile(r"redis|cache|session|pool|connection", re.I), [
        ("Restart the affected worker/session processes to release leaked connections", "Connection or pool exhaustion is the most likely mechanism; restarting frees held connections.", "high"),
        ("Scale the cache/connection tier (add replicas or raise the pool limit)", "Adds headroom so new requests stop queuing.", "high"),
    ]),
    (re.compile(r"postgres|database|sql|deadlock|query|db", re.I), [
        ("Inspect long-running queries and lock waits; kill blockers if safe", "Contention or slow queries are saturating the database.", "high"),
        ("Increase database connection pool size or fail over to a replica", "Restores capacity while the cause is investigated.", "high"),
    ]),
    (re.compile(r"memory|oom|heap", re.I), [
        ("Restart the pod with a higher memory limit and capture a heap profile", "Memory exhaustion will cause repeated restarts.", "high"),
    ]),
    (re.compile(r"disk|space|enospc", re.I), [("Free disk space (rotate/compress logs, clear temp data) or expand the volume", "The service fails when it cannot write.", "low")]),
    (re.compile(r"timeout|timed out|upstream|502|503|504|gateway", re.I), [
        ("Enable/verify the circuit breaker and retry backoff for the failing dependency", "Stops retries amplifying the failure.", "low"),
        ("Scale out the failing upstream service", "Relieves the saturated dependency.", "high"),
    ]),
    (re.compile(r"unauthori|denied|forbidden|token|credential", re.I), [("Check for expired or rotated credentials on the failing integration", "A burst of auth failures usually means a credential expired.", "low")]),
]


def _actions_for(top_errors: list[dict], service: str, similar: list[dict], playbook: list[str] | None, deployment: dict | None) -> list[dict]:
    actions: list[dict] = []
    seen: set[str] = set()

    def add(text: str, why: str, risk: str, src: str) -> None:
        k = re.sub(r"\W+", " ", text.lower()).strip()
        if k not in seen:
            seen.add(k)
            actions.append({"text": text, "rationale": why, "risk_level": risk, "source": src})

    for s in similar[:2]:
        for a in (s.get("resolved_actions") or [])[:2]:
            add(a, f"Resolved the similar incident \"{s.get('label', '')}\" on {_ordinal_date(s.get('incident_start'))}.", "high" if re.search(r"restart|scale|rollback|failover|kill", a, re.I) else "low", "historical")
    for p in (playbook or [])[:2]:
        add(p, f"Step from the configured playbook for {service}.", "high" if re.search(r"restart|scale|rollback|failover", p, re.I) else "low", "playbook")
    if deployment and deployment.get("regression"):
        add(f"Roll back {service} to {deployment['from']['version']}", f"Errors rose after {deployment['to']['version']} was deployed: {'; '.join(deployment.get('regression_reasons') or [])}.", "high", "llm")
    blob = " ".join(e.get("message", "") for e in top_errors)
    for pat, rules in _ACTION_RULES:
        if pat.search(blob):
            for text, why, risk in rules:
                add(text, why, risk, "llm")
    if not actions:
        add(f"Investigate the top error in {service} and check recent changes", "No specific failure pattern matched; start with the dominant error and recent deployments.", "low", "llm")
    return actions[:5]


def risk_explanation(ctx: dict) -> str:
    svc = ctx.get("service", "service")
    score = float(ctx.get("risk_score", 0))
    sig = ctx.get("signals") or {}
    v, sim, b = sig.get("velocity") or {}, sig.get("similarity") or {}, sig.get("baseline") or {}
    similar = ctx.get("similar_incidents") or []
    eta = ctx.get("eta") or {}
    top_errors = ctx.get("top_errors") or []
    prob = max(0.05, min(0.97, score / 100.0))

    contrib = {"velocity": ctx.get("velocity_score", 0) * 0.3, "similarity": ctx.get("similarity_score", 0) * 0.4, "baseline": ctx.get("baseline_score", 0) * 0.3}
    primary = max(contrib, key=contrib.get)
    ratio = b.get("ratio") or 0
    if primary == "velocity":
        signal = f"Error velocity is {v.get('errors_per_min', 0):.1f}/min and accelerating ({v.get('acceleration', 0):+.2f}/min²)" + (f", {ratio:.1f}x baseline" if ratio > 1 else "")
    elif primary == "baseline":
        signal = f"Error rate is {ratio:.1f}x its normal level for this time of day (z={b.get('z', 0):.1f})"
    else:
        signal = f"The current error pattern closely matches known pre-failure signatures (similarity {sim.get('best_match', 0):.2f})"
    if top_errors:
        signal += f", led by \"{top_errors[0].get('message', '')[:90]}\""
    matched = ""
    if similar:
        matched = f" It matches the pattern that preceded the {_when_list(similar[:3])} outage{'s' if len(similar) > 1 else ''}."
    eta_txt = f" Estimated time to impact: {eta['low']:.0f}–{eta['high']:.0f} minutes." if eta.get("low") else ""
    actions = _actions_for(top_errors, svc, similar, ctx.get("playbook"), ctx.get("deployment"))
    rec = f" Recommended: {_join([_lower_first(a['text'].split(' (')[0]) if i else a['text'].split(' (')[0] for i, a in enumerate(actions[:2])])}." if actions else ""
    if ctx.get("degraded"):
        alert = f"{svc}: risk score {score:.0f}/100 (threshold-based alert: AI reasoning is currently unavailable). {signal}."
    else:
        alert = f"{svc}: {prob * 100:.0f}% failure probability. {signal}.{matched}{eta_txt}{rec}"
    parts = [f"{svc} is at risk score {score:.0f}/100 ({ctx.get('level', 'elevated')}).", signal + "."]
    if similar:
        parts.append(f"Historical evidence: {len(similar)} similar pre-incident window(s); the closest ({_ordinal_date(similar[0].get('incident_start'))}, similarity {similar[0].get('similarity', 0):.2f}) was \"{similar[0].get('label', '')}\"" + (f" and was resolved by {_join(similar[0].get('resolved_actions') or [])}." if similar[0].get("resolved_actions") else "."))
    else:
        parts.append("No comparable past incident is on record yet, so this rests on velocity and baseline deviation alone.")
    return json.dumps({"failure_probability": round(prob, 3), "alert_text": alert, "explanation": " ".join(parts), "recommended_actions": actions})


# ---- reports -----------------------------------------------------------------------------------------------------
def incident_report(ctx: dict) -> str:
    w = ctx.get("window") or {}
    imp = ctx.get("impact") or {}
    services = ctx.get("services") or []
    rca = ctx.get("rca") or {}
    chain = rca.get("causal_chain") or []
    root = chain[0]["service"] if chain else (services[0]["service"] if services else "the affected service")
    dur = w.get("duration_min")
    title_svc = _join([s["service"] for s in services[:3]]) or root
    summary = (f"Between {_ts(w.get('start'))} and {_ts(w.get('end'))}"
               + (f" ({dur:.0f} minutes)" if dur else "") + f", **{title_svc}** experienced elevated errors: **{imp.get('total_errors', 0):,}** error records"
               + (f" touching roughly {imp['affected_requests']:,} distinct requests" if imp.get("affected_requests") else "")
               + f". The most likely root cause is **{root}**" + (f" (confidence {rca.get('confidence', 0) * 100:.0f}%)." if rca else "."))
    tl = ctx.get("timeline") or []
    timeline = "\n".join(f"- **{_hm(e.get('time'))}** {e.get('event')}" for e in tl[:20]) or "- No timeline events were captured."
    affected = "\n".join(f"- **{s['service']}** - {s.get('errors', 0):,} errors, peak {s.get('peak_per_min', 0):.0f}/min, first error {_hm(s.get('first_error'))}. Top errors: {_top_msgs(s.get('top_errors') or [], 2)}" for s in services[:8]) or "- No affected services identified."
    impact = (f"- Total error records: **{imp.get('total_errors', 0):,}**\n- Services affected: **{imp.get('services_affected', len(services))}**\n"
              f"- Peak error rate: **{_pct(imp.get('peak_error_rate'))}** of log volume\n" + (f"- Duration: **{dur:.0f} minutes**\n" if dur else "")
              + (f"- Distinct requests with errors: **{imp['affected_requests']:,}**\n" if imp.get("affected_requests") else ""))
    root_body = (rca.get("explanation") or f"The earliest anomalous behaviour was in {root}.") + ("\n\nCausal chain: " + " → ".join(c["service"] for c in chain) if chain else "")
    outcomes = ctx.get("outcomes") or []
    acts = ctx.get("actions") or []
    if outcomes:
        resolution = "\n".join(f"- Outcome **{o.get('outcome')}**" + (f"; action taken: {o.get('action_taken')}" if o.get("action_taken") else "") + (f"; resolved in {o['time_to_resolve'] // 60} min" if o.get("time_to_resolve") else "") for o in outcomes)
    else:
        resolution = "Resolution has not been recorded yet. Once engineers log the outcome (what was done and how long it took), it will appear here." + ("\n\nActions proposed by the agent:\n" + "\n".join(f"- {a.get('text')} ({a.get('status', 'proposed')})" for a in acts[:5]) if acts else "")
    prev = [a.get("text") for a in acts if a.get("text")][:5]
    prevent = "\n".join(f"- {p}" for p in prev) or "- Add alerting on the leading indicators identified above.\n- Review capacity limits for the root-cause service."
    prevent += "\n- Keep Proactive Failure Forecasting enabled for the affected services so the next occurrence is flagged before impact."
    return _sections([("summary", "Summary", summary), ("timeline", "Timeline", timeline), ("affected_services", "Affected Services", affected),
                      ("impact_analysis", "Impact Analysis", impact), ("root_cause", "Root Cause", root_body),
                      ("resolution", "Resolution", resolution), ("preventive_actions", "Preventive Actions", prevent)])


def pre_mortem(ctx: dict) -> str:
    svc = ctx.get("service", "the service")
    a = ctx.get("alert") or {}
    sig = ctx.get("signals") or {}
    v, b = sig.get("velocity") or {}, sig.get("baseline") or {}
    similar = ctx.get("similar_incidents") or []
    eta = ctx.get("eta") or {}
    deps = ctx.get("dependents") or []
    actions = ctx.get("actions") or []
    top = ctx.get("top_errors") or []
    rca = ctx.get("rca") or {}
    eta_txt = f"within roughly **{eta['low']:.0f}–{eta['high']:.0f} minutes**" if eta.get("low") else "soon"
    summary = (f"**{svc}** is forecast to fail {eta_txt} (risk score **{a.get('risk_score', 0):.0f}/100**, {a.get('level', 'critical')}). "
               "This pre-mortem describes the failure the agent expects, why, who it would hit, and how to prevent it.")
    happening = (f"Error volume in {svc} is climbing at {v.get('errors_per_min', 0):.1f} errors/min ({b.get('ratio', 0):.1f}x its normal level) and accelerating. "
                 f"If the trend continues, {svc} will start failing user requests, then callers of {svc} will time out waiting for it.")
    why = "\n".join([f"- Error velocity {ctx.get('velocity_score', 0):.0f}/100 – {v.get('errors_per_min', 0):.1f}/min, acceleration {v.get('acceleration', 0):+.2f}/min²",
                     f"- Baseline deviation {ctx.get('baseline_score', 0):.0f}/100 – {b.get('ratio', 0):.1f}x typical (z={b.get('z', 0):.1f})",
                     f"- Pattern similarity {ctx.get('similarity_score', 0):.0f}/100"] +
                    [f"- Similar to \"{s.get('label')}\" on {_ordinal_date(s.get('incident_start'))} (similarity {s.get('similarity', 0):.2f})" for s in similar[:3]] +
                    ([f"- Dominant errors: {_top_msgs(top, 3)}"] if top else []))
    affected = (f"Directly: **{svc}** and its users.\n" + (f"Indirectly (depends on {svc}): {_join(deps)}." if deps else "No dependent services were identified from traces."))
    blast = (f"Estimated blast radius: {1 + len(deps)} service(s)" + (f" ({svc} + {_join(deps)})" if deps else "") +
             ". Customer-facing impact is likely if any dependent path is user-facing.")
    prevent = "\n".join(f"{i}. {x.get('text')}" + (f" – {x.get('rationale')}" if x.get("rationale") else "") + (f" *(risk: {x.get('risk_level')})*" if x.get("risk_level") else "")
                        for i, x in enumerate(actions[:6], 1)) or "1. Investigate the dominant error and recent changes."
    conf = (f"Confidence reflects the risk score ({a.get('risk_score', 0):.0f}/100). "
            + ("Root cause analysis has been run automatically: " + (rca.get("explanation", "")[:400]) if rca else "") +
            " Assumptions: current traffic continues; no manual mitigation is applied. Every action above is propose-only and needs human approval.")
    return _sections([("summary", "Summary", summary), ("what_is_about_to_happen", "What Is About To Happen", happening),
                      ("why_we_believe_this", "Why We Believe This", why), ("who_is_affected", "Affected Users & Services", affected),
                      ("blast_radius", "Estimated Blast Radius", blast), ("prevention", "How To Prevent It", prevent),
                      ("confidence", "Confidence & Assumptions", conf)])


def executive_summary(ctx: dict) -> str:
    p = ctx.get("period") or {}
    incidents = ctx.get("incidents") or []
    al = ctx.get("alerts") or {}
    svcs = ctx.get("top_services") or []
    head = (f"Between {_ts(p.get('start'))} and {_ts(p.get('end'))} the agent monitored **{ctx.get('services_monitored', 0)}** services, "
            f"raised **{al.get('total', 0)}** pre-incident alert(s) and flagged **{ctx.get('anomalies', 0)}** anomalies.")
    overview = "\n".join(f"- **{s['service']}** – {s.get('errors', 0):,} errors ({_pct(s.get('error_rate'))}), status {s.get('status')}" for s in svcs[:6]) or "- No reliability concerns in this period."
    notable = "\n".join(f"- {i.get('title')} – {i.get('summary', '')}" for i in incidents[:6]) or "- No incidents recorded."
    acc = al.get("accuracy")
    forecasting = (f"Alert accuracy (true-positive rate) is **{acc * 100:.0f}%** over {al.get('labelled', 0)} labelled outcomes; {al.get('prevented', 0)} incident(s) were prevented." if acc is not None else "Not enough labelled outcomes yet to score alert accuracy - log outcomes on alerts to improve the forecasts.")
    risks = "\n".join(f"- **{r['service']}** – risk {r['risk_score']:.0f}/100 ({r.get('trend', 'steady')})" for r in (ctx.get("risks_ahead") or [])[:5]) or "- No services currently above the warning threshold."
    recs = "\n".join(f"- {x}" for x in (ctx.get("recommendations") or [])[:6]) or "- Continue monitoring; no action required."
    return _sections([("headline", "Headline", head), ("reliability_overview", "Reliability Overview", overview), ("notable_incidents", "Notable Incidents", notable),
                      ("forecasting_performance", "Forecasting Performance", forecasting), ("risks_ahead", "Risks Ahead", risks), ("recommendations", "Recommendations", recs)])


def generic(messages, ctx: dict) -> str:
    last = next((m.content for m in reversed(messages) if m.role == "user"), "")
    return f"(offline provider) {last[:400]}"


TASKS: dict[str, Callable[[dict], str]] = {
    "chat_answer": chat_answer, "log_explanation": log_explanation, "rca_reasoning": rca_reasoning,
    "incident_report": incident_report, "pre_mortem": pre_mortem, "executive_summary": executive_summary,
    "risk_explanation": risk_explanation, "anomaly_explanation": anomaly_explanation, "cluster_label": cluster_label,
    "narrate_deployment": narrate_deployment,
}
