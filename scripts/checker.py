"""
Sarkari Naukri Multi-Source Watcher — Production Grade
========================================================
Upgrade of the original single-purpose "SSC CHSL watcher" into a generic
engine that watches MULTIPLE government + trusted aggregator sites and
alerts on Telegram whenever a NEW job/form/exam item appears.

- Multi-source (see sources.py — add a source there, no code change needed)
- Generic new-item detection via link diffing (title+url hash) per source
- First run per source = "baseline" (silently remembers current items,
  does NOT spam you with everything that already existed)
- Persistent state via state.json (committed back to repo by the workflow)
- Error cooldown: alert on first total-failure, then silence repeats for 6h
- Daily digest at 9 AM IST
- Rich Telegram messages, batched per source, rate-limited sends
"""

import os
import re
import sys
import json
import time
import hashlib
import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

from sources import SOURCES, JOB_KEYWORDS, EXCLUDE_GENERIC, classify_eligibility

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
CHAT_ID     = os.environ.get("CHAT_ID", "")
STATE_FILE  = Path("state.json")
ERROR_COOLDOWN_HOURS = 6
IST = timezone(timedelta(hours=5, minutes=30))

MAX_SEEN_PER_SOURCE = 600      # cap so state.json doesn't grow forever
SEEN_MAX_AGE_DAYS   = 120      # prune items older than this
MAX_ITEMS_PER_ALERT_MSG = 10   # avoid giant Telegram messages

# Heartbeat pings — "bot zinda hai" alerts through the day, independent of
# whether any new job was found. 9 AM slot also carries the full daily digest.
HEARTBEAT_HOURS_IST = [9, 13, 17, 21]   # 4x/day
DIGEST_HOUR_IST     = 9

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── State Schema ─────────────────────────────────────────────────────────────
@dataclass
class State:
    # Per-source tracking: key -> {"seen": {hash: {"title","url","first_seen"}},
    #                               "last_page_hash": str, "baseline_done": bool}
    sources: dict = field(default_factory=dict)

    # Error tracking (global — based on ALL sources failing at once)
    consecutive_errors:   int              = 0
    last_error_alert_ts:  Optional[str]    = None

    # Alert tracking
    total_checks:         int              = 0
    total_alerts:         int              = 0
    last_new_job_ts:      Optional[str]    = None   # last time a real new-job alert was sent

    # Heartbeat / daily digest — key format "YYYY-MM-DD-HH" (IST), prevents
    # re-sending within the same hour slot across multiple 10-min cron runs
    last_heartbeat_key:   Optional[str]    = None

    def save(self):
        STATE_FILE.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))
        log.info(f"State saved → {STATE_FILE}")

    @classmethod
    def load(cls) -> "State":
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
                return cls(**valid)
            except Exception as e:
                log.warning(f"State load failed ({e}), starting fresh")
        return cls()

    def src(self, key: str) -> dict:
        if key not in self.sources:
            self.sources[key] = {"seen": {}, "last_page_hash": None, "baseline_done": False}
        return self.sources[key]

# ── Telegram ─────────────────────────────────────────────────────────────────
def tg_send(text: str, disable_preview: bool = True) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        log.error("Telegram credentials missing — set BOT_TOKEN and CHAT_ID secrets")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": disable_preview,
            },
            timeout=15,
        )
        r.raise_for_status()
        log.info("Telegram ✓ message sent")
        return True
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False

# ── HTTP Fetch ────────────────────────────────────────────────────────────────
def fetch(url: str) -> Optional[str]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        r.raise_for_status()
        return r.text
    except requests.exceptions.HTTPError as e:
        log.warning(f"HTTP {e.response.status_code} for {url}")
    except requests.exceptions.Timeout:
        log.warning(f"Timeout fetching {url}")
    except Exception as e:
        log.warning(f"Fetch error for {url}: {e}")
    return None

# ── Content Hash (page-level fingerprint, skip re-parsing unchanged pages) ────
def content_hash(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "meta", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())
    return hashlib.sha256(text.encode()).hexdigest()[:16]

# ── Generic item extraction (works across differently-structured sites) ──────
def is_job_like(text: str) -> bool:
    t = text.lower().strip()
    if len(t) < 12:
        return False
    if any(x in t for x in EXCLUDE_GENERIC) and not any(k in t for k in JOB_KEYWORDS):
        return False
    has_year = bool(re.search(r"20(2[5-9]|3[0-9])", t))
    has_kw = any(k in t for k in JOB_KEYWORDS)
    return has_year or has_kw

