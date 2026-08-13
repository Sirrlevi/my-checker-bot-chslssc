"""
Source registry — Sarkari Naukri Multi-Watcher
================================================
Har source ek dict hai:
  key        : unique id (state.json me isi se track hota hai)
  name       : Telegram message me dikhne wala naam
  url        : jo page fetch karna hai
  kind       : "apply_highlight" -> SSC-jaisa "FORM ACTIVE" wala dramatic alert
               "listing"         -> normal "Naya Update" wala alert
  trust      : "official" | "aggregator"  (sirf labeling ke liye, message me dikhta hai)

NOTE (important): Kuch government sites (RRB zones, kai State PSC) JS-heavy hain
ya bot-blocking/captcha use karte hain — plain requests+BeautifulSoup unhe
reliably nahi padh sakta. Isliye humne un official sites ko liya hai jo
server-rendered HTML dete hain, aur baaki coverage (Railway/RRB, Banking,
State-level) teeno aggregator sources se aa jaati hai jo already sab
official notifications collect karke apni site pe list karte hain.

Naya source add karna ho to bas neeche list me ek dict add karo — baaki
sab (dedup, state, alerting) generic engine khud sambhal lega.
"""

SOURCES = [
    # ── Official government sites ──────────────────────────────────────────
    {
        "key": "ssc_apply",
        "name": "SSC — Apply Page (Official)",
        "url": "https://ssc.gov.in/home/apply",
        "kind": "apply_highlight",
        "trust": "official",
    },
    {
        "key": "ssc_notice",
        "name": "SSC — Latest Notices (Official)",
        "url": "https://ssc.gov.in/home/latestNotice",
        "kind": "listing",
        "trust": "official",
    },
    {
        "key": "upsc_whatsnew",
        "name": "UPSC — What's New (Official)",
        "url": "https://upsc.gov.in/whats-new",
        "kind": "listing",
        "trust": "official",
    },
    {
        "key": "ibps_home",
        "name": "IBPS — Notices (Official)",
        "url": "https://www.ibps.in/",
        "kind": "listing",
        "trust": "official",
    },

    # ── Trusted aggregator portals (in ek jagah sab dept/state jobs mil jaate hain) ──
    {
        "key": "sarkariresult_latestjob",
        "name": "SarkariResult — Latest Jobs",
        "url": "https://www.sarkariresult.com/latestjob/",
        "kind": "listing",
        "trust": "aggregator",
    },
    {
        "key": "sarkarijobs_home",
        "name": "SarkariJobs.com — Home",
        "url": "https://www.sarkarijobs.com/",
        "kind": "listing",
        "trust": "aggregator",
    },
    {
        "key": "sarkariexam_home",
        "name": "SarkariExam.com — Home",
        "url": "https://www.sarkariexam.com/",
        "kind": "listing",
        "trust": "aggregator",
    },
]

# Keywords that suggest a link is actually a job/exam/form item (not nav junk)
JOB_KEYWORDS = [
    "recruitment", "bharti", "भर्ती", "vacancy", "vacancies", "online form",
    "apply online", "notification", "admit card", "result", "answer key",
    "syllabus", "exam date", "cut off", "cgl", "chsl", "mts", "je exam",
    "group d", "constable", "clerk", "probationary officer", " po ",
    "si exam", "upsc", "ibps", "rrb", "ntpc", "ssc ", "exam calendar",
]

# Links whose text matches these are almost certainly nav/footer junk — skip them
EXCLUDE_GENERIC = [
    "home", "contact us", "contact", "about us", "privacy policy",
    "disclaimer", "terms", "sitemap", "login", "sign in", "sign up",
    "register now for updates", "advertise", "download app", "subscribe",
    "follow us", "copyright", "facebook", "twitter", "instagram",
    "whatsapp group", "telegram channel", "skip to content", "menu",
]

# ── Eligibility filter (12th pass / 10+2 only — no graduation required) ──────
# Best-effort keyword matching. Job LISTING TITLES usually don't state the
# eligibility explicitly (that's inside the notification PDF), so this is
# NOT 100% accurate — treat "unknown" items as "go check yourself", not as
# guaranteed-eligible.

