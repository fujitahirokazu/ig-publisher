#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ワンタイム・トークンヘルパー（あなたのPCで1回だけ実行）

目的: Graph API Explorer で取った「短期ユーザートークン」から、
  - 非失効の Page アクセストークン（= IG_ACCESS_TOKEN に使う）
  - Instagram ビジネスアカウントID（= IG_USER_ID に使う）
を自動算出して表示する。これを GitHub Secrets に入れれば完全自動運用が回る。

前提:
  - Meta開発者アプリを作成済み（APP_ID / APP_SECRET を控える）
  - そのアプリに instagram_basic, instagram_content_publish, pages_show_list,
    pages_read_engagement, business_management 権限を付与し、
    Graph API Explorer で「短期ユーザートークン」を1つ発行済み
  - 投稿先IG(@mota_h.fujita)がFBページ「藤田弘和」にリンク済み（済）

使い方（PowerShell例）:
  $env:APP_ID="xxxx"; $env:APP_SECRET="yyyy"; $env:SHORT_TOKEN="zzzz"
  python ig_token_helper.py
"""
import os, json, sys, urllib.request, urllib.parse

VER = os.environ.get("GRAPH_VERSION", "v22.0")
BASE = f"https://graph.facebook.com/{VER}"
APP_ID     = os.environ.get("APP_ID", "").strip()
APP_SECRET = os.environ.get("APP_SECRET", "").strip()
SHORT      = os.environ.get("SHORT_TOKEN", "").strip()

def get(path, params):
    q = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{BASE}/{path}?{q}", timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))

def main():
    if not (APP_ID and APP_SECRET and SHORT):
        print("APP_ID / APP_SECRET / SHORT_TOKEN を環境変数で渡してください。"); sys.exit(1)

    # 1) 短期 -> 長期ユーザートークン
    long_user = get("oauth/access_token", {
        "grant_type": "fb_exchange_token",
        "client_id": APP_ID, "client_secret": APP_SECRET, "fb_exchange_token": SHORT,
    })["access_token"]
    print("\n[1] 長期ユーザートークン 取得OK")

    # 2) ページ一覧（各ページのトークンは長期ユーザートークン由来＝実質非失効）
    pages = get("me/accounts", {"access_token": long_user}).get("data", [])
    if not pages:
        print("ページが見つかりません。アプリ権限/ページ管理者権限を確認。"); sys.exit(1)

    print("\n=== 結果（この2つを GitHub Secrets に登録）===")
    for p in pages:
        page_id, page_name, page_token = p["id"], p.get("name",""), p["access_token"]
        try:
            iga = get(page_id, {"fields": "instagram_business_account", "access_token": page_token})
            ig_id = iga.get("instagram_business_account", {}).get("id")
        except Exception as e:
            ig_id = f"(取得失敗: {e})"
        print(f"\n● ページ: {page_name} (id={page_id})")
        print(f"  IG_USER_ID      = {ig_id}")
        print(f"  IG_ACCESS_TOKEN = {page_token}")
    print("\n※ IG_USER_ID が数字で出ているページ（藤田弘和）の2値を使ってください。")
    print("※ Pageトークンは長期ユーザートークン由来なので基本失効しません（パスワード変更/権限剥奪で無効化）。")

if __name__ == "__main__":
    main()
