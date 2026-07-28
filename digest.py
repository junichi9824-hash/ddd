"""AI動向 朝の要約ダイジェスト生成・送信スクリプト。

前日24時間分のAI関連ニュースを収集し、Claude Haikuで最大5件に厳選して要約、
加えて「今日の一言」（AI用語解説）を1つ生成し、Gmail経由でメール送信する。
"""

import json
import os
import re
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from xml.etree import ElementTree

import feedparser
import requests
from anthropic import Anthropic

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
MODEL = "claude-haiku-4-5-20251001"
MAX_PICKS = 5
LOOKBACK_HOURS = 24
SEEN_URL_HISTORY_DAYS = 14
WORD_HISTORY_MAX = 200
MAX_CANDIDATES_PER_SOURCE = 30
REQUEST_TIMEOUT = 15

BASE_DIR = Path(__file__).resolve().parent
TOPICS_LOG_PATH = BASE_DIR / "data" / "topics_log.json"

JST = timezone(timedelta(hours=9))

RSS_FEEDS = [
    ("OpenAI", "https://openai.com/news/rss.xml"),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml"),
    ("arXiv cs.CL", "http://export.arxiv.org/rss/cs.CL"),
    ("arXiv cs.AI", "http://export.arxiv.org/rss/cs.AI"),
]

# Anthropicは公式RSSを提供していないため、sitemap.xmlのlastmodで新着/newsを検出する
ANTHROPIC_SITEMAP_URL = "https://www.anthropic.com/sitemap.xml"

HN_ALGOLIA_URL = "https://hn.algolia.com/api/v1/search_by_date"
HN_QUERIES = ["AI", "LLM", "Claude", "GPT", "Gemini"]
HN_MIN_POINTS = 20
HN_MAX_ITEMS = 10

WORD_CATEGORIES = [
    "モデルアーキテクチャ",
    "学習・ファインチューニング手法",
    "評価・ベンチマーク",
    "推論最適化・インフラ",
    "エージェント技術",
    "安全性・アライメント",
    "ビジネス活用・プロダクト",
]

GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
MAIL_TO = os.environ.get("MAIL_TO", GMAIL_ADDRESS)
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]


# ---------------------------------------------------------------------------
# topics_log.json の読み書き
# ---------------------------------------------------------------------------
def load_topics_log() -> dict:
    if not TOPICS_LOG_PATH.exists():
        return {"category_index": 0, "seen_urls": [], "word_history": []}
    with open(TOPICS_LOG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_topics_log(log: dict) -> None:
    TOPICS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TOPICS_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def prune_seen_urls(seen_urls: list) -> list:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEEN_URL_HISTORY_DAYS)).isoformat()
    return [entry for entry in seen_urls if entry.get("date", "") >= cutoff]


# ---------------------------------------------------------------------------
# 候補ニュースの収集
# ---------------------------------------------------------------------------
def within_lookback(published_struct) -> bool:
    if published_struct is None:
        return False
    published = datetime(*published_struct[:6], tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    return published >= cutoff


def collect_rss_items() -> list:
    items = []
    for source, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] RSS取得失敗: {source} ({url}): {exc}")
            continue

        count = 0
        for entry in feed.entries:
            if count >= MAX_CANDIDATES_PER_SOURCE:
                break
            published_struct = entry.get("published_parsed") or entry.get("updated_parsed")
            if not within_lookback(published_struct):
                continue
            items.append(
                {
                    "source": source,
                    "title": entry.get("title", "").strip(),
                    "url": entry.get("link", "").strip(),
                    "summary": re.sub("<[^<]+?>", "", entry.get("summary", ""))[:300].strip(),
                }
            )
            count += 1
    return items


def extract_title_from_html(html: str) -> str:
    match = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html
    )
    if not match:
        match = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
    if not match:
        return ""
    title = match.group(1).strip()
    title = re.sub(r"\s*[\|\-]\s*Anthropic\s*$", "", title)
    return title


def collect_anthropic_items() -> list:
    items = []
    try:
        resp = requests.get(ANTHROPIC_SITEMAP_URL, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Anthropic sitemap取得失敗: {exc}")
        return items

    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    for url_el in root.findall("sm:url", ns):
        loc_el = url_el.find("sm:loc", ns)
        lastmod_el = url_el.find("sm:lastmod", ns)
        if loc_el is None or lastmod_el is None:
            continue
        loc = loc_el.text.strip()
        if "/news/" not in loc:
            continue
        try:
            lastmod = datetime.fromisoformat(lastmod_el.text.strip().replace("Z", "+00:00"))
        except ValueError:
            continue
        if lastmod < cutoff:
            continue

        title = ""
        try:
            page_resp = requests.get(loc, timeout=REQUEST_TIMEOUT)
            page_resp.raise_for_status()
            title = extract_title_from_html(page_resp.text)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] Anthropicページ取得失敗: {loc}: {exc}")

        items.append(
            {
                "source": "Anthropic",
                "title": title or loc.rstrip("/").rsplit("/", 1)[-1].replace("-", " "),
                "url": loc,
                "summary": "",
            }
        )
    return items


