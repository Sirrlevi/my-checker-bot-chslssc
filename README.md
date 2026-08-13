# 🔔 Sarkari Naukri Multi-Source Watcher

**Upgrade of the original SSC-CHSL-only bot.** Ab ye ek generic engine hai jo
kayi government + trusted aggregator sites check karta hai aur **kisi bhi
naye job/form/notification** ke aate hi Telegram pe alert bhejta hai — sirf
CHSL nahi, balki jo bhi naya SSC/UPSC/IBPS exam ho ya kisi bhi department ki
recruitment ho.

GitHub ke FREE servers pe 24/7 chalta hai. Koi cost nahi.

---

## Kya badla hai (v1 → v2)

| | v1 (old) | v2 (ye upgrade) |
|---|---|---|
| Scope | Sirf SSC CHSL | Koi bhi naya job/form/exam, kisi bhi source pe |
| Sources | 3 SSC pages | 7 sources (SSC, UPSC, IBPS + 3 aggregators) — `scripts/sources.py` me aur add kar sakte ho |
| Detection | CHSL-specific keyword logic | Generic new-link detector (per-site keyword tuning ki zaroorat nahi) |
| State | `chsl_active: true/false` | Per-source "seen items" list, dedup by title+link |
| First run | — | "Baseline" run — jo already active hai wo silently record hota hai, spam nahi aata |

**Important:** State file format badla hai, isliye pehla run ke baad naye
system ka baseline set hoga — us pehle run me koi alert nahi aayega (jo bhi
tab active hoga wo record ho jayega). Uske baad se sirf **naye** items pe
alert milega.

---

## Sources (`scripts/sources.py`)

**Official (trusted, direct sarkari websites):**
- SSC — Apply Page (dramatic 🚨 alert, jaisa pehle CHSL ke liye tha)
- SSC — Latest Notices
- UPSC — What's New
- IBPS — Notices

**Aggregators (private portals jo sab departments/states ka data ek jagah collect karte hain — Railway/RRB, Banking, State PSC bhi cover ho jaata hai):**
- SarkariResult.com — Latest Jobs
- SarkariJobs.com
- SarkariExam.com

Naya source add karna ho, bas `scripts/sources.py` ki `SOURCES` list me ek
naya dict daal do:

```python
{
    "key": "unique_id",
    "name": "Display Name",
    "url": "https://example.gov.in/notices",
    "kind": "listing",       # ya "apply_highlight" for dramatic alert
    "trust": "official",     # ya "aggregator"
},
```

Baaki sab (dedup, state tracking, alerting) generic engine khud sambhal
lega — koi extra code likhne ki zaroorat nahi.

## Eligibility Filter — sirf 12th pass (10+2) wali jobs

`scripts/sources.py` me `ELIGIBLE_12TH_KEYWORDS` aur
`EXCLUDE_GRADUATE_KEYWORDS` ke through automatic filtering hoti hai:

- ✅ **CHSL, MTS, Group D, Constable, Postman, GDS** jaisi
  well-known 12th-pass exams → normal alert milega
- ❌ **CGL, Bank PO, JE, Civil Services, "graduate"/"B.Tech" mention wali**
  postings → automatically **skip** ho jaati hain, alert hi nahi aata
- ❓ Jin postings ka level title se pata nahi chalta (jaise generic
  "XYZ Recruitment 2026") → alert aayega but **"eligibility check karo"**
  tag ke saath

## Gender Filter — sirf male candidates ke liye open jobs

`EXCLUDE_FEMALE_ONLY_KEYWORDS` ke through female-only postings bhi skip ho
jaati hain — jaise **Anganwadi Worker/Helper, ASHA Worker, Mahila
Constable, "Women Only"/"Ladies Only"** wali. Postings jo dono ke liye open
hain (jaise "Male/Female" mention wali) ya jo gender specify nahi karti, wo
normal alert me aati hain.

## Location Filter — Haryana (Rewari/Bawal) preferred

`OTHER_STATE_EXCLUDE_KEYWORDS` ke through **doosre states ki state-specific**
recruitment (state Police, state PSC — jaise UPPSC, BPSC, RPSC, MPPSC etc.)
skip ho jaati hai, kyunki wo usually sirf us state ke domicile candidates
ke liye hoti hai.

