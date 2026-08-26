#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data/approved_reels_ig.json の3方向マージ器（git の merge driver として呼ばれる）

なぜ要るのか（2026-08-23 実測）
------------------------------
このファイルには**書き手が2人**いる。
  ・GitHub Actions（ig-publish.yml）… 投稿できた reel に status=posted / media_id / posted_at を書く
  ・ローカルの auto-git-sync   … 新しく承認した reel を足す
両方が同じファイルの同じ行を触るので、間があくと必ず衝突する。実測：
  08-22 05:00 / 08-22 23:00 / 08-23 00,01,02,03,04,05:00 → **8回連続で rebase 失敗**
  そのたびに auto-git-sync が `Manual fix needed` で降り、藤田が手で直していた。

衝突の中身は毎回同じで、しかも**機械的に解ける**：
  posted は CI しか書かない・一度 posted になったら戻らない（単調）
  新規 reel はローカルしか足さない
だから「id で突き合わせて、posted 側を採り、新規は残す」で答えが一意に決まる。

rebase 中の ours/theirs について
--------------------------------
**rebase では ours がリモート側、theirs が自分の commit になる（逆転する）。**
なので「ours＝ローカル」と決め打ちした実装は rebase で必ず壊れる。
ここでは A/B を対称に扱い、どちらが上流かに依存しない規則にしてある。

git の merge driver 規約
  引数: %O(共通祖先) %A(ours) %B(theirs) %P(パス)
  結果は **%A に書く**。exit 0 = 解決済み / exit≠0 = 衝突として git に返す。
  → 判断がつかないときは必ず exit 1。黙って片側を捨てない。

