# ledger.json のスキーマ

`build_ledger.py` に渡すJSON。`note` / `lede` / `why` / `footer` / `criteria[].text`
はHTMLをそのまま書ける（`<b>` で日付や社名を立てる）。`name` / `domain` /
`industry` は自動でエスケープされる。

下の例の社名・ドメインはすべて架空。

```jsonc
{
  "title": "AIO提案先 優先台帳",
  "eyebrow": "送信済みメール 1年分 / 2025-09 → 2026-09",
  "lede": "Gmailの送信済みスレッド約400件から、<b>自社ドメイン・提供元・フリーメール・ツール自動通知を除いた社外の取引先 161社</b>を抽出し、AIOマスターとの相性順に並べた台帳。",

  "criteria_sub": "AIOマスターは「AI検索で非指名の50問を定点観測し、掲載されたら1問1万円」という成果報酬型。したがって<b>掲載される余地があるか</b>と<b>掲載が売上につながるか</b>の2点で決まる。",
  "criteria": [
    { "label": "非指名検索の有無", "text": "顧客が社名ではなく「地域＋業種」でAIに聞く業種か。…" }
  ],

  "categories": [
    { "key": "partner", "label": "再販・紹介" },
    { "key": "web",     "label": "Web・広告" }
  ],

  "tiers": [
    {
      "key": "S",
      "name": "いま出せば決まる",
      "why": "AIOの商談が動いている、または契約・請求が走っていて提案を持ち込みやすい先。",
      "rows": [
        {
          "name": "サンプル司法書士事務所",
          "person": "山田",
          "email": "yamada@sample-shihou.example.jp",
          "domain": "sample-shihou.example.jp",
          "cat": "consul",
          "industry": "司法書士",
          "tag": "aio",
          "note": "「AIOの件、先日はありがとうございました」<b>2026-08-27</b>（最終接触から33日）。見積の返事待ち。",
          "bounced": false
        }
      ]
    }
  ],

  "exclusions": [
    { "heading": "自社ドメイン", "items": ["example-partner.co.jp（全アドレス）"] },
    { "heading": "提供元", "items": ["writeup.co.jp（ライトアップとのやりとり）"] }
  ],

  "footer": [
    "<strong>この台帳の作り方。</strong>Gmailの <code>in:sent newer_than:1y</code> を全ページ走査し…",
    "<strong>確からしさの内訳。</strong>…",
    "<strong>次にやるなら。</strong>…"
  ],

  "tally_extra": [
    { "label": "バウンス", "value": 4, "unit": "社" }
  ]
}
```

## 一覧表（CSV・スプレッドシート）

- `row.last_contact` … 最終接触日（`YYYY-MM-DD`）。CSVの「最終接触日」列に入る
- `sheet_url` … Googleスプレッドシートを作ったときだけ入れる。台帳上部に「スプレッドシートで開く」が出る
- CSVは `build_ledger.py` が台帳と同時に `<出力名>.csv` として書き出し、台帳にも埋め込む（ダウンロードボタン用）。
  列：ランク / ランク名 / 会社名 / 担当者名 / メールアドレス / ドメイン / 業種 / カテゴリ / 確からしさ / 最終接触日 / バウンス / 注記

## 自動で計算されるもの（書かない）

- 集計ストリップの「抽出社数 / AIO商談が稼働中 / 再販できる立場 / 業種の裏取り待ち」
- 各Tierの見出しに付く「— N社」
- フィルタのチップ（`categories` のうち、実際に行があるものだけ出る）

手で書いた数字は必ずズレるので、書ける口を用意していない。
追加で見せたい数字がある場合だけ `tally_extra` に足す。

## `key` に使える値

- `tier.key` … `S` / `A` / `B` / `C`（CSSは `tier--s` と `tier--a` に階調を持たせてある）
- `row.cat` … `references/rubric.md` のカテゴリ表を参照
- `row.tag` … `aio` / `fact` / `ask`（省略可）
- `row.bounced` … `true` にすると注記に「※メールがバウンス」が付く
- `row.person` / `row.email` … 1列目が「会社名 / 〇〇 様 / メールアドレス」の3段になる。
  `companies_raw.json` の `contacts[0]` をそのまま入れる。無ければドメインだけ出る
- `row.name` … `companies_raw.json` の `company_name`（メールに出た社名）があればそれを使う。
  無ければドメインから読める名前にして `tag: "ask"`

## 落ちる条件（わざと落としている）

- `cat` が `categories` にない
- `tag` が3種以外
- 同じ `domain` が2行に出てくる
- `categories` か `tiers` が空

台帳は数字と一意性がすべてなので、壊れたまま出さない。
