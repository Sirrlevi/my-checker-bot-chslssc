"""
SSC CHSL Form Watcher — Production Grade
=========================================
- Multi-source detection (apply page + notices + exam calendar)
- Persistent state via state.json (committed back to repo)
- Deduplication: alert only on status CHANGE (inactive → active)
- Error cooldown: alert on first error, silence repeats for 6 hours
- Daily digest at 9 AM IST
- Rich Telegram messages
"""

import os
import re
import sys
import json
import hashlib
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID     = os.environ.get("TELEGRAM_CHAT_ID", "")
STATE_FILE  = Path("state.json")
ERROR_COOLDOWN_HOURS = 6
IST = timezone(timedelta(hours=5, minutes=30))

SOURCES = [
    {
        "name": "SSC Apply Page",
        "url":  "https://ssc.gov.in/home/apply",
        "type": "apply",
    },
    {
        "name": "SSC Latest Notices",
        "url":  "https://ssc.gov.in/home/latestNotice",
        "type": "notice",
    },
    {
        "name": "SSC Exam Calendar",
        "url":  "https://ssc.gov.in/home/examCalendar",
        "type": "calendar",
    },
]

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
    # CHSL status
    chsl_active:          bool             = False
    chsl_first_seen:      Optional[str]    = None   # ISO timestamp

    # Fingerprints to detect real page changes (avoid re-alerting same content)
    last_page_hash:       dict             = field(default_factory=dict)  # url → hash

    # Error tracking
    consecutive_errors:   int              = 0
    last_error_alert_ts:  Optional[str]    = None
    last_error_msg:       Optional[str]    = None

    # Alert tracking
    last_alert_ts:        Optional[str]    = None
    total_checks:         int              = 0
    total_alerts:         int              = 0

    # Daily digest
    last_digest_date:     Optional[str]    = None   # "YYYY-MM-DD" in IST

    def save(self):
        STATE_FILE.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))
        log.info(f"State saved → {STATE_FILE}")

    @classmethod
    def load(cls) -> "State":
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                # Forward-compat: ignore unknown keys
                valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
                return cls(**valid)
            except Exception as e:
                log.warning(f"State load failed ({e}), starting fresh")
        return cls()

# ── Telegram ─────────────────────────────────────────────────────────────────
def tg_send(text: str, disable_preview: bool = True) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        log.error("Telegram credentials missing — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID secrets")
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

