#!/usr/bin/env python3
"""weekly_valuation_summary_api テーブルからダッシュボード用JSONを生成する。

BigQuery から集計済みテーブル(週次×エリア)を読み、以下を data/ に出力する:
  - weekly_valuation_summary.json : テーブル全行のJSON(汎用API)
  - weekly.json  : index.html 用(都道府県×週 査定ユーザー数)
  - monthly.json : monthly-dashboard.html 用(都道府県×月)
  - seiyaku.json : seiyaku-dashboard.html 用(査定/コホート成約/実契約/成約率)

ローカル検証: python3 scripts/update_data.py --from-csv fixture.csv --out data
本番(GitHub Actions): python3 scripts/update_data.py --project buddica-direct --out data
"""
import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

COLUMNS = [
    "week_start_date",
    "area_code",
    "total_valuation_users",
    "referral_users_in_this_week",
    "contracted_from_this_week_users",
    "total_actual_contracts_in_week",
    "user_cohort_contract_rate",
]

PREF_REGION = {
    "北海道": "北海道",
    "青森県": "東北", "岩手県": "東北", "宮城県": "東北", "秋田県": "東北",
    "山形県": "東北", "福島県": "東北",
    "茨城県": "関東", "栃木県": "関東", "群馬県": "関東", "埼玉県": "関東",
    "千葉県": "関東", "東京都": "関東", "神奈川県": "関東",
    "新潟県": "中部", "富山県": "中部", "石川県": "中部", "福井県": "中部",
    "山梨県": "中部", "長野県": "中部", "岐阜県": "中部", "静岡県": "中部",
    "愛知県": "中部",
    "三重県": "近畿", "滋賀県": "近畿", "京都府": "近畿", "大阪府": "近畿",
    "兵庫県": "近畿", "奈良県": "近畿", "和歌山県": "近畿",
    "鳥取県": "中国", "島根県": "中国", "岡山県": "中国", "広島県": "中国",
    "山口県": "中国",
    "徳島県": "四国", "香川県": "四国", "愛媛県": "四国", "高知県": "四国",
    "福岡県": "九州・沖縄", "佐賀県": "九州・沖縄", "長崎県": "九州・沖縄",
    "熊本県": "九州・沖縄", "大分県": "九州・沖縄", "宮崎県": "九州・沖縄",
    "鹿児島県": "九州・沖縄", "沖縄県": "九州・沖縄",
}

PREF_ORDER = list(PREF_REGION.keys())


def load_zip3_map():
    with open(os.path.join(HERE, "zip3_to_pref.json"), encoding="utf-8") as f:
        return json.load(f)


def fetch_rows_bq(project, table):
    from google.cloud import bigquery
    client = bigquery.Client(project=project)
    field_list = ", ".join(COLUMNS)
    sql = f"SELECT {field_list} FROM `{project}.{table}`"
    rows = []
    for r in client.query(sql).result():
        rows.append({c: r[c] for c in COLUMNS})
    return rows


