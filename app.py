#!/usr/bin/env python3
"""
YouTube Downloader - simple Flask web app using yt-dlp.

Routes:
  GET  /              -> the single page (paste link -> Get Video -> quality buttons)
  POST /api/info      -> JSON {url} -> {title, thumbnail, duration, duration_str}
  GET  /download      -> ?url=...&quality=best|720p|480p|mp3 -> streams file to browser

No login, no database. Downloads are stored in a temp folder, sent to the
browser, then deleted.
"""

import os
import re
import shutil
import threading
import uuid

from flask import Flask, jsonify, render_template_string, request, send_file, after_this_request

import yt_dlp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(BASE_DIR, "downloads_tmp")
os.makedirs(TMP_DIR, exist_ok=True)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SUPPORTED_RE = re.compile(
    r"(youtube\.com/(watch\?.*v=|shorts/|live/|embed/)|youtu\.be/|"
    r"(www\.|m\.|vm\.|vt\.)?tiktok\.com/|douyin\.com/|v\.douyin\.com/)",
    re.IGNORECASE,
)


def is_supported_url(url: str) -> bool:
    return bool(url) and bool(SUPPORTED_RE.search(url.strip()))


def fmt_duration(seconds):
    if not seconds:
        return ""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def safe_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "", name or "video").strip()
    name = re.sub(r"\s+", " ", name)
    return (name[:max_len].strip() or "video")


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


# Format selectors per quality. Each falls back to a single-file format so the
# app still works on hosts without ffmpeg.
FORMATS = {
    # Best available, merged to mp4 when ffmpeg exists; else best single file.
    "best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
    "720p": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]/best",
    "480p": "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio/best[height<=480]/best",
    "mp3": "bestaudio/best",
}

