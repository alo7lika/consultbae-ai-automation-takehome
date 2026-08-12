"""Small Flask audio collection app. Run `flask --app app run --debug`."""
from __future__ import annotations
import audioop, os, sqlite3, struct, subprocess, uuid, wave
from pathlib import Path
from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, url_for
from ingest import normal_phone, normal_email, normal_name, display_name

BASE = Path(__file__).parent
DATABASE = Path(os.environ.get("DATABASE_PATH", BASE / "consultbae.sqlite3"))
UPLOADS = BASE / "uploads"; UPLOADS.mkdir(exist_ok=True)
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

def db():
    conn=sqlite3.connect(DATABASE); conn.row_factory=sqlite3.Row; return conn

def wav_metadata(path):
    with wave.open(str(path), "rb") as w:
        frames, rate, channels, width = w.getnframes(), w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw=w.readframes(frames)
        rms=audioop.rms(raw, width) if raw else 0
        max_rms=float(2 ** (width*8-1)); loudness=20 * __import__('math').log10(max(rms,1)/max_rms)
        return round(frames/rate,2), rate, round(rate*channels*width*8/1000,1), round(loudness,1), "WAV measured directly"

def media_metadata(path):
    try:
        data=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration,bit_rate","-show_entries","stream=sample_rate","-of","default=noprint_wrappers=1",str(path)],capture_output=True,text=True,check=True,timeout=20).stdout
        values=dict(line.split("=",1) for line in data.splitlines() if "=" in line)
        duration=float(values.get("duration",0)); rate=int(values.get("sample_rate",0) or 0); bitrate=float(values.get("bit_rate",0) or 0)/1000
        return round(duration,2), rate or None, round(bitrate,1) or None, None, "Metadata extracted with ffprobe; loudness requires WAV or an audio-analysis service"
    except (FileNotFoundError, subprocess.SubprocessError, ValueError):
        return None,None,None,None,"Could not read this format locally. Install FFmpeg for browser-recorded WebM/M4A metadata."

@app.get("/")
def index(): return render_template("index.html")

@app.post("/submit")
def submit():
    name=request.form.get("name","").strip(); phone=normal_phone(request.form.get("phone")); email=normal_email(request.form.get("email"))
    audio=request.files.get("audio")
    if not name or not audio or not audio.filename: abort(400,"Name and an audio file are required.")
    suffix=Path(audio.filename).suffix.lower() or ".webm"; stored=f"{uuid.uuid4().hex}{suffix}"; target=UPLOADS/stored; audio.save(target)
    meta=wav_metadata(target) if suffix==".wav" else media_metadata(target)
    conn=db(); found=conn.execute("SELECT id FROM people WHERE phone=? OR email=?",(phone,email)).fetchone()
    if found: pid=found[0]
    else:
        cur=conn.execute("INSERT INTO people(full_name,normalized_name,email,phone) VALUES (?,?,?,?)",(display_name(name),normal_name(name),email,phone)); pid=cur.lastrowid
    conn.execute("INSERT INTO audio_submissions(person_id,original_filename,stored_filename,mime_type,duration_seconds,sample_rate_hz,bitrate_kbps,loudness_db,quality_note) VALUES (?,?,?,?,?,?,?,?,?)",(pid,audio.filename,stored,audio.mimetype,*meta)); conn.commit(); conn.close()
    return redirect(url_for("submissions"))

@app.get("/submissions")
def submissions():
    conn=db(); rows=conn.execute("SELECT a.*,p.full_name,p.phone FROM audio_submissions a JOIN people p ON p.id=a.person_id ORDER BY a.id DESC").fetchall(); conn.close(); return render_template("submissions.html", rows=rows)

@app.get("/audio/<path:filename>")
def audio(filename): return send_from_directory(UPLOADS,filename)

@app.post("/api/check-duplicate")
def duplicate_check():
    payload=request.get_json(silent=True) or {}; email=normal_email(payload.get("email")); phone=normal_phone(payload.get("phone")); conn=db()
    rows=conn.execute("SELECT id,full_name,email,phone FROM people WHERE email=? OR phone=?",(email,phone)).fetchall(); conn.close()
    return jsonify({"duplicate":bool(rows),"matches":[dict(x) for x in rows]})