def collect_hn_items() -> list:
    cutoff_ts = int((datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).timestamp())
    seen_ids = set()
    candidates = []

    for query in HN_QUERIES:
        try:
            resp = requests.get(
                HN_ALGOLIA_URL,
                params={
                    "query": query,
                    "tags": "story",
                    "numericFilters": f"created_at_i>{cutoff_ts},points>={HN_MIN_POINTS}",
                },
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] HN Algolia取得失敗: {query}: {exc}")
            continue

        for hit in data.get("hits", []):
            object_id = hit.get("objectID")
            if object_id in seen_ids:
                continue
            seen_ids.add(object_id)
            candidates.append(
                {
                    "source": "Hacker News",
                    "title": hit.get("title") or "",
                    "url": hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}",
                    "summary": "",
                    "points": hit.get("points", 0),
                }
            )

    candidates.sort(key=lambda x: x.get("points", 0), reverse=True)
    return candidates[:HN_MAX_ITEMS]


# ---------------------------------------------------------------------------
# Claude Haiku による厳選・要約 + 今日の一言
# ---------------------------------------------------------------------------
def build_prompt(candidates: list, category: str, avoid_urls: list, avoid_words: list) -> str:
    candidate_lines = []
    for c in candidates:
        line = f"- [{c['source']}] {c['title']} ({c['url']})"
        if c.get("summary"):
            line += f" — {c['summary']}"
        candidate_lines.append(line)
    candidates_block = "\n".join(candidate_lines) if candidate_lines else "(候補なし)"

    avoid_words_block = "、".join(avoid_words[-30:]) if avoid_words else "(なし)"

    return f"""あなたはAIエンジニア向けに毎朝配信するニュースダイジェストの編集者です。
以下は過去24時間に収集されたAI関連の候補記事一覧です。

{candidates_block}

# タスク
1. 上記候補から、重要度・速報性の高いものを最大{MAX_PICKS}件選んでください。候補が少ない/重要なものがない場合は無理に{MAX_PICKS}件選ばず、0件でも構いません。
2. 選んだ各記事について、日本語で1〜2行の簡潔な要約を書いてください（深掘りはせず、何が起きたかが分かれば十分です）。
3. 「今日の一言」として、カテゴリ「{category}」に関するAI用語・基礎知識を1つ選び、中級者（実務でAIを使うがアルゴリズムの専門家ではない層）向けに3〜4文で解説してください。
   ただし、以下の用語は直近で既に解説済みのため避けてください: {avoid_words_block}

# 出力形式
必ず以下のJSON形式のみを出力してください（前後に説明文やMarkdownのコードフェンスを付けないこと）:
{{
  "picks": [
    {{"title": "記事タイトル", "summary": "1〜2行の要約", "url": "URL", "source": "出典名"}}
  ],
  "word_of_day": {{"term": "用語", "explanation": "解説文"}}
}}
"""


def parse_json_response(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def call_claude(candidates: list, category: str, avoid_urls: list, avoid_words: list) -> dict:
    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = build_prompt(candidates, category, avoid_urls, avoid_words)
    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    return parse_json_response(text)


# ---------------------------------------------------------------------------
# メール作成・送信
# ---------------------------------------------------------------------------
def compose_email(result: dict, today: str) -> tuple:
    picks = result.get("picks", [])
    word = result.get("word_of_day", {})

    lines = [f"おはようございます。{today} のAI動向ダイジェストです。", ""]

    lines.append("【注目ニュース】")
    if picks:
        for i, pick in enumerate(picks, start=1):
            lines.append(f"{i}. {pick.get('title', '')}")
            lines.append(f"   {pick.get('summary', '')}")
            lines.append(f"   出典: {pick.get('url', '')} ({pick.get('source', '')})")
    else:
        lines.append("本日は目立った大きな動きはありませんでした。")
    lines.append("")

    lines.append("【今日の一言】")
    if word:
        lines.append(f"■ {word.get('term', '')}")
        lines.append(word.get("explanation", ""))
    lines.append("")
    lines.append("---")
    lines.append("本メールはGitHub Actionsにより自動生成されています。")

    subject = f"AI動向ダイジェスト {today}"
    body = "\n".join(lines)
    return subject, body


def send_email(subject: str, body: str) -> None:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = MAIL_TO

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, [MAIL_TO], msg.as_string())


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------
def main() -> None:
    log = load_topics_log()
    seen_urls = {entry["url"] for entry in log.get("seen_urls", [])}
    word_history = log.get("word_history", [])
    category_index = log.get("category_index", 0)
    category = WORD_CATEGORIES[category_index % len(WORD_CATEGORIES)]

    candidates = collect_rss_items() + collect_anthropic_items() + collect_hn_items()
    candidates = [c for c in candidates if c["url"] and c["url"] not in seen_urls]

    print(f"[INFO] 候補記事件数: {len(candidates)}")

    result = call_claude(candidates, category, list(seen_urls), word_history)

    today = datetime.now(JST).strftime("%Y-%m-%d")
    subject, body = compose_email(result, today)
    send_email(subject, body)
    print("[INFO] メール送信完了")

    new_seen = list(log.get("seen_urls", []))
    now_iso = datetime.now(timezone.utc).isoformat()
    for pick in result.get("picks", []):
        if pick.get("url"):
            new_seen.append({"url": pick["url"], "date": now_iso})
    log["seen_urls"] = prune_seen_urls(new_seen)

    word = result.get("word_of_day", {})
    if word.get("term"):
        word_history.append(word["term"])
    log["word_history"] = word_history[-WORD_HISTORY_MAX:]
    log["category_index"] = (category_index + 1) % len(WORD_CATEGORIES)

    save_topics_log(log)
    print("[INFO] topics_log.json 更新完了")


if __name__ == "__main__":
    main()
