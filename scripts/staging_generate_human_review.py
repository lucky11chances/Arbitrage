#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import market_db
import staging_pair_from_db as pairer


def sorted_event(event):
    return sorted(event, key=lambda row: (row.outcome_key, row.outcome_name))


def event_ref(event):
    return sorted_event(event)[0] if event else None


def event_outcomes(event) -> str:
    return " vs ".join(row.outcome_name for row in sorted_event(event)) if event else ""


def same_core_scope(left_event, right_event) -> bool:
    left = event_ref(left_event)
    right = event_ref(right_event)
    return bool(
        left
        and right
        and (left.universe, left.competition_gender, left.market_type)
        == (right.universe, right.competition_gender, right.market_type)
    )


def name_similarity(left_event, right_event) -> float:
    left = [row.outcome_name for row in sorted_event(left_event)]
    right = [row.outcome_name for row in sorted_event(right_event)]
    if len(left) != 2 or len(right) != 2:
        return 0.0
    direct = SequenceMatcher(None, left[0].lower(), right[0].lower()).ratio()
    direct += SequenceMatcher(None, left[1].lower(), right[1].lower()).ratio()
    crossed = SequenceMatcher(None, left[0].lower(), right[1].lower()).ratio()
    crossed += SequenceMatcher(None, left[1].lower(), right[0].lower()).ratio()
    return max(direct, crossed) / 2


def closest_counterparty(source_event, candidates):
    source = event_ref(source_event)
    if not source:
        return None, 0.0
    scoped = [
        event
        for event in candidates
        if same_core_scope(source_event, event) and pairer.dates_compatible(source, event_ref(event))
    ]
    if not scoped:
        return None, 0.0
    scored = []
    for event in scoped:
        score = name_similarity(source_event, event)
        ref = event_ref(event)
        date_bonus = 0.1 if ref and ref.event_date == source.event_date else 0.0
        scored.append((score + date_bonus, score, event))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][2], scored[0][1]


def group_pm(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.pm_market_id].append(row)
    return [items for items in grouped.values() if len(items) == 2]


def group_ks(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.ks_event_ticker].append(row)
    return [items for items in grouped.values() if len(items) == 2]


def friendly_market_type(value: str) -> str:
    return {
        "game_winner": "Game winner",
        "match_winner": "Match winner",
        "fighter_winner": "Fight winner",
    }.get(value, value or "")


UNIVERSE_LABELS = {
    "mlb": ("Baseball", "MLB"),
    "nba": ("Basketball", "NBA"),
    "wnba": ("Basketball", "WNBA"),
    "nfl": ("Football", "NFL"),
    "cfb": ("Football", "College football"),
    "atp": ("Tennis", "ATP men's singles"),
    "atp_challenger": ("Tennis", "ATP Challenger men's singles"),
    "wta": ("Tennis", "WTA women's singles"),
    "itf_men": ("Tennis", "ITF men's singles"),
    "itf_women": ("Tennis", "ITF women's singles"),
    "valorant": ("Esports", "Valorant"),
    "cs2": ("Esports", "Counter-Strike 2"),
    "lol": ("Esports", "League of Legends"),
    "ufc": ("Combat sports", "UFC / MMA"),
    "boxing": ("Combat sports", "Boxing"),
}


UNIVERSE_LABELS_ZH = {
    "mlb": ("棒球", "MLB"),
    "nba": ("篮球", "NBA"),
    "wnba": ("篮球", "WNBA"),
    "nfl": ("美式橄榄球", "NFL"),
    "cfb": ("美式橄榄球", "大学橄榄球"),
    "atp": ("网球", "ATP男单"),
    "atp_challenger": ("网球", "ATP挑战赛男单"),
    "wta": ("网球", "WTA女单"),
    "itf_men": ("网球", "ITF男单"),
    "itf_women": ("网球", "ITF女单"),
    "valorant": ("电子竞技", "Valorant"),
    "cs2": ("电子竞技", "Counter-Strike 2"),
    "lol": ("电子竞技", "英雄联盟"),
    "ufc": ("格斗", "UFC / MMA"),
    "boxing": ("格斗", "拳击"),
}


