#!/usr/bin/env python3
"""
Job bot: pulls entry-level defense / finance / investment roles in San Diego
straight from company job boards, filters and scores them, and updates a dashboard (docs/index.html).

Usage:
  python jobbot.py            normal run: fetch, filter, update the dashboard
  python jobbot.py --check    test every board in config.toml and report
  python jobbot.py --dry-run  run everything but don't save
"""

import argparse
import html
import json
import os
import re
import smtplib
import sys
import time
import tomllib
from datetime import date, datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urlparse

import requests

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.toml"
DATA_PATH = HERE / "docs" / "jobs.json"   # the dashboard reads this

UA = "Mozilla/5.0 (job-bot; personal use)"
TIMEOUT = 25
session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept": "application/json"})


# ---------------------------------------------------------------- helpers

def term_re(term):
    """Whole-word, case-insensitive match that also works for 'fp&a', 'a&d'."""
    return re.compile(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])")


def hits(text, terms):
    t = (text or "").lower()
    return [term for term in terms if term_re(term).search(t)]


def strip_html(s):
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def get_json(url, **kw):
    r = session.get(url, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r.json()


def post_json(url, body):
    r = session.post(url, json=body, timeout=TIMEOUT,
                     headers={"Content-Type": "application/json"})
    r.raise_for_status()
    return r.json()


def job(company, category, source, jid, title, location, url, description=""):
    return {
        "id": f"{source}:{company}:{jid}",
        "company": company,
        "category": category,
        "source": source,
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "url": url,
        "description": description or "",
    }


# ---------------------------------------------------------------- board detection

def detect_board(url):
    """Return (type, params) from a careers URL."""
    p = urlparse(url)
    host = p.netloc.lower()
    parts = [x for x in p.path.split("/") if x]

    if "greenhouse.io" in host and parts:
        return "greenhouse", {"slug": parts[0]}
    if host == "jobs.lever.co" and parts:
        return "lever", {"slug": parts[0]}
    if host == "jobs.ashbyhq.com" and parts:
        return "ashby", {"slug": parts[0]}
    if host.endswith("myworkdayjobs.com"):
        tenant = host.split(".")[0]
        parts = [x for x in parts if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", x)]
        if parts:
            return "workday", {"host": host, "tenant": tenant, "site": parts[0]}
    return "manual", {}


# ---------------------------------------------------------------- fetchers

def fetch_greenhouse(c, slug):
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                    params={"content": "true"})
    out = []
    for j in data.get("jobs", []):
        out.append(job(c["name"], c["category"], "greenhouse", j.get("id"),
                       j.get("title"), (j.get("location") or {}).get("name", ""),
                       j.get("absolute_url"), strip_html(j.get("content", ""))))
    return out


def fetch_lever(c, slug):
    data = get_json(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
    out = []
    for j in data:
        cats = j.get("categories") or {}
        locs = [cats.get("location") or ""] + list(cats.get("allLocations") or [])
        desc = " ".join([j.get("descriptionPlain") or ""] +
                        [strip_html(l.get("content", "")) for l in j.get("lists") or []] +
                        [j.get("additionalPlain") or ""])
        out.append(job(c["name"], c["category"], "lever", j.get("id"), j.get("text"),
                       " / ".join(dict.fromkeys(x for x in locs if x)),
                       j.get("hostedUrl"), desc))
    return out


def fetch_ashby(c, slug):
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    out = []
    for j in data.get("jobs", []):
        locs = [j.get("location") or ""] + [
            (s.get("location") if isinstance(s, dict) else s) or ""
            for s in j.get("secondaryLocations") or []]
        if j.get("isRemote"):
            locs.append("Remote")
        out.append(job(c["name"], c["category"], "ashby", j.get("id"), j.get("title"),
                       " / ".join(dict.fromkeys(x for x in locs if x)),
                       j.get("jobUrl"), j.get("descriptionPlain") or ""))
    return out


WORKDAY_SEARCHES = ["analyst", "finance", "associate", "investment", "pricing"]


def fetch_workday(c, host, tenant, site, title_ok):
    """Workday has no simple feed; use the same search API its site uses.
    Only pulls full descriptions for titles that pass the title filter."""
    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    seen, out = set(), []
    for term in WORKDAY_SEARCHES:
        offset = 0
        while offset < 400:
            data = post_json(f"{base}/jobs", {"appliedFacets": {}, "limit": 20,
                                              "offset": offset, "searchText": term})
            posts = data.get("jobPostings") or []
            for p in posts:
                path = p.get("externalPath")
                if not path or path in seen:
                    continue
                seen.add(path)
                title = p.get("title", "")
                if not title_ok(title):
                    continue
                loc, desc = p.get("locationsText", ""), ""
                try:
                    d = get_json(base + path).get("jobPostingInfo", {})
                    desc = strip_html(d.get("jobDescription", ""))
                    extra = [d.get("location", "")] + list(d.get("additionalLocations") or [])
                    loc = " / ".join(dict.fromkeys(x for x in extra if x)) or loc
                    time.sleep(0.3)
                except Exception:
                    pass
                out.append(job(c["name"], c["category"], "workday", path, title, loc,
                               f"https://{host}/{site}{path}", desc))
            offset += 20
            if len(posts) < 20 or offset >= (data.get("total") or 0):
                break
            time.sleep(0.3)
    return out


def fetch_usajobs(cfg):
    key = os.environ.get("USAJOBS_API_KEY")
    email = os.environ.get("USAJOBS_EMAIL")
    if not (key and email):
        return [], "skipped (no USAJOBS_API_KEY / USAJOBS_EMAIL set)"
    headers = {"Host": "data.usajobs.gov", "User-Agent": email, "Authorization-Key": key}
    out, seen = [], set()
    for kw in cfg.get("keywords", []):
        data = get_json("https://data.usajobs.gov/api/search", headers=headers, params={
            "Keyword": kw, "LocationName": "San Diego, California",
            "Radius": 30, "ResultsPerPage": 250})
        items = (data.get("SearchResult") or {}).get("SearchResultItems") or []
        for it in items:
            m = it.get("MatchedObjectDescriptor") or {}
            pid = m.get("PositionID")
            if pid in seen:
                continue
            seen.add(pid)
            details = (m.get("UserArea") or {}).get("Details") or {}
            try:
                low = int(details.get("LowGrade") or 0)
            except ValueError:
                low = 0
            if low and low > cfg.get("max_grade", 9):
                continue
            grade = f" (GS-{details.get('LowGrade')}" + (
                f"/{details.get('HighGrade')})" if details.get("HighGrade") else ")")
            out.append(job(m.get("OrganizationName", "Federal"), "federal", "usajobs", pid,
                           (m.get("PositionTitle") or "") + grade,
                           m.get("PositionLocationDisplay", ""), m.get("PositionURI"),
                           " ".join([details.get("JobSummary") or "",
                                     m.get("QualificationSummary") or ""])))
        time.sleep(0.5)
    return out, f"ok ({len(out)} postings)"


# ---------------------------------------------------------------- filtering + scoring

YEARS_RE = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*\+?\s*)?(?:years?|yrs)")

CLEARANCE_REQUIRED = re.compile(
    r"(active|current)\s+(dod\s+)?(secret|top secret|ts|ts/sci)\b"
    r"|(secret|top secret|ts/sci|ts)\s+(security\s+)?clearance\s+(is\s+)?required"
    r"|must\s+(possess|have|hold)\s+an?\s+(active\s+)?(dod\s+)?(secret|top secret|ts)",
    re.I)
CLEARANCE_OBTAIN = re.compile(r"(ability|able|eligib\w*)\s+to\s+obtain\b.{0,40}clearance"
                              r"|clearance.{0,40}\b(eligib\w*|obtain)", re.I)


def min_years_required(text):
    """Smallest 'N years ... experience' figure in the posting, or None."""
    t = (text or "").lower()
    found = []
    for m in YEARS_RE.finditer(t):
        window = t[m.end(): m.end() + 80]
        if "experience" in window or "experience" in t[max(0, m.start() - 40): m.start()]:
            found.append(int(m.group(1)))
    return min(found) if found else None


def location_ok(loc, f):
    l = (loc or "").lower()
    if any(x in l for x in f["locations"]):
        return True
    if f.get("include_la_oc") and any(x in l for x in f["la_oc_locations"]):
        return True
    if f.get("allow_remote") and "remote" in l and ("us" in l or "united states" in l
                                                     or "california" in l or l.strip() == "remote"):
        return True
    return False


def make_title_ok(f):
    def ok(title):
        return bool(hits(title, f["title_include"])) and not hits(title, f["title_exclude"])
    return ok


def evaluate(j, f, s):
    """Return (keep, score, notes)."""
    text = f"{j['title']} {j['description']}"
    notes = []

    yrs = min_years_required(j["description"])
    if yrs is not None and yrs > f.get("max_years_required", 2):
        return False, 0, [f"asks {yrs}+ yrs"]

    score = 0
    if hits(j["title"], s["finance_title"]):
        score += 3
    if j["category"] in ("defense", "federal"):
        score += 2
    if hits(text, s["entry_terms"]) or (yrs is not None and yrs <= 1):
        score += 2
        notes.append("entry level")
    bg = hits(j["description"], s["background_terms"])
    score += min(len(bg), 3)

    if CLEARANCE_REQUIRED.search(text):
        score -= 2
        notes.append("ACTIVE CLEARANCE REQUIRED")
    elif CLEARANCE_OBTAIN.search(text):
        notes.append("clearance sponsored/obtainable")
    if yrs:
        notes.append(f"{yrs}+ yrs exp")
    if "remote" in j["location"].lower():
        notes.append("remote")
    return True, score, notes


# ---------------------------------------------------------------- dashboard data

def load_store():
    if DATA_PATH.exists():
        try:
            return json.loads(DATA_PATH.read_text())
        except Exception:
            pass
    return {"jobs": []}


def update_store(store, matches, manual, report):
    """Merge today's matches into the saved list. Jobs that disappear from
    their board are kept but marked closed so your notes on them stay."""
    today = date.today().isoformat()
    old = {j["id"]: j for j in store.get("jobs", [])}
    live_ids = set()
    new = []
    for m in matches:
        live_ids.add(m["id"])
        rec = old.get(m["id"])
        fields = {k: m[k] for k in ("id", "company", "category", "title", "location",
                                     "url", "score", "notes")}
        if rec:
            rec.update(fields)
            rec["last_seen"], rec["open"] = today, True
        else:
            rec = {**fields, "first_seen": today, "last_seen": today, "open": True}
            old[m["id"]] = rec
            new.append(rec)
    ok_sources = {n for n, msg in report if msg.startswith("ok")}
    for rec in old.values():
        # only close a job if its board actually loaded today
        board_name = rec["company"] if rec.get("category") != "federal" else "USAJobs"
        if rec["id"] not in live_ids and board_name in ok_sources:
            rec["open"] = False
    store["jobs"] = sorted(old.values(), key=lambda r: (-r["score"], r["company"], r["title"]))
    store["updated"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    store["manual"] = [{"name": c["name"], "url": c["url"]} for c in manual]
    store["report"] = [{"board": n, "status": m} for n, m in report]
    return new


# ---------------------------------------------------------------- optional email

def send_email(new_jobs):
    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    if not (user and pw and new_jobs):
        return
    to = os.environ.get("TO_EMAIL") or user
    rows = "".join(f'<li><a href="{html.escape(j["url"] or "")}">{html.escape(j["title"])}</a>'
                   f' — {html.escape(j["company"])}</li>' for j in new_jobs)
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = f"Job bot: {len(new_jobs)} new", user, to
    msg.attach(MIMEText(f"<ul>{rows}</ul>", "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, pw)
        s.sendmail(user, [to], msg.as_string())


# ---------------------------------------------------------------- main

def collect(cfg, check=False):
    f = cfg["filters"]
    title_ok = make_title_ok(f)
    all_jobs, manual, report = [], [], []

    for c in cfg.get("company", []):
        kind, params = detect_board(c["url"])
        if c.get("region") == "la_oc" and not f.get("include_la_oc") and not check:
            report.append((c["name"], "skipped (LA/OC toggle off)"))
            continue
        if kind == "manual":
            manual.append(c)
            report.append((c["name"], "manual link"))
            continue
        try:
            if kind == "greenhouse":
                got = fetch_greenhouse(c, **params)
            elif kind == "lever":
                got = fetch_lever(c, **params)
            elif kind == "ashby":
                got = fetch_ashby(c, **params)
            else:
                got = fetch_workday(c, **params, title_ok=title_ok)
            all_jobs += got
            report.append((c["name"], f"ok ({kind}, {len(got)} postings pulled)"))
        except Exception as e:
            report.append((c["name"], f"FAILED ({kind}): {type(e).__name__}: {str(e)[:120]}"))
        time.sleep(0.5)

    if cfg.get("usajobs", {}).get("enabled"):
        try:
            got, msg = fetch_usajobs(cfg["usajobs"])
            all_jobs += got
            report.append(("USAJobs", msg))
        except Exception as e:
            report.append(("USAJobs", f"FAILED: {type(e).__name__}: {str(e)[:120]}"))
    return all_jobs, manual, report


def filter_and_score(all_jobs, cfg):
    f, s = cfg["filters"], cfg["scoring"]
    title_ok = make_title_ok(f)
    keep = []
    for j in all_jobs:
        if j["source"] != "usajobs" and not title_ok(j["title"]):
            continue
        if not location_ok(j["location"], f):
            continue
        ok, score, notes = evaluate(j, f, s)
        if ok:
            keep.append({**j, "score": score, "notes": notes})
    return keep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="test each board and exit")
    ap.add_argument("--dry-run", action="store_true", help="don't save anything")
    args = ap.parse_args()

    cfg = tomllib.loads(CONFIG_PATH.read_text())
    all_jobs, manual, report = collect(cfg, check=args.check)

    if args.check:
        width = max(len(n) for n, _ in report)
        for n, m in report:
            print(f"{n:<{width}}  {m}")
        return

    matches = filter_and_score(all_jobs, cfg)
    store = load_store()
    new = update_store(store, matches, manual, report)

    print(f"Pulled {len(all_jobs)} postings, {len(matches)} match, {len(new)} new.")
    for n, m in report:
        print(f"  {n}: {m}")
    if args.dry_run:
        for j in matches:
            print(f"  [{j['score']}] {j['company']} — {j['title']} — {j['location']}")
        return

    DATA_PATH.parent.mkdir(exist_ok=True)
    DATA_PATH.write_text(json.dumps(store, indent=1))
    send_email(new)


if __name__ == "__main__":
    sys.exit(main())
