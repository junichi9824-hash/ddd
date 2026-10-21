"""AI活用・DX 朝の要約ダイジェスト生成・送信スクリプト。

国内のIT/ビジネスメディアから「AIを使った業務効率化・活用事例」と
「企業がAIを扱ううえでのリスク・権利・規制」の記事を集め、本文を読んだうえで
Claude Haiku が要約し、Gmail 経由でメール送信する。

コスト削減のため Message Batches API(50%割引)で処理し、
一定時間内に終わらなければ通常APIにフォールバックする。
"""

import json
import os
import re
import smtplib
import ssl
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path

import feedparser
import requests
import trafilatura
from anthropic import Anthropic

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
MODEL = "claude-haiku-5-5"
EFFORT = "low"  # 要約用途なので思考は最小限(=出力トークン節約)
MAX_TOKENS = 16000

MAX_CASES = 3  # 活用事例の最大件数
MAX_OTHERS = 3  # 「その他の注目記事」の最大件数
LOOKBACK_HOURS = 48  # 国内メディアは土日の更新が少ないため48時間
SEEN_URL_HISTORY_DAYS = 14

MAX_BODY_FETCH = 15  # 本文を取得する候補記事の上限
MAX_BODY_CHARS = 3000  # 1記事あたりAIに渡す本文の上限文字数
REQUEST_TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (compatible; ai-dx-digest/1.0)"

# Batch API の待ち時間上限。超えたらキャンセルして通常APIで処理する
BATCH_TIMEOUT_MINUTES = 60
BATCH_POLL_SECONDS = 30

BASE_DIR = Path(__file__).resolve().parent
TOPICS_LOG_PATH = BASE_DIR / "data" / "topics_log.json"

JST = timezone(timedelta(hours=9))

# (表示名, RSS URL, AIキーワードで絞り込むか)
# AI専門媒体は全件、総合媒体はAI関連キーワードを含む記事だけを候補にする
RSS_FEEDS = [
    ("ITmedia AI+", "https://rss.itmedia.co.jp/rss/2.0/aiplus.xml", False),
    ("ITmedia エンタープライズ", "https://rss.itmedia.co.jp/rss/2.0/enterprise.xml", True),
    ("ITmedia ビジネスオンライン", "https://rss.itmedia.co.jp/rss/2.0/business.xml", True),
    ("ITmedia NEWS セキュリティ", "https://rss.itmedia.co.jp/rss/2.0/news_security.xml", True),
    ("ZDNET Japan", "http://feed.japan.zdnet.com/rss/news/index.rdf", True),
]

# 英字キーワードは単語単位で判定する("MAIL" や "FAIL" の中の AI に反応しないように)
AI_KEYWORD_PATTERN = re.compile(
    r"(?<![A-Za-z])(AI|LLM|RAG|ChatGPT|Claude|Gemini|Copilot)(?![A-Za-z])"
    r"|ＡＩ|人工知能|生成AI|エージェント|機械学習"
)

# 本文取得の優先順位付けに使うキーワード
CASE_KEYWORDS = [
    "活用", "事例", "導入", "効率", "削減", "時短", "自動化", "業務", "現場", "DX", "生産性",
]
RISK_KEYWORDS = [
    "著作権", "個人情報", "漏えい", "漏洩", "規制", "ガイドライン", "法", "リスク",
    "セキュリティ", "訴訟", "ハルシネーション", "ガバナンス", "AI Act", "利用規約", "情報管理",
]

# リスク系ニュースがない日に解説する基礎テーマ(順番にローテーション)
RISK_THEMES = [
    "生成AIと著作権(学習利用・生成物の権利・他者の著作物の入力)",
    "個人情報・顧客情報を生成AIに入力する際の注意点",
    "社外秘・機密情報の漏えい対策(入力データの学習利用設定など)",
    "ハルシネーション(もっともらしい誤り)への対策とチェック体制",
    "社内AI利用ガイドラインの作り方と運用",
    "AIサービスの利用規約・契約で確認すべき点",
    "海外のAI規制(EU AI Actなど)が日本企業に与える影響",
    "プロンプトインジェクション等AI特有のセキュリティリスク",
]

GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
MAIL_TO = os.environ.get("MAIL_TO") or GMAIL_ADDRESS
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]