単体で確かめる: python tools/merge_approved_reels.py --selftest
"""
import io, json, sys

FIELDS_CI = ("status", "media_id", "posted_at")   # CI だけが書く欄


def load(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def reels_map(d):
    return {r["id"]: r for r in (d.get("reels") or []) if isinstance(r, dict) and r.get("id")}


def order_ids(o, a, b):
    """並びは**祖先の順を保つ**。新規は A→B の順で後ろに足す。
       全体を並べ替えると差分が毎回巨大になり、次の衝突を自分で作ることになる。"""
    out, seen = [], set()
    for src in ([r.get("id") for r in (o.get("reels") or [])],
                [r.get("id") for r in (a.get("reels") or [])],
                [r.get("id") for r in (b.get("reels") or [])]):
        for i in src:
            if i and i not in seen:
                seen.add(i); out.append(i)
    return out


def pick(oid, O, A, B):
    """1件ぶんの3方向解決。返り値 (採用オブジェクト or None, 解決できたか)"""
    o, a, b = O.get(oid), A.get(oid), B.get(oid)

    # --- 片側にしか無い場合：祖先にあれば「消した」、無ければ「足した」
    if a is None or b is None:
        present, other = (a, b) if a is not None else (b, a)
        if o is None:
            return present, True          # 新規追加 → 残す
        if present == o:
            return None, True             # 片側が消し、もう片側は無変更 → 消す
        return present, False             # 片側が消し、片側が編集 → 人が決める
    if a == b:
        return a, True

    # --- posted は CI しか書かない・戻らない。posted 側が真。
    ap, bp = (a.get("status") == "posted"), (b.get("status") == "posted")
    if ap != bp:
        posted, draft = (a, b) if ap else (b, a)
        m = dict(draft)                    # 本文（caption など）は編集側を活かし…
        for k in FIELDS_CI:                # …CI の欄は posted 側で上書きする
            if k in posted:
                m[k] = posted[k]
        return m, True
    if ap and bp:
        # 両方 posted。CI の欄が一致していれば本文編集側を採る。
        if all(a.get(k) == b.get(k) for k in FIELDS_CI):
            return (a if a != o else b), True
        # --- 2026-08-26 追加：両方 posted で media_id / posted_at だけが割れる形は
        #     「同じ reel が2回投稿された」痕跡。実際に 2026-08-22-thegoal-toc で発生し、
        #     ここが人待ちになった結果 9夜 rebase が止まり push が死んだ。
        #     どちらを採っても再投稿は起きない（両側 posted）ので、機械で決めてよい。
        #     採用＝posted_at が**早い側**（本当に最初に出た記録）。捨てない方の media_id は
        #     dup_media_ids に残し、健診が拾えるようにする。黙って片側を消さない。
        pa_, pb_ = str(a.get("posted_at") or ""), str(b.get("posted_at") or "")
        if pa_ and pb_ and pa_ != pb_:
            first, second = (a, b) if pa_ < pb_ else (b, a)
            m = dict(first)
            dup = list(m.get("dup_media_ids") or [])
            for src in (a, b):
                for v in list(src.get("dup_media_ids") or []):
                    if v and v not in dup:
                        dup.append(v)
            sm = second.get("media_id")
            if sm and sm != m.get("media_id") and sm not in dup:
                dup.append(sm)
            if dup:
                m["dup_media_ids"] = sorted(dup)
            return m, True
        return None, False

    # --- どちらも未投稿。祖先から変わった側を採る。両方変わっていたら人に返す。
    da, db = (a != o), (b != o)
    if da and not db:
        return a, True
    if db and not da:
        return b, True
    return None, False


def merge(po, pa, pb):
    O, A, B = load(po), load(pa), load(pb)
    mo, ma, mb = reels_map(O), reels_map(A), reels_map(B)

    reels, bad = [], []
    for oid in order_ids(O, A, B):
        obj, ok = pick(oid, mo, ma, mb)
        if not ok:
            bad.append(oid); continue
        if obj is not None:
            reels.append(obj)

    # series は素直な3方向
    so, sa, sb = O.get("series"), A.get("series"), B.get("series")
    if sa == sb:
        series = sa
    elif sa == so:
        series = sb
    elif sb == so:
        series = sa
    else:
        bad.append("series"); series = sa

    if bad:
        sys.stderr.write("merge_approved_reels: 自動で決められない: %s\n" % ", ".join(bad[:8]))
        return None
    return {"series": series, "reels": reels}


def write(path, data):
    # 書き手（ig_publish.py:142）と同じ形にそろえる: ensure_ascii=False, indent=2
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def selftest():
    import tempfile, os
    def R(i, st="pending", **kw):
        d = {"id": i, "caption": "c-" + i, "scheduled_date": "2026-01-01",
             "post_time": "19:00", "status": st}
        d.update(kw); return d
    def W(d):
        p = tempfile.mktemp(suffix=".json"); write(p, d); return p

    ng = 0
    def case(name, O, A, B, expect_ids, check=None):
        nonlocal ng
        r = merge(W(O), W(A), W(B))
        got = None if r is None else [x["id"] for x in r["reels"]]
        ok = (got == expect_ids) and (check is None or (r is not None and check(r)))
        print(("ok   " if ok else "NG   ") + name + "  → " + str(got))
        if not ok: ng += 1

    base = {"series": "s", "reels": [R("a"), R("b")]}
    # ① CI が a を posted に / ローカルが c を追加（実際に毎晩起きている形）
    case("CI posted + ローカル追加",
         base,
         {"series": "s", "reels": [R("a", "posted", media_id="M1", posted_at="T1"), R("b")]},
         {"series": "s", "reels": [R("a"), R("b"), R("c")]},
         ["a", "b", "c"],
         lambda r: r["reels"][0]["status"] == "posted" and r["reels"][0]["media_id"] == "M1")
    # ② ①の A と B を入れ替えても同じ答えになるか（rebase の ours/theirs 逆転対策）
    case("左右を入れ替えても同じ",
         base,
         {"series": "s", "reels": [R("a"), R("b"), R("c")]},
         {"series": "s", "reels": [R("a", "posted", media_id="M1", posted_at="T1"), R("b")]},
         ["a", "b", "c"],
         lambda r: r["reels"][0]["status"] == "posted" and r["reels"][0]["media_id"] == "M1")
    # ④ 両方 posted で media_id/posted_at が割れる＝二重投稿の痕跡。早い側を採り dup を残す
    case("両方posted・二重投稿の痕跡",
         {"series": "s", "reels": [R("a", "posted", media_id="M1", posted_at="2026-08-22T19:14:02+09:00")]},
         {"series": "s", "reels": [R("a", "posted", media_id="M1", posted_at="2026-08-22T19:14:02+09:00")]},
         {"series": "s", "reels": [R("a", "posted", media_id="M2", posted_at="2026-08-23T19:15:00+09:00")]},
         ["a"],
         lambda r: r["reels"][0]["media_id"] == "M1" and r["reels"][0]["dup_media_ids"] == ["M2"])
    # ④b 左右を入れ替えても同じ答え（rebase では ours/theirs が反転するため対称性が必須）
    case("二重投稿の痕跡・左右反転",
         {"series": "s", "reels": [R("a", "posted", media_id="M1", posted_at="2026-08-22T19:14:02+09:00")]},
         {"series": "s", "reels": [R("a", "posted", media_id="M2", posted_at="2026-08-23T19:15:00+09:00")]},
         {"series": "s", "reels": [R("a", "posted", media_id="M1", posted_at="2026-08-22T19:14:02+09:00")]},
         ["a"],
         lambda r: r["reels"][0]["media_id"] == "M1" and r["reels"][0]["dup_media_ids"] == ["M2"])
    # ③ posted 側の CI 欄を残しつつ、未投稿側の本文編集を活かす
    case("本文編集は活かす",
         base,
         {"series": "s", "reels": [R("a", "posted", media_id="M1", posted_at="T1"), R("b")]},
         {"series": "s", "reels": [dict(R("a"), caption="なおした"), R("b")]},
         ["a", "b"],
         lambda r: r["reels"][0]["caption"] == "なおした" and r["reels"][0]["media_id"] == "M1")
    # ④ 削除は尊重する
    case("片側の削除",
         base, {"series": "s", "reels": [R("a")]}, {"series": "s", "reels": [R("a"), R("b")]},
         ["a"])
    # ⑤ 両側が別々に本文を書き換えたら**人に返す**（黙って片側を捨てない）
    case("本文が両側で衝突→人に返す",
         base,
         {"series": "s", "reels": [dict(R("a"), caption="A案"), R("b")]},
         {"series": "s", "reels": [dict(R("a"), caption="B案"), R("b")]},
         None)
    print("selftest: " + ("全て通過" if ng == 0 else "%d件 NG" % ng))
    return 1 if ng else 0


def main(argv):
    if "--selftest" in argv:
        return selftest()
    if len(argv) < 4:
        sys.stderr.write("usage: merge_approved_reels.py %O %A %B [%P]\n")
        return 2
    po, pa, pb = argv[1], argv[2], argv[3]
    try:
        r = merge(po, pa, pb)
    except Exception as e:
        sys.stderr.write("merge_approved_reels: 例外 %s: %s\n" % (type(e).__name__, e))
        return 1                      # 例外時は**必ず衝突として返す**。壊れた結果を書かない。
    if r is None:
        return 1
    write(pa, r)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