def extract_items(html: str, base_url: str) -> list[dict]:
    """Pull candidate job/form/notice links out of a page's HTML."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    items = []
    seen_hrefs = set()
    for a in soup.find_all("a", href=True):
        text = " ".join(a.get_text(separator=" ").split())
        href = a["href"].strip()
        if not text or not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        if not is_job_like(text):
            continue
        full_url = urljoin(base_url, href)
        dedup_key = (text.lower(), full_url)
        if dedup_key in seen_hrefs:
            continue
        seen_hrefs.add(dedup_key)

        elig = classify_eligibility(text)
        if elig == "exclude":
            continue  # graduate-only post — user is 12th pass, skip it

        items.append({"title": text[:200], "url": full_url, "eligibility": elig})

    return items

def item_hash(item: dict) -> str:
    raw = (item["title"].lower().strip() + "|" + item["url"].lower().strip())
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

# ── Now() helpers ──────────────────────────────────────────────────────────────
def now_ist() -> datetime:
    return datetime.now(IST)

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def ts() -> str:
    return now_utc().isoformat()

def ist_str(dt: Optional[datetime] = None) -> str:
    d = dt or now_ist()
    return d.strftime("%d %b %Y, %I:%M %p IST")

# ── Alert Messages ────────────────────────────────────────────────────────────
def msg_new_items(source_name: str, trust: str, kind: str, items: list[dict]) -> str:
    trust_tag = "🏛️ Official" if trust == "official" else "🌐 Aggregator (verify on official site)"
    shown = items[:MAX_ITEMS_PER_ALERT_MSG]

    def bullet(i):
        tag = "❓ <i>eligibility check karo</i>" if i.get("eligibility") == "unknown" else "✅ 12th pass"
        return f"• <a href='{i['url']}'>{i['title']}</a>\n   {tag}"

    bullets = "\n".join(bullet(i) for i in shown)
    extra = ""
    if len(items) > MAX_ITEMS_PER_ALERT_MSG:
        extra = f"\n…aur {len(items) - MAX_ITEMS_PER_ALERT_MSG} items. Site pe check karo."

    if kind == "apply_highlight":
        header = "🚨🚨🚨 <b>NAYA FORM / UPDATE — SSC APPLY PAGE!</b> 🚨🚨🚨"
    else:
        header = "🆕 <b>Naya Job/Form Update Mila!</b>"

    return (
        f"{header}\n\n"
        f"📌 <b>Source:</b> {source_name} ({trust_tag})\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"{bullets}{extra}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ Apply/details se pehle hamesha official website pe cross-check karo.\n\n"
        f"🕐 Detected: {ist_str()}"
    )

def msg_daily_digest(state: State) -> str:
    lines = [f"🔍 Total checks: {state.total_checks}", f"🔔 Total alerts sent: {state.total_alerts}"]
    for s in SOURCES:
        st = state.sources.get(s["key"], {})
        count = len(st.get("seen", {}))
        lines.append(f"   • {s['name']}: {count} items tracked")
    err_line = ""
    if state.consecutive_errors > 0:
        err_line = f"\n⚠️ Consecutive total-failure checks: {state.consecutive_errors}"
    body = "\n".join(lines)
    return (
        "📊 <b>Sarkari Naukri Watcher — Daily Report</b>\n\n"
        f"{body}{err_line}\n\n"
        f"🕐 Report time: {ist_str()}"
    )

def msg_heartbeat(state: State) -> str:
    last_job = "abhi tak koi nahi" if not state.last_new_job_ts else \
        datetime.fromisoformat(state.last_new_job_ts).astimezone(IST).strftime("%d %b, %I:%M %p")
    return (
        "✅ <b>Bot Active Hai</b> — Sarkari Naukri Watcher chal raha hai 🟢\n\n"
        f"🔍 Total checks so far: {state.total_checks}\n"
        f"📌 Last naya job/form mila: {last_job}\n\n"
        f"🕐 {ist_str()}"
    )

def msg_error(error_msg: str, count: int) -> str:
    return (
        "⚠️ <b>Watcher — Fetch Error</b>\n\n"
        f"Saare sources temporarily unreachable.\n"
        f"Error: <code>{error_msg[:200]}</code>\n"
        f"Consecutive failures: {count}\n\n"
        "Watcher chal raha hai — agle check pe retry hoga.\n\n"
        f"🕐 {ist_str()}"
    )

def msg_error_resolved() -> str:
    return (
        "✅ <b>Watcher — Connection Restored</b>\n\n"
        "Sources wapas accessible hain. Monitoring normal hai.\n\n"
        f"🕐 {ist_str()}"
    )

# ── Per-source processing ──────────────────────────────────────────────────────
def prune_seen(seen: dict) -> dict:
    """Drop entries older than SEEN_MAX_AGE_DAYS; if still too big, drop oldest."""
    cutoff = now_utc() - timedelta(days=SEEN_MAX_AGE_DAYS)
    kept = {}
    for h, v in seen.items():
        try:
            fs = datetime.fromisoformat(v["first_seen"])
        except Exception:
            fs = now_utc()
        if fs >= cutoff:
            kept[h] = v
    if len(kept) > MAX_SEEN_PER_SOURCE:
        # keep the newest MAX_SEEN_PER_SOURCE
        ordered = sorted(kept.items(), key=lambda kv: kv[1].get("first_seen", ""), reverse=True)
        kept = dict(ordered[:MAX_SEEN_PER_SOURCE])
    return kept

def process_source(state: State, source: dict) -> Optional[str]:
    """Returns a Telegram message string if there's something new to alert, else None."""
    key, name, url, kind, trust = source["key"], source["name"], source["url"], source["kind"], source["trust"]
    st = state.src(key)

    html = fetch(url)
    if html is None:
        log.warning(f"  [{name}] fetch failed — skipped this run")
        return None

    new_hash = content_hash(html)
    if st["last_page_hash"] == new_hash:
        log.info(f"  [{name}] page unchanged — skipping deep parse")
        return None
    st["last_page_hash"] = new_hash

    items = extract_items(html, url)
    log.info(f"  [{name}] extracted {len(items)} candidate items")

    if not st["baseline_done"]:
        # First run for this source: remember everything, alert nothing
        for it in items:
            h = item_hash(it)
            st["seen"][h] = {
                "title": it["title"], "url": it["url"], "first_seen": ts(),
                "eligibility": it.get("eligibility", "unknown"),
            }
        st["baseline_done"] = True
        log.info(f"  [{name}] baseline established with {len(items)} items — no alert sent")
        return None

    new_items = []
    for it in items:
        h = item_hash(it)
        if h not in st["seen"]:
            st["seen"][h] = {
                "title": it["title"], "url": it["url"], "first_seen": ts(),
                "eligibility": it.get("eligibility", "unknown"),
            }
            new_items.append(it)

    st["seen"] = prune_seen(st["seen"])

    if new_items:
        log.info(f"  [{name}] 🎉 {len(new_items)} NEW item(s) found")
        return msg_new_items(name, trust, kind, new_items)

    log.info(f"  [{name}] no new items")
    return None