QUALITY_LABELS = {
    "best": "Best Quality (MP4)",
    "720p": "720p",
    "480p": "480p",
    "mp3": "Audio MP3",
}


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>YouTube Downloader — HD Video + MP3</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
         background:#0f1115; color:#f2f3f5; min-height:100vh; }
  .wrap { max-width:640px; margin:0 auto; padding:28px 16px 48px; }
  h1 { font-size:30px; margin:8px 0 4px; text-align:center; }
  h1 .red { color:#ff3b30; }
  p.sub { text-align:center; color:#a7adb8; margin:0 0 24px; font-size:15px; }
  .card { background:#1a1d24; border:1px solid #2a2e38; border-radius:14px; padding:18px; }
  label { display:block; font-size:14px; color:#c8cdd6; margin-bottom:8px; }
  .row { display:flex; gap:10px; }
  input[type=url] { flex:1; min-width:0; padding:14px; font-size:16px; border-radius:10px;
                    border:1px solid #343945; background:#101216; color:#fff; }
  input[type=url]:focus { outline:2px solid #ff3b30; outline-offset:0; border-color:transparent; }
  button { cursor:pointer; border:none; border-radius:10px; font-weight:600; }
  .btn-main { background:#ff3b30; color:#fff; padding:14px 20px; font-size:16px; white-space:nowrap; }
  .btn-main:disabled { opacity:.55; cursor:wait; }
  .error { display:none; margin-top:14px; background:#3a1719; border:1px solid #7a2b2e; color:#ffb3b0;
           padding:12px 14px; border-radius:10px; font-size:14px; }
  .result { display:none; margin-top:20px; }
  .thumb { width:100%; border-radius:10px; display:block; background:#000; }
  .vtitle { font-size:18px; font-weight:700; margin:12px 0 2px; line-height:1.35; }
  .vdur { color:#a7adb8; font-size:14px; margin-bottom:16px; }
  .qgrid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .qbtn { background:#262b36; color:#fff; padding:14px 10px; font-size:15px; border:1px solid #343945; }
  .qbtn:hover { background:#313847; }
  .qbtn.wide { grid-column:1 / -1; background:#ff3b30; }
  .qbtn.wide:hover { background:#e0322a; }
  .note { text-align:center; color:#7d8494; font-size:13px; margin-top:22px; line-height:1.5; }
  .spinner { display:none; text-align:center; color:#a7adb8; margin-top:16px; font-size:14px; }
  .steps { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-top:22px; }
  .step { background:#1a1d24; border:1px solid #2a2e38; border-radius:12px; padding:14px; font-size:14px; color:#c8cdd6; line-height:1.5; }
  .step b { display:block; color:#fff; font-size:15px; margin-bottom:4px; }
  .step .n { color:#ff3b30; font-weight:800; margin-right:6px; }
  .faq { margin-top:26px; }
  .faq h2 { font-size:19px; margin:0 0 10px; }
  .faq .q { font-weight:700; margin:14px 0 4px; font-size:15px; }
  .faq .a { color:#a7adb8; font-size:14px; line-height:1.55; margin:0; }
  @media (max-width:560px){ .steps{ grid-template-columns:1fr; } }
  @media (max-width:480px){ .row{ flex-direction:column; } .btn-main{ width:100%; } }
</style>
</head>
<body>
<div class="wrap">
  <h1><span class="red">YouTube</span> Downloader</h1>
  <p class="sub">YouTube videos seconds mein download karein — HD quality ya sirf MP3 audio. TikTok links bhi chal jate hain. Koi account nahi chahiye.</p>

  <div class="card">
    <label for="url">YouTube video link</label>
    <div class="row">
      <input type="url" id="url" placeholder="YouTube ka link yahan paste karein..." autocomplete="off">
      <button class="btn-main" id="getBtn" onclick="getInfo()">Get Video</button>
    </div>
    <div class="spinner" id="spinner">Loading video info, please wait...</div>
    <div class="error" id="error"></div>

    <div class="result" id="result">
      <img class="thumb" id="thumb" alt="Video thumbnail">
      <div class="vtitle" id="vtitle"></div>
      <div class="vdur" id="vdur"></div>
      <div class="qgrid">
        <button class="qbtn wide" onclick="download('best')">⬇ Best Quality (MP4)</button>
        <button class="qbtn" onclick="download('720p')">⬇ 720p</button>
        <button class="qbtn" onclick="download('480p')">⬇ 480p</button>
        <button class="qbtn wide" style="background:#262b36" onclick="download('mp3')">🎵 Audio MP3</button>
      </div>
      <p class="note" id="dlNote" style="margin-top:14px">Download tayyar honay mein thora waqt lag sakta hai — page band na karein.</p>
    </div>
  </div>

  <div class="steps">
    <div class="step"><b><span class="n">1.</span>Link copy karein</b>YouTube app ya website se video ka link copy karein.</div>
    <div class="step"><b><span class="n">2.</span>Yahan paste karein</b>Link upar wale box mein paste karke <b style="display:inline;color:#fff">Get Video</b> dabayein.</div>
    <div class="step"><b><span class="n">3.</span>Quality chunein</b>Best Quality, 720p, 480p ya MP3 — jo chahiye wo download karein.</div>
  </div>

  <div class="faq">
    <h2>Aksar pooche janay walay sawal</h2>
    <p class="q">Kya ye bilkul free hai?</p>
    <p class="a">Ji haan. Koi account, koi fees, koi limit nahi — jitni videos chahiye download karein.</p>
    <p class="q">Sab se achi quality kaise milegi?</p>
    <p class="a">"Best Quality" wala button video ki sab se unchi quality deta hai — agar video 1080p ya 4K mein hai to wahi milegi.</p>
    <p class="q">Sirf gana/audio chahiye to?</p>
    <p class="a">"Audio MP3" wala button dabayein — sirf awaz MP3 file mein download ho jayegi.</p>
    <p class="q">Kya TikTok videos bhi ho jati hain?</p>
    <p class="a">Haan, TikTok ka link bhi yahin paste kar sakte hain — jahan mumkin ho, bina watermark wali file milti hai.</p>
    <p class="q">Private video download hogi?</p>
    <p class="a">Nahi. Sirf public videos hoti hain — private, deleted ya sirf login par khulne wali videos nahi ho sakti.</p>
  </div>

  <p class="note">Sirf apni personal use ke liye download karein.<br>Free forever — koi account nahi, koi fees nahi.</p>
</div>

<script>
let currentUrl = "";

function showError(msg) {
  const e = document.getElementById('error');
  e.textContent = msg;
  e.style.display = 'block';
}
function hideError() { document.getElementById('error').style.display = 'none'; }

async function getInfo() {
  hideError();
  document.getElementById('result').style.display = 'none';
  const url = document.getElementById('url').value.trim();
  if (!url) { showError('Pehle video ka link paste karein.'); return; }
  const btn = document.getElementById('getBtn');
  const spin = document.getElementById('spinner');
  btn.disabled = true; spin.style.display = 'block';
  try {
    const res = await fetch('/api/info', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url: url})
    });
    const data = await res.json();
    if (!res.ok || data.error) { showError(data.error || 'Kuch ghalat ho gaya. Dobara try karein.'); return; }
    currentUrl = url;
    document.getElementById('thumb').src = data.thumbnail || '';
    document.getElementById('vtitle').textContent = data.title || 'Untitled';
    document.getElementById('vdur').textContent = data.duration_str ? ('Duration: ' + data.duration_str) : '';
    document.getElementById('result').style.display = 'block';
  } catch (err) {
    showError('Server se jawab nahi aya. Internet check karke dobara try karein.');
  } finally {
    btn.disabled = false; spin.style.display = 'none';
  }
}

function download(quality) {
  if (!currentUrl) return;
  const link = '/download?url=' + encodeURIComponent(currentUrl) + '&quality=' + encodeURIComponent(quality);
  window.location.href = link;
}

document.getElementById('url').addEventListener('keydown', function(e){
  if (e.key === 'Enter') { e.preventDefault(); getInfo(); }
});
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE)


# ---------------------------------------------------------------------------
# Info endpoint (no download)
# ---------------------------------------------------------------------------

@app.route("/api/info", methods=["POST"])
def api_info():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or request.form.get("url") or "").strip()

    if not is_supported_url(url):
        return jsonify(error="Ye link sahi nahi lagta. Please ek valid YouTube ya TikTok video link paste karein."), 400

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return jsonify(error="Video ki info nahi mil saki. Link check karein — video private ya deleted ho sakti hai."), 422

    if not info:
        return jsonify(error="Video ki info nahi mil saki. Dobara try karein."), 422

    return jsonify(
        title=info.get("title") or "",
        thumbnail=info.get("thumbnail") or "",
        duration=info.get("duration") or 0,
        duration_str=fmt_duration(info.get("duration")),
    )


# ---------------------------------------------------------------------------
# Download endpoint
# ---------------------------------------------------------------------------

@app.route("/download")
def download():
    url = (request.args.get("url") or "").strip()
    quality = (request.args.get("quality") or "best").strip().lower()
    if quality not in FORMATS:
        quality = "best"

    if not is_supported_url(url):
        return "Invalid video link.", 400

    job_id = uuid.uuid4().hex
    job_dir = os.path.join(TMP_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    outtmpl = os.path.join(job_dir, "%(title).80s.%(ext)s")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": FORMATS[quality],
        "outtmpl": outtmpl,
        "restrictfilenames": False,
    }

    if quality == "mp3":
        if has_ffmpeg():
            ydl_opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        else:
            # No ffmpeg: fall back to the best audio-only single file as-is.
            ydl_opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
    else:
        if has_ffmpeg():
            ydl_opts["merge_output_format"] = "mp4"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        return "Download fail ho gaya. Link check karke dobara try karein.", 422

    # prepare_filename may point at the pre-merge/pre-conversion name; find the
    # actual finished file in the job folder.
    if not os.path.exists(file_path):
        candidates = [
            os.path.join(job_dir, f) for f in os.listdir(job_dir)
            if os.path.isfile(os.path.join(job_dir, f)) and not f.endswith(".part")
        ]
        if not candidates:
            shutil.rmtree(job_dir, ignore_errors=True)
            return "Download fail ho gaya. Dobara try karein.", 422
        file_path = max(candidates, key=os.path.getsize)
    else:
        # If a post-processed file (e.g. .mp3) exists next to it, prefer it.
        base, _ = os.path.splitext(file_path)
        for ext in (".mp3", ".mp4", ".m4a", ".webm"):
            alt = base + ext
            if ext == ".mp3" and quality == "mp3" and os.path.exists(alt):
                file_path = alt
                break

    title = safe_filename(info.get("title") if info else "video")
    ext = os.path.splitext(file_path)[1] or ".mp4"
    download_name = title + ext

    @after_this_request
    def cleanup(response):
        def _rm():
            shutil.rmtree(job_dir, ignore_errors=True)
        # Small delay so the file is fully sent before removal on all servers.
        threading.Timer(5.0, _rm).start()
        return response

    return send_file(
        file_path,
        as_attachment=True,
        download_name=download_name,
        conditional=False,
    )


@app.route("/health")
def health():
    return jsonify(status="ok", ffmpeg=has_ffmpeg())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
