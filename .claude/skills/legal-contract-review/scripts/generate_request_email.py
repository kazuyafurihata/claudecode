#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate legal review request email draft (Markdown).

Usage:
    python generate_request_email.py --input review_data.json --output email.md
"""

import argparse
import json
import sys
from pathlib import Path


MGR_APPROVERS = ["sugiyama", "edo", "tahara", "kawakami", "shiozaki"]
MGR_APPROVERS_JP = ["杉山", "江戸", "田原", "川上", "塩崎"]

CATEGORY_SUBJECT_MAP = {
    "nda": "秘密保持契約書",
    "gyomu-itaku": "業務委託契約書",
    "gyomu-teikei": "業務提携契約書",
    "dairiten": "代理店契約書",
    "jinzai-shokai": "人材紹介基本契約書",
    "kakoyo": "覚書",
    "ryakin-shohi": "金銭消費貸借契約書",
    "riyo-kiyaku": "利用規約",
    "kojin-joho": "個人情報取扱いに関する覚書",
}


def build_subject(data):
    # 正式テンプレ：件名＝「文章名 先方正式会社名」（接頭辞なし・スペース区切り）
    meta = data.get("email_meta", {}) or {}
    title = (data.get("contract_title") or "").strip()
    if not title:
        title = CATEGORY_SUBJECT_MAP.get(data.get("category", ""), "契約書")
    counterparty = (meta.get("counterparty_name") or "").strip()
    if not counterparty:
        counterparty = "【先方正式会社名を記入してください】"
    if counterparty and counterparty not in title:
        return title + "　" + counterparty
    return title


def build_opening(data):
    meta = data.get("email_meta", {}) or {}
    # 送信者氏名は固定で埋めず、本人が手書きする前提で ◯◯◯ プレースホルダにする
    dept = (meta.get("sender_department") or "").strip() or "◯◯"
    name = (meta.get("sender_name") or "").strip() or "◯◯◯"
    return (
        "村越さん\n"
        "お疲れ様です。" + dept + "の" + name + "です。\n"
        "以下、リーガルチェックをお願いいたします。"
    )


def build_purpose_block(data):
    meta = data.get("email_meta", {}) or {}
    purpose = (meta.get("purpose_summary") or "").strip()
    if not purpose:
        category_label = data.get("category_label") or data.get("category") or ""
        counterparty = (meta.get("counterparty_name") or "").strip() or "【先方】"
        purpose = (
            "表題企業（" + counterparty + "）との間で、"
            + category_label + "を締結します。\n"
            "（取引の背景・目的・当社/先方の役割を2〜3行で平易にご記入ください。"
            "部門外の方にも分かる表現を心がけてください）"
        )
    return "■本契約の目的や取引概要\n" + purpose


def build_findings_block(data):
    findings = data.get("findings") or []
    critical = [f for f in findings if f.get("severity") == "必須"]
    recommended = [f for f in findings if f.get("severity") == "推奨"]

    if not critical and not recommended:
        body = "特に問題がない認識です。"
    else:
        lines = [
            "以下の点についてリスクが想定されるため、修正案を添付の契約書に反映しています。ご意見を頂けますと幸いです。",
            "",
        ]
        for f in critical:
            article = f.get("article") or "該当条項"
            topic = f.get("topic") or ""
            lines.append("・" + article + "：" + topic + "（必須）")
        for f in recommended:
            article = f.get("article") or "該当条項"
            topic = f.get("topic") or ""
            lines.append("・" + article + "：" + topic + "（推奨）")

        top3 = data.get("summary_top3") or []
        if top3:
            lines.append("")
            lines.append("【特に注意すべき論点】")
            for i, t in enumerate(top3, 1):
                lines.append("  " + str(i) + ". " + t)

        body = "\n".join(lines)

    return "■本書面に関する申請部門の見解\n" + body


def build_reviewer_block(data):
    # 法務へ送るメール本文には「MGR以上の事前確認が必要」「確認権限保有者：…」の注記は入れない。
    # （内部統制のための事前確認であり、村越氏宛の本文に含めるものではないため）
    # MGR事前確認のリマインドはメール本文の外側（build_mgr_warning）にのみ表示する。
    meta = data.get("email_meta", {}) or {}
    reviewer = (meta.get("reviewer_name") or "").strip()

    lines = ["■規約確認者"]

    if reviewer:
        lines.append(reviewer)
    else:
        lines.append("◯◯◯")

    return "\n".join(lines)


def build_confirmation_block():
    return (
        "■本人確認欄（以下、確認のうえ送付しました）\n"
        "１．ファイル添付が漏れ、添付ファイルの誤りがないことを確認しました\n"
        "２．送付先アドレスはlegal@宛に送付したことを確認しました\n"
        "３．メール件名は文章名＋相手先正式名称を記載しました"
    )


def build_mgr_warning(data):
    meta = data.get("email_meta", {}) or {}
    needs_mgr = bool(meta.get("needs_mgr_approval"))

    findings = data.get("findings") or []
    keywords = ["違約金", "競業避止", "損害賠償", "先方ひな型", "先方書式"]
    has_critical = any(
        any(kw in ((f.get("topic") or "") + (f.get("expected_comment") or "")) for kw in keywords)
        for f in findings if f.get("severity") == "必須"
    )

    if not needs_mgr and not has_critical:
        return ""

    reason = (meta.get("approval_reason") or "").strip()
    if not reason and has_critical:
        reason = "当社にとって重要な条項（違約金・競業避止・損害賠償等）の変更が含まれる"

    if reason:
        clean = reason.rstrip().rstrip("。、").rstrip()
        if clean.endswith("ため") or clean.endswith("から"):
            reason_phrase = clean + "、"
        else:
            reason_phrase = clean + "ため、"
    else:
        reason_phrase = ""

    approvers = "／".join(MGR_APPROVERS_JP)
    return (
        "> ⚠️ **事前確認の注意（※この注意書きはメール本文には含めません）**\n"
        "> " + reason_phrase + "本メール送付前にMGR以上の役職者の事前確認を受けてください。\n"
        "> 確認権限保有者：" + approvers + "\n"
    )


def build_disclaimer():
    return (
        "<!--\n"
        "この下書きは AI (legal-contract-review スキル) により自動生成されました。\n"
        "送信前に必ず以下を実施してください：\n"
        "  1. ◯◯（部署）／◯◯◯（氏名）／【先方正式会社名】等のプレースホルダを実際の値に置換\n"
        "  2. 「本契約の目的や取引概要」の記述を具体化（2-3行で）\n"
        "  3. 「申請部門の見解」を自社の判断として再構成\n"
        "  4. 規約確認者（MGR以上）に事前承諾を取得\n"
        "  5. この HTML コメントそのものを削除\n"
        "-->\n"
    )


def build_placeholder_list(data):
    meta = data.get("email_meta", {}) or {}
    placeholders = []
    # 送信者氏名（◯◯◯）は本人が手書きする前提のため、データ有無にかかわらず常に明示する
    placeholders.append("- 冒頭の `◯◯` → 自分の所属部署（例：経営コンサルティング局）")
    placeholders.append("- 冒頭の `◯◯◯` → 送信者本人の氏名（本人確認のうえ送付するため必ず手入力）")
    if not (meta.get("counterparty_name") or "").strip():
        placeholders.append("- `【先方正式会社名を記入してください】` → 契約相手の会社名")
    if not (meta.get("purpose_summary") or "").strip():
        placeholders.append("- 「本契約の目的や取引概要」の記述 → 具体的な取引概要に書き換え")
    approvers = " / ".join(MGR_APPROVERS_JP)
    placeholders.append(
        "- 規約確認者の `◯◯◯` → 事前に確認したMGR以上の役職者名（"
        + approvers + "のいずれか）"
    )
    return placeholders


def build_email(data):
    subject = build_subject(data)
    warning = build_mgr_warning(data)
    opening = build_opening(data)
    purpose = build_purpose_block(data)
    findings = build_findings_block(data)
    reviewer = build_reviewer_block(data)
    confirmation = build_confirmation_block()
    disclaimer = build_disclaimer()

    parts = []
    parts.append("# 法務リーガルチェック依頼メール 下書き\n")
    parts.append("**件名**：" + subject + "\n")
    parts.append("**宛先**：legal@writeup.co.jp\n")
    if warning:
        parts.append(warning)
    parts.append("---\n")
    parts.append("```")
    parts.append("件名：" + subject)
    parts.append("")
    parts.append(opening)
    parts.append("")
    parts.append(purpose)
    parts.append("")
    parts.append(findings)
    parts.append("")
    parts.append(reviewer)
    parts.append("")
    parts.append(confirmation)
    parts.append("```\n")
    parts.append("---\n")
    parts.append("## 手動で差し替えが必要なプレースホルダ\n")
    placeholders = build_placeholder_list(data)
    if placeholders:
        parts.append("\n".join(placeholders) + "\n")
    else:
        parts.append("（プレースホルダなし。そのまま送信可能です）\n")
    parts.append(disclaimer)
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Generate legal review request email.")
    parser.add_argument("--input", "-i", required=True, help="review_data.json path")
    parser.add_argument("--output", "-o", required=True, help="output .md path")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print("ERROR: input not found: " + str(input_path), file=sys.stderr)
        sys.exit(1)

    with input_path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_email(data), encoding="utf-8")
    print("OK: " + str(output_path))


if __name__ == "__main__":
    main()
