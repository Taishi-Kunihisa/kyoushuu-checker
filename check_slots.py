"""
教習所（OBIC 教習生予約サイト）の空き時限を確認して、新しく空いた枠を ntfy でスマホに通知する。

必要な環境変数（GitHub Secrets に登録）:
  FAVORITE_URL   お気に入り登録用ページのURL（ZADFavo/Favorite.aspx?p=...）
  STUDENT_NO     教習生番号
  PASSWORD       予約サイトのパスワード
  NTFY_TOPIC     ntfy のトピック名（推測されにくい文字列にする）

任意の環境変数（GitHub Variables か workflow の env で設定）:
  WEEKS          何週先まで見るか（デフォルト 3）
  WEEKDAYS       通知する曜日。例 "土,日"（空なら全曜日）
  PERIODS        通知する時限。例 "1-4,8"（空なら全時限）
  STATE_FILE     前回の結果を保存するファイル（デフォルト state.json）
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

JST = timezone(timedelta(hours=9))

FAVORITE_URL = os.environ["FAVORITE_URL"]
STUDENT_NO = os.environ["STUDENT_NO"]
PASSWORD = os.environ["PASSWORD"]
NTFY_TOPIC = os.environ["NTFY_TOPIC"]

WEEKS = int(os.environ.get("WEEKS") or 3)
WEEKDAYS = [w.strip() for w in (os.environ.get("WEEKDAYS") or "").split(",") if w.strip()]
PERIODS_RAW = os.environ.get("PERIODS") or ""
STATE_FILE = os.environ.get("STATE_FILE") or "state.json"

DAY_RE = re.compile(r"^(\d{4}/\d{2}/\d{2})\s*\((.)\)\s*(空|×)")
SLOT_RE = re.compile(r"^(\d{1,2})時限\s*(\d{1,2}:\d{2})\s*～\s*(\d{1,2}:\d{2})")


def parse_periods(raw):
    s = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            s.update(range(int(a), int(b) + 1))
        else:
            s.add(int(part))
    return s


PERIODS = parse_periods(PERIODS_RAW)


def parse_week_text(text):
    """時限選択画面の innerText から空き枠を抜き出す。"""
    slots = []
    target = None
    m = re.search(r"予約対象\s*(\S+)", text)
    if m:
        target = m.group(1)
    current = None
    for line in text.splitlines():
        line = line.strip()
        dm = DAY_RE.match(line)
        if dm:
            current = (dm.group(1), dm.group(2)) if dm.group(3) == "空" else None
            continue
        sm = SLOT_RE.match(line)
        if sm and current:
            slots.append({
                "date": current[0],
                "weekday": current[1],
                "period": int(sm.group(1)),
                "start": sm.group(2),
                "end": sm.group(3),
            })
    return target, slots


def wanted(slot):
    if WEEKDAYS and slot["weekday"] not in WEEKDAYS:
        return False
    if PERIODS and slot["period"] not in PERIODS:
        return False
    return True


def slot_key(s):
    return f'{s["date"]}-{s["period"]:02d}'


def fmt(s):
    return f'{s["date"][5:]}({s["weekday"]}) {s["period"]}限 {s["start"]}～{s["end"]}'


def notify(title, message, priority=4):
    r = requests.post(
        "https://ntfy.sh/",
        json={
            "topic": NTFY_TOPIC,
            "title": title,
            "message": message,
            "priority": priority,
            "tags": ["car"],
            "click": FAVORITE_URL,
        },
        timeout=20,
    )
    r.raise_for_status()


def wait_week_change(frame, old_value, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = frame.locator("#ddlWeeks").input_value(timeout=2000)
            if v != old_value:
                return v
        except PWTimeout:
            pass
        time.sleep(0.5)
    raise RuntimeError("次週への切り替えがタイムアウトしました")


def scrape():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(locale="ja-JP", timezone_id="Asia/Tokyo")
        page = ctx.new_page()
        page.set_default_timeout(30000)

        page.goto(FAVORITE_URL)
        page.get_by_text("ログインページへ").click()

        frame = page.frame_locator("iframe")
        frame.locator("#txtKyoushuuseiNO").fill(STUDENT_NO)
        frame.locator("#txtPassword").fill(PASSWORD)
        frame.locator("#btnAuthentication").click()

        try:
            frame.locator("#btnMenu_Kyoushuuyoyaku").wait_for(timeout=30000)
        except PWTimeout:
            body = frame.locator("body").inner_text()
            raise RuntimeError("ログインに失敗しました:\n" + body[:300])

        frame.locator("#btnMenu_Kyoushuuyoyaku").click()
        frame.locator("#ddlWeeks").wait_for()

        all_slots = []
        target = None
        for i in range(WEEKS):
            time.sleep(1)
            text = frame.locator("body").inner_text()
            t, slots = parse_week_text(text)
            target = target or t
            all_slots.extend(slots)
            if i == WEEKS - 1:
                break
            old = frame.locator("#ddlWeeks").input_value()
            # 次週ボタンは非表示扱いなので JS でクリックする
            frame.locator("#btnNextWeek").evaluate("el => el.click()")
            wait_week_change(frame, old)

        # 放置セッションが残らないようにログアウト
        try:
            frame.locator("#btnLogOut").evaluate("el => el.click()", timeout=5000)
            time.sleep(1)
        except Exception:
            pass

        browser.close()
        return target, all_slots


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def main():
    state = load_state()
    try:
        target, slots = scrape()
    except Exception as e:
        # 詳細はログに出さず通知でだけ送る（画面の文言に個人情報が含まれることがあるため）
        print(f"エラー: {type(e).__name__}", file=sys.stderr)
        # 連続エラーで通知が鳴り続けないよう、エラー状態に入ったときだけ通知
        if not state.get("error"):
            try:
                notify("教習所チェック失敗", str(e)[:500], priority=3)
            except Exception:
                pass
        state["error"] = True
        save_state(state)
        sys.exit(1)

    now = datetime.now(JST)
    today = now.strftime("%Y/%m/%d")
    hits = [s for s in slots if wanted(s) and s["date"] >= today]
    keys = {slot_key(s) for s in hits}
    prev = set(state.get("slots", []))
    new = [s for s in hits if slot_key(s) not in prev]

    # リポジトリが Public なのでログには件数だけ出す（日時や予約対象は通知でのみ送る）
    print(f"[{now:%Y-%m-%d %H:%M}] 空き {len(hits)} 件 / 新規 {len(new)} 件")

    if new:
        lines = [fmt(s) for s in sorted(new, key=slot_key)]
        notify(f"教習の空き {len(new)}件（{target or ''}）", "\n".join(lines))

    if state.get("error"):
        print("エラーから復帰しました")

    save_state({"slots": sorted(keys), "error": False, "checked_at": now.isoformat()})


if __name__ == "__main__":
    main()
