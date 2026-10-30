# ワイモバイル店舗 来店予約 空き状況チェッカー

- **スマホ画面（Render）**: 都道府県（または現在地の近く）の全店舗をまとめて調べ、予約できる開始時刻を空きのある店だけ早い順に表示。日付で絞り込み可
- **自動通知（GitHub Actions）**: 8:00〜21:00 に15分ごとに確認し、新しく空いた枠を Discord に通知

## 初期設定

1. **Discord 通知**: Settings → Secrets and variables → Actions → `DISCORD_WEBHOOK_URL` に Webhook URL を登録
2. **スマホ画面**: Render で New → Blueprint → このリポジトリを選ぶ（`render.yaml` で自動設定）
3. **動作確認**: Actions → Ymobile shop availability → Run workflow → `test` を選ぶと今の空きが Discord に届く

## 監視条件（`config.json`）

スマホの GitHub アプリ/ブラウザで `config.json` を編集すれば次回から反映されます。

| 項目 | 意味 | 例 |
|---|---|---|
| `purpose` | 来店目的（下の表） | `"mnp_out"` |
| `prefecture` | 県内の全店舗を対象にする（空なら `lat`/`lon` の近く） | `"埼玉県"` |
| `cities` | 市区町村で絞る | `["本庄市", "深谷市"]` |
| `lat`, `lon`, `max_shops` | `prefecture` が空のとき、この地点から近い順に何店舗 | 本庄・伊勢崎周辺 / `5` |
| `shops` | 店名の一部か店舗コードで絞る | `["本庄", "T704"]` |
| `dates` | 対象日（空=2週間すべて） | `["11/3", "11/7"]` |
| `weekdays` | 対象曜日（空=全曜日） | `["土", "日"]` |
| `time_from`, `time_to` | 予約開始時刻の範囲 | `"10:00"`, `"13:00"` |

来店目的 `purpose`: `mnp_out`（MNP予約番号発行）/ `change`（機種変更）/ `mnp_in`（他社⇒ワイモバイル）/
`sb_to_ym` / `ym_to_sb` / `new`（新規）/ `contract`（契約内容確認・変更）/ `return`（返却・下取り）/
`repair`（故障修理）/ `cancel`（解約）/ `data` / `support`（店頭スマホサポート）

## 仕組み

公式サイトの店舗ページが使っている公開データ（`/shop/api_json.php`、`/shop/api/shop_traffic.php`）を読み、
来店目的ごとの予約できる開始時刻を出します（ソフトバンクショップとワイモバイルショップでは業務コードが違うので自動で切り替え）。負荷をかけないようリクエスト間隔をあけています。
