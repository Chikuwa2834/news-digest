"""ニュースの自動収集と日本語ダイジェスト配信。"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import config as C
from . import fetch, mailer, store, summarize

log = logging.getLogger("newsbot")


# ================================================================ 収集と通知

def poll(con, cfg) -> None:
    """全フィードを取得して新着を DB に入れ、キーワードに一致したものをすぐ通知する。"""
    feeds = [f for f in C.load_feeds() if f.enabled]
    keywords = [k for k in C.load_keywords() if k.enabled]
    alert_cfg = cfg["alert"]
    max_age = timedelta(hours=alert_cfg.get("max_age_hours", 6))
    now = store.now_utc()

    hits: list[tuple[dict, str, str]] = []
    for feed in feeds:
        first_time = not store.feed_known(con, feed.url)
        try:
            items = fetch.fetch_feed(feed)
        except Exception as e:
            log.warning("取得失敗 %s: %s", feed.name, e)
            continue
        new = store.insert_new(con, items)
        log.info("%s: 新着 %d 件", feed.name, len(new))
        if first_time:
            # 追加したばかりのフィードは既存記事で通知を浴びせない
            store.mark(con, "alerted", [it["id"] for it in new])
            continue
        for it in new:
            if it["published"] and now - datetime.fromisoformat(it["published"]) > max_age:
                continue
            text = f'{it["title"]}\n{it["summary"]}'
            for kw in keywords:
                word = kw.matches(text)
                if word:
                    hits.append((it, kw.name, word))
                    break

    if hits and alert_cfg.get("enabled", True):
        send_alert(con, cfg, hits)


def send_alert(con, cfg, hits) -> None:
    translated, _ = summarize.translate([h[0] for h in hits], cfg["llm"])
    hits = [(t, kw, w) for t, (_, kw, w) in zip(translated, hits)]
    prefix = cfg["mail"].get("alert_prefix", "[速報]")
    if len(hits) == 1:
        it, kw, _ = hits[0]
        subject = f"{prefix} {kw}: {it['title_ja']}"
    else:
        names = list(dict.fromkeys(kw for _, kw, _ in hits))
        subject = f"{prefix} {len(hits)} 件 — {', '.join(names)}"
    mailer.send(subject, mailer.render_alert(hits), cfg["mail"].get("to", ""))
    store.mark(con, "alerted", [it["id"] for it, _, _ in hits])
    log.info("速報を送信: %s", subject)


# ================================================================ ダイジェスト

def latest_slot(times: list[str], now: datetime) -> datetime | None:
    """now 以前で最も新しい配信時刻（ローカル時刻）。"""
    slots = []
    for day in (now.date() - timedelta(days=1), now.date()):
        for t in times:
            h, m = map(int, t.split(":"))
            slots.append(datetime(day.year, day.month, day.day, h, m).astimezone())
    past = [s for s in slots if s <= now]
    return max(past) if past else None


def digest(con, cfg, dry_run: bool = False) -> int:
    dcfg = cfg["digest"]
    rows = store.pending_digest(con, dcfg.get("lookback_hours", 36))
    per_feed = dcfg.get("max_items_per_feed", 15)
    items, count = [], {}
    for r in rows:
        count[r["feed_url"]] = count.get(r["feed_url"], 0) + 1
        if count[r["feed_url"]] <= per_feed:
            items.append(dict(r))
    if not items:
        log.info("ダイジェスト: 新着なし")
        store.mark(con, "digested", [r["id"] for r in rows])
        return 0

    translated, highlights = summarize.translate(items, cfg["llm"])
    body = mailer.render_digest(translated, highlights)
    subject = f'{cfg["mail"].get("digest_prefix", "[ニュース]")} {datetime.now():%m/%d %H:%M} のダイジェスト（{len(items)} 件）'
    if dry_run:
        out = C.HOME / "data" / "preview.html"
        out.write_text(body, encoding="utf-8")
        print(f"送信せずに保存しました: {out}")
        return len(items)
    mailer.send(subject, body, cfg["mail"].get("to", ""))
    store.mark(con, "digested", [r["id"] for r in rows])
    log.info("ダイジェストを送信: %d 件", len(items))
    return len(items)


def run(con, cfg) -> None:
    """スケジューラから定期実行する入口。収集・速報に加え、配信時刻を過ぎていればダイジェストを送る。"""
    poll(con, cfg)
    slot = latest_slot(cfg["digest"].get("times", ["07:00", "19:00"]), datetime.now().astimezone())
    if slot is None:
        return
    last = store.get_meta(con, "last_digest_slot")
    if last is None:
        store.set_meta(con, "last_digest_slot", slot.isoformat())  # 初回は次の配信時刻から
    elif datetime.fromisoformat(last) < slot:
        digest(con, cfg)
        store.set_meta(con, "last_digest_slot", slot.isoformat())
    store.prune(con)


# ================================================================ フィード・キーワード管理

def cmd_feed(args) -> None:
    feeds = C.load_feeds()
    if args.action == "list":
        for i, f in enumerate(feeds, 1):
            mark = "✓" if f.enabled else "–"
            cat = f" [{f.category}]" if f.category else ""
            print(f"{i:>2}. {mark} {f.name}{cat}\n      {f.url}")
        return
    if args.action == "add":
        found = fetch.discover(args.target)
        if not found:
            sys.exit(f"フィードが見つかりませんでした: {args.target}")
        if len(found) > 1 and args.pick is None:
            print("複数のフィードが見つかりました。--pick 番号 で選んでください:")
            for i, (u, t) in enumerate(found, 1):
                print(f"  {i}. {t}  {u}")
            return
        url, title = found[(args.pick or 1) - 1]
        if any(f.url == url for f in feeds):
            sys.exit(f"登録済みです: {url}")
        feeds.append(C.Feed(name=args.name or title or url, url=url, category=args.category or ""))
        C.save_feeds(feeds)
        print(f"追加しました: {feeds[-1].name}  {url}")
        return
    f = _pick(feeds, args.target, lambda f: (f.name, f.url))
    if args.action == "remove":
        feeds.remove(f)
        print(f"削除しました: {f.name}")
    elif args.action in ("enable", "disable"):
        f.enabled = args.action == "enable"
        print(f"{'再開' if f.enabled else '休止'}しました: {f.name}")
    elif args.action == "test":
        items = fetch.fetch_feed(f, max_items=5)
        for it in items:
            print(f"- {it['title']}\n  {it['link']}")
        return
    C.save_feeds(feeds)


def cmd_kw(args) -> None:
    kws = C.load_keywords()
    if args.action == "list":
        for i, k in enumerate(kws, 1):
            mark = "✓" if k.enabled else "–"
            ex = f"  除外: {', '.join(k.exclude)}" if k.exclude else ""
            print(f"{i:>2}. {mark} {k.name}: {', '.join(k.words)}{ex}")
        return
    if args.action == "add":
        words = args.words or [args.target]
        kws.append(C.Keyword(name=args.target, words=words, exclude=args.exclude or []))
        print(f"追加しました: {args.target} ({', '.join(words)})")
    else:
        k = _pick(kws, args.target, lambda k: (k.name,))
        if args.action == "remove":
            kws.remove(k)
            print(f"削除しました: {k.name}")
        else:
            k.enabled = args.action == "enable"
            print(f"{'再開' if k.enabled else '休止'}しました: {k.name}")
    C.save_keywords(kws)


def _pick(seq, target: str, keys):
    """番号・名前・URL のいずれかで要素を選ぶ。"""
    if target.isdigit() and 1 <= int(target) <= len(seq):
        return seq[int(target) - 1]
    for x in seq:
        if target in keys(x):
            return x
    sys.exit(f"見つかりません: {target}（list で番号を確認してください）")


# ================================================================ Windows タスク

TASK = "newsbot"


def cmd_schedule(args, cfg) -> None:
    if args.action == "remove":
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Unregister-ScheduledTask -TaskName {TASK} -Confirm:$false"], check=True)
        print("タスクを削除しました")
        return
    pyw = Path(sys.executable).with_name("pythonw.exe")
    minutes = int(cfg.get("poll_interval_minutes", 10))
    ps = f"""