def fetch_rows_csv(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({
                "week_start_date": r["week_start_date"] or None,
                "area_code": r["area_code"] or None,
                "total_valuation_users": int(r["total_valuation_users"] or 0),
                "referral_users_in_this_week": int(r["referral_users_in_this_week"] or 0),
                "contracted_from_this_week_users": int(r["contracted_from_this_week_users"] or 0),
                "total_actual_contracts_in_week": int(r["total_actual_contracts_in_week"] or 0),
                "user_cohort_contract_rate": float(r["user_cohort_contract_rate"]) if r.get("user_cohort_contract_rate") else None,
            })
    return rows


def normalize_week(v):
    if v is None or v == "":
        return None
    return str(v)[:10]


def build(rows, generated_at):
    zip3 = load_zip3_map()

    weeks = sorted({normalize_week(r["week_start_date"]) for r in rows if normalize_week(r["week_start_date"])})
    widx = {w: i for i, w in enumerate(weeks)}
    nw = len(weeks)

    # pref × week の集計器
    def zeros():
        return {"vu": [0] * nw, "ref": [0] * nw, "cu": [0] * nw, "ac": [0] * nw}
    per_pref = defaultdict(zeros)
    unknown_vu = 0
    pre_actual = 0

    for r in rows:
        week = normalize_week(r["week_start_date"])
        area = (r["area_code"] or "").strip()
        pref = zip3.get(area)
        if week is None:
            # カレンダー範囲外(2025年以前の査定に紐づく契約など)
            pre_actual += r["total_actual_contracts_in_week"] or 0
            continue
        if pref is None:
            unknown_vu += r["total_valuation_users"] or 0
            continue
        i = widx[week]
        agg = per_pref[pref]
        agg["vu"][i] += r["total_valuation_users"] or 0
        agg["ref"][i] += r["referral_users_in_this_week"] or 0
        agg["cu"][i] += r["contracted_from_this_week_users"] or 0
        agg["ac"][i] += r["total_actual_contracts_in_week"] or 0

    prefs_present = [p for p in PREF_ORDER if p in per_pref]

    # --- weekly.json (index.html: 査定ユーザー数) ---
    weekly = {
        "generated_at": generated_at,
        "weeks": weeks,
        "prefs": [
            {"pref": p, "region": PREF_REGION[p],
             "v": per_pref[p]["vu"], "tv": sum(per_pref[p]["vu"])}
            for p in prefs_present
        ],
        "unknown": unknown_vu,
    }

    # --- monthly.json (週開始日の属する月に集約) ---
    months = sorted({w[:7] for w in weeks})
    midx = {m: i for i, m in enumerate(months)}
    monthly_prefs = []
    for p in prefs_present:
        mv = [0] * len(months)
        for w, x in zip(weeks, per_pref[p]["vu"]):
            mv[midx[w[:7]]] += x
        monthly_prefs.append({"pref": p, "region": PREF_REGION[p], "v": mv, "tv": sum(mv)})
    monthly = {
        "generated_at": generated_at,
        "months": months,
        "region": {p: PREF_REGION[p] for p in prefs_present},
        "prefs": monthly_prefs,
        "unknown": unknown_vu,
    }

    # --- seiyaku.json ---
    seiyaku_prefs = []
    for p in prefs_present:
        a = per_pref[p]
        tvu, tref, tcu, tac = (sum(a["vu"]), sum(a["ref"]), sum(a["cu"]), sum(a["ac"]))
        seiyaku_prefs.append({
            "pref": p, "region": PREF_REGION[p],
            "vu": a["vu"], "ref": a["ref"], "cu": a["cu"], "ac": a["ac"],
            "tvu": tvu, "tref": tref, "tcu": tcu, "tac": tac,
            "rate": round(100.0 * tcu / tvu, 1) if tvu else 0,
        })
    seiyaku = {
        "generated_at": generated_at,
        "weeks": weeks,
        "prefs": seiyaku_prefs,
        "pre_actual": pre_actual,
    }

    # --- 汎用API JSON (テーブル全行) ---
    api = {
        "generated_at": generated_at,
        "source_table": "weekly_valuation_summary_api",
        "columns": COLUMNS,
        "rows": [
            [normalize_week(r["week_start_date"]), r["area_code"],
             r["total_valuation_users"], r["referral_users_in_this_week"],
             r["contracted_from_this_week_users"], r["total_actual_contracts_in_week"],
             float(r["user_cohort_contract_rate"]) if r["user_cohort_contract_rate"] is not None else None]
            for r in rows
        ],
    }
    return weekly, monthly, seiyaku, api


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="buddica-direct")
    ap.add_argument("--table", default="rds_postgres_prod.weekly_valuation_summary_api")
    ap.add_argument("--from-csv", help="BigQueryの代わりにCSVから読む(ローカル検証用)")
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    if args.from_csv:
        rows = fetch_rows_csv(args.from_csv)
    else:
        rows = fetch_rows_bq(args.project, args.table)
    if not rows:
        print("ERROR: no rows fetched — aborting without writing", file=sys.stderr)
        sys.exit(1)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    weekly, monthly, seiyaku, api = build(rows, generated_at)

    os.makedirs(args.out, exist_ok=True)
    for name, obj in [("weekly.json", weekly), ("monthly.json", monthly),
                      ("seiyaku.json", seiyaku), ("weekly_valuation_summary.json", api)]:
        with open(os.path.join(args.out, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    print(f"OK: {len(rows)} rows -> {args.out}/ (weeks={len(weekly['weeks'])}, prefs={len(weekly['prefs'])}, unknown_vu={weekly['unknown']}, pre_actual={seiyaku['pre_actual']})")


if __name__ == "__main__":
    main()