GENDER_LABELS = {
    "men": "Men",
    "women": "Women",
    "mixed": "Mixed",
    "open": "Open",
    "unknown": "Unknown - inventory only",
}


def collapse_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def human_sport(universe: str) -> str:
    return UNIVERSE_LABELS.get(universe, ("Unknown sport", universe or ""))[0]


def human_competition(universe: str) -> str:
    return UNIVERSE_LABELS.get(universe, ("Unknown sport", universe or "Unknown competition"))[1]


def human_gender(value: str) -> str:
    return GENDER_LABELS.get(value, value.title() if value else "")


def human_sport_zh(universe: str) -> str:
    return UNIVERSE_LABELS_ZH.get(universe, ("未知运动", universe or ""))[0]


def human_competition_zh(universe: str) -> str:
    return UNIVERSE_LABELS_ZH.get(universe, ("未知运动", universe or "未知赛事"))[1]


def search_text_from_values(universe: str, event_date: str, match_name: str, outcomes: str) -> str:
    # This is intentionally human-first: names + date + competition are what the
    # website search boxes need, while internal IDs stay out of the review file.
    basis = outcomes or match_name
    return collapse_spaces(" ".join(part for part in (human_competition(universe), event_date, basis) if part))


def event_search_text(primary_event, fallback_event=None) -> str:
    event = primary_event or fallback_event
    ref = event_ref(event)
    if not ref:
        return ""
    return search_text_from_values(ref.universe, ref.event_date, ref.match_name, event_outcomes(event))


def friendly_status(value: str) -> str:
    return {
        "LIKELY_ALIAS_REVIEW": "疑似同一场，需人工确认",
        "PM_ONLY_UNMATCHED": "PM有，KS未匹配",
        "KS_ONLY_UNMATCHED": "KS有，PM未匹配",
    }.get(value, value)


def human_action(status: str) -> str:
    if status == "LIKELY_ALIAS_REVIEW":
        return "打开两边页面，确认是否同一场、同日期、同胜负方向"
    if status == "PM_ONLY_UNMATCHED":
        return "去Kalshi搜索，确认KS是否其实也有这场"
    if status == "KS_ONLY_UNMATCHED":
        return "去Polymarket搜索，确认PM是否其实也有这场"
    return ""


def human_reason(status: str, universe: str) -> str:
    if status == "LIKELY_ALIAS_REVIEW":
        return "疑似同一场：名字、顺序或日期显示不同"
    if status == "PM_ONLY_UNMATCHED":
        if universe == "valorant":
            return "用户已确认：这些Valorant比赛KS没有"
        if universe == "wnba":
            return "用户已确认：这些WNBA比赛KS没有"
        if universe in {"table_tennis", "wtt"}:
            return "用户已确认：KS没有table tennis"
        return "本地DB暂未找到KS对应比赛"
    if status == "KS_ONLY_UNMATCHED":
        if universe == "cfb":
            return "用户已确认：PM暂未更新这批CFB"
        if universe in {"itf_men", "itf_women"}:
            return "用户已确认：ITF目前KS更多，PM只有少量"
        if universe in {"atp", "wta", "cs2"}:
            return "可能是PM漏抓或名字/时间差异，需优先修复"
        return "本地DB暂未找到PM对应比赛"
    return ""


def review_instruction(status: str) -> str:
    if status == "LIKELY_ALIAS_REVIEW":
        return "Open both sites. Confirm exact same match, same tournament/league, same date, and same yes-outcome direction. If yes, mark ALLOW and write the alias difference."
    if status == "PM_ONLY_UNMATCHED":
        return "Search Kalshi using the Kalshi Search Text. If found, write the Kalshi title and whether names/date/tour differ from PM."
    if status == "KS_ONLY_UNMATCHED":
        return "Search Polymarket using the PM Search Text. If found, write the PM title and whether names/date/tour differ from Kalshi."
    return ""