$a = New-ScheduledTaskAction -Execute '{pyw}' -Argument '-m newsbot run' -WorkingDirectory '{C.HOME}'
$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes {minutes})
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName {TASK} -Action $a -Trigger $t -Settings $s -Description 'ニュース収集・速報・ダイジェスト' -Force | Out-Null
"""
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    print(f"タスク「{TASK}」を登録しました（{minutes} 分ごと、ログオン中に実行）")


# ================================================================ main

def _setup_logging(verbose: bool) -> None:
    C.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [RotatingFileHandler(C.LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")]
    if sys.stderr is not None and verbose:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    for noisy in ("httpx", "httpx2", "anthropic", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> None:
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="newsbot", description="ニュースを収集して日本語ダイジェストを Gmail で送る")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("run", help="収集＋速報＋（配信時刻なら）ダイジェスト。スケジューラ用")
    sub.add_parser("poll", help="収集と速報だけ")
    d = sub.add_parser("digest", help="今すぐダイジェストを作って送る")
    d.add_argument("--dry-run", action="store_true", help="送らずに data/preview.html に保存")
    sub.add_parser("test-mail", help="テストメールを送る")

    f = sub.add_parser("feed", help="収集対象サイトの管理")
    f.add_argument("action", choices=["list", "add", "remove", "enable", "disable", "test"])
    f.add_argument("target", nargs="?", default="", help="add: サイトまたはフィードの URL / 他: 番号か名前")
    f.add_argument("--name")
    f.add_argument("--category")
    f.add_argument("--pick", type=int, help="複数見つかったときの番号")

    k = sub.add_parser("kw", help="速報キーワードの管理")
    k.add_argument("action", choices=["list", "add", "remove", "enable", "disable"])
    k.add_argument("target", nargs="?", default="", help="add: キーワード名 / 他: 番号か名前")
    k.add_argument("--words", nargs="+", help="一致させる語（省略時は名前そのもの）")
    k.add_argument("--exclude", nargs="+")

    s = sub.add_parser("schedule", help="Windows タスクスケジューラへの登録")
    s.add_argument("action", choices=["install", "remove"])

    args = p.parse_args(argv)
    _setup_logging(args.verbose or args.cmd not in ("run",))
    cfg = C.load_config()

    if args.cmd in ("feed", "kw") and args.action not in ("list",) and not args.target:
        p.error("対象を指定してください")
    if args.cmd == "feed":
        return cmd_feed(args)
    if args.cmd == "kw":
        return cmd_kw(args)
    if args.cmd == "schedule":
        return cmd_schedule(args, cfg)
    if args.cmd == "test-mail":
        mailer.send("[ニュース] テストメール", "<p>newsbot からのテストメールです。</p>", cfg["mail"].get("to", ""))
        print("送信しました")
        return

    con = store.connect()
    try:
        if args.cmd == "run":
            run(con, cfg)
        elif args.cmd == "poll":
            poll(con, cfg)
        elif args.cmd == "digest":
            digest(con, cfg, dry_run=args.dry_run)
    except Exception:
        log.exception("実行中にエラー")
        raise
    finally:
        con.close()
