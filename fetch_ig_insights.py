#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Fetch Instagram Reel insights for posted media via Graph API (Instagram Login).
# Runs in GitHub Actions; reads IG_ACCESS_TOKEN / GRAPH_VERSION from env (secrets/vars).
# Writes data/ig_insights.json. The access token is NEVER printed (scrubbed from errors).
#
# v2 (2026-07-27) — media_id の自己修復を追加
#   旧版は approved_reels_ig.json に書かれた media_id をそのまま叩くだけで、
#   IDがズレていると "Unsupported get request. Object with ID ... does not exist" で
#   その1本が永久に欠測になっていた（実測: ④好意 18105882722038453）。
#   v2 は /me/media を1回引いて、投稿日＋キャプション先頭で本当のIDを引き直す。
#   approved_reels_ig.json は書き換えない。差分は data/ig_media_id_fixes.json に出すだけ。
import os, json, datetime, urllib.parse, urllib.request, urllib.error, re

TOKEN = os.environ.get("IG_ACCESS_TOKEN", "")
VER = os.environ.get("GRAPH_VERSION") or "v25.0"
BASE = "https://graph.instagram.com"
APPROVED = "data/approved_reels_ig.json"
OUT = "data/ig_insights.json"
FIXES = "data/ig_media_id_fixes.json"
METRICS = ["views", "reach", "likes", "comments", "saved", "shares", "total_interactions"]


def scrub(s):
    return s.replace(TOKEN, "***") if TOKEN else s


def get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def api_insights(mid, metrics):
    q = urllib.parse.urlencode({"metric": ",".join(metrics), "access_token": TOKEN})
    return get("{}/{}/{}/insights?{}".format(BASE, VER, mid, q))


def list_media(limit=200):
    """自分の投稿を新しい順に取る。media_id を引き直すための索引。"""
    out, url = [], "{}/{}/me/media?{}".format(BASE, VER, urllib.parse.urlencode(
        {"fields": "id,timestamp,caption,media_type,permalink", "limit": 50, "access_token": TOKEN}))
    while url and len(out) < limit:
        try:
            d = get(url)
        except Exception as e:
            print("[warn] /me/media 取得失敗:", scrub(str(e))[:200]); break
        out += d.get("data", [])
        url = (d.get("paging") or {}).get("next")
    return out


def norm(s):
    return re.sub(r"\s+", "", (s or ""))[:24]


def resolve(reel, media):
    """投稿日 + キャプション先頭24字（空白除去）で本当の media_id を探す。"""
    want_day = (reel.get("posted_at") or reel.get("scheduled_date") or "")[:10]
    want_cap = norm(reel.get("caption"))
    best = None
    for m in media:
        day = (m.get("timestamp") or "")[:10]
        if want_cap and norm(m.get("caption")) == want_cap:
            return m, "caption一致"
        if day and day == want_day and best is None:
            best = m
    return (best, "投稿日一致") if best else (None, None)


def fetch(mid):
    res, err = {}, None
    try:
        d = api_insights(mid, METRICS)
        for it in d.get("data", []):
            try:
                res[it["name"]] = it["values"][0]["value"]
            except Exception:
                pass
        if res:
            return res, None
    except urllib.error.HTTPError as e:
        try:
            err = scrub(e.read().decode("utf-8", "ignore"))[:400]
        except Exception:
            err = "HTTPError"
    except Exception as e:
        err = scrub(str(e))[:400]
    for m in METRICS + ["plays"]:                    # 一括が駄目なら1つずつ
        try:
            d = api_insights(mid, [m])
            for it in d.get("data", []):
                res[it["name"]] = it["values"][0]["value"]
        except Exception:
            pass
    return res, (None if res else err)


def main():
    approved = json.load(open(APPROVED, encoding="utf-8"))
    reels = [r for r in approved.get("reels", []) if r.get("status") == "posted" and r.get("media_id")]
    media = list_media() if TOKEN else []
    print("me/media: {}件を索引化".format(len(media)))

    out = {"fetched_at": datetime.datetime.utcnow().isoformat() + "Z", "graph_version": VER,
           "token_present": bool(TOKEN), "count": len(reels), "media_indexed": len(media), "reels": []}
    fixes = []
    for r in reels:
        mid = r["media_id"]
        m, err = fetch(mid)
        note = None
        if not m and media:                          # ここが v2 の肝：IDを引き直して再挑戦
            cand, how = resolve(r, media)
            if cand and cand["id"] != mid:
                m2, err2 = fetch(cand["id"])
                if m2:
                    fixes.append({"theme": r.get("theme"), "date": (r.get("posted_at") or "")[:10],
                                  "old_media_id": mid, "new_media_id": cand["id"],
                                  "matched_by": how, "permalink": cand.get("permalink")})
                    m, err, mid, note = m2, None, cand["id"], "media_id を {} で引き直した".format(how)
                else:
                    err = err or err2
        out["reels"].append({"media_id": mid, "media_id_original": r["media_id"],
                             "date": (r.get("posted_at") or r.get("scheduled_date") or "")[:10],
                             "theme": r.get("theme"), "metrics": m, "error": err, "note": note})

    os.makedirs("data", exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({"generated": out["fetched_at"], "fixes": fixes},
              open(FIXES, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    ok = sum(1 for x in out["reels"] if x["metrics"])
    print("wrote {} : {}/{} reels have metrics, token_present={}".format(OUT, ok, len(reels), bool(TOKEN)))
    for x in out["reels"]:
        tail = x["metrics"] if x["metrics"] else ("ERR: " + str(x["error"])[:140])
        print(x["date"], x["theme"], tail, ("<- " + x["note"]) if x.get("note") else "")
    if fixes:
        print("\nmedia_id のズレ {}件 -> {} に出力。approved_reels_ig.json は書き換えていない。".format(len(fixes), FIXES))
        for f in fixes:
            print("  {} {} : {} -> {} ({})".format(f["date"], f["theme"], f["old_media_id"],
                                                   f["new_media_id"], f["matched_by"]))


if __name__ == "__main__":
    main()
