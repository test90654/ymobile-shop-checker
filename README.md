# ワイモバイル店舗 来店予約 空き状況チェッカー

- **スマホ画面（Render）**: 近くの店舗の2週間分の空き（◎△☎）と、日付をタップすると空き時間帯を表示
- **自動通知（GitHub Actions）**: 8:00〜21:00 に15分ごとに確認し、新しく空いた枠を Discord に通知

## 初期設定

1. **Discord 通知**: Settings → Secrets and variables → Actions → `DISCORD_WEBHOOK_URL` に Webhook URL を登録
2. **スマホ画面**: Render で New → Blueprint → このリポジトリを選ぶ（`render.yaml` で自動設定）
3. **動作確認**: Actions → Ymobile shop availability → Run workflow → `test` を選ぶと今の空きが Discord に届く

## 監視条件（`config.json`）

スマホの GitHub アプリ/ブラウザで `config.json` を編集すれば次回から反映されます。

| 項目 | 意味 | 例 |
|---|---|---|
| `lat`, `lon` | 探す地点（近い順に最大10店） | 本庄・伊勢崎周辺 |
| `max_shops` | 監視する店舗数（近い順） | `5` |
| `shops` | 店名の一部か店舗コードで絞る（空=近い順） | `["本庄", "T704"]` |
| `dates` | 対象日（空=2週間すべて） | `["11/3", "11/7"]` |
| `weekdays` | 対象曜日（空=全曜日） | `["土", "日"]` |
| `time_from`, `time_to` | 時間帯 | `"10:00"`, `"13:00"` |

## 仕組み

公式サイトの店舗ページが使っている公開データ（`/shop/api_json.php`、`/shop/api/shop_traffic.php`）を読み、
公式と同じ判定で ◎△☎ を出します。負荷をかけないようリクエスト間隔をあけています。
