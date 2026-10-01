#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判定済みの会社リスト(JSON)から、提案先台帳のHTMLを組む。

集計値と各Tierの社数は必ずデータから数える。手で書くと必ずズレて、
「161社」と言いながら内訳が132社、という台帳ができあがる。数字が合わない
台帳は営業現場で一度で信用を失うので、ここは人間にもモデルにも触らせない。

使い方:
  python build_ledger.py ledger.json -o aio-target-ledger.html

入力スキーマは references/ledger_schema.md を見る。
"""
import argparse
import csv
import html
import io
import json
import os
import re
import sys

# Windowsのコンソール(cp932)で日本語を出すと落ちるので、UTF-8に寄せる。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TAGS = {
    "aio": ("AIO", "AIOの商談がすでに動いている"),
    "fact": ("確", "件名から業務内容が確認できた"),
    "ask": ("推", "ドメイン名からの推定"),
}


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def die(msg):
    sys.exit(f"[build_ledger] {msg}")


def validate(d):
    """出す前に落とす。台帳は数字がすべてなので、壊れたまま出さない。"""
    cats = {c["key"] for c in d.get("categories", [])}
    if not cats:
        die("categories が空。フィルタのチップが作れない。")
    if not d.get("tiers"):
        die("tiers が空。")
    seen, problems = {}, []
    for t in d["tiers"]:
        for r in t.get("rows", []):
            if not r.get("name"):
                problems.append(f"名前のない行がある (tier {t.get('key')})")
            if r.get("cat") not in cats:
                problems.append(f"{r.get('name')}: 未定義のcat '{r.get('cat')}'")
            if r.get("tag") and r["tag"] not in TAGS:
                problems.append(f"{r.get('name')}: 未定義のtag '{r.get('tag')}'")
            dom = (r.get("domain") or "").lower()
            if dom:
                if dom in seen:
                    problems.append(f"ドメイン重複: {dom} ({seen[dom]} / {r.get('name')})")
                seen[dom] = r.get("name")
    if problems:
        die("入力エラー:\n  - " + "\n  - ".join(problems))


def tally_block(d, rows):
    total = len(rows)
    aio = sum(1 for r in rows if r.get("tag") == "aio")
    partner = sum(1 for r in rows if r.get("cat") == "partner")
    unsure = sum(1 for r in rows if r.get("tag") == "ask")
    items = [("抽出社数", total, "社"),
             ("AIO商談が稼働中", aio, "社"),
             ("再販できる立場", partner, "社"),
             ("業種の裏取り待ち", unsure, "社")]
    for extra in d.get("tally_extra", []):
        items.append((extra["label"], extra["value"], extra.get("unit", "")))
    out = []
    for label, value, unit in items:
        u = f"<small>{esc(unit)}</small>" if unit else ""
        out.append(f'    <div><dt>{esc(label)}</dt><dd>{esc(value)}{u}</dd></div>')
    return "\n".join(out)


def criteria_block(d):
    out = []
    for c in d.get("criteria", []):
        out.append(
            f'    <div class="crit">\n'
            f'      <h3>{esc(c["label"])}</h3>\n'
            f'      <p>{c["text"]}</p>\n'
            f'    </div>'
        )
    return "\n".join(out)


def chips_block(d, rows):
    used = {r.get("cat") for r in rows}
    out = ['    <button class="chip" data-f="all" aria-pressed="true">すべて</button>']
    for c in d["categories"]:
        if c["key"] not in used:
            continue  # 0件のチップを出すと押しても何も起きず、壊れて見える
        out.append(f'    <button class="chip" data-f="{esc(c["key"])}" '
                   f'aria-pressed="false">{esc(c["label"])}</button>')
    return "\n".join(out)


def row_html(r):
    tag = ""
    if r.get("tag"):
        label = TAGS[r["tag"]][0]
        tag = f'<span class="tag tag--{esc(r["tag"])}">{esc(label)}</span>'
    note = r.get("note", "")
    if r.get("bounced"):
        note += ' <b>※メールがバウンス</b>'
    # 1列目は「会社名 / 担当者名 / メールアドレス」の3段。担当者やアドレスが無ければドメインだけ出す
    person = (f'<div class="person">{esc(r["person"])} 様</div>' if r.get("person") else "")
    email = r.get("email") or r.get("domain", "")
    return (f'      <div class="row" data-cat="{esc(r["cat"])}">'
            f'<div class="who"><div class="co"><span class="co-name">{esc(r["name"])}</span>{tag}</div>'
            f'{person}<div class="dom">{esc(email)}</div></div>'
            f'<div class="ind">{esc(r.get("industry", "要確認"))}</div>'
            f'<div class="note">{note}</div></div>')


def tiers_block(d):
    out = []
    for t in d["tiers"]:
        rows = t.get("rows", [])
        if not rows:
            continue
        key = esc(t["key"])
        cls = f'tier tier--{key.lower()}'
        out.append(
            f'  <div class="{cls}" data-tier="{key}">\n'
            f'    <div class="tier-head">\n'
            f'      <span class="tier-mark">{key}</span>\n'
            f'      <h3 class="tier-name">{esc(t["name"])} — {len(rows)}社</h3>\n'
            f'      <p class="tier-why">{t["why"]}</p>\n'
            f'    </div>\n'
            f'    <div class="rows">\n'
            + "\n".join(row_html(r) for r in rows) +
            f'\n    </div>\n'
            f'  </div>'
        )
    return "\n\n".join(out)


def exclusions_block(d):
    out = []
    for e in d.get("exclusions", []):
        lis = "\n".join(f'        <li>{esc(i)}</li>' for i in e.get("items", []))
        out.append(f'    <div>\n      <h3>{esc(e["heading"])}</h3>\n'
                   f'      <ul>\n{lis}\n      </ul>\n    </div>')
    return "\n".join(out)


CSV_COLS = ["ランク", "ランク名", "会社名", "担当者名", "メールアドレス", "ドメイン", "業種",
            "カテゴリ", "確からしさ", "最終接触日", "バウンス", "注記"]


def plain(s):
    """注記のHTMLを表計算向けの素の文字列にする。"""
    return html.unescape(re.sub(r"<[^>]+>", "", str(s or ""))).strip()


def csv_text(d):
    """台帳と同じ並び・同じ判定で一覧表を作る。台帳とCSVで中身がズレないよう、ここだけを正にする。"""
    cats = {c["key"]: c["label"] for c in d["categories"]}
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_COLS)
    for t in d["tiers"]:
        for r in t.get("rows", []):
            w.writerow([
                t["key"], t["name"], r.get("name", ""), r.get("person", ""),
                r.get("email", ""), r.get("domain", ""), r.get("industry", "要確認"),
                cats.get(r.get("cat"), ""), TAGS[r["tag"]][1] if r.get("tag") else "",
                r.get("last_contact", ""), "あり" if r.get("bounced") else "", plain(r.get("note")),
            ])
    return buf.getvalue()


def export_block(d, csv_name, csv_body):
    sheet = d.get("sheet_url")
    out = ['  <div class="export">']
    if sheet:
        out.append(f'    <a class="btn btn--primary" href="{esc(sheet)}" target="_blank" rel="noopener">'
                   f'スプレッドシートで開く</a>')
    out.append(f'    <button type="button" class="btn{"" if sheet else " btn--primary"}" id="dl-csv" '
               f'data-filename="{esc(csv_name)}">CSVをダウンロード（Excel可）</button>')
    out.append('    <span class="export-note">台帳と同じ内容・同じ並びの一覧です</span>')
    out.append('  </div>')
    # </script> で埋め込みが途切れないよう、"</" だけ逃がす
    # HTMLはテキストモードで書くのでWindowsでは改行が二重(\r\r\n)になる。埋め込みは\nにして、JS側でCRLFに戻す
    body = csv_body.replace("\r\n", "\n").replace("</", "<\\/")
    out.append('  <script type="text/plain" id="csv-data">' + body + '</script>')
    return "\n".join(out)


def footer_block(d):
    return "\n".join(f'  <p>{p}</p>' for p in d.get("footer", []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data", help="台帳データのJSON")
    ap.add_argument("-o", "--out", default="aio-target-ledger.html")
    ap.add_argument("--template", default=None)
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    tpl_path = args.template or os.path.join(here, "..", "assets", "ledger_template.html")
    with open(os.path.expanduser(tpl_path), encoding="utf-8") as f:
        tpl = f.read()
    with open(os.path.expanduser(args.data), encoding="utf-8") as f:
        d = json.load(f)

    validate(d)
    rows = [r for t in d["tiers"] for r in t.get("rows", [])]

    # 一覧表(CSV)を台帳の隣に書き出し、同じ中身を台帳にも埋め込む(ダウンロードボタン用)
    out = os.path.expanduser(args.out)
    csv_path = os.path.splitext(out)[0] + ".csv"
    csv_body = csv_text(d)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(csv_body)

    filled = tpl
    for token, value in [
        ("{{TITLE}}", esc(d.get("title", "AIO提案先 優先台帳"))),
        ("{{EYEBROW}}", esc(d.get("eyebrow", ""))),
        ("{{LEDE}}", d.get("lede", "")),
        ("{{TALLY}}", tally_block(d, rows)),
        ("{{EXPORT}}", export_block(d, os.path.basename(csv_path), csv_body)),
        ("{{CRITERIA_SUB}}", d.get("criteria_sub", "")),
        ("{{CRITERIA}}", criteria_block(d)),
        ("{{CHIPS}}", chips_block(d, rows)),
        ("{{TIERS}}", tiers_block(d)),
        ("{{EXCLUSIONS}}", exclusions_block(d)),
        ("{{FOOTER}}", footer_block(d)),
    ]:
        filled = filled.replace(token, value)

    left = [t for t in ("{{TITLE}}", "{{TIERS}}", "{{TALLY}}") if t in filled]
    if left:
        die(f"テンプレートに未置換のトークンが残っている: {left}")

    with open(out, "w", encoding="utf-8") as f:
        f.write(filled)

    per_tier = " / ".join(f'{t["key"]}={len(t.get("rows", []))}'
                          for t in d["tiers"] if t.get("rows"))
    print(f"{len(rows)}社 ({per_tier}) -> {os.path.abspath(out)}")
    print(f"一覧表(CSV) -> {os.path.abspath(csv_path)}"
          + ("" if d.get("sheet_url") else "  ※スプレッドシートのリンクは未設定"))


if __name__ == "__main__":
    main()
