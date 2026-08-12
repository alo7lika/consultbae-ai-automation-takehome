# ConsultBae AI Automation Take-Home

This submission merges the three supplied exports into SQLite, exposes a small browser audio-collection app, and includes an importable n8n duplicate-check workflow.

## Quick start

Requires Python 3.11+ and FFmpeg on `PATH` for browser-recorded WebM/M4A metadata (WAV works without it).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python ingest.py --naukri C:\Users\ASUS\Downloads\source1_naukri_applicants.csv --gig C:\Users\ASUS\Downloads\source2_gig_workers.csv --cbnexus C:\Users\ASUS\Downloads\source3_cbnexus_contacts.csv
flask --app app run --debug
```

Open `http://127.0.0.1:5000`, upload/record audio, then use the submissions view to play it and see extracted metadata. The database is `consultbae.sqlite3`; audio files are saved in `uploads/`.

## Match design

The entity key is a verified, normalized email **or** a verified, normalized 10-digit Indian phone. Names are deliberately not used as a merge key: they are not stable enough. This links Naukri ↔ Gig Workers by email and Naukri ↔ CBNexus by phone. A match requiring conflicting IDs is skipped and logged for manual review. The raw provenance of every accepted row remains in `person_sources`.

## n8n automation

Import [`n8n/duplicate-alert-workflow.json`](n8n/duplicate-alert-workflow.json) in n8n. It accepts `POST /webhook/consultbae-new-person` with JSON such as `{"email":"tanvi.gupta31@example.com","phone":"9000000254"}`, calls the Flask app's `/api/check-duplicate`, branches visually, and returns an alert-style response for duplicates. For local n8n in Docker, `host.docker.internal:5000` reaches Flask; for desktop/cloud n8n replace that URL with your reachable app URL. In a real deployment, replace the duplicate response node with Slack/email credentials.

## Data issues report

| Issue | Evidence | Handling |
|---|---|---|
| Identifier formatting | Emails vary in case; phones are `+91`, `91`, `0` prefixed, and hyphenated | Lowercase emails; retain exactly 10 phone digits after India/leading-zero cleanup |
| City inconsistency | `Bangalore/Bengaluru`, `Gurgaon/Gurugram`, `Delhi NCR/New Delhi`, casing | Canonical city mapping |
| Skills inconsistency | Same skills appear in mixed case/spelling | Canonical skill vocabulary and de-duplicated lists |
| Date formats | ISO, `dd-mm`, `dd/mm`, `mm/dd`, and `7 Jul 2026` | Parse explicitly to ISO dates; ambiguous strings are documented by parser order |
| CTC units | Values such as `4.2`, `11.9`, and `417964` coexist | Treat values below 100 as lakh INR; save as INR integer |
| Gig rate units | `/hr` and `k/month` occur together | Preserve rate text rather than fabricate a comparison |
| Blank gig record | CSV row 11 is entirely blank | Log and skip |
| Shifted gig record | Row 19 puts a skills list in `email_id` and an email in `worker_name` | Log `shifted_columns` and skip rather than mis-ingest |
| Repeated CBNexus header | A `Name, Phone Number...` header appears as row 16 | Log `embedded_header` and skip |
| Duplicate Naukri person | Rohit Verma occurs twice with the same email and phone | Merge into one person; retain both provenance rows |
| Multiple emails for one person | Nikhil Chopra has `alt...` and primary emails but same phone | Merge on phone, preserve both source rows; canonical email is first seen |
| Same-name collision | Arjun Mehta appears with distinct identities (including two CBNexus phone numbers) | Never name-only merge; log as manual-review candidates |
| Cross-system variation | Isha Chopra email casing, city values, verification `Y/Yes/N/No` and status casing vary | Normalize casing/booleans/status while preserving raw source rows |

The pipeline writes each detected structural/identity issue to the `data_issues` table for audit.

## Stretch: 5,000 workers in one weekend

The first failure is likely upload reliability and storage, not SQLite reads. Before launch I would put direct-to-object-storage multipart uploads behind pre-signed URLs, enforce MIME/size/duration limits at the edge, and queue metadata extraction with retry/dead-letter handling. I would use Postgres for transactions and an idempotency key based on worker + submission UUID so browser retries cannot duplicate rows. CDN-backed playback, encryption/access-controlled object storage, lifecycle deletion rules, monitoring/alerts, rate limits, consent, and a clear recovery path for failed processing would be mandatory. Audio processing should autoscale separately from the web app; ffprobe/loudness jobs should never run in the request path.

## Stuck log

1. **Identity resolution without a universal ID.** I first considered name + city as a fallback, then rejected it: the data contains same-name Arjun Mehtas and locations are not authoritative. I used email/phone only and made name-only rows a reviewable exception. This favors avoiding irreversible false merges.
2. **Browser audio is commonly WebM, not WAV.** The browser `MediaRecorder` API produces a format that Python's `wave` module cannot parse. I separated direct WAV measurement (including RMS loudness) from FFmpeg/ffprobe metadata extraction for other codecs, and surface a useful quality note if FFmpeg is absent rather than inventing values.
3. **Malformed CSV that still parses.** The shifted gig row is valid CSV syntactically, so a normal CSV reader does not throw. I added a semantic validation rule: if `email_id` is not email-shaped while `worker_name` is, it is logged and quarantined. I rejected trying to auto-repair it because guessing its intended fields would contaminate the master record.
