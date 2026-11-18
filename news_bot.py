"""
Pakistan Cricket News Video Bot
Google News se taza khabrain -> Gemini se Urdu script/title/description ->
Urdu awaaz (edge-tts) -> slides ki video + thumbnail -> YouTube upload.
"""
import asyncio
import json
import os
import pathlib
import subprocess
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import edge_tts
from PIL import Image, ImageDraw, ImageFont, features
from googleapiclient.http import MediaFileUpload

from bot import youtube_client

STATE = pathlib.Path("posted.json")
MAX_PER_DAY = int(os.environ.get("MAX_PER_DAY", "5"))
WINDOW = os.environ.get("NEWS_WINDOW", "1h")
VOICE = os.environ.get("VOICE", "ur-PK-AsadNeural")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
PRIVACY = os.environ.get("PRIVACY", "public")
HASHTAGS = "#PakistanCricket #CricketNews #PCB #Cricket"
W, H = 1280, 720
GREEN, GOLD, WHITE = (1, 65, 28), (255, 200, 40), (255, 255, 255)
HEADER = "پاکستان کرکٹ نیوز"

RAQM = features.check("raqm")
RT = {"direction": "rtl", "language": "ur"} if RAQM else {}
if not RAQM:
    print("WARNING: raqm nahi mila, Urdu text theek nahi dikhega.")


# ---------- state ----------
def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"links": [], "uploads": []}


def save_state(st):
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- news ----------
def fetch_news():
    q = urllib.parse.quote(f"Pakistan cricket when:{WINDOW}")
    url = f"https://news.google.com/rss/search?q={q}&hl=en-PK&gl=PK&ceid=PK:en"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    root = ET.fromstring(urllib.request.urlopen(req, timeout=30).read())
    return [
        {
            "title": it.findtext("title", ""),
            "link": it.findtext("link", ""),
            "source": it.findtext("source", ""),
        }
        for it in root.iter("item")
    ]


def ask_gemini(headlines):
    prompt = (
        "Neeche Pakistan cricket ki taza headlines hain. Sirf inhi me di gayi "
        "maloomat istemal karo, koi nayi baat ya number apni taraf se mat banao.\n\n"
        + "\n".join(f"- {h}" for h in headlines)
        + "\n\nSirf JSON wapas do, in keys ke saath:\n"
        '"title": YouTube title, max 90 characters, Urdu aur English mix, sachcha aur dilchasp,\n'
        '"description": 3 se 5 lines, Urdu aur English, headlines ka khulasa,\n'
        '"thumb_text": thumbnail ke liye Urdu me max 6 alfaaz,\n'
        '"slides": 3 se 5 strings, har ek Urdu me 1-2 chhote jumle (max 18 alfaaz), '
        "yehi awaaz me bola jayega. Pehli slide intro, aakhri slide me subscribe ki guzarish."
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
    }
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}"
        f":generateContent?key={os.environ['GEMINI_API_KEY']}"
    )
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    data = json.load(urllib.request.urlopen(req, timeout=120))
    return json.loads(data["candidates"][0]["content"]["parts"][0]["text"])


# ---------- drawing ----------
_font_cache = {}


def urdu_font(size):
    if "path" not in _font_cache:
        path = ""
        try:
            path = subprocess.check_output(
                ["fc-match", "-f", "%{file}", "Noto Naskh Arabic:bold"], text=True
            ).strip()
        except Exception:
            pass
        _font_cache["path"] = path or "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    layout = ImageFont.Layout.RAQM if RAQM else ImageFont.Layout.BASIC
    return ImageFont.truetype(_font_cache["path"], size, layout_engine=layout)


def latin_font(size):
    return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)