# ---------------------------------------------------------------------------
# topics_log.json の読み書き
# ---------------------------------------------------------------------------
def load_topics_log() -> dict:
    if not TOPICS_LOG_PATH.exists():
        return {"seen_urls": [], "risk_theme_index": 0}
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
# 候補記事の収集
# ---------------------------------------------------------------------------
def within_lookback(published_struct) -> bool:
    if published_struct is None:
        return False
    published = datetime(*published_struct[:6], tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    return published >= cutoff


def contains_any(text: str, keywords: list) -> bool:
    return any(k in text for k in keywords)


def clean_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub("<[^<]+?>", "", text or "")).strip()


def collect_rss_items() -> list:
    items = []
    for source, url, needs_ai_filter in RSS_FEEDS:
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] RSS取得失敗: {source} ({url}): {exc}")
            continue

        count = 0
        for entry in feed.entries:
            published_struct = entry.get("published_parsed") or entry.get("updated_parsed")
            if not within_lookback(published_struct):
                continue
            title = clean_html(entry.get("title", ""))
            summary = clean_html(entry.get("summary", ""))[:200]
            if needs_ai_filter and not AI_KEYWORD_PATTERN.search(title + " " + summary):
                continue
            items.append(
                {
                    "source": source,
                    "title": title,
                    "url": entry.get("link", "").strip(),
                    "summary": summary,
                }
            )
            count += 1
        print(f"[INFO] {source}: {len(feed.entries)}件中 {count}件を候補に追加")
    return items


def dedupe(items: list) -> list:
    seen, result = set(), []
    for item in items:
        key = item["url"]
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def priority(item: dict) -> int:
    text = item["title"] + item["summary"]
    score = 0
    if contains_any(text, CASE_KEYWORDS):
        score += 2
    if contains_any(text, RISK_KEYWORDS):
        score += 2
    if item["source"] == "ITmedia AI+":
        score += 1
    return score


def fetch_body(url: str) -> str:
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        text = trafilatura.extract(resp.content, include_comments=False, include_tables=False)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 本文取得失敗: {url}: {exc}")
        return ""
    return (text or "").strip()[:MAX_BODY_CHARS]


def attach_bodies(candidates: list) -> list:
    ranked = sorted(candidates, key=priority, reverse=True)
    for item in ranked[:MAX_BODY_FETCH]:
        item["body"] = fetch_body(item["url"])
    fetched = [c for c in ranked[:MAX_BODY_FETCH] if c.get("body")]
    print(f"[INFO] 本文取得: {len(fetched)}/{min(len(ranked), MAX_BODY_FETCH)}件")
    # 本文が取れた記事だけをAIに渡す(本文なしで「要約」させると内容を推測で埋めるため)
    return fetched


