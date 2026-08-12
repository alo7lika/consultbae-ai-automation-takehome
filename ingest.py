"""Ingest three supplied CSV exports into a normalized SQLite database.

Run: python ingest.py --naukri PATH --gig PATH --cbnexus PATH --database consultbae.sqlite3
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def clean_text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def normal_name(value: str | None) -> str | None:
    value = clean_text(value)
    if not value:
        return None
    return re.sub(r"[^a-z0-9]", "", value.lower())


def display_name(value: str | None) -> str | None:
    value = clean_text(value)
    if not value:
        return None
    # Keep initials readable while making casing consistent.
    return " ".join(part.capitalize() if len(part) > 1 else part.upper() for part in value.split())


def normal_email(value: str | None) -> str | None:
    value = clean_text(value)
    return value.lower() if value and "@" in value else None


def normal_phone(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def normal_city(value: str | None) -> str | None:
    value = (clean_text(value) or "").lower()
    aliases = {"bangalore": "Bengaluru", "bengaluru": "Bengaluru", "gurgaon": "Gurugram",
               "gurugram": "Gurugram", "new delhi": "Delhi", "delhi ncr": "Delhi", "delhi": "Delhi",
               "noida": "Noida", "pune": "Pune"}
    return aliases.get(value, value.title() or None)


def split_skills(value: str | None) -> list[str]:
    aliases = {"rest apis": "REST APIs", "web scraping": "Web Scraping", "n8n": "n8n",
               "fastapi": "FastAPI", "langchain": "LangChain", "mysql": "MySQL", "mongodb": "MongoDB",
               "sql": "SQL", "javascript": "JavaScript", "python": "Python", "react": "React",
               "docker": "Docker", "selenium": "Selenium", "zapier": "Zapier", "pandas": "Pandas"}
    return sorted({aliases.get(x.strip().lower(), x.strip().title()) for x in (value or "").split(",") if x.strip()})


def parse_date(value: str | None) -> str | None:
    value = clean_text(value)
    if not value:
        return None
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d %b %Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def parse_ctc(value: str | None) -> int | None:
    try:
        num = float(value or "")
    except ValueError:
        return None
    # Values below 100 are clearly reported in lakh INR, otherwise INR.
    return round(num * 100000) if num < 100 else round(num)


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS people (
 id INTEGER PRIMARY KEY, full_name TEXT NOT NULL, normalized_name TEXT NOT NULL,
 email TEXT UNIQUE, phone TEXT UNIQUE, city TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS person_sources (
 id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES people(id), source TEXT NOT NULL,
 source_row INTEGER NOT NULL, raw_json TEXT NOT NULL, UNIQUE(source, source_row)
);
CREATE TABLE IF NOT EXISTS applications (
 person_id INTEGER PRIMARY KEY REFERENCES people(id), experience_years REAL, current_ctc_inr INTEGER,
 applied_date TEXT, skills TEXT
);
CREATE TABLE IF NOT EXISTS gig_profiles (
 person_id INTEGER PRIMARY KEY REFERENCES people(id), rate_raw TEXT, worker_status TEXT, skills TEXT
);
CREATE TABLE IF NOT EXISTS cbnexus_profiles (
 person_id INTEGER PRIMARY KEY REFERENCES people(id), verified INTEGER, projects_completed INTEGER
);
CREATE TABLE IF NOT EXISTS data_issues (
 id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_row INTEGER, issue_type TEXT NOT NULL, details TEXT NOT NULL,
 action TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audio_submissions (
 id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES people(id), original_filename TEXT NOT NULL,
 stored_filename TEXT NOT NULL UNIQUE, mime_type TEXT, duration_seconds REAL, sample_rate_hz INTEGER,
 bitrate_kbps REAL, loudness_db REAL, quality_note TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Merger:
    def __init__(self, connection: sqlite3.Connection):
        self.db = connection
        self.index: dict[str, dict[str, int]] = defaultdict(dict)
        for row in self.db.execute("SELECT id, normalized_name, email, phone FROM people"):
            for key, value in (("name", row[1]), ("email", row[2]), ("phone", row[3])):
                if value:
                    self.index[key][value] = row[0]

    def issue(self, source, row, issue, details, action):
        self.db.execute("INSERT INTO data_issues(source,source_row,issue_type,details,action) VALUES (?,?,?,?,?)",
                        (source, row, issue, details, action))

    def person(self, source, row_no, name, email=None, phone=None, city=None) -> int | None:
        name_key, email, phone = normal_name(name), normal_email(email), normal_phone(phone)
        if not name_key:
            self.issue(source, row_no, "missing_identity", "No usable name", "Skipped row")
            return None
        matches = {self.index[k][v] for k, v in (("email", email), ("phone", phone)) if v and v in self.index[k]}
        if len(matches) > 1:
            self.issue(source, row_no, "conflicting_identifiers", "Email and phone resolve to different people", "Skipped for manual review")
            return None
        person_id = next(iter(matches), None)
        # A name by itself is never enough to merge: common names and missing IDs are too risky.
        if person_id is None and name_key in self.index["name"]:
            candidate = self.index["name"][name_key]
            existing = self.db.execute("SELECT email, phone FROM people WHERE id=?", (candidate,)).fetchone()
            if (email and existing[0] and email != existing[0]) or (phone and existing[1] and phone != existing[1]):
                self.issue(source, row_no, "same_name_conflict", f"{name!r} has a different identifier", "Kept as separate person")
            else:
                self.issue(source, row_no, "name_only_match", f"{name!r} has no matching email/phone", "Kept separate to avoid a false merge")
        if person_id is None:
            cursor = self.db.execute("INSERT INTO people(full_name,normalized_name,email,phone,city) VALUES (?,?,?,?,?)",
                                     (display_name(name), name_key, email, phone, normal_city(city)))
            person_id = cursor.lastrowid
        else:
            self.db.execute("UPDATE people SET email=COALESCE(email,?), phone=COALESCE(phone,?), city=COALESCE(city,?) WHERE id=?",
                            (email, phone, normal_city(city), person_id))
        record = self.db.execute("SELECT email,phone FROM people WHERE id=?", (person_id,)).fetchone()
        for key, value in (("name", name_key), ("email", record[0]), ("phone", record[1])):
            if value:
                self.index[key][value] = person_id
        return person_id


def usable(row: dict[str, str]) -> bool:
    return any(clean_text(v) for v in row.values())


def run(args):
    db_path = Path(args.database)
    if db_path.exists(): db_path.unlink()
    db = sqlite3.connect(db_path)
    db.executescript(SCHEMA)
    merger = Merger(db)

    def source_rows(path):
        with open(path, newline="", encoding="utf-8-sig") as file:
            yield from enumerate(csv.DictReader(file), start=2)

    for row_no, r in source_rows(args.naukri):
        if not usable(r): merger.issue("naukri", row_no, "blank_row", "Entire row blank", "Skipped"); continue
        pid = merger.person("naukri", row_no, r["Full Name"], r["Email"], r["Phone"], r["City"])
        if not pid: continue
        db.execute("INSERT INTO person_sources(person_id,source,source_row,raw_json) VALUES (?,?,?,?)", (pid,"naukri",row_no,str(r)))
        db.execute("INSERT OR REPLACE INTO applications VALUES (?,?,?,?,?)", (pid, float(r["Experience (Years)"]), parse_ctc(r["Current CTC"]), parse_date(r["Applied Date"]), ", ".join(split_skills(r["Skills"]))))

    for row_no, r in source_rows(args.gig):
        if not usable(r): merger.issue("gig_workers",row_no,"blank_row","Entire row blank","Skipped"); continue
        # A shifted CSV row places a skills string in email_id and an email in worker_name.
        if not normal_email(r["email_id"]) and normal_email(r["worker_name"]):
            merger.issue("gig_workers",row_no,"shifted_columns","Malformed row has fields shifted right","Skipped; source needs repair"); continue
        pid = merger.person("gig_workers", row_no, r["worker_name"], r["email_id"], city=r["location"])
        if not pid: continue
        db.execute("INSERT INTO person_sources(person_id,source,source_row,raw_json) VALUES (?,?,?,?)", (pid,"gig_workers",row_no,str(r)))
        db.execute("INSERT OR REPLACE INTO gig_profiles VALUES (?,?,?,?)", (pid, clean_text(r["rate"]), (clean_text(r["status"]) or "").title(), ", ".join(split_skills(r["skill_tags"]))))

    for row_no, r in source_rows(args.cbnexus):
        if normal_name(r["Name"]) == "name" and normal_phone(r["Phone Number"]) is None:
            merger.issue("cbnexus",row_no,"embedded_header","A repeated header is present as a data row","Skipped"); continue
        pid = merger.person("cbnexus", row_no, r["Name"], phone=r["Phone Number"], city=r["City"])
        if not pid: continue
        verified = (clean_text(r["Verified"]) or "").lower() in {"y", "yes"}
        db.execute("INSERT INTO person_sources(person_id,source,source_row,raw_json) VALUES (?,?,?,?)", (pid,"cbnexus",row_no,str(r)))
        db.execute("INSERT OR REPLACE INTO cbnexus_profiles VALUES (?,?,?)", (pid, verified, int(r["Projects Completed"])))
    db.commit()
    print(f"Created {db_path}: {db.execute('SELECT count(*) FROM people').fetchone()[0]} people, {db.execute('SELECT count(*) FROM data_issues').fetchone()[0]} logged issues")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--naukri", required=True); parser.add_argument("--gig", required=True)
    parser.add_argument("--cbnexus", required=True); parser.add_argument("--database", default="consultbae.sqlite3")
    run(parser.parse_args())
