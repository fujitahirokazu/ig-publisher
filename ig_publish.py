#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IG Reels 自動投稿 publisher（Instagram Graph API / 依存ライブラリなし＝標準ライブラリのみ）

仕組み（3ステップ）:
  1) POST /{IG_USER_ID}/media  (media_type=REELS, video_url, caption)  -> container_id
  2) GET  /{container_id}?fields=status_code  を FINISHED になるまでポーリング
  3) POST /{IG_USER_ID}/media_publish (creation_id=container_id)        -> media_id

スケジュール:
  data/approved_reels_ig.json の reels[] のうち
    status == "pending" かつ scheduled_date <= 今日(JST)
  を投稿する。GitHub Actions の cron を 19:00 JST に当てて1日1本運用。

必要な環境変数（GitHub Secrets）:
  IG_USER_ID        : Instagram のビジネスアカウントID（数字）
  IG_ACCESS_TOKEN   : instagram_content_publish 権限つきトークン（非失効Pageトークン推奨）
  GRAPH_VERSION     : 省略可。既定 v21.0（Graph API Explorer に出ているバージョンに合わせると安全）
  DRY_RUN           : "1" で実際のAPIを叩かずに対象だけ表示（テスト用）

使い方:
  python ig_publish.py
"""
import os, json, time, sys, datetime, urllib.request, urllib.parse, urllib.error

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data", "approved_reels_ig.json")
if not os.path.exists(DATA):
    DATA = os.path.join(ROOT, "approved_reels_ig.json")  # flat構成(リポジトリ直下)にも対応
LOG  = os.path.join(ROOT, "logs", "posted_log.json")

# このアプリは「Instagramログインによる API設定」＝graph.instagram.com 経路（実機確認済 2026-07-10）。
# ユースケース画面「トークンを生成」で出るIGユーザートークンを使う（FBページトークン/token_helperは使わない）。
# 権限=instagram_business_content_publish、IG_USER_ID=17841480088005154（fujita.meicho・実機一致）。
GRAPH_HOST    = os.environ.get("GRAPH_HOST", "graph.instagram.com")
GRAPH_VERSION = os.environ.get("GRAPH_VERSION", "v22.0")
# このアプリで接続したIGビジネスアカウントID（@fujita.meicho）。secret不要なので既定値に焼き込み。
IG_USER_ID    = (os.environ.get("IG_USER_ID") or "17841480088005154").strip()
TOKEN         = os.environ.get("IG_ACCESS_TOKEN", "").strip()
DRY_RUN       = os.environ.get("DRY_RUN", "") == "1"
BASE = f"https://{GRAPH_HOST}/{GRAPH_VERSION}"

JST = datetime.timezone(datetime.timedelta(hours=9))
def today_jst():
    return datetime.datetime.now(JST).strftime("%Y-%m-%d")
def now_iso():
    return datetime.datetime.now(JST).isoformat(timespec="seconds")

def _post(path, params):
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(f"{BASE}/{path}", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))

def _get(path, params):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{BASE}/{path}?{q}", method="GET")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))

def _err(e):
    try:
        return e.read().decode("utf-8")
    except Exception:
        return str(e)

def create_container(reel):
    params = {
        "media_type": "REELS",
        "video_url": reel["video_url"],
        "caption": reel["caption"],
        "share_to_feed": "true",
        "access_token": TOKEN,
    }
    res = _post(f"{IG_USER_ID}/media", params)
    return res["id"]

def wait_finished(container_id, timeout_s=600, interval_s=8):
    waited = 0
    while waited < timeout_s:
        res = _get(container_id, {"fields": "status_code,status", "access_token": TOKEN})
        sc = res.get("status_code")
        print(f"    status: {sc} ({res.get('status','')})")
        if sc == "FINISHED":
            return True
        if sc in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {container_id} -> {sc}: {res}")
        time.sleep(interval_s)
        waited += interval_s
    raise TimeoutError(f"container {container_id} not FINISHED in {timeout_s}s")

def publish(container_id):
    res = _post(f"{IG_USER_ID}/media_publish", {"creation_id": container_id, "access_token": TOKEN})
    return res["id"]

def load(path, default):
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    return default

def main():
    if not DRY_RUN and (not IG_USER_ID or not TOKEN):
        print("ERROR: IG_USER_ID / IG_ACCESS_TOKEN が未設定です（GitHub Secrets を確認）。")
        sys.exit(1)

    db = load(DATA, {"reels": []})
    log = load(LOG, [])
    today = today_jst()
    due = [r for r in db["reels"]
           if r.get("status") == "pending" and r.get("scheduled_date", "9999") <= today]

    print(f"Run at {now_iso()} (JST). today={today}  due={len(due)}  graph={GRAPH_VERSION}")
    if not due:
        print("対象なし。終了。")
        return

    for reel in due:
        rid = reel["id"]
        print(f"- {rid} | {reel.get('theme','')} | {reel['video_url']}")
        if DRY_RUN:
            print("    [DRY_RUN] 投稿せずスキップ")
            continue
        try:
            cid = create_container(reel)
            print(f"    container={cid}")
            wait_finished(cid)
            media_id = publish(cid)
            reel["status"] = "posted"
            reel["media_id"] = media_id
            reel["posted_at"] = now_iso()
            log.append({"id": rid, "media_id": media_id, "posted_at": reel["posted_at"]})
            print(f"    ✅ posted media_id={media_id}")
        except urllib.error.HTTPError as e:
            reel["status"] = "failed"; reel["fail_at"] = now_iso(); reel["error"] = _err(e)[:500]
            print(f"    ❌ HTTPError: {reel['error']}")
        except Exception as e:
            reel["status"] = "failed"; reel["fail_at"] = now_iso(); reel["error"] = str(e)[:500]
            print(f"    ❌ {e}")

    os.makedirs(os.path.dirname(LOG), exist_ok=True)  # logs/が無い初回でも落ちないように
    json.dump(db, open(DATA, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(log, open(LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("done. JSON更新済み。")

if __name__ == "__main__":
    main()