def wrap(draw, text, font, maxw):
    lines, cur = [], ""
    for word in text.split():
        t = (cur + " " + word).strip()
        if draw.textlength(t, font=font, **RT) <= maxw:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def draw_block(draw, text, size, y_center, fill=WHITE, maxw=1100):
    font = urdu_font(size)
    lines = wrap(draw, text, font, maxw)
    gap = int(size * 1.5)
    y = y_center - (len(lines) * gap) // 2
    for line in lines:
        draw.text((W // 2, y), line, font=font, fill=fill, anchor="ma", **RT)
        y += gap


def make_slide(text, path):
    img = Image.new("RGB", (W, H), GREEN)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 14], fill=GOLD)
    d.text((W // 2, 40), HEADER, font=urdu_font(46), fill=GOLD, anchor="ma", **RT)
    draw_block(d, text, 64, H // 2 + 30)
    d.rectangle([0, H - 14, W, H], fill=GOLD)
    img.save(path)


def make_thumbnail(text, path):
    img = Image.new("RGB", (W, H), GREEN)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 40, H], fill=GOLD)
    d.rectangle([0, H - 110, W, H], fill=(0, 35, 15))
    d.text((W // 2, H - 95), HEADER, font=urdu_font(58), fill=GOLD, anchor="ma", **RT)
    d.text((60, 30), "PAKISTAN CRICKET NEWS", font=latin_font(34), fill=WHITE)
    draw_block(d, text, 120, (H - 110) // 2 + 30, maxw=1100)
    img.save(path, quality=92)


# ---------- video ----------
async def tts(text, path):
    await edge_tts.Communicate(text, VOICE, rate="+5%").save(path)


def run(cmd):
    subprocess.run(cmd, check=True)


def make_video(slides, folder):
    clips = []
    for i, text in enumerate(slides):
        img, aud, clip = (folder / f"s{i}.png", folder / f"s{i}.mp3", folder / f"c{i}.mp4")
        make_slide(text, img)
        asyncio.run(tts(text, str(aud)))
        run([
            "ffmpeg", "-y", "-loop", "1", "-framerate", "25", "-i", str(img),
            "-i", str(aud), "-af", "apad=pad_dur=0.4",
            "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p", "-r", "25",
            "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest", str(clip),
        ])
        clips.append(clip)
    lst = folder / "list.txt"
    lst.write_text("".join(f"file '{c}'\n" for c in clips), encoding="utf-8")
    out = folder / "news.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])
    return out


# ---------- upload ----------
def upload(yt, video, meta, sources):
    desc = meta["description"] + "\n\nSources: " + ", ".join(sources) + "\n\n" + HASHTAGS
    body = {
        "snippet": {
            "title": meta["title"][:100],
            "description": desc[:4900],
            "tags": ["Pakistan cricket", "cricket news", "PCB", "Pakistan cricket team"],
            "categoryId": "17",
        },
        "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(video), mimetype="video/mp4", resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]


def main():
    st = load_state()
    now = datetime.now(timezone.utc)
    recent = [t for t in st["uploads"] if datetime.fromisoformat(t) > now - timedelta(hours=24)]
    if len(recent) >= MAX_PER_DAY:
        print(f"Aaj ki limit ({MAX_PER_DAY}) poori ho gayi.")
        return

    news = [n for n in fetch_news() if n["link"] not in st["links"]][:6]
    if not news:
        print("Koi nayi khabar nahi mili.")
        return

    meta = ask_gemini([n["title"] for n in news])
    sources = sorted({n["source"] for n in news if n["source"]})

    with tempfile.TemporaryDirectory() as d:
        folder = pathlib.Path(d)
        video = make_video(meta["slides"], folder)
        thumb = folder / "thumb.jpg"
        make_thumbnail(meta["thumb_text"], thumb)
        yt = youtube_client()
        vid = upload(yt, video, meta, sources)
        print("Uploaded:", vid)
        try:
            yt.thumbnails().set(
                videoId=vid, media_body=MediaFileUpload(str(thumb), mimetype="image/jpeg")
            ).execute()
        except Exception as e:
            print("Thumbnail set nahi hua (channel verify karen):", e)

    st["links"] = (st["links"] + [n["link"] for n in news])[-300:]
    st["uploads"] = recent + [now.isoformat()]
    save_state(st)


if __name__ == "__main__":
    main()
