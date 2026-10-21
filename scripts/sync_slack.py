"""Sync #above-the-noise from Slack into docs/feed.json and docs/img/.

Runs in GitHub Actions (see .github/workflows/sync.yml). Needs:
  SLACK_BOT_TOKEN   bot token (xoxb-...) with channels:history and files:read
  SLACK_CHANNEL_ID  the channel id (C0B1T18SH0Q for #above-the-noise)
Optional:
  RESYNC_DAYS       how many recent days to re-read so edits/deletes show up (default 7)
  FULL_RESYNC=1     rebuild the whole feed from the channel history

How a Slack post becomes a card:
  first line   "#Tag: Headline" or "#Tag + #Tag: Headline" (backticks optional)
  link lines   "POST: <url>", "MOVIE SITE: <url>" -> labelled links; a bare <url> gets a label from its site
  other lines  become the short description under the headline
  first image attached becomes the card background
Messages without a #Tag: headline (chat, thread replies, joins) are skipped.
"""
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent / "docs"
FEED = ROOT / "feed.json"
IMG = ROOT / "img"
TZ = ZoneInfo("America/New_York")
TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
CHANNEL = os.environ.get("SLACK_CHANNEL_ID", "")
RESYNC_DAYS = float(os.environ.get("RESYNC_DAYS", "7"))
FULL = os.environ.get("FULL_RESYNC") == "1"
IMG_WIDTH = 760

HEAD = re.compile(r"^\s*((?:#[\w&'’-]+\s*(?:\+|,|&|and)?\s*)+):\s*(.+?)\s*$", re.S)
LINK = re.compile(r"<(https?://[^|>]+)(?:\|[^>]*)?>")
LABELED = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 &/'’().-]{0,40}?)\s*:\s*<https?://", re.S)


# ---------- Slack API ----------
def slack(method, **params):
    url = f"https://slack.com/api/{method}?" + urllib.parse.urlencode(params)
    for attempt in range(5):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(int(e.headers.get("Retry-After", "5")))
                continue
            raise
        if not data.get("ok"):
            sys.exit(f"Slack {method} failed: {data.get('error')}")
        return data
    sys.exit(f"Slack {method}: rate limited too many times")


def history(oldest):
    msgs, cursor = [], None
    while True:
        params = {"channel": CHANNEL, "limit": 200, "oldest": oldest}
        if cursor:
            params["cursor"] = cursor
        data = slack("conversations.history", **params)
        msgs += data.get("messages", [])
        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return msgs


def download(url):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        if "text/html" in r.headers.get("Content-Type", ""):
            raise RuntimeError("got a login page instead of the image (bot is missing files:read?)")
        return r.read()


# ---------- parsing ----------
def unescape(s):
    return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def label_for(url):
    u = urllib.parse.urlparse(url)
    host, path = u.netloc.lower().removeprefix("www."), u.path.lower()
    if "instagram.com" in host and "/reel" in path:
        return "Reel"
    if any(h in host for h in ("instagram.com", "lnkd.in", "linkedin.com", "tiktok.com", "x.com", "twitter.com", "threads.")):
        return "Post"
    if any(h in host for h in ("youtube.com", "youtu.be", "vimeo.com")):
        return "Video"
    return "Article"


def nice_label(raw):
    raw = re.sub(r"\s+", " ", raw.strip())
    return raw[:1].upper() + raw[1:].lower() if raw.isupper() else raw


def parse(text):
    """Return (tags, title, links, body) or None if the message is not a feed post."""
    text = unescape(text.replace("```", "\n")).strip()
    lines = [l for l in text.split("\n")]
    first_i = next((i for i, l in enumerate(lines) if l.strip()), None)
    if first_i is None:
        return None
    m = HEAD.match(lines[first_i])
    if not m:
        return None
    tags = re.findall(r"#([\w&'’-]+)", m.group(1))
    title = m.group(2).strip().rstrip()
    title = LINK.sub(lambda x: x.group(1), title)
    links, body = [], []
    for line in lines[first_i + 1:]:
        if not line.strip():
            continue
        urls = LINK.findall(line)
        if urls:
            lab = LABELED.match(line)
            for u in urls:
                links.append([nice_label(lab.group(1)) if lab else label_for(u), u])
            continue
        body.append(line.strip())
    return tags, title, links, " ".join(body) or None


