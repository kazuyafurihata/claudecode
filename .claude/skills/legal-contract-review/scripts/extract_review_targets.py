#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract review targets from a Word document (.docx).

Detects:
- Colored text (especially red) — likely counterparty additions/highlights
- Tracked changes (w:ins, w:del)
- Comments (w:comment)

Outputs JSON so the skill can focus review on these flagged sections.

Usage:
    python extract_review_targets.py --input contract.docx --output targets.json
    python extract_review_targets.py -i contract.docx -o targets.json --verbose

For .doc files: open in Word and Save As .docx first (this environment has no libreoffice).
"""

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


# Word namespace
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}


def is_reddish(color_hex):
    """判定: 色コードが赤系か (FF0000 など)"""
    if not color_hex or color_hex == "auto":
        return False
    c = color_hex.upper().lstrip("#")
    if len(c) != 6:
        return False
    try:
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        return False
    # 赤成分が G・B より明確に大きく、赤成分自体もある程度濃い
    return r >= 0x80 and r > g + 0x30 and r > b + 0x30


def get_run_text(run):
    """run 要素のテキスト内容（タブや改行を含む）を抽出"""
    parts = []
    for child in run.iter():
        tag = child.tag.split("}", 1)[-1] if "}" in child.tag else child.tag
        if tag == "t":
            parts.append(child.text or "")
        elif tag == "tab":
            parts.append("\t")
        elif tag == "br":
            parts.append("\n")
    return "".join(parts)


def get_run_color(run):
    """run 要素の文字色を取得 (hex または 'auto')"""
    rpr = run.find("w:rPr", NS)
    if rpr is None:
        return None
    color_elem = rpr.find("w:color", NS)
    if color_elem is None:
        return None
    return color_elem.get("{%s}val" % W)


def get_run_highlight(run):
    """run 要素のハイライト色を取得"""
    rpr = run.find("w:rPr", NS)
    if rpr is None:
        return None
    hl = rpr.find("w:highlight", NS)
    if hl is None:
        return None
    return hl.get("{%s}val" % W)


def get_run_underline(run):
    """run 要素のアンダーライン有無"""
    rpr = run.find("w:rPr", NS)
    if rpr is None:
        return False
    u = rpr.find("w:u", NS)
    if u is None:
        return False
    val = u.get("{%s}val" % W)
    return val not in (None, "none", "0", "false")


def get_paragraph_text(paragraph):
    """段落全体のテキストを取得（全 run を連結）"""
    parts = []
    for run in paragraph.iter("{%s}r" % W):
        parts.append(get_run_text(run))
    return "".join(parts)


def extract_comments(docx_path):
    """comments.xml を読んで {comment_id: text} の辞書を返す"""
    comments = {}
    try:
        with zipfile.ZipFile(docx_path) as zf:
            if "word/comments.xml" not in zf.namelist():
                return comments
            xml = zf.read("word/comments.xml")
    except Exception:
        return comments

    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return comments

    for c in root.iter("{%s}comment" % W):
        cid = c.get("{%s}id" % W)
        author = c.get("{%s}author" % W, "")
        text_parts = []
        for t in c.iter("{%s}t" % W):
            text_parts.append(t.text or "")
        comments[cid] = {
            "author": author,
            "text": "".join(text_parts).strip(),
        }
    return comments


def extract_targets(docx_path):
    """docx から変更箇所（赤文字/挿入/削除/コメント）を抽出"""
    docx_path = Path(docx_path)
    with zipfile.ZipFile(docx_path) as zf:
        document_xml = zf.read("word/document.xml")

    root = ET.fromstring(document_xml)
    comments_map = extract_comments(docx_path)

    result = {
        "file": docx_path.name,
        "colored_runs": [],       # 色付きテキスト (主に赤)
        "tracked_insertions": [],  # 変更履歴(挿入)
        "tracked_deletions": [],   # 変更履歴(削除)
        "comments": [],            # コメント
        "summary": {},
    }

    # 段落単位で処理
    for p_idx, p in enumerate(root.iter("{%s}p" % W), 1):
        para_text = get_paragraph_text(p)
        if not para_text.strip():
            continue

        # 1. colored runs (直接の子と w:ins/w:del 配下を含む)
        for run in p.iter("{%s}r" % W):
            text = get_run_text(run)
            if not text.strip():
                continue
            color = get_run_color(run)
            highlight = get_run_highlight(run)
            underline = get_run_underline(run)

            is_red = color and is_reddish(color)
            is_highlighted = highlight not in (None, "none")

            if is_red or is_highlighted:
                result["colored_runs"].append({
                    "paragraph_no": p_idx,
                    "paragraph_text": para_text.strip()[:200],
                    "text": text.strip(),
                    "color": color,
                    "highlight": highlight,
                    "underline": underline,
                    "is_red": bool(is_red),
                })

        # 2. tracked insertions (w:ins)
        for ins in p.iter("{%s}ins" % W):
            author = ins.get("{%s}author" % W, "")
            date = ins.get("{%s}date" % W, "")
            ins_text = ""
            for run in ins.iter("{%s}r" % W):
                ins_text += get_run_text(run)
            if ins_text.strip():
                result["tracked_insertions"].append({
                    "paragraph_no": p_idx,
                    "paragraph_text": para_text.strip()[:200],
                    "inserted_text": ins_text.strip(),
                    "author": author,
                    "date": date,
                })

        # 3. tracked deletions (w:del)
        for delete in p.iter("{%s}del" % W):
            author = delete.get("{%s}author" % W, "")
            date = delete.get("{%s}date" % W, "")
            del_text = ""
            for dt in delete.iter("{%s}delText" % W):
                del_text += dt.text or ""
            if del_text.strip():
                result["tracked_deletions"].append({
                    "paragraph_no": p_idx,
                    "paragraph_text": para_text.strip()[:200],
                    "deleted_text": del_text.strip(),
                    "author": author,
                    "date": date,
                })

        # 4. comment references
        for ref in p.iter("{%s}commentReference" % W):
            cid = ref.get("{%s}id" % W)
            if cid in comments_map:
                result["comments"].append({
                    "paragraph_no": p_idx,
                    "paragraph_text": para_text.strip()[:200],
                    "comment_id": cid,
                    "author": comments_map[cid]["author"],
                    "comment_text": comments_map[cid]["text"],
                })

    result["summary"] = {
        "colored_runs_count": len(result["colored_runs"]),
        "red_runs_count": sum(1 for r in result["colored_runs"] if r.get("is_red")),
        "highlighted_runs_count": sum(1 for r in result["colored_runs"] if r.get("highlight")),
        "tracked_insertions_count": len(result["tracked_insertions"]),
        "tracked_deletions_count": len(result["tracked_deletions"]),
        "comments_count": len(result["comments"]),
    }

    return result


def print_human_summary(data, verbose=False):
    """人が読みやすい形でターミナル出力"""
    s = data["summary"]
    print("=" * 60)
    print("File: " + data["file"])
    print("-" * 60)
    print("赤色テキスト:        " + str(s["red_runs_count"]) + " 箇所")
    print("ハイライト:          " + str(s["highlighted_runs_count"]) + " 箇所")
    print("色付き全体:          " + str(s["colored_runs_count"]) + " 箇所")
    print("トラックチェンジ挿入: " + str(s["tracked_insertions_count"]) + " 箇所")
    print("トラックチェンジ削除: " + str(s["tracked_deletions_count"]) + " 箇所")
    print("コメント:            " + str(s["comments_count"]) + " 箇所")
    print("=" * 60)

    if not verbose:
        return

    if data["colored_runs"]:
        print("\n## 色付きテキスト詳細:")
        for i, r in enumerate(data["colored_runs"], 1):
            flag = "[赤]" if r.get("is_red") else ("[HL:" + str(r.get("highlight")) + "]" if r.get("highlight") else "[色]")
            print(str(i) + ". " + flag + " 段落" + str(r["paragraph_no"]) + ": " + r["text"][:120])

    if data["tracked_insertions"]:
        print("\n## トラックチェンジ挿入:")
        for i, r in enumerate(data["tracked_insertions"], 1):
            print(str(i) + ". 段落" + str(r["paragraph_no"]) + " by " + r.get("author", "?") + ": +" + r["inserted_text"][:120])

    if data["tracked_deletions"]:
        print("\n## トラックチェンジ削除:")
        for i, r in enumerate(data["tracked_deletions"], 1):
            print(str(i) + ". 段落" + str(r["paragraph_no"]) + " by " + r.get("author", "?") + ": -" + r["deleted_text"][:120])

    if data["comments"]:
        print("\n## コメント:")
        for i, r in enumerate(data["comments"], 1):
            print(str(i) + ". 段落" + str(r["paragraph_no"]) + " by " + r.get("author", "?") + ": " + r["comment_text"][:150])


def main():
    parser = argparse.ArgumentParser(description="Extract review targets from .docx")
    parser.add_argument("--input", "-i", required=True, help=".docx input path")
    parser.add_argument("--output", "-o", help="JSON output path (optional)")
    parser.add_argument("--verbose", "-v", action="store_true", help="print details")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print("ERROR: not found: " + str(input_path), file=sys.stderr)
        sys.exit(1)

    if input_path.suffix.lower() != ".docx":
        print("ERROR: input must be .docx. For .doc, open in Word and Save As .docx first.", file=sys.stderr)
        sys.exit(1)

    data = extract_targets(input_path)
    print_human_summary(data, verbose=args.verbose)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\nJSON: " + str(out_path))


if __name__ == "__main__":
    main()