# Exam names / phrases that are well known to be 12th-pass (10+2) level
ELIGIBLE_12TH_KEYWORDS = [
    "10+2", "12th pass", "12th-pass", "12th std", "class 12", "intermediate pass",
    "chsl",                     # SSC CHSL = Combined HIGHER SECONDARY Level = 12th pass
    "mts",                      # SSC MTS = 10th pass
    "gd constable", "constable gd", "group d", "railway group d",
    "police constable", "constable recruitment", "havildar", "sepoy",
    "postman", "mail guard", "mts havaldar", "steno", "stenographer",
    "ntpc undergraduate", "ntpc ug",
    "peon", "chowkidar", "watchman", "office attendant", "class iv", "class 4",
    "driver", "cook", "safaiwala", "tradesman", "fireman",
    "gramin dak sevak", "gds ", "multi tasking",
]

# Exam names / phrases / degree mentions that require graduation or higher
EXCLUDE_GRADUATE_KEYWORDS = [
    "cgl",                       # SSC CGL = Combined GRADUATE Level
    "graduate level", "graduation required", "must be a graduate",
    "b.tech", "btech", "b.e.", " be ", "engineering degree",
    "b.sc", "bsc ", "b.a.", "b.com", "bca", "mba", "mca", "m.tech",
    "post graduate", "postgraduate", "phd", "ph.d",
    "probationary officer", " po ", "bank po", "specialist officer",
    "assistant professor", "lecturer", "civil services", "ias ", "ips ",
    "junior engineer", " je ", "je civil", "je mechanical", "je electrical",
    "scientist", "medical officer", "law officer", "chartered accountant",
    "company secretary", "llb", "mbbs", "management trainee",
]

# Posts that are reserved for / specifically advertised for female candidates
# (India me kayi posts explicitly women-only hoti hain — inhe skip karna hai
# male candidates ke liye)
EXCLUDE_FEMALE_ONLY_KEYWORDS = [
    "mahila", "women only", "women candidates only", "female only",
    "female candidates only", "for female", "for women", "ladies only",
    "ladies special", "girls only", "for girls", "only for female",
    "only women", "anganwadi worker", "anganwadi helper", "anganwadi",
    "asha worker", "asha karyakarti", "matron", "lady constable",
    "women constable", "mahila constable", "female si", "lady si",
    "nursing sister",
]

# State-specific recruitment (state Police / state PSC) usually requires
# domicile of THAT state — user is based in Haryana (Rewari/Bawal), so
# other-states' state-only recruitment gets excluded. Central government
# jobs (SSC/UPSC/IBPS/RRB/CAPF/Assam Rifles etc.) are NOT domicile-restricted
# and stay visible regardless of posting location — only true STATE-level
# recruitment bodies/forces are matched here, not generic state-name mentions.
OTHER_STATE_EXCLUDE_KEYWORDS = [
    "uttar pradesh", "up police", "uppsc", "uppcl", "up si ",
    "bihar police", "bpsc", "bihar ssc", "bssc",
    "rajasthan police", "rpsc", "rsmssb",
    "madhya pradesh police", "mp police", "mppsc", "vyapam", "mppeb",
    "maharashtra police", "mpsc",
    "gujarat police", "gpsc", "gsssb",
    "punjab police", "ppsc", "punjab psc",
    "west bengal police", "wbpsc", "wbssc", "wbcs",
    "odisha police", "opsc", "osssc",
    "jharkhand police", "jpsc", "jssc",
    "chhattisgarh police", "cgpsc", "cg police",
    "uttarakhand police", "ukpsc", "ukssc",
    "himachal police", "hppsc", "hp police",
    "kerala psc", "kerala police",
    "tamil nadu police", "tnpsc",
    "karnataka police", "kpsc", "karnataka psc",
    "andhra pradesh police", "appsc",
    "telangana police", "tspsc",
    "assam police", "assam psc", "apsc",   # note: "assam rifles" NOT matched here (different keyword, central force)
    "jkssb", "jammu kashmir police",
    "goa psc", "goa police",
    "tripura psc", "manipur psc", "meghalaya psc", "mizoram psc",
    "nagaland psc", "sikkim psc", "arunachal psc",
]

def classify_eligibility(text: str) -> str:
    """Returns 'eligible' | 'exclude' | 'unknown' based on title keywords."""
    t = text.lower()
    if any(k in t for k in EXCLUDE_FEMALE_ONLY_KEYWORDS):
        return "exclude"
    if any(k in t for k in OTHER_STATE_EXCLUDE_KEYWORDS):
        return "exclude"
    is_excluded = any(k in t for k in EXCLUDE_GRADUATE_KEYWORDS)
    is_eligible = any(k in t for k in ELIGIBLE_12TH_KEYWORDS)
    if is_excluded and not is_eligible:
        return "exclude"
    if is_eligible:
        return "eligible"
    return "unknown"