def unmatched_reason(status: str) -> str:
    if status == "LIKELY_ALIAS_REVIEW":
        return (
            "Possible name/title alias. Sport, league/tour, gender, market type, and date window look compatible, "
            "but the participant names are not identical enough for automatic safe pairing."
        )
    if status == "PM_ONLY_UNMATCHED":
        return (
            "PM has this event, but the local DB found no Kalshi event with the same sport, league/tour, gender, "
            "date window, market type, and participant set."
        )
    if status == "KS_ONLY_UNMATCHED":
        return (
            "Kalshi has this event, but the local DB found no PM event with the same sport, league/tour, gender, "
            "date window, market type, and participant set."
        )
    return ""


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def display_utc(value: Any) -> str:
    if not value:
        return ""
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def build_review(db_path: Path, from_date: str, out_dir: Path) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    conn = market_db.connect(db_path)

    pm_rows, pm_skipped = pairer.build_pm_outcomes(conn, "all", from_date)
    ks_rows, ks_skipped = pairer.build_ks_outcomes(conn, "all", from_date)
    pairs, _diagnostics, warnings = pairer.safe_pair_rows(pm_rows, ks_rows)

    matched_pm_tokens = {pair.pm_token_id for pair in pairs}
    matched_ks_tickers = {pair.ks_market_ticker for pair in pairs}
    pm_events = group_pm(pm_rows)
    ks_events = group_ks(ks_rows)
    unmatched_pm_events = [
        event for event in pm_events if not any(row.pm_token_id in matched_pm_tokens for row in event)
    ]
    unmatched_ks_events = [
        event for event in ks_events if not any(row.ks_market_ticker in matched_ks_tickers for row in event)
    ]

    ks_event_titles = {}
    ks_event_outcomes = {}
    for event in ks_events:
        ref = event_ref(event)
        if ref:
            ks_event_titles[ref.ks_event_ticker] = ref.match_name
            ks_event_outcomes[ref.ks_event_ticker] = event_outcomes(event)
    pm_market_genders = {}
    for event in pm_events:
        ref = event_ref(event)
        if ref:
            pm_market_genders[ref.pm_market_id] = ref.competition_gender

    pair_groups: dict[tuple[str, str, str, str, str, str, str, str], list[Any]] = defaultdict(list)
    for pair in pairs:
        key = (
            pair.universe,
            pair.event_date,
            pair.canonical_event_id,
            pair.market_type,
            pair.pm_event_slug,
            pair.pm_market_id,
            pair.ks_event_ticker,
            pair.match_name,
        )
        pair_groups[key].append(pair)

    paired_rows = []
    for (
        universe,
        event_date,
        _canonical_event_id,
        market_type,
        _pm_event_slug,
        _pm_market_id,
        ks_event_ticker,
        match_name,
    ), group in sorted(pair_groups.items()):
        group = sorted(group, key=lambda pair: pair.pm_yes_outcome)
        paired_rows.append(
            {
                "Review Status": "Already paired",
                "Sport": human_sport(universe),
                "League / Tour": human_competition(universe),
                "Gender": human_gender(pm_market_genders.get(_pm_market_id, "")),
                "Event Date": event_date,
                "Market Type": friendly_market_type(market_type),
                "PM Search Text": search_text_from_values(
                    universe,
                    event_date,
                    match_name,
                    " vs ".join(pair.pm_yes_outcome for pair in group),
                ),
                "PM Match": match_name,
                "PM Outcomes": " vs ".join(pair.pm_yes_outcome for pair in group),
                "Kalshi Search Text": search_text_from_values(
                    universe,
                    event_date,
                    ks_event_titles.get(ks_event_ticker, ""),
                    " vs ".join(pair.ks_yes_outcome for pair in group),
                ),
                "KS Match": ks_event_titles.get(ks_event_ticker, ""),
                "KS Outcomes": " vs ".join(pair.ks_yes_outcome for pair in group),
                "Why It Paired": "Same sport/league, compatible date, same market type, and same outcome direction under current DB rules.",
                "What To Verify": "Spot-check PM and Kalshi page text/rules: this must be the same binary $1/$0 winner market, not series/prop/future/draw.",
                "Human Decision": "",
                "Human Notes": "",
            }
        )

    paired_fields = [
        "Review Status",
        "Sport",
        "League / Tour",
        "Gender",
        "Event Date",
        "Market Type",
        "PM Search Text",
        "PM Match",
        "PM Outcomes",
        "Kalshi Search Text",
        "KS Match",
        "KS Outcomes",
        "Why It Paired",
        "What To Verify",
        "Human Decision",
        "Human Notes",
    ]

    unmatched_rows = []
    human_unmatched_rows = []
    alias_pm_ids = set()
    alias_ks_ids = set()

    def add_unmatched(
        status: str,
        reason: str,
        pm_event=None,
        ks_event=None,
        score: float | None = None,
        closest=None,
        closest_score: float | None = None,
    ) -> None:
        pm_ref = event_ref(pm_event)
        ks_ref = event_ref(ks_event)
        ref = pm_ref or ks_ref
        closest_ref = event_ref(closest)
        pm_search_text = event_search_text(pm_event, ks_event)
        ks_search_text = event_search_text(ks_event, pm_event)
        unmatched_rows.append(
            {
                "Review Status": friendly_status(status),
                "Priority": {"LIKELY_ALIAS_REVIEW": 1, "PM_ONLY_UNMATCHED": 2, "KS_ONLY_UNMATCHED": 3}.get(status, 9),
                "Sport": human_sport(ref.universe if ref else ""),
                "League / Tour": human_competition(ref.universe if ref else ""),
                "Gender": human_gender(ref.competition_gender if ref else ""),
                "Event Date": ref.event_date if ref else "",
                "Market Type": friendly_market_type(ref.market_type if ref else ""),
                "Why Unmatched": reason,
                "PM Search Text": pm_search_text,
                "PM Match": pm_ref.match_name if pm_ref else "",
                "PM Outcomes": event_outcomes(pm_event),
                "Kalshi Search Text": ks_search_text,
                "KS Match": ks_ref.match_name if ks_ref else "",
                "KS Outcomes": event_outcomes(ks_event),
                "Closest Candidate On Other Site": closest_ref.match_name if closest_ref else "",
                "Closest Candidate Outcomes": event_outcomes(closest),
                "Closest Candidate Date": closest_ref.event_date if closest_ref else "",
                "Name Similarity": "" if score is None else round(score, 3),
                "What To Check On Websites": review_instruction(status),
                "Human Decision": "",
                "Human Notes": "",
            }
        )
        search_on = ""
        search_text = ""
        if status == "PM_ONLY_UNMATCHED":
            search_on = "Kalshi"
            search_text = ks_search_text
        elif status == "KS_ONLY_UNMATCHED":
            search_on = "Polymarket"
            search_text = pm_search_text
        else:
            search_on = "Polymarket + Kalshi"
            search_text = pm_search_text or ks_search_text
        human_unmatched_rows.append(
            {
                "状态": friendly_status(status),
                "运动": human_sport_zh(ref.universe if ref else ""),
                "联赛/赛事": human_competition_zh(ref.universe if ref else ""),
                "日期": ref.event_date if ref else "",
                "原因": human_reason(status, ref.universe if ref else ""),
                "PM上的事件名": pm_ref.match_name if pm_ref else "",
                "PM选项": event_outcomes(pm_event),
                "KS上的事件名": ks_ref.match_name if ks_ref else "",
                "KS选项": event_outcomes(ks_event),
                "去哪里查": search_on,
                "建议搜索词": search_text,
                "最近候选": closest_ref.match_name if closest_ref else "",
                "最近候选选项": event_outcomes(closest),
                "你审核后的备注": "",
            }
        )

    alias_candidates = []
    for pm_event in unmatched_pm_events:
        for ks_event in unmatched_ks_events:
            if not same_core_scope(pm_event, ks_event):
                continue
            if not pairer.dates_compatible(event_ref(pm_event), event_ref(ks_event)):
                continue
            score = name_similarity(pm_event, ks_event)
            if score >= 0.70:
                alias_candidates.append((score, pm_event, ks_event))

    for score, pm_event, ks_event in sorted(
        alias_candidates,
        key=lambda item: (-item[0], event_ref(item[1]).universe, event_ref(item[1]).match_name),
    ):
        alias_pm_ids.add(event_ref(pm_event).pm_market_id)
        alias_ks_ids.add(event_ref(ks_event).ks_event_ticker)
        add_unmatched(
            "LIKELY_ALIAS_REVIEW",
            unmatched_reason("LIKELY_ALIAS_REVIEW"),
            pm_event=pm_event,
            ks_event=ks_event,
            score=score,
        )

    for pm_event in sorted(
        unmatched_pm_events,
        key=lambda event: (event_ref(event).universe, event_ref(event).event_date, event_ref(event).match_name),
    ):
        if event_ref(pm_event).pm_market_id in alias_pm_ids:
            continue
        closest, closest_score = closest_counterparty(pm_event, unmatched_ks_events)
        add_unmatched(
            "PM_ONLY_UNMATCHED",
            unmatched_reason("PM_ONLY_UNMATCHED"),
            pm_event=pm_event,
            closest=closest,
            closest_score=closest_score if closest else None,
        )

    for ks_event in sorted(
        unmatched_ks_events,
        key=lambda event: (event_ref(event).universe, event_ref(event).event_date, event_ref(event).match_name),
    ):
        if event_ref(ks_event).ks_event_ticker in alias_ks_ids:
            continue
        closest, closest_score = closest_counterparty(ks_event, unmatched_pm_events)
        add_unmatched(
            "KS_ONLY_UNMATCHED",
            unmatched_reason("KS_ONLY_UNMATCHED"),
            ks_event=ks_event,
            closest=closest,
            closest_score=closest_score if closest else None,
        )

    unmatched_fields = [
        "Review Status",
        "Priority",
        "Sport",
        "League / Tour",
        "Gender",
        "Event Date",
        "Market Type",
        "Why Unmatched",
        "PM Search Text",
        "PM Match",
        "PM Outcomes",
        "Kalshi Search Text",
        "KS Match",
        "KS Outcomes",
        "Closest Candidate On Other Site",
        "Closest Candidate Outcomes",
        "Closest Candidate Date",
        "Name Similarity",
        "What To Check On Websites",
        "Human Decision",
        "Human Notes",
    ]
    human_unmatched_fields = [
        "状态",
        "运动",
        "联赛/赛事",
        "日期",
        "原因",
        "PM上的事件名",
        "PM选项",
        "KS上的事件名",
        "KS选项",
        "去哪里查",
        "建议搜索词",
        "最近候选",
        "最近候选选项",
        "你审核后的备注",
    ]

    row_counts = market_db.row_counts(conn)
    active_pm = conn.execute("SELECT COUNT(*) AS count FROM pm_tokens WHERE active=1 AND closed=0").fetchone()["count"]
    active_ks = conn.execute(
        "SELECT COUNT(*) AS count FROM ks_markets WHERE LOWER(COALESCE(status,'open')) IN ('','open','active')"
    ).fetchone()["count"]
    latest_observation = conn.execute("SELECT MAX(collected_ts_utc) AS ts FROM orderbook_observations").fetchone()["ts"]

    summary_rows = [
        {"Metric": "Generated at UTC", "Value": display_utc(generated_at), "Plain English": "When this human review workbook was created."},
        {"Metric": "From date", "Value": from_date, "Plain English": "Only events on or after this date are included."},
        {"Metric": "Source database", "Value": str(db_path), "Plain English": "SQLite remains the source of truth."},
        {"Metric": "Latest orderbook observation UTC", "Value": display_utc(latest_observation), "Plain English": "Confirms the local DB is being updated."},
        {"Metric": "PM events in DB", "Value": row_counts.get("pm_events", ""), "Plain English": ""},
        {"Metric": "PM markets in DB", "Value": row_counts.get("pm_markets", ""), "Plain English": ""},
        {"Metric": "PM tokens in DB", "Value": row_counts.get("pm_tokens", ""), "Plain English": ""},
        {"Metric": "Active PM tokens", "Value": active_pm, "Plain English": ""},
        {"Metric": "KS events in DB", "Value": row_counts.get("ks_events", ""), "Plain English": ""},
        {"Metric": "KS markets in DB", "Value": row_counts.get("ks_markets", ""), "Plain English": ""},
        {"Metric": "Active KS markets", "Value": active_ks, "Plain English": ""},
        {"Metric": "Orderbook observations", "Value": row_counts.get("orderbook_observations", ""), "Plain English": ""},
        {"Metric": "Safe paired contracts", "Value": len(pairs), "Plain English": "Contract-level; usually two per game/match."},
        {"Metric": "Safe paired events", "Value": len(pair_groups), "Plain English": "Human-level games or matches."},
        {"Metric": "Unmatched rows for review", "Value": len(unmatched_rows), "Plain English": "Rows you can inspect on PM/KS websites."},
        {"Metric": "Likely alias rows", "Value": sum(1 for row in unmatched_rows if row["Priority"] == 1), "Plain English": "Check these first."},
        {"Metric": "PM-only rows", "Value": sum(1 for row in unmatched_rows if row["Priority"] == 2), "Plain English": "PM event found, no KS event matched."},
        {"Metric": "KS-only rows", "Value": sum(1 for row in unmatched_rows if row["Priority"] == 3), "Plain English": "KS event found, no PM event matched."},
    ]
    for universe, count in Counter(pair.universe for pair in pairs).most_common():
        summary_rows.append(
            {
                "Metric": f"Paired contracts - {human_sport(universe)} / {human_competition(universe)}",
                "Value": count,
                "Plain English": "",
            }
        )
    for reason, count in pm_skipped.most_common():
        summary_rows.append({"Metric": "PM skipped before safe pairing", "Value": count, "Plain English": reason})
    for reason, count in ks_skipped.most_common():
        summary_rows.append({"Metric": "KS skipped before safe pairing", "Value": count, "Plain English": reason})
    for warning in warnings:
        summary_rows.append({"Metric": "Pairer warning", "Value": "", "Plain English": warning})

    summary_fields = ["Metric", "Value", "Plain English"]

    out_dir.mkdir(parents=True, exist_ok=True)
    paired_csv = out_dir / "review_paired_events.csv"
    unmatched_csv = out_dir / "review_unmatched_events.csv"
    human_unmatched_csv = out_dir / "review_unmatched_human.csv"
    summary_csv = out_dir / "review_pairing_summary.csv"
    payload_json = out_dir / "human_review_payload.json"

    write_csv(paired_csv, paired_rows, paired_fields)
    write_csv(unmatched_csv, unmatched_rows, unmatched_fields)
    write_csv(human_unmatched_csv, human_unmatched_rows, human_unmatched_fields)
    write_csv(summary_csv, summary_rows, summary_fields)
    payload_json.write_text(
        json.dumps(
            {
                "summary": summary_rows,
                "paired": paired_rows,
                "unmatched": unmatched_rows,
                "human_unmatched": human_unmatched_rows,
                "generated_at_utc": generated_at,
                "from_date": from_date,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    conn.close()
    return {
        "paired_csv": str(paired_csv),
        "unmatched_csv": str(unmatched_csv),
        "human_unmatched_csv": str(human_unmatched_csv),
        "summary_csv": str(summary_csv),
        "payload_json": str(payload_json),
        "paired_events": len(pair_groups),
        "paired_contracts": len(pairs),
        "unmatched_rows": len(unmatched_rows),
        "alias_rows": sum(1 for row in unmatched_rows if row["Priority"] == 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate human-friendly PM/KS pairing review CSVs from local SQLite.")
    parser.add_argument("--db", default=str(market_db.DEFAULT_DB_PATH))
    parser.add_argument("--from-date", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--out-dir", default="data/staging")
    args = parser.parse_args()
    result = build_review(Path(args.db), args.from_date, Path(args.out_dir))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
