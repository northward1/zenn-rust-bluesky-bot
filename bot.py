import json
import os
import time
from pathlib import Path

import feedparser
from atproto import Client

RSS_URL = "https://zenn.dev/topics/rust/feed"
STATE_FILE = Path("data/posted_ids.json")
BLUESKY_MAX_GRAPHEMES = 300


def load_state() -> set[str]:
    if not STATE_FILE.exists():
        return set()
    with STATE_FILE.open() as f:
        data = json.load(f)
    return set(data.get("posted_ids", []))


def save_state(posted_ids: set[str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w") as f:
        json.dump({"posted_ids": sorted(posted_ids)}, f, ensure_ascii=False, indent=2)


def fetch_new_entries(posted_ids: set[str]) -> list[dict]:
    feed = feedparser.parse(RSS_URL)
    new_entries = [e for e in feed.entries if e.id not in posted_ids]
    # 古い順にソート
    new_entries.sort(key=lambda e: e.get("published_parsed") or 0)
    return new_entries


def build_post_text(title: str, url: str) -> str:
    text = f"{title}\n{url}"
    # 300グラフィムを超えないようにタイトルを切り詰める
    graphemes = list(text)
    if len(graphemes) > BLUESKY_MAX_GRAPHEMES:
        max_title_len = BLUESKY_MAX_GRAPHEMES - len(url) - 4  # "\n..." の分
        title = title[:max_title_len] + "..."
        text = f"{title}\n{url}"
    return text


def build_facets(text: str, url: str) -> list:
    text_bytes = text.encode("utf-8")
    url_bytes = url.encode("utf-8")
    byte_start = text_bytes.find(url_bytes)
    if byte_start == -1:
        return []
    byte_end = byte_start + len(url_bytes)
    return [
        {
            "$type": "app.bsky.richtext.facet",
            "index": {
                "$type": "app.bsky.richtext.facet#byteSlice",
                "byteStart": byte_start,
                "byteEnd": byte_end,
            },
            "features": [
                {
                    "$type": "app.bsky.richtext.facet#link",
                    "uri": url,
                }
            ],
        }
    ]


def post_to_bluesky(client: Client, entry: dict) -> None:
    title = entry.get("title", "(no title)")
    url = entry.get("link", "")
    text = build_post_text(title, url)
    facets = build_facets(text, url)

    client.send_post(
        text=text,
        facets=facets if facets else None,
        langs=["ja"],
    )
    print(f"Posted: {title}")


def main() -> None:
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    posted_ids = load_state()
    new_entries = fetch_new_entries(posted_ids)

    if not new_entries:
        print("No new articles.")
        return

    print(f"Found {len(new_entries)} new article(s).")

    if dry_run:
        print("\n--- DRY RUN: 以下の内容が投稿されます ---")
        for entry in new_entries:
            title = entry.get("title", "(no title)")
            url = entry.get("link", "")
            text = build_post_text(title, url)
            print(f"\n{text}\n{'-' * 40}")
        return

    identifier = os.environ["BLUESKY_IDENTIFIER"]
    app_password = os.environ["BLUESKY_APP_PASSWORD"]
    client = Client()
    client.login(identifier, app_password)

    for entry in new_entries:
        try:
            post_to_bluesky(client, entry)
            posted_ids.add(entry.id)
            save_state(posted_ids)
        except Exception as e:
            print(f"Failed to post '{entry.get('title')}': {e}")
        time.sleep(2)


if __name__ == "__main__":
    main()
