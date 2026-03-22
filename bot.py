import json
import os
import time
from pathlib import Path

import feedparser
import httpx
from atproto import Client, models
from bs4 import BeautifulSoup

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


def build_post_text(title: str) -> str:
    # サムネイル付きリンクカードを使う場合、本文はタイトルのみ（URLは埋め込みに含まれる）
    graphemes = list(title)
    if len(graphemes) > BLUESKY_MAX_GRAPHEMES:
        title = title[: BLUESKY_MAX_GRAPHEMES - 3] + "..."
    return title


def fetch_ogp(url: str) -> dict:
    """記事URLからOGPメタデータを取得する。失敗した場合は空dictを返す。"""
    try:
        resp = httpx.get(url, timeout=10, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except Exception as e:
        print(f"OGP fetch failed for {url}: {e}")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")

    def og(prop: str) -> str:
        tag = soup.find("meta", property=f"og:{prop}") or soup.find("meta", attrs={"name": f"og:{prop}"})
        return tag["content"] if tag and tag.get("content") else ""

    return {
        "title": og("title") or soup.title.string if soup.title else "",
        "description": og("description"),
        "image_url": og("image"),
    }


def upload_image(client: Client, image_url: str):
    """OG画像をダウンロードしてBlueSkyにアップロードする。失敗した場合はNoneを返す。"""
    if not image_url:
        return None
    try:
        resp = httpx.get(image_url, timeout=10, follow_redirects=True)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
        blob = client.upload_blob(resp.content)
        return blob.blob
    except Exception as e:
        print(f"Image upload failed: {e}")
        return None


def build_embed(client: Client, url: str) -> models.AppBskyEmbedExternal.Main | None:
    """リンクカード用のembedを構築する。OGP取得に失敗した場合はNoneを返す。"""
    ogp = fetch_ogp(url)
    if not ogp:
        return None

    thumb = upload_image(client, ogp.get("image_url", ""))

    return models.AppBskyEmbedExternal.Main(
        external=models.AppBskyEmbedExternal.External(
            uri=url,
            title=ogp.get("title", ""),
            description=ogp.get("description", ""),
            thumb=thumb,
        )
    )


def post_to_bluesky(client: Client, entry: dict) -> None:
    title = entry.get("title", "(no title)")
    url = entry.get("link", "")
    text = build_post_text(title)
    embed = build_embed(client, url)

    client.send_post(
        text=text,
        embed=embed,
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
            text = build_post_text(title)
            print(f"\n{text}\n{url}\n{'-' * 40}")
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