# ---------------------------------------------------------------------------
# Claude による選定・要約
# ---------------------------------------------------------------------------
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "cases": {
            "type": "array",
            "description": f"活用事例(最大{MAX_CASES}件。該当なしなら空配列)",
            "items": {
                "type": "object",
                "properties": {
                    "article_id": {"type": "integer"},
                    "headline": {"type": "string"},
                    "who": {"type": "string", "description": "企業・業種・規模"},
                    "target": {"type": "string", "description": "対象業務・課題"},
                    "method": {"type": "string", "description": "使ったAI・ツールと方法"},
                    "effect": {"type": "string", "description": "効果。記事に数字があれば必ず含める"},
                    "hint": {"type": "string", "description": "中小企業で試すならのヒント1文"},
                },
                "required": ["article_id", "headline", "who", "target", "method", "effect", "hint"],
                "additionalProperties": False,
            },
        },
        "risk": {
            "type": "object",
            "description": "リスク・権利・規制。article_idは記事が無ければ-1",
            "properties": {
                "article_id": {"type": "integer"},
                "headline": {"type": "string"},
                "summary": {"type": "string", "description": "何が起きた/何が論点か"},
                "watch_points": {"type": "string", "description": "企業が注意すべき点"},
                "actions": {"type": "string", "description": "DX担当が取るべき対策"},
            },
            "required": ["article_id", "headline", "summary", "watch_points", "actions"],
            "additionalProperties": False,
        },
        "others": {
            "type": "array",
            "description": f"その他の注目記事(最大{MAX_OTHERS}件)",
            "items": {
                "type": "object",
                "properties": {
                    "article_id": {"type": "integer"},
                    "one_line": {"type": "string"},
                },
                "required": ["article_id", "one_line"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["cases", "risk", "others"],
    "additionalProperties": False,
}


def build_prompt(articles: list, fallback_theme: str) -> str:
    blocks = []
    for i, a in enumerate(articles):
        blocks.append(
            f"<article id=\"{i}\">\n出典: {a['source']}\nタイトル: {a['title']}\n本文:\n{a['body']}\n</article>"
        )
    articles_block = "\n\n".join(blocks) if blocks else "(記事なし)"

    return f"""あなたは中小企業(建設コンサル・従業員35名程度)のDX担当者向けに、毎朝届くメールの編集者です。
読者は「AIを業務で使って時短・効率化したい」「AIを社内で使う際のリスクや権利を押さえておきたい」人です。
読了5分程度を目安に、絵文字を適度に使った親しみやすい文体で、要点を簡潔にまとめてください。

以下は直近に国内メディアで公開された記事です。

{articles_block}

# タスク
1. cases: AIを使った業務効率化・時短・活用の**具体的な事例**を最大{MAX_CASES}件選び、
   企業/業種、対象業務、方法、効果、中小企業で試すならのヒントを要約してください。
   製品発表だけの記事より、実際に導入・運用した事例を優先してください。
2. risk: 企業がAIを扱ううえでのリスク・権利・法規制・セキュリティに関する記事を1件選び、
   概要、企業が注意すべき点、取るべき対策を要約してください。
   該当する記事が無い場合は article_id を -1 とし、テーマ「{fallback_theme}」について
   一般的な基礎知識を解説してください。
3. others: 上で選ばなかった記事のうち、DX担当者が知っておくべきものを最大{MAX_OTHERS}件、1行で紹介してください。

# 厳守事項
- 記事本文に書かれていない数字・企業名・事実を補ったり推測で書いたりしないこと。
  本文から分からない項目は「記事内に記載なし」と書くこと。
- 法律・規制の解説は断定を避け、要点のみに留めること。
- 該当する記事がなければ無理に選ばず、空配列で構いません。
"""


def build_request_params(prompt: str) -> dict:
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
        },
    }


def extract_json(message) -> dict:
    text = "".join(block.text for block in message.content if block.type == "text")
    return json.loads(text)


def run_batch(client: Anthropic, params: dict):
    """Batch APIで実行。時間内に終わらなければ None を返す。"""
    batch = client.messages.batches.create(
        requests=[{"custom_id": "daily-digest", "params": params}]
    )
    batch_id = batch.id
    print(f"[INFO] Batch作成: {batch_id}")

    deadline = time.monotonic() + BATCH_TIMEOUT_MINUTES * 60
    while time.monotonic() < deadline:
        status = client.messages.batches.retrieve(batch_id).processing_status
        if status == "ended":
            break
        time.sleep(BATCH_POLL_SECONDS)
    else:
        print(f"[WARN] Batchが{BATCH_TIMEOUT_MINUTES}分以内に終わらなかったためキャンセル")
        try:
            client.messages.batches.cancel(batch_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] Batchキャンセル失敗: {exc}")
        return None

    for entry in client.messages.batches.results(batch_id):
        if entry.result.type == "succeeded":
            return entry.result.message
        print(f"[WARN] Batch結果が失敗: {entry.result.type}")
    return None


def call_claude(params: dict) -> dict:
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    message = None
    try:
        message = run_batch(client, params)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Batch処理でエラー: {exc}")

    if message is None:
        print("[INFO] 通常APIにフォールバック")
        message = client.messages.create(**params)

    usage = message.usage
    print(f"[INFO] トークン使用量: 入力{usage.input_tokens} / 出力{usage.output_tokens}")
    return extract_json(message)


# ---------------------------------------------------------------------------
# メール作成・送信
# ---------------------------------------------------------------------------
def article_ref(articles: list, article_id: int) -> str:
    if 0 <= article_id < len(articles):
        a = articles[article_id]
        return f"   🔗 {a['url']} ({a['source']})"
    return ""


