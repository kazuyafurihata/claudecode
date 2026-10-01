#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""送信済みメールのスレッドJSONから、社外の取引先ドメインを抽出する。

Gmailコネクタの search_threads は結果をセッションの tool-results/ に .txt (中身はJSON)
として落とす。それを全ページぶん食わせると、除外ルールを当てたうえで
「ドメイン / 宛先アドレス / 接触回数 / 初回・最終接触日 / 件名」に畳んで出す。

数百スレッドを本文ごと読むとコンテキストが飽和するので、集計はここでやる。
読むのは出力のCSV/JSONだけでよい。

使い方:
  python extract_domains.py \
      --glob "<tool-resultsのフォルダ>/*search_threads*.txt" \
      --out companies_raw.json --csv companies_raw.csv

--me を省略すると、送信済みスレッドで最も多い送信者を自分のアドレスとみなす。
パートナー各社で使い回すので、アドレスをスキルに書き込まないための仕組み。

除外ドメインを足したいときは --extra-exclude domain1,domain2 で渡す。
スキル側のリストは書き換えない(次に使う人の結果が黙って変わるため)。
"""
import argparse
import csv
import glob as globmod
import html
import json
import os
import re
import sys
from collections import Counter, OrderedDict

# Windowsのコンソール(cp932)で日本語や絵文字の件名を出すと落ちるので、UTF-8に寄せる。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# --- 除外ルール ---------------------------------------------------------
# フリーメール・ISPメール。会社として数えると台帳が個人で埋まる。
FREEMAIL = {
    "gmail.com", "googlemail.com", "yahoo.co.jp", "yahoo.com", "ybb.ne.jp",
    "hotmail.com", "hotmail.co.jp", "outlook.com", "outlook.jp", "live.jp",
    "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com",
    "docomo.ne.jp", "ezweb.ne.jp", "au.com", "softbank.ne.jp", "i.softbank.jp",
    "ocn.ne.jp", "so-net.ne.jp", "nifty.com", "biglobe.ne.jp", "plala.or.jp",
    "zaq.ne.jp", "jcom.home.ne.jp", "dion.ne.jp", "infoseek.jp", "excite.co.jp",
    "msn.com", "zoho.com",
}

# ツールの自動通知・決済・配信基盤。人が読んでいない宛先。
TOOL_DOMAINS = {
    "stripe.com", "squareup.com", "messaging.squareup.com", "paypal.com",
    "sendgrid.net", "sendgrid.com", "mailchimp.com", "hubspot.com",
    "salesforce.com", "zoom.us", "calendly.com", "docusign.net",
    "cloudsign.jp", "freee.co.jp", "moneyforward.com", "slack.com",
    "notion.so", "github.com", "atlassian.net", "google.com",
    "accounts.google.com", "docs.google.com", "drive.google.com",
    "amazonaws.com", "amazon.co.jp", "apple.com", "microsoft.com",
    "amazonses.com", "mktomail.com", "loom.com", "peatix.com",
}

# 商材の提供元。パートナーが使うと提供元とのやりとりが大量に出るが、提案先ではない。
SUPPLIER_DOMAINS = {"writeup.co.jp"}

# 自動送信であることがアドレスから分かるローカル部。
AUTO_LOCAL = re.compile(
    r"^(no-?reply|do-?not-?reply|postmaster|mailer-daemon|bounce|bounces|"
    r"notification|notifications|automail|auto|system|info-?bot|support-?bot|"
    r"newsletter|mailmagazine|magazine|delivery)([-+._].*)?$",
    re.I,
)

# 自動返信(不在通知)の件名。育休・長期不在の期間に数百件たまり、宛先がすべて配信基盤になる。
AUTO_REPLY_SUBJECT = re.compile(
    r"(不在のお知らせ|不在通知|自動返信|自動応答|お休みをいただいて|休業のお知らせ|"
    r"out of (the )?office|automatic reply|auto[- ]?reply)",
    re.I,
)

# バウンス通知の送信元。ここに出てきたドメインは「宛先が死んでいる」印として拾う。
BOUNCE_SENDER = re.compile(r"(mailer-daemon|postmaster)@", re.I)

ADDR = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")

# 社名・担当者名の拾い出し。本文は読まないので、スニペット(冒頭約200字)だけが材料になる。
# 自分が送ったメールの冒頭「株式会社〇〇 山田さま」から宛名を、
# 相手が送ってきたメールの「株式会社〇〇」表記から社名を拾う。
_NAME_STOP = r"\s、。,，:：（）()｜|/／「」『』【】\[\]<>＜＞&;"
CORP = re.compile(
    rf"((?:株式会社|有限会社|合同会社|一般社団法人|一般財団法人|公益財団法人|医療法人|社会福祉法人|"
    rf"税理士法人|弁護士法人|行政書士法人)[^{_NAME_STOP}]{{1,18}}"
    rf"|[^{_NAME_STOP}]{{1,18}}(?:株式会社|有限会社|合同会社|事務所|法律事務所|クリニック))"
)
SALUTATION = re.compile(
    rf"^\s*(?:(?P<corp>[^{_NAME_STOP}]{{2,24}})\s+)?(?P<name>[^{_NAME_STOP}]{{1,10}}?)\s*(?:さま|様|さん)(?:[\s、,]|$)"
)
NAME_NG = {"皆", "皆様", "みな", "各位", "ご担当者", "担当者", "ご担当", "お客", "関係者", "パートナー",
           "松井", "お世話になっております", "いつもお世話になっております"}


def corp_names(text, ng_words):
    out = []
    for m in CORP.finditer(html.unescape(text or "")):
        # 「株式会社〇〇の山田です」の「の山田です」、署名の飾り記号を落とす
        s = re.sub(r"^[★☆■□◆◇●○・*＊=＝\-—]+", "", m.group(1).strip())
        s = re.sub(r"の[^の]{1,10}?(です|と申します|でございます)[!！]*$", "", s)
        # 罫線入りの署名、「〇〇会社の株式会社」「すでに事務所」のような文中の切れ端、途中で切れた社名は捨てる
        if (len(s) < 3 or re.search(r"[━┃┏┓┗┛─│]", s) or re.search(r"の(株式会社|事務所)$", s)
                or s.endswith(("・", "－", "-")) or re.match(r"^[ぁ-ん]{2,}(事務所|株式会社)$", s)):
            continue
        if any(w in s for w in ng_words):
            continue
        out.append(s)
    return out

# Git Bash から渡すと /c/Users/... の形で来るが、Windows版Pythonはこれを解決できない。
# 毎回ここで詰まるので受け付けて直す。
MSYS_PATH = re.compile(r"^/([a-zA-Z])/")


def winpath(p):
    if os.name != "nt":
        return p
    return MSYS_PATH.sub(lambda m: m.group(1).upper() + ":/", p)


def fixpath(p):
    return os.path.expanduser(winpath(p))


def norm_addr(raw):
    """'山田太郎 <a@b.jp>' や '"A" <a@b.jp>' からアドレス本体を取り出す。"""
    if not raw:
        return None
    m = ADDR.search(str(raw))
    return m.group(0).lower() if m else None


def domain_of(addr):
    return addr.rsplit("@", 1)[1] if addr and "@" in addr else None


def registrable(dom):
    """mail.example.co.jp と example.co.jp を同じ会社として畳む。

    完全なPublic Suffix判定はしない(依存を増やしたくない)。
    日本企業でよく出る2段TLDだけ手当てすれば実務では足りる。
    """
    if not dom:
        return None
    parts = dom.split(".")
    two_level = {"co.jp", "or.jp", "ne.jp", "ac.jp", "go.jp", "gr.jp", "lg.jp",
                 "com.cn", "co.uk", "com.au", "co.kr"}
    if len(parts) >= 3 and ".".join(parts[-2:]) in two_level:
        return ".".join(parts[-3:])
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return dom


def load_threads(paths):
    """search_threads の保存結果を読む。1ファイル1ページ。"""
    threads, seen_ids = [], set()
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:  # ページによっては別ツールの結果が混ざる
            print(f"  skip {p}: {e}", file=sys.stderr)
            continue
        for t in data.get("threads", []) or []:
            tid = t.get("id")
            if tid in seen_ids:
                continue
            seen_ids.add(tid)
            threads.append(t)
    return threads


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--me", default=None,
                    help="自分のアドレス。これが sender のメッセージだけを送信済みとして見る。"
                         "省略時は最多の送信者から自動判定")
    ap.add_argument("--glob", action="append", required=True,
                    help="search_threads の保存ファイルのglob。複数回指定可")
    ap.add_argument("--own-domain", action="append", default=[],
                    help="自社ドメイン。未指定なら --me のドメインから推定")
    ap.add_argument("--extra-exclude", default="",
                    help="追加で除外するドメインのカンマ区切り")
    ap.add_argument("--own-name", action="append", default=[],
                    help="自社の社名(相手の社名と取り違えないための除外語)。複数回指定可")
    ap.add_argument("--out", default="companies_raw.json")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--max-subjects", type=int, default=6)
    args = ap.parse_args()
    args.out = fixpath(args.out)
    if args.csv:
        args.csv = fixpath(args.csv)

    paths = []
    for g in args.glob:
        paths.extend(sorted(globmod.glob(fixpath(g))))
    if not paths:
        sys.exit("スレッドのファイルが1件も見つからない。--glob のパスを確認する。")
    threads = load_threads(paths)
    print(f"ページ {len(paths)} 件 / スレッド {len(threads)} 件")
    if not threads:
        sys.exit("スレッドが0件。search_threads の保存ファイルではないものを読んでいる可能性がある。")

    if args.me:
        # 途中でアドレスが変わった人のために、カンマ区切りで複数受け付ける
        mes = {a.strip().lower() for a in args.me.split(",") if a.strip()}
        me = sorted(mes)[0]
    else:
        senders = Counter(norm_addr(m.get("sender"))
                          for t in threads for m in (t.get("messages") or []))
        senders.pop(None, None)
        if not senders:
            sys.exit("送信者が取れない。--me で自分のアドレスを渡す。")
        me = senders.most_common(1)[0][0]
        mes = {me}
        print(f"自分のアドレス(自動判定): {me}  ※違えば --me で指定し直す")
    own = set(d.lower() for d in args.own_domain) or {registrable(domain_of(a)) for a in mes}
    extra = {d.strip().lower() for d in args.extra_exclude.split(",") if d.strip()}
    supplier = SUPPLIER_DOMAINS - own

    companies = OrderedDict()
    bounced = set()
    excluded = {"own": set(), "supplier": set(), "freemail": set(),
                "tool": set(), "auto": set()}
    sent_msgs = 0
    auto_replies = 0
    person_hits, corp_hits = {}, {}
    # 自社・提供元の社名を相手先の社名と取り違えないための除外語
    corp_ng = {"ライトアップ", "WriteUp", "Writeup", "writeup"} | set(args.own_name or [])

    for t in threads:
        msgs = t.get("messages", []) or []
        for m in msgs:
            sender = norm_addr(m.get("sender"))
            if sender and BOUNCE_SENDER.search(sender + "@"):
                # バウンス通知。本文中に出てくるドメインを死に宛先として控える
                for a in ADDR.findall(m.get("snippet", "") or ""):
                    d = registrable(domain_of(a.lower()))
                    if d and d not in own:
                        bounced.add(d)
                continue
            if sender not in mes:
                # 受信メールは母集団に入れない(送ったことのある相手が対象)。
                # ただし相手のスニペットに出る社名は、表示名の材料として拾っておく
                sreg = registrable(domain_of(sender)) if sender else None
                if sreg and sreg not in own and sreg not in supplier:
                    for s in corp_names(m.get("snippet"), corp_ng):
                        corp_hits.setdefault(sreg, Counter())[s] += 1
                continue
            date = (m.get("date") or "")[:10]
            subject = (m.get("subject") or "").strip()
            if AUTO_REPLY_SUBJECT.search(subject):
                auto_replies += 1
                continue
            sent_msgs += 1
            # 宛先が社外1人だけのときに限り、冒頭の宛名をそのアドレスの担当者名とみなす
            ext_to = [a for a in (norm_addr(x) for x in (m.get("toRecipients") or []))
                      if a and registrable(domain_of(a)) not in own]
            if len(ext_to) == 1:
                sm = SALUTATION.match(html.unescape(m.get("snippet") or ""))
                if sm:
                    nm = sm.group("name").strip()
                    if nm and nm not in NAME_NG and not any(w in nm for w in corp_ng):
                        person_hits.setdefault(ext_to[0], Counter())[nm] += 1
                    if sm.group("corp"):
                        for s in corp_names(sm.group("corp"), corp_ng):
                            corp_hits.setdefault(registrable(domain_of(ext_to[0])), Counter())[s] += 1
            for raw in (m.get("toRecipients") or []) + (m.get("ccRecipients") or []):
                addr = norm_addr(raw)
                if not addr:
                    continue
                local, dom = addr.split("@", 1)
                reg = registrable(dom)
                if reg in own:
                    excluded["own"].add(addr)
                    continue
                if reg in supplier:
                    excluded["supplier"].add(addr)
                    continue
                if dom in FREEMAIL or reg in FREEMAIL:
                    excluded["freemail"].add(addr)
                    continue
                if dom in TOOL_DOMAINS or reg in TOOL_DOMAINS or reg in extra:
                    excluded["tool"].add(addr)
                    continue
                if AUTO_LOCAL.match(local):
                    excluded["auto"].add(addr)
                    continue
                c = companies.setdefault(reg, {
                    "domain": reg, "addresses": set(), "threads": set(),
                    "messages": 0, "first": date, "last": date, "subjects": [],
                })
                c["addresses"].add(addr)
                c["threads"].add(t.get("id"))
                c["messages"] += 1
                if date:
                    c["first"] = min(c["first"] or date, date)
                    c["last"] = max(c["last"] or date, date)
                if subject and subject not in c["subjects"]:
                    c["subjects"].append(subject)

    if sent_msgs == 0:
        sys.exit(f"{', '.join(sorted(mes))} が送信者のメッセージが0件。宛先が取れないviewで取得したか、"
                 "--me が違う可能性がある。")

    # 3社以上のスニペットに出てくる社名は、相手の社名ではなく自社名(「株式会社〇〇 山田様」の宛名側)
    spread = Counter(s for hits in corp_hits.values() for s in hits)
    for hits in corp_hits.values():
        for s in [s for s in hits if spread[s] >= 3]:
            del hits[s]

    rows = []
    for c in companies.values():
        subs = sorted(c["subjects"], key=len)[: args.max_subjects]
        contacts = []
        for a in sorted(c["addresses"]):
            ph = person_hits.get(a)
            contacts.append({"address": a, "name": ph.most_common(1)[0][0] if ph else ""})
        # 名前が分かっている人・やりとりの多い人を先頭に
        contacts.sort(key=lambda x: (x["name"] == "", -sum(person_hits.get(x["address"], {}).values())))
        ch = corp_hits.get(c["domain"])
        rows.append({
            "domain": c["domain"],
            "company_name": ch.most_common(1)[0][0] if ch else "",
            "contacts": contacts,
            "addresses": sorted(c["addresses"]),
            "thread_count": len(c["threads"]),
            "message_count": c["messages"],
            "first_contact": c["first"],
            "last_contact": c["last"],
            "subjects": subs,
            "bounced": c["domain"] in bounced,
        })
    # 直近に触っている先ほど提案が通るので、最終接触の新しい順に並べておく
    rows.sort(key=lambda r: (r["last_contact"], r["thread_count"]), reverse=True)

    payload = {
        "source": {"pages": len(paths), "threads": len(threads),
                   "sent_messages": sent_msgs, "me": sorted(mes),
                   "own_domains": sorted(own),
                   "supplier_domains": sorted(supplier)},
        "excluded_counts": {k: len(v) for k, v in excluded.items()},
        "excluded_samples": {k: sorted(v)[:20] for k, v in excluded.items()},
        "bounced_domains": sorted(bounced),
        "companies": rows,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["domain", "last_contact", "first_contact", "threads",
                        "messages", "bounced", "addresses", "subjects"])
            for r in rows:
                w.writerow([r["domain"], r["last_contact"], r["first_contact"],
                            r["thread_count"], r["message_count"],
                            "1" if r["bounced"] else "",
                            " ".join(r["addresses"]), " / ".join(r["subjects"])])

    print(f"社外ドメイン {len(rows)} 件 / 送信メッセージ {sent_msgs} 件"
          + (f"（自動返信 {auto_replies} 件は除外）" if auto_replies else ""))
    print("除外: " + ", ".join(f"{k}={len(v)}" for k, v in excluded.items()))
    if bounced:
        print("バウンス: " + ", ".join(sorted(bounced)))
    print(f"-> {args.out}" + (f" / {args.csv}" if args.csv else ""))


if __name__ == "__main__":
    main()