# ── Content Hash (fingerprint) ────────────────────────────────────────────────
def content_hash(html: str) -> str:
    """Hash only the meaningful text content, ignoring dynamic tokens/timestamps."""
    soup = BeautifulSoup(html, "html.parser")
    # Remove script/style noise
    for tag in soup(["script", "style", "meta", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())
    return hashlib.sha256(text.encode()).hexdigest()[:16]

# ── Detection: Apply Page ──────────────────────────────────────────────────────
def check_apply_page(html: str) -> tuple[bool, str]:
    """
    Returns (is_active, reason_string).
    Strategy: find the CHSL tab/card, then verify it has an active Apply link
    (not just "Application is not active" text).
    """
    soup = BeautifulSoup(html, "html.parser")
    page_text_lower = soup.get_text(separator=" ").lower()

    # 1. Confirm CHSL section exists on this page
    if "chsl" not in page_text_lower:
        return False, "CHSL section not found on apply page"

    # 2. Collect all text+links in the CHSL vicinity
    #    Look for elements whose text contains "chsl"
    chsl_contexts = []
    for el in soup.find_all(True):
        el_text = el.get_text(separator=" ").lower()
        if "chsl" in el_text and len(el_text) < 2000:  # skip huge containers
            chsl_contexts.append(el)

    # 3. In those contexts, find active apply links
    active_apply_urls = []
    for ctx in chsl_contexts:
        for a in ctx.find_all("a", href=True):
            href = a["href"]
            link_text = a.get_text(separator=" ").lower()
            # An active apply link: href points to apply/registration, not a PDF/notice
            if any(kw in link_text for kw in ["apply", "register", "submit application"]):
                if not href.lower().endswith(".pdf"):
                    active_apply_urls.append(href)

    # 4. Check for explicit "not active" message in CHSL context
    not_active_phrases = [
        "application is not active",
        "not active",
        "applications are closed",
        "registration closed",
        "link will be activated",
    ]
    for ctx in chsl_contexts:
        ctx_text = ctx.get_text(separator=" ").lower()
        for phrase in not_active_phrases:
            if phrase in ctx_text:
                return False, f"Explicit inactive message: '{phrase}'"

    # 5. Positive verdict
    if active_apply_urls:
        return True, f"Active apply link found: {active_apply_urls[0]}"

    # 6. Secondary signal: apply page has CHSL + general positive keywords
    #    (some SSC pages load apply links via JS; this catches edge cases)
    positive_kw = ["apply now", "apply online", "click here to apply", "start registration"]
    for kw in positive_kw:
        if kw in page_text_lower:
            return True, f"Keyword signal: '{kw}'"

    return False, "CHSL present but no active apply link found"


# ── Detection: Notices Page ────────────────────────────────────────────────────
def check_notices_page(html: str) -> tuple[bool, str]:
    """Look for CHSL application/notification PDF or announcement in notices."""
    soup = BeautifulSoup(html, "html.parser")

    chsl_notice_keywords = [
        "chsl.*(?:application|apply|notification|advertisement|recruitment)",
        "combined higher secondary.*(?:application|notification)",
    ]

    for a in soup.find_all("a", href=True):
        text = a.get_text(separator=" ")
        href = a["href"]
        for pattern in chsl_notice_keywords:
            if re.search(pattern, text, re.IGNORECASE) or re.search(pattern, href, re.IGNORECASE):
                # Filter out result/admit card notices — we want application notices
                combined = (text + " " + href).lower()
                if any(x in combined for x in ["result", "admit card", "tier-2", "tier 2", "tier ii", "answer key", "cut off"]):
                    log.info(f"Skipping non-application CHSL notice: {text[:80]}")
                    continue
                return True, f"Notice found: {text[:120]} → {href}"

    return False, "No new CHSL application notice found"


# ── Detection: Exam Calendar ──────────────────────────────────────────────────
def check_calendar_page(html: str) -> tuple[bool, str]:
    """Check if CHSL is listed with upcoming application dates in the exam calendar."""
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.find_all("tr")
    now = datetime.now(IST)

    for row in rows:
        row_text = row.get_text(separator=" ")
        if "chsl" not in row_text.lower():
            continue
        # Look for date patterns: DD/MM/YYYY or DD-MM-YYYY or Month YYYY
        date_patterns = [
            r"\d{2}[/-]\d{2}[/-]\d{4}",
            r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}",
        ]
        dates_found = []
        for p in date_patterns:
            dates_found += re.findall(p, row_text, re.IGNORECASE)

        if dates_found:
            log.info(f"Calendar CHSL row: {row_text[:150].strip()}")
            return False, f"Calendar shows CHSL dates: {dates_found} (not yet active)"

    return False, "No CHSL calendar entry found"


# ── Now() helper ──────────────────────────────────────────────────────────────
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
def msg_form_found(reasons: list[str]) -> str:
    bullets = "\n".join(f"  • {r}" for r in reasons)
    return (
        "🚨🚨🚨 <b>SSC CHSL FORM AA GAYA!</b> 🚨🚨🚨\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ <b>Application ACTIVE ho gaya hai!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔍 <b>Evidence:</b>\n{bullets}\n\n"
        "👉 <b>ABHI APPLY KARO:</b>\n"
        "https://ssc.gov.in/home/apply\n\n"
        "⚠️ <b>Der mat karo!</b> Last date miss mat karna!\n"
        "OTR ID ready rakho aur documents pehle se scan karo.\n\n"
        f"🕐 Detected: {ist_str()}"
    )

def msg_daily_digest(state: State, checks_today: int) -> str:
    status = "✅ ACTIVE" if state.chsl_active else "⏳ Abhi Active Nahi"
    since = ""
    if state.chsl_first_seen:
        since = f"\n   Active since: {ist_str(datetime.fromisoformat(state.chsl_first_seen))}"
    err_line = ""
    if state.consecutive_errors > 0:
        err_line = f"\n⚠️ Consecutive errors: {state.consecutive_errors}"
    return (
        "📊 <b>SSC CHSL Watcher — Daily Report</b>\n\n"
        f"📋 CHSL Status: <b>{status}</b>{since}\n"
        f"🔍 Total checks: {state.total_checks}\n"
        f"🔔 Alerts sent: {state.total_alerts}"
        f"{err_line}\n\n"
        f"🌐 <a href='https://ssc.gov.in/home/apply'>SSC Apply Page</a>\n"
        f"🕐 Report time: {ist_str()}"
    )

def msg_error(error_msg: str, count: int) -> str:
    return (
        "⚠️ <b>SSC Watcher — Fetch Error</b>\n\n"
        f"SSC website temporarily unreachable.\n"
        f"Error: <code>{error_msg[:200]}</code>\n"
        f"Consecutive failures: {count}\n\n"
        "Watcher chal raha hai — agle check pe retry hoga.\n"
        "Jab site wapas aayegi, automatically resume ho jaayega.\n\n"
        f"🕐 {ist_str()}"
    )