def compose_email(result: dict, articles: list, today: str, fallback_theme: str) -> tuple:
    lines = [f"おはようございます!☀️ {today} のAI活用・DXダイジェストです。", ""]

    lines.append("📊 AI活用事例")
    cases = result.get("cases", [])[:MAX_CASES]
    if cases:
        for i, c in enumerate(cases, start=1):
            lines.append(f"{i}. {c['headline']}")
            lines.append(f"   🏢 誰が: {c['who']}")
            lines.append(f"   🎯 対象業務: {c['target']}")
            lines.append(f"   🛠 方法: {c['method']}")
            lines.append(f"   📈 効果: {c['effect']}")
            lines.append(f"   💡 試すなら: {c['hint']}")
            ref = article_ref(articles, c["article_id"])
            if ref:
                lines.append(ref)
            lines.append("")
    else:
        lines.append("本日は目立った活用事例の記事はありませんでした。")
        lines.append("")

    risk = result.get("risk") or {}
    if risk:
        has_article = 0 <= risk.get("article_id", -1) < len(articles)
        label = "ニュース" if has_article else f"基礎知識:{fallback_theme}"
        lines.append(f"⚖️ リスク・ルール({label})")
        lines.append(f"■ {risk['headline']}")
        lines.append(f"   📝 概要: {risk['summary']}")
        lines.append(f"   ⚠️ 注意点: {risk['watch_points']}")
        lines.append(f"   ✅ 対策: {risk['actions']}")
        if has_article:
            lines.append(article_ref(articles, risk["article_id"]))
        else:
            lines.append("   ※AIによる一般的な解説です。社内ルール化の際は公的資料・専門家で確認してください。")
        lines.append("")

    others = result.get("others", [])[:MAX_OTHERS]
    if others:
        lines.append("📰 その他の注目記事")
        for o in others:
            lines.append(f"・{o['one_line']}")
            ref = article_ref(articles, o["article_id"])
            if ref:
                lines.append(ref)
        lines.append("")

    lines.append("---")
    lines.append("記事本文をもとにAI(Claude Haiku)が要約しています。正確な内容はリンク先で確認してください。")

    subject = f"🤖 AI活用・DXダイジェスト {today}"
    return subject, "\n".join(lines)


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
    theme_index = log.get("risk_theme_index", 0)
    fallback_theme = RISK_THEMES[theme_index % len(RISK_THEMES)]

    candidates = dedupe(collect_rss_items())
    candidates = [c for c in candidates if c["url"] not in seen_urls]
    print(f"[INFO] 候補記事件数(既出除外後): {len(candidates)}")

    articles = attach_bodies(candidates)
    params = build_request_params(build_prompt(articles, fallback_theme))
    result = call_claude(params)

    today = datetime.now(JST).strftime("%Y-%m-%d")
    subject, body = compose_email(result, articles, today, fallback_theme)
    send_email(subject, body)
    print("[INFO] メール送信完了")

    # 取り上げた記事を既出として記録
    used_ids = [c["article_id"] for c in result.get("cases", [])[:MAX_CASES]]
    used_ids += [o["article_id"] for o in result.get("others", [])[:MAX_OTHERS]]
    risk_id = (result.get("risk") or {}).get("article_id", -1)
    used_ids.append(risk_id)

    now_iso = datetime.now(timezone.utc).isoformat()
    new_seen = list(log.get("seen_urls", []))
    for i in used_ids:
        if 0 <= i < len(articles):
            new_seen.append({"url": articles[i]["url"], "date": now_iso})

    new_log = {
        "seen_urls": prune_seen_urls(new_seen),
        # 基礎知識テーマを使った日だけ次のテーマに進める
        "risk_theme_index": theme_index + (1 if not 0 <= risk_id < len(articles) else 0),
    }
    save_topics_log(new_log)
    print("[INFO] topics_log.json 更新完了")


def notify_failure(error_text: str) -> None:
    today = datetime.now(JST).strftime("%Y-%m-%d")
    body = (
        "本日のダイジェスト生成に失敗しました。\n"
        "GitHub Actions のログを確認してください。\n\n"
        f"{error_text[-2000:]}"
    )
    try:
        send_email(f"⚠️ AI活用・DXダイジェスト 生成失敗 {today}", body)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] 失敗通知メールも送信できませんでした: {exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        error_text = traceback.format_exc()
        print(error_text)
        notify_failure(error_text)
        sys.exit(1)
