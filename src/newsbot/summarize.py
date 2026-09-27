"""記事に日本語のタイトル・要約を付ける。

llm.provider で使う AI を選ぶ。
  gemini: Gemini API（無料枠あり。GEMINI_API_KEY が必要）
  claude: Claude API（有料。ANTHROPIC_API_KEY が必要）
  none:   翻訳しない（原文のまま）
どれも失敗したときは原文のまま返し、メールは必ず送れるようにする。
"""

from __future__ import annotations

import json
import logging
import os

import requests

log = logging.getLogger(__name__)

SYSTEM = """\
あなたはニュースダイジェストの編集者です。渡された記事リスト（JSON）の各記事について、
日本語のタイトルと、1〜2文の日本語要約を書いてください。

- 与えられたタイトルと概要に書かれている事実だけを使う。推測で補わない
- 日本語の記事はタイトルを原文のまま残し、要約だけ書く
- 概要が空で内容がわからないときは summary_ja を空文字にする
- 固有名詞は一般的な日本語表記があればそれを使い、なければ原語のまま
- highlights には、全体の中で特に重要な出来事を最大 5 件、各 1 文で挙げる
"""

FORMAT_HINT = """
次の形の JSON だけを出力してください:
{"items": [{"id": "...", "title_ja": "...", "summary_ja": "..."}], "highlights": ["..."]}
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title_ja": {"type": "string"},
                    "summary_ja": {"type": "string"},
                },
                "required": ["id", "title_ja", "summary_ja"],
                "additionalProperties": False,
            },
        },
        "highlights": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["items", "highlights"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------- Gemini

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _call_gemini(payload: list[dict], cfg: dict) -> dict:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY がありません")
    resp = requests.post(
        GEMINI_URL.format(model=cfg.get("gemini_model", "gemini-3.1-flash-lite")),
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": SYSTEM + FORMAT_HINT}]},
            "contents": [{"role": "user", "parts": [{"text": json.dumps(payload, ensure_ascii=False)}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
        },
        timeout=120,
    )
    if resp.status_code == 429:
        raise RuntimeError("Gemini の無料枠の上限に達しました")
    if not resp.ok:
        raise RuntimeError(f"Gemini API {resp.status_code}: {resp.text[:300]}")
    parts = resp.json()["candidates"][0]["content"]["parts"]
    return json.loads("".join(p.get("text", "") for p in parts))


# ---------------------------------------------------------------- Claude

def _call_claude(payload: list[dict], cfg: dict) -> dict:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY がありません")
    import anthropic

    resp = anthropic.Anthropic().messages.create(
        model=cfg.get("model", "claude-opus-5"),
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        output_config={
            "effort": cfg.get("effort", "low"),
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
        # 安全分類器に断られたときは推奨モデルでサーバー側が再実行する
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
        extra_body={"fallbacks": "default"},
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"refusal: {resp.stop_details}")
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


PROVIDERS = {"gemini": (_call_gemini, 40, 600), "claude": (_call_claude, 40, 600)}
#             プロバイダ: (関数, 1 回に送る記事数, 概要の最大文字数)


# ---------------------------------------------------------------- 入口

def translate(items: list[dict], llm_cfg: dict) -> tuple[list[dict], list[str]]:
    """items に title_ja / summary_ja を付けて返す。第 2 要素はハイライト。"""
    if not items:
        return [], []
    provider = llm_cfg.get("provider", "gemini") if llm_cfg.get("enabled", True) else "none"
    if provider not in PROVIDERS:
        return [{**it, "title_ja": it["title"], "summary_ja": it["summary"][:200]} for it in items], []
    call, chunk_size, max_chars = PROVIDERS[provider]

    by_id: dict[str, dict] = {}
    highlights: list[str] = []
    for start in range(0, len(items), chunk_size):
        chunk = items[start:start + chunk_size]
        payload = [
            {"id": it["id"][:12], "source": it["feed_name"], "title": it["title"], "summary": it["summary"][:max_chars]}
            for it in chunk
        ]
        try:
            data = call(payload, llm_cfg)
            for r in data.get("items", []):
                by_id[str(r.get("id"))] = r
            highlights += data.get("highlights", [])
        except Exception as e:  # 失敗してもメールは原文で送る
            log.warning("要約に失敗したので原文を使います（%s）: %s", provider, e)

    out = []
    for it in items:
        r = by_id.get(it["id"][:12])
        if r:
            out.append({**it, "title_ja": r.get("title_ja") or it["title"], "summary_ja": r.get("summary_ja", "")})
        else:
            out.append({**it, "title_ja": it["title"], "summary_ja": it["summary"][:200]})
    return out, highlights[:5]