**Central government jobs hamesha dikhengi, chahe posting location kahi bhi
ho** — SSC, UPSC, IBPS, Railway/RRB, CAPF, Assam Rifles jaisi central bharti
me koi state-domicile restriction nahi hoti, isliye ye filter unhe touch
nahi karta. Sirf genuinely state-restricted postings (jaise "UP Police",
"Bihar Police", "RSMSSB") exclude hoti hain. Haryana/HSSC wali postings
hamesha dikhengi.

**Imaandaari se limitation:** Job listing ka title *hamesha* eligibility
mention nahi karta (wo notification PDF ke andar hoti hai), isliye ye
keyword-matching hai, 100% guarantee nahi. False negative (koi eligible job
miss ho jaye) se bachne ke liye system "unknown" cases ko bhi bhej deta hai
— bas unme khud eligibility confirm karni padegi. Agar koi specific exam
galat classify ho raha lage, `sources.py` me uske keyword add/adjust kar
sakte ho.

### ⚠️ Limitation (important, imaandaari se bata rahe hain)
Kuch government sites — jaise ki alag-alag RRB zones, kayi State PSC portals
— ya to JavaScript se content load karte hain ya bot-blocking/CAPTCHA use
karte hain. Plain Python requests+BeautifulSoup se ye reliably nahi padhe ja
sakte, isliye unhe direct source nahi banaya. Unki coverage teeno aggregator
sites se mil jaati hai (wo khud in sab departments ka data collect karke
list karte hain), lekin agar koi specific official site chahiye jo abhi list
me nahi hai, mujhe bata do — check karke dekh sakta hoon ki wo scrape-able
hai ya nahi.

---

## Setup (same as before)

### Step 1 — Telegram Bot banao
1. Telegram mein **@BotFather** open karo
2. `/newbot` bhejo → naam do
3. **Token** copy karo (ye `BOT_TOKEN` secret banega)

### Step 2 — Chat ID lo
1. Apne bot ko Telegram pe message karo (`/start`)
2. `https://api.telegram.org/bot<TOKEN>/getUpdates` open karo browser me
3. `"chat":{"id": ...}` wala number copy karo (ye `CHAT_ID` secret banega)

### Step 3 — GitHub repo secrets set karo
Repo → Settings → Secrets and variables → Actions → New repository secret:
- `BOT_TOKEN` = bot ka token
- `CHAT_ID` = tumhara chat id

### Step 4 — Push kar do
Actions tab me jaake workflow ko enable karo (agar disabled dikhe) ya
"Run workflow" se manually ek baar chala ke test kar lo.

---

## Kitne baar / kis frequency pe alerts aate hain

- **Job checks:** har 10 minute (naya job milte hi turant alert)
- **Heartbeat pings ("Bot Active hai" confirmation):** din me **4 baar** — 9 AM, 1 PM, 5 PM, 9 PM (IST). 9 AM wala poora daily digest hota hai (stats ke saath), baaki 3 chhote heartbeat pings hote hain.
- **Deploy notification:** jab bhi tum `main` branch pe naya commit push karte ho (state.json ke auto-commits ignore ho jaate hain — sirf tumhare khud ke code-change commits pe fire hota hai), turant ek "🚀 Bot Updated" message aata hai commit details ke saath.

Heartbeat hours `scripts/checker.py` me `HEARTBEAT_HOURS_IST` list se change kar sakte ho — jaise `[9, 12, 15, 18, 21]` kar do to 5 baar/din ho jayega.

## Architecture

```
GitHub Actions (cron: */10 * * * *)
         │
         ▼
   checker.py runs
         │
         ├─► For each source in sources.py:
         │     fetch page → hash check (skip if unchanged)
         │     → extract job-like links → diff vs state.json "seen"
         │     → new items? queue an alert
         │
         ▼
   state.json (persisted in repo, per-source seen items)
         │
         ▼
   Send queued Telegram alerts (rate-limited)
   + Daily digest at 9 AM IST
   + Error alert if ALL sources fail (6h cooldown)
```
