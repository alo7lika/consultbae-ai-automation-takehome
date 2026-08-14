<div align="center">

# ConsultBae AI Automation Take-Home

*A practical data-merging pipeline, no-code duplicate check, and browser audio collection app.*

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Database](https://img.shields.io/badge/Database-SQLite-003B57?logo=sqlite&logoColor=white)
![Automation](https://img.shields.io/badge/Automation-n8n-EA4B71?logo=n8n&logoColor=white)
![Status](https://img.shields.io/badge/Status-Working-2EA44F)

</div>

---

## Overview

This project combines three messy people exports into one auditable SQLite database, detects duplicate people through an **n8n** workflow, and collects browser audio submissions with extracted technical metadata.

| Deliverable | Implementation |
|---|---|
| **Data merge** | CSV ingestion pipeline with conservative email/phone entity matching |
| **Automation** | n8n webhook -> duplicate API check -> duplicate/no-duplicate branch |
| **Audio app** | Flask upload/recording form, playback view, and metadata storage |
| **Auditability** | Raw source provenance and detected data problems retained in SQLite |

> **Design principle:** avoid an irreversible false merge. A matching name alone is not enough to combine people.

---

## Architecture

```text
Naukri CSV ───────┐
Gig Workers CSV ──┼──> ingest.py ──> SQLite master database
CBNexus CSV ──────┘                         │
                                            ├──> Flask audio collection app
                                            └──> n8n duplicate-check workflow
```

## Quick Start

### Prerequisites

- Python 3.11 or newer
- Node.js only if you want to run n8n locally
- FFmpeg on `PATH` for metadata from browser-recorded WebM/M4A files. WAV metadata works without FFmpeg.

### 1. Create the environment

> On Windows, you do **not** need to run `Activate.ps1`.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. Build the merged database

The commands below assume the provided CSV files are in `data/`. If they remain in Downloads, replace the paths with their full paths.

```powershell
.\.venv\Scripts\python.exe ingest.py `
  --naukri ".\data\source1_naukri_applicants.csv" `
  --gig ".\data\source2_gig_workers.csv" `
  --cbnexus ".\data\source3_cbnexus_contacts.csv"
```

Expected result:

```text
Created consultbae.sqlite3: 60 people, 10 logged issues
```

### 3. Start the audio app

```powershell
.\.venv\Scripts\python.exe -m flask --app app run --debug
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000). Submit an audio file, then open the submissions view to play it and inspect its metadata.

---
![Audio app](assets/Screenshot%202026-08-14%20150146.png)

## 1. Entity Resolution and Data Merge

### Matching strategy

| Field | Treatment | Reason |
|---|---|---|
| Email | Trim and lowercase | Links Naukri to Gig Workers despite casing differences |
| Phone | Strip punctuation; normalize to a 10-digit Indian number | Links Naukri to CBNexus despite `+91`, `91`, and leading-zero formats |
| Name | Display and review only - **not a merge key** | Same names can represent different people |
| City | Map aliases to canonical values | Makes records easier to compare and report |

This creates a person master table while preserving every accepted source row in `person_sources`. If email and phone point to different people, the row is skipped for review instead of being merged incorrectly.

### Data quality findings

| Issue | Evidence | Handling |
|---|---|---|
| Identifier formatting | Email casing and `+91`/`91`/`0` phone prefixes vary | Normalize email and phone values |
| City inconsistency | `Bangalore/Bengaluru`, `Gurgaon/Gurugram`, `Delhi NCR/New Delhi` | Canonical city mapping |
| Skills inconsistency | Mixed casing and spelling | Canonical skill vocabulary and de-duplicated lists |
| Date formats | ISO, dash, slash, and written-month dates | Explicit parsing to ISO date values |
| CTC units | `4.2`, `11.9`, and `417964` coexist | Values below 100 treated as lakh INR; stored as INR |
| Gig rates | `/hr` and `k/month` both occur | Preserve raw rate text; do not invent a comparison |
| Blank Gig row | Entire row is blank | Log and skip |
| Shifted Gig row | Skills appear in `email_id`; email appears in `worker_name` | Log `shifted_columns` and quarantine the row |
| Repeated CBNexus header | Header occurs inside data | Log `embedded_header` and skip |
| Duplicate Naukri row | Rohit Verma repeats with same email and phone | Merge person; retain both source-provenance rows |
| Multiple emails | Nikhil Chopra has alternate and primary email with shared phone | Merge on phone; retain source history |
| Same-name collision | Arjun Mehta has distinct identifiers | Never merge by name alone |

All detected structural and identity issues are stored in the `data_issues` table for audit.

---

## 2. n8n Duplicate-Check Automation

The workflow export is available at [`n8n/duplicate-alert-workflow.json`](n8n/duplicate-alert-workflow.json).

```text
Webhook receives person
        ↓
HTTP Request calls /api/check-duplicate
        ↓
IF duplicate?
   ├─ true  -> Duplicate alert response
   └─ false -> No-duplicate response
```

### Run locally

```powershell
npx.cmd n8n
```

Open [http://localhost:5678](http://localhost:5678), import the workflow JSON, and update the **Check SQLite-backed app API** URL to:

```text
http://127.0.0.1:5000/api/check-duplicate
```

Test payload:

```json
{
  "email": "tanvi.gupta31@example.com",
  "phone": "9000000254"
}
```

> For a production deployment, replace the duplicate response node with Slack, email, or another alerting destination using real credentials.

---
![n8n duplicate-check workflow](assets/Screenshot%202026-08-14%20150323.png)

## 3. Audio Collection App

The Flask app lets a worker provide their name, phone number, and an audio upload or browser recording. Every submission creates a database record and stores:

- Duration in seconds
- Sample rate in kHz
- Bitrate in kbps
- Loudness in dB for WAV files
- A quality/processing note

Audio is stored in `uploads/`; submission records are stored in `audio_submissions` and linked to the master `people` table.

---

## Stretch: Launching to 5,000 Workers

The first likely failure is upload reliability and storage, not basic database reads. Before a weekend launch I would:

1. Use direct multipart uploads to object storage with pre-signed URLs.
2. Validate MIME type, size, and duration at the edge.
3. Process metadata asynchronously in a queued worker with retries and a dead-letter queue.
4. Move transactional data to Postgres and add idempotency keys for retried submissions.
5. Use private encrypted object storage, lifecycle deletion rules, monitoring, rate limits, and consent controls.

*Audio processing must scale independently from the web request path.*

---

## Stuck Log

1. **Identity resolution without a universal ID**  
   I initially considered name plus city as a fallback, then rejected it because the data contains same-name Arjun Mehtas and city is not authoritative. I used normalized email/phone only and made name-only matches reviewable exceptions.

2. **Browser audio is usually WebM, not WAV**  
   The browser `MediaRecorder` API commonly creates WebM, which Python's `wave` module cannot parse. I separated direct WAV measurement (including RMS loudness) from `ffprobe` metadata extraction for other codecs. If FFmpeg is unavailable, the app reports that clearly instead of inventing values.

3. **Malformed CSV that still parses**  
   The shifted Gig Workers row is syntactically valid CSV, so a normal reader does not fail. I added semantic validation: when `email_id` is not email-shaped but `worker_name` is, the row is logged and quarantined rather than guessed.

---

## Repository Guide

```text
app.py                         Flask audio collection application
ingest.py                      CSV normalization and SQLite ingestion pipeline
n8n/duplicate-alert-workflow.json  Importable no-code automation
templates/                     Browser views for submission and playback
data/                          Supplied CSV inputs (if included)
README.md                      Setup, decisions, report, and scale plan
```

<div align="center">

*Built for the ConsultBae AI Automation Take-Home Assignment.*

</div>