# ── Main Logic ────────────────────────────────────────────────────────────────
def run():
    state = State.load()
    state.total_checks += 1
    log.info(f"Check #{state.total_checks} | consecutive_errors={state.consecutive_errors}")
    today_ist = now_ist().strftime("%Y-%m-%d")

    # ── 1. Heartbeat / Daily Digest (4x/day: 9 AM, 1 PM, 5 PM, 9 PM IST) ──────
    current_hour = now_ist().hour
    if current_hour in HEARTBEAT_HOURS_IST:
        heartbeat_key = f"{today_ist}-{current_hour}"
        if state.last_heartbeat_key != heartbeat_key:
            if current_hour == DIGEST_HOUR_IST:
                log.info("Sending daily digest (heartbeat slot)...")
                tg_send(msg_daily_digest(state))
            else:
                log.info("Sending heartbeat ping...")
                tg_send(msg_heartbeat(state))
            state.last_heartbeat_key = heartbeat_key

    # ── 2. Process each source, tracking global failure state ────────────────
    fail_count = 0
    alert_msgs = []
    for source in SOURCES:
        try:
            msg = process_source(state, source)
            if msg:
                alert_msgs.append(msg)
        except Exception as e:
            log.error(f"  [{source['name']}] unexpected error: {e}")
            fail_count += 1

    all_failed = fail_count == len(SOURCES)

    # ── 3. Global error handling (only if EVERY source errored) ──────────────
    if all_failed:
        state.consecutive_errors += 1
        should_alert = state.last_error_alert_ts is None
        if not should_alert:
            last = datetime.fromisoformat(state.last_error_alert_ts)
            should_alert = (now_utc() - last.replace(tzinfo=timezone.utc)).total_seconds() > ERROR_COOLDOWN_HOURS * 3600
        if should_alert:
            tg_send(msg_error("All sources unreachable", state.consecutive_errors))
            state.last_error_alert_ts = ts()
            state.total_alerts += 1
        state.save()
        sys.exit(0)

    if state.consecutive_errors > 0:
        log.info("Errors resolved — sources are back")
        tg_send(msg_error_resolved())
        state.consecutive_errors = 0
        state.last_error_alert_ts = None
        state.total_alerts += 1

    # ── 4. Send all alert messages (rate-limited) ─────────────────────────────
    for i, msg in enumerate(alert_msgs):
        tg_send(msg, disable_preview=False)
        state.total_alerts += 1
        state.last_new_job_ts = ts()
        if i < len(alert_msgs) - 1:
            time.sleep(1.5)  # be gentle on Telegram API

    state.save()
    log.info(f"Run complete | checks={state.total_checks} | alerts={state.total_alerts}")


if __name__ == "__main__":
    run()