def msg_error_resolved() -> str:
    return (
        "✅ <b>SSC Watcher — Connection Restored</b>\n\n"
        "SSC website wapas accessible hai.\n"
        "Monitoring normal hai.\n\n"
        f"🕐 {ist_str()}"
    )


# ── Main Logic ────────────────────────────────────────────────────────────────
def run():
    state = State.load()
    state.total_checks += 1
    log.info(f"Check #{state.total_checks} | Current CHSL active={state.chsl_active} | "
             f"Consecutive errors={state.consecutive_errors}")

    # ── 1. Daily Digest (9 AM IST) ────────────────────────────────────────────
    today_ist = now_ist().strftime("%Y-%m-%d")
    ist_hour  = now_ist().hour
    if state.last_digest_date != today_ist and ist_hour >= 9:
        log.info("Sending daily digest...")
        tg_send(msg_daily_digest(state, state.total_checks))
        state.last_digest_date = today_ist

    # ── 2. Fetch all sources ─────────────────────────────────────────────────
    fetch_results = {}
    all_failed = True
    for source in SOURCES:
        html = fetch(source["url"])
        fetch_results[source["name"]] = {"html": html, "type": source["type"], "url": source["url"]}
        if html is not None:
            all_failed = False

    # ── 3. Error handling ────────────────────────────────────────────────────
    if all_failed:
        state.consecutive_errors += 1
        err_msg = "All SSC sources unreachable"
        log.warning(f"All fetches failed (consecutive={state.consecutive_errors})")

        # Alert only: first failure OR every 6 hours
        should_alert = False
        if state.last_error_alert_ts is None:
            should_alert = True
        else:
            last = datetime.fromisoformat(state.last_error_alert_ts)
            if (now_utc() - last.replace(tzinfo=timezone.utc)).total_seconds() > ERROR_COOLDOWN_HOURS * 3600:
                should_alert = True

        if should_alert:
            tg_send(msg_error(err_msg, state.consecutive_errors))
            state.last_error_alert_ts = ts()
            state.total_alerts += 1

        state.save()
        sys.exit(0)

    # ── 4. Errors resolved? ──────────────────────────────────────────────────
    if state.consecutive_errors > 0:
        log.info("Errors resolved — site is back")
        tg_send(msg_error_resolved())
        state.consecutive_errors = 0
        state.last_error_alert_ts = None
        state.total_alerts += 1

    # ── 5. Run detectors ─────────────────────────────────────────────────────
    detection_signals: list[str] = []

    for name, result in fetch_results.items():
        html = result["html"]
        if html is None:
            log.warning(f"  [{name}] skipped (fetch failed)")
            continue

        src_type = result["type"]
        url      = result["url"]

        # Fingerprint — skip if page unchanged vs last run
        new_hash = content_hash(html)
        old_hash = state.last_page_hash.get(url)
        if old_hash and old_hash == new_hash:
            log.info(f"  [{name}] page unchanged (hash={new_hash}) — skipping deep parse")
            continue
        state.last_page_hash[url] = new_hash
        log.info(f"  [{name}] page changed/new (hash {old_hash} → {new_hash})")

        if src_type == "apply":
            active, reason = check_apply_page(html)
        elif src_type == "notice":
            active, reason = check_notices_page(html)
        elif src_type == "calendar":
            active, reason = check_calendar_page(html)
        else:
            active, reason = False, "Unknown source type"

        log.info(f"  [{name}] active={active} | {reason}")
        if active:
            detection_signals.append(f"[{name}] {reason}")

    # ── 6. Decision & Alert ──────────────────────────────────────────────────
    newly_active = bool(detection_signals) and not state.chsl_active

    if newly_active:
        log.info(f"🎉 STATUS CHANGE: inactive → ACTIVE | signals={detection_signals}")
        state.chsl_active = True
        state.chsl_first_seen = ts()
        state.last_alert_ts = ts()
        state.total_alerts += 1
        tg_send(msg_form_found(detection_signals), disable_preview=False)

    elif detection_signals and state.chsl_active:
        log.info("Form still active — already alerted, no duplicate sent")

    elif not detection_signals and state.chsl_active:
        # Form was active but now signals gone — could be SSC pulled it temporarily
        log.warning("Previously active form no longer detected — NOT clearing state (may be transient)")
        # We keep chsl_active=True to avoid flip-flop alerts
        # Only manual reset clears this

    else:
        log.info("Form not active — all clear, continuing watch")

    state.save()
    log.info(f"Run complete | checks={state.total_checks} | alerts={state.total_alerts}")


if __name__ == "__main__":
    run()
