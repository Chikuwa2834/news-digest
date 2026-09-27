"""設定ファイル（config.yaml / feeds.yaml / keywords.yaml）の読み書き。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

# 設定ファイルの置き場所。環境変数 NEWSBOT_HOME で上書きできる。
HOME = Path(os.environ.get("NEWSBOT_HOME", Path(__file__).resolve().parents[2]))
CONFIG_PATH = HOME / "config.yaml"
FEEDS_PATH = HOME / "feeds.yaml"
KEYWORDS_PATH = HOME / "keywords.yaml"
DB_PATH = HOME / "data" / "state.db"
LOG_PATH = HOME / "data" / "newsbot.log"

load_dotenv(HOME / ".env")


@dataclass
class Feed:
    name: str
    url: str
    enabled: bool = True
    category: str = ""


@dataclass
class Keyword:
    name: str
    words: list[str]
    exclude: list[str] = field(default_factory=list)
    enabled: bool = True

    def matches(self, text: str) -> str | None:
        """一致した語を返す（大文字小文字は区別しない）。除外語を含めば None。"""
        low = text.lower()
        if any(x.lower() in low for x in self.exclude):
            return None
        for w in self.words:
            if w.lower() in low:
                return w
        return None


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_yaml(path: Path, data: dict, header: str) -> None:
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000, default_flow_style=None)
    path.write_text(header + body, encoding="utf-8")


def load_config() -> dict:
    cfg = _read_yaml(CONFIG_PATH)
    cfg.setdefault("mail", {})
    cfg.setdefault("digest", {})
    cfg.setdefault("alert", {})
    cfg.setdefault("llm", {})
    return cfg


# ---------------------------------------------------------------- feeds

FEEDS_HEADER = """\
# 収集対象のニュースサイト（RSS/Atom フィード）
# 直接編集しても、`newsbot feed add <サイトURL>` で追加してもよい。
#   name:     メールに表示するソース名
#   url:      フィードの URL
#   category: 任意。ダイジェストの見出しに使う
#   enabled:  false にすると収集を止める（削除せずに休止できる）
"""


def load_feeds() -> list[Feed]:
    data = _read_yaml(FEEDS_PATH)
    return [Feed(**f) for f in data.get("feeds", [])]


def save_feeds(feeds: list[Feed]) -> None:
    items = []
    for f in feeds:
        d = {"name": f.name, "url": f.url}
        if f.category:
            d["category"] = f.category
        d["enabled"] = f.enabled
        items.append(d)
    _write_yaml(FEEDS_PATH, {"feeds": items}, FEEDS_HEADER)


# ---------------------------------------------------------------- keywords

KEYWORDS_HEADER = """\
# リアルタイム通知のキーワード
# 新着記事のタイトルか概要に words のどれかが含まれると、すぐにメールが届く。
# 英語記事も拾いたいときは英語の表記も words に並べる。
#   name:    通知メールに表示する名前
#   words:   一致させる語（大文字小文字は区別しない、部分一致）
#   exclude: これを含む記事は通知しない（任意）
#   enabled: false で一時停止
"""


def load_keywords() -> list[Keyword]:
    data = _read_yaml(KEYWORDS_PATH)
    out = []
    for k in data.get("keywords", []):
        if isinstance(k, str):
            out.append(Keyword(name=k, words=[k]))
        else:
            k.setdefault("words", [k["name"]])
            out.append(Keyword(**k))
    return out


def save_keywords(keywords: list[Keyword]) -> None:
    items = []
    for k in keywords:
        d = {"name": k.name, "words": k.words}
        if k.exclude:
            d["exclude"] = k.exclude
        d["enabled"] = k.enabled
        items.append(d)
    _write_yaml(KEYWORDS_PATH, {"keywords": items}, KEYWORDS_HEADER)
