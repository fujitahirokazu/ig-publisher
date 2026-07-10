# IG Reels 完全自動化（Instagram Graph API）セットアップ手順書

毎日 19:00 JST に IG リールを**無人で1本自動投稿**する仕組み。月額ほぼ¥0（API無料／動画は同リポジトリで無料ホスト／GitHub Actions無料枠）。
X運用(x-growth-suite)と同じ「GitHub Actions cron + JSONキュー」方式。

---

## 0. 私(Claude)が用意済みのも（このフォルダ）
- `ig_publish.py` … 投稿本体（Graph API 3ステップ＝container作成→FINISHED待ち→publish）。標準ライブラリのみ＝pip不要。
- `.github/workflows/ig-publish.yml` … 毎日19:00 JSTのcron＋手動実行(dry-run可)。投稿後にJSONを自動コミット。
- `data/approved_reels_ig.json` … 投稿キュー6本（世界標準の経営理論／6/29-7/4 19:00／キャプション入り）。
- `videos/reel1〜6_sekai_voiced.mp4` … 声入り6本（このリポジトリを公開にすればMetaが取得できる公開URLになる）。
- `ig_token_helper.py` … 短期トークン→**非失効Pageトークン＋IG_USER_ID**を自動算出するヘルパー。

## あなたがやること＝下の「STEP 1〜4」だけ（合計15〜20分・一度きり）

---

## STEP 1. Metaアプリを作る（FBログインが要るので私は代行不可）
1. https://developers.facebook.com/apps/ →「アプリを作成」
2. ユースケース＝**「Instagramの管理」/「他のビジネス系」**（Instagram Graph APIが使えるもの）。アプリ名は任意（例: mota-ig-publisher）。
3. 作成後、アプリに **Instagram（Graph API）** プロダクトを追加。
4. アプリ設定 → ベーシック で **アプリID（APP_ID）** と **app secret（APP_SECRET）** を控える。
   - ※IG(@fujita.meicho)はFBページ「藤田弘和」に既にリンク済みなので、新規リンク作業は不要。

## STEP 2. 短期トークンを発行
1. https://developers.facebook.com/tools/explorer/ （Graph API Explorer）
2. 右上で 上記アプリ を選択。
3. 「Permissions」で次を追加：`instagram_basic` / `instagram_content_publish` / `pages_show_list` / `pages_read_engagement` / `business_management`
4. 「Generate Access Token」→ FBで承認（ページ「藤田弘和」とIGを選択）。出てきた文字列＝**短期トークン（SHORT_TOKEN）**。

## STEP 3. ヘルパーで本番トークン＆IDを算出（あなたのPCで1回）
PowerShellで（このフォルダに移動して）：
```powershell
$env:APP_ID="(STEP1のアプリID)"; $env:APP_SECRET="(STEP1のsecret)"; $env:SHORT_TOKEN="(STEP2の短期トークン)"
python ig_token_helper.py
```
→ 出力の中から、`IG_USER_ID`（数字）と `IG_ACCESS_TOKEN`（長い文字列）の2つをコピー。
（このPageトークンは長期ユーザートークン由来＝基本失効しません。）

## STEP 4. GitHubに置いて秘密情報を登録→稼働
1. GitHubで**公開(public)リポジトリ**「**ig-publisher**」を作成（videos公開URLのため public 必須。コードは見られて問題なし、トークンはSecretsなので安全）。
2. このフォルダ一式（`videos/`含む）をアップロード（GitHub web の「Add file→Upload files」でドラッグ&ドロップでOK）。
3. リポジトリ **Settings → Secrets and variables → Actions → New repository secret** で2つ登録：
   - `IG_USER_ID` = STEP3の数字
   - `IG_ACCESS_TOKEN` = STEP3のトークン
   - （任意）**Variables**タブに `GRAPH_VERSION` = Graph API Explorerに出ているバージョン（例 `v22.0`）。未設定なら既定 v22.0。
4. **Actions** タブ → 左「ig-publish」→「Run workflow」→ dry_run に `1` を入れて実行 → ログに対象6本が出れば配線OK。
5. 以降は**毎日19:00 JSTに自動**で当日分を1本投稿。dry_runを使わず放置でOK。

---

## 仕組み（3ステップ／中身）
1. `POST /{IG_USER_ID}/media` に `media_type=REELS, video_url, caption` → 生成コンテナID
2. `GET /{container}?fields=status_code` を **FINISHED** までポーリング
3. `POST /{IG_USER_ID}/media_publish` に `creation_id` → 公開

`approved_reels_ig.json` の `status=="pending"` かつ `scheduled_date<=今日(JST)` を投稿し、`posted` に更新。

## 運用（弾を足す）
次シリーズを足す時は `data/approved_reels_ig.json` の `reels[]` に
`{id, theme, video_url, caption, scheduled_date, post_time:"19:00", status:"pending"}` を追記して push するだけ。
（動画は `videos/` に置けば `https://raw.githubusercontent.com/<user>/ig-publisher/main/videos/<file>` が公開URLになる）。
→ この追記＆動画生成は私(Claude)が継続対応します。

## 注意・前提
- **App Review不要**：自分のアカウント(@fujita.meicho)への投稿はStandard Accessで可能（他人の垢に投稿する段階で初めて審査が要る）。
- **動画仕様**：声入り6本は 1080×1920(9:16)/H.264/AAC/33-43秒＝Reels条件(5-90秒)に**適合済み**。
- **レート上限**：100投稿/24h・200call/h＝1日1本に余裕。
- **トークン失効時**（パスワード変更や権限剥奪で起きうる）：STEP2→3をやり直して `IG_ACCESS_TOKEN` を更新するだけ。
- **動画URLは公開必須**：Metaがvideo_urlを取りに来るため、ig-publisherリポジトリは public にすること。

## コスト
API ¥0 ／ 動画ホスティング ¥0（同リポジトリ・極小ファイル）／ GitHub Actions ¥0（無料枠）。実質タダで無人運用。
