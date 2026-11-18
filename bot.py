"""
YouTube Cricket Shorts Bot
queue.txt ki pehli line uthata hai -> download -> 9:16 short edit -> YouTube upload.

queue.txt line format:
URL | Title | start_seconds | duration_seconds
(Title, start, duration optional hain)
"""
import os
import pathlib
import subprocess
import tempfile
import textwrap

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

QUEUE = pathlib.Path("queue.txt")
FAILED = pathlib.Path("failed.txt")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
HASHTAGS = os.environ.get("HASHTAGS", "#Shorts #Cricket #CricketShorts #Highlights")
PRIVACY = os.environ.get("PRIVACY", "public")  # public / unlisted / private


def read_queue():
    if not QUEUE.exists():
        return []
    return QUEUE.read_text(encoding="utf-8").splitlines()


def pick_job(lines):
    for i, line in enumerate(lines):
        s = line.strip()
        if s and not s.startswith("#"):
            return i, s
    return None, None


def parse(s):
    p = [x.strip() for x in s.split("|")]
    url = p[0]
    title = p[1] if len(p) > 1 and p[1] else "Cricket Highlights"
    start = float(p[2]) if len(p) > 2 and p[2] else 0.0
    dur = float(p[3]) if len(p) > 3 and p[3] else 50.0
    return url, title, start, min(dur, 58.0)


def run(cmd):
    print(">>", " ".join(cmd))
    subprocess.run(cmd, check=True)


def download(url, folder):
    out = str(folder / "src.%(ext)s")
    run([
        "yt-dlp", "--no-playlist",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
        "--merge-output-format", "mp4",
        "-o", out, url,
    ])
    return folder / "src.mp4"


def edit(src, folder, title, start, dur):
    txt = folder / "title.txt"
    txt.write_text(textwrap.fill(title, 24), encoding="utf-8")
    out = folder / "short.mp4"
    fc = (
        "[0:v]split[a][b];"
        "[a]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=25:5[bg];"
        "[b]scale=1080:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
        f"drawtext=fontfile={FONT}:textfile={txt}:fontcolor=white:fontsize=56:"
        "box=1:boxcolor=black@0.6:boxborderw=20:x=(w-text_w)/2:y=160[v]"
    )
    run([
        "ffmpeg", "-y", "-ss", str(start), "-t", str(dur), "-i", str(src),
        "-filter_complex", fc,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(out),
    ])
    return out


def youtube_client():
    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    return build("youtube", "v3", credentials=creds)


def upload(video, title):
    yt = youtube_client()
    body = {
        "snippet": {
            "title": f"{title} #Shorts"[:100],
            "description": f"{title}\n\n{HASHTAGS}",
            "tags": ["cricket", "shorts", "highlights"],
            "categoryId": "17",  # Sports
        },
        "status": {
            "privacyStatus": PRIVACY,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(str(video), mimetype="video/mp4", resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            print(f"Upload {int(status.progress() * 100)}%")
    print("Uploaded! Video ID:", resp["id"])


def main():
    lines = read_queue()
    idx, job = pick_job(lines)
    if job is None:
        print("Queue khali hai. queue.txt me links add karein.")
        return
    url, title, start, dur = parse(job)
    try:
        with tempfile.TemporaryDirectory() as d:
            folder = pathlib.Path(d)
            src = download(url, folder)
            short = edit(src, folder, title, start, dur)
            upload(short, title)
        lines.pop(idx)  # kamyab: queue se hata do
    except Exception as e:
        print("FAILED:", e)
        with FAILED.open("a", encoding="utf-8") as f:
            f.write(job + "\n")
        lines.pop(idx)
        QUEUE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        raise
    QUEUE.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
