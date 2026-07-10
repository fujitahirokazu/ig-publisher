#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IG長期トークン 自動延命（月1実行）

仕組み:
  1) GET https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=現トークン
     -> 新しい60日トークン(access_token, expires_in)
  2) GitHub API でリポジトリの公開鍵を取得
  3) 新トークンを libsodium(sealed box) で暗号化
  4) PUT で Secret(IG_ACCESS_TOKEN) を上書き更新

必要な環境変数(GitHub Secrets / Actions):
  IG_ACCESS_TOKEN   : 現在の長期トークン(60日)。24時間以上経過していること。
  GH_PAT            : fine-grained PAT。対象repoの "Secrets: Read and write" 権限。
  GITHUB_REPOSITORY : owner/repo（GitHub Actionsが自動で渡す）

依存: pynacl（workflowで pip install）
"""
import os, json, sys, urllib.request, urllib.parse, urllib.error
from base64 import b64encode
from nacl import encoding, public

TOKEN = os.environ.get("IG_ACCESS_TOKEN", "").strip()
GH_PAT = os.environ.get("GH_PAT", "").strip()
REPO   = os.environ.get("GITHUB_REPOSITORY", "").strip()  # owner/repo

def http(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.read().decode("utf-8")

def main():
    if not (TOKEN and GH_PAT and REPO):
        print("ERROR: IG_ACCESS_TOKEN / GH_PAT / GITHUB_REPOSITORY が未設定"); sys.exit(1)

    # 1) 長期トークンを延長（60日リセット）
    q = urllib.parse.urlencode({"grant_type": "ig_refresh_token", "access_token": TOKEN})
    try:
        _, body = http(f"https://graph.instagram.com/refresh_access_token?{q}")
    except urllib.error.HTTPError as e:
        print("refresh失敗:", e.read().decode("utf-8")); sys.exit(1)
    res = json.loads(body)
    new_token = res["access_token"]
    exp = int(res.get("expires_in", 0))
    print(f"refresh OK. expires_in={exp}s (~{exp//86400}日)")

    # 2) リポジトリの公開鍵を取得
    api = f"https://api.github.com/repos/{REPO}/actions/secrets"
    hdr = {"Authorization": f"Bearer {GH_PAT}",
           "Accept": "application/vnd.github+json",
           "X-GitHub-Api-Version": "2022-11-28",
           "User-Agent": "ig-token-refresh"}
    _, body = http(f"{api}/public-key", headers=hdr)
    pk = json.loads(body)

    # 3) 新トークンを暗号化（libsodium sealed box）
    sealed = public.SealedBox(public.PublicKey(pk["key"].encode("utf-8"), encoding.Base64Encoder()))
    enc = b64encode(sealed.encrypt(new_token.encode("utf-8"))).decode("utf-8")

    # 4) Secret(IG_ACCESS_TOKEN) を上書き
    payload = json.dumps({"encrypted_value": enc, "key_id": pk["key_id"]}).encode("utf-8")
    h2 = dict(hdr); h2["Content-Type"] = "application/json"
    status, _ = http(f"{api}/IG_ACCESS_TOKEN", data=payload, headers=h2, method="PUT")
    print(f"secret更新 status={status} (201=新規/204=更新 どちらも成功)")
    if status not in (201, 204):
        sys.exit(1)

if __name__ == "__main__":
    main()