def first_image(msg):
    for f in msg.get("files") or []:
        if (f.get("mimetype") or "").startswith("image/"):
            return f.get("url_private_download") or f.get("url_private")
    return None


def save_image(ts, url):
    from PIL import Image  # imported here so parse tests run without Pillow

    path = IMG / f"{ts}.jpg"
    if path.exists():
        return f"img/{path.name}"
    im = Image.open(io.BytesIO(download(url)))
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (29, 15, 20))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    im = im.convert("RGB")
    if im.width > IMG_WIDTH:
        im = im.resize((IMG_WIDTH, round(im.height * IMG_WIDTH / im.width)), Image.LANCZOS)
    im.save(path, "JPEG", quality=80, optimize=True, progressive=True)
    return f"img/{path.name}"


def to_post(msg):
    if msg.get("subtype") not in (None, "file_share", "thread_broadcast"):
        return None
    if msg.get("thread_ts") and msg.get("thread_ts") != msg.get("ts") and msg.get("subtype") != "thread_broadcast":
        return None
    parsed = parse(msg.get("text") or "")
    if not parsed:
        return None
    tags, title, links, body = parsed
    ts = msg["ts"]
    dt = datetime.fromtimestamp(float(ts), TZ)
    post = {"ts": ts, "date": dt.strftime("%Y-%m-%d"), "time": dt.strftime("%-I:%M %p"),
            "tags": tags, "title": title, "links": links}
    if body:
        post["body"] = body
    url = first_image(msg)
    if url:
        try:
            post["img"] = save_image(ts, url)
        except Exception as e:  # keep the post as a color card if the image fails
            print(f"  image skipped for {ts}: {e}")
    return post


# ---------- main ----------
def main():
    if not TOKEN or not CHANNEL:
        sys.exit("Set SLACK_BOT_TOKEN and SLACK_CHANNEL_ID")
    IMG.mkdir(parents=True, exist_ok=True)
    feed = json.loads(FEED.read_text()) if FEED.exists() and not FULL else {"posts": []}
    posts = {p["ts"]: p for p in feed.get("posts", [])}

    newest = max((float(t) for t in posts), default=0)
    oldest = 0 if FULL or not posts else min(newest, time.time() - RESYNC_DAYS * 86400)
    msgs = history(f"{oldest:.6f}")
    print(f"Read {len(msgs)} Slack messages since {datetime.fromtimestamp(oldest, TZ):%b %d %Y}")

    seen = set()
    for msg in msgs:
        p = to_post(msg)
        if p:
            seen.add(p["ts"])
            if p["ts"] not in posts:
                print(f"  + {p['date']} #{'/#'.join(p['tags'])}: {p['title'][:70]}")
            posts[p["ts"]] = p
    # posts deleted (or un-tagged) in Slack inside the re-read window drop off the feed
    for ts in [t for t in posts if float(t) > oldest and t not in seen]:
        print(f"  - removed {ts}: {posts[ts]['title'][:70]}")
        posts.pop(ts)

    ordered = sorted(posts.values(), key=lambda p: float(p["ts"]), reverse=True)
    old = [{k: v for k, v in p.items()} for p in feed.get("posts", [])]
    if ordered == old:
        print("No changes.")
        return
    out = {"updated": datetime.now(TZ).strftime("%b %-d, %-I:%M %p ET"), "channel": CHANNEL, "posts": ordered}
    FEED.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(f"Wrote {len(ordered)} posts to {FEED.relative_to(ROOT.parent)}")


if __name__ == "__main__":
    main()
