# 🔔 SSC CHSL Form Watcher — Production Grade

**Apna phone band karo, so jao — form aate hi Telegram pe turant message aayega.**

GitHub ke FREE servers pe 24/7 chalta hai. Koi cost nahi. Koi maintenance nahi.

---

## Architecture

```
GitHub Actions (cron: */5 * * * *)
         │
         ▼
   checker.py runs
         │
         ├─► Fetch SSC Apply Page  ──► detect active apply link
         ├─► Fetch SSC Notices     ──► detect CHSL notification/PDF
         └─► Fetch SSC Calendar    ──► detect upcoming dates
                   │
                   ▼
           state.json (persisted in repo)
           ┌──────────────────────────────────┐
           │ chsl_active, last_page_hash,     │
           │ consecutive_errors, timestamps…  │
           └──────────────────────────────────┘
                   │
                   ▼
         Smart Decision Engine
         ├─ Status changed inactive→active?  → 🚨 ALERT
         ├─ Page content unchanged?          → skip (save minutes)
         ├─ All sources failed?              → error alert (6hr cooldown)
         └─ 9 AM IST daily?                 → 📊 digest
```

---

## Features

| Feature | Detail |
|---|---|
| **Detection sources** | Apply page + Notices + Exam Calendar |
| **Deduplication** | Alerts ONLY on inactive→active change |
| **Page fingerprinting** | Skips parsing if page content unchanged |
| **Error cooldown** | Error alert once, then silence for 6 hrs |
| **Auto-resolve** | "Site is back" message when errors clear |
| **Daily digest** | One clean summary at 9 AM IST |
| **State persistence** | `state.json` committed back to repo |
| **Race-condition safe** | `concurrency: group` prevents parallel runs |

---

## Setup (10 minutes, one time)

### Step 1 — Telegram Bot banao (3 min)

1. Telegram mein **@BotFather** open karo
2. `/newbot` bhejo → naam do (e.g. `MySSCWatcherBot`)
3. **Token** milega — copy karo:
   ```
   7412345678:AAFxyz_abcdefghijklmnopqrstuvwxyz
   ```
4. Apna bot search karo Telegram mein → `/start` bhejo (zaruri hai!)
5. Browser mein ye URL kholo (token replace karo):
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
6. JSON mein dhundo: `"chat":{"id":123456789}` — ye hai tera **Chat ID**

---

### Step 2 — GitHub Repo banao (3 min)

1. **github.com** pe free account banao
2. **New repository** → naam: `ssc-chsl-watcher` → **Public** → Create
3. Ye 4 files/folders upload karo (ZIP se extract karo):
   ```
   .github/
     workflows/
       watcher.yml
   scripts/
     checker.py
   state.json
   README.md
   ```

   **Upload karna easy tarika:**
   - GitHub page pe `Add file` → `Upload files`
   - `.github/workflows/watcher.yml` ke liye: file drag karo, path field mein
     `.github/workflows/watcher.yml` type karo
   - Baaki files normal upload

---

### Step 3 — Secrets add karo (2 min)

Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from Step 1 |
| `TELEGRAM_CHAT_ID` | Chat ID from Step 1 |

---

### Step 4 — Actions permission do (30 sec)

Repo → **Settings** → **Actions** → **General** → **Workflow permissions**
→ Select **"Read and write permissions"** → **Save**

*(Ye zaruri hai taaki watcher `state.json` ko commit kar sake)*

---

### Step 5 — Test karo (1 min)

1. Repo → **Actions** tab
2. `SSC CHSL Watcher 🔔` → **Run workflow** → **Run workflow**
3. Job green ho jaaye → sab theek hai ✅
4. Telegram pe aayega:
   - Pehli baar: `📊 Daily Report` ya console output
   - Error nahi aana chahiye

---

## Telegram Messages

### 🚨 Jab Form Aaye
```
🚨🚨🚨 SSC CHSL FORM AA GAYA! 🚨🚨🚨

━━━━━━━━━━━━━━━━━━━━━
✅ Application ACTIVE ho gaya hai!
━━━━━━━━━━━━━━━━━━━━━

🔍 Evidence:
  • [SSC Apply Page] Active apply link found: /apply/chsl2026
  • [SSC Latest Notices] Notice found: CHSL 2026 Application...

👉 ABHI APPLY KARO:
https://ssc.gov.in/home/apply

⚠️ Der mat karo! Last date miss mat karna!
OTR ID ready rakho aur documents pehle se scan karo.

🕐 Detected: 20 Jun 2026, 10:05 AM IST
```

### 📊 Daily Digest (9 AM IST)
```
📊 SSC CHSL Watcher — Daily Report

📋 CHSL Status: ⏳ Abhi Active Nahi
🔍 Total checks: 1728
🔔 Alerts sent: 0

🌐 SSC Apply Page
🕐 Report time: 20 Jun 2026, 09:00 AM IST
```

### ⚠️ Error Alert (sirf pehli baar, phir 6 ghante silence)
```
⚠️ SSC Watcher — Fetch Error

SSC website temporarily unreachable.
Error: Connection timeout
Consecutive failures: 1

Watcher chal raha hai — agle check pe retry hoga.
```

---

## FAQ

**Q: Ye kitna free hai?**
A: 100% free. GitHub Actions gives 2,000 free minutes/month.
   Ye watcher uses ~2 min/run × ~8,640 runs/month ≈ 288 minutes — way under limit.
   (Fingerprinting aur early exits se actual usage aur bhi kam hai.)

**Q: Agar CHSL pehle se active ho toh?**
A: `state.json` mein `"chsl_active": true` manually set karo aur commit karo.
   Bot phir duplicate alert nahi bhejega.

**Q: Manually reset karna ho toh?**
A: `state.json` ko initial values pe reset karo aur commit karo.

**Q: WhatsApp bhi mil sakta hai?**
A: WhatsApp official API free nahi hai. Telegram best free option hai.
   Telegram bahut fast aur reliable hai — ek baar try karo.

**Q: Agar GitHub Actions band ho toh?**
A: GitHub Actions 99.9% uptime hai. Agar koi issue ho, Actions tab pe
   dekh sakte ho. Typically self-heal ho jaata hai.

---

## Monitoring Stats

`state.json` mein real-time stats hote hain:
```json
{
  "chsl_active": false,
  "total_checks": 1234,
  "total_alerts": 0,
  "consecutive_errors": 0,
  "last_digest_date": "2026-06-20"
}
```

---

*Teri preparation ke liye best of luck! 🎯*
