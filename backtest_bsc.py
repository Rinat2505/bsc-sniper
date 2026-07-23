#!/usr/bin/env python3
"""BSC Sniper Backtester v2 — анализ истории + прогноз 3 и 6 месяцев."""
import json, os
from datetime import datetime, timedelta
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))

def load_data():
    with open(os.path.join(HERE, "sniper_history.json"), encoding="utf-8") as f:
        data = json.load(f)
    buys = {t["token"]: t for t in data if t.get("action") == "buy"}
    sells = []
    for t in data:
        if t.get("action") == "sell" and t.get("pnl_bnb") is not None:
            buy_meta = buys.get(t["token"], {})
            merged = {**buy_meta, **t}
            sells.append(merged)
    return sells

def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def st(trades):
    if not trades:
        return None
    n = len(trades)
    wins = [t for t in trades if t["pnl_bnb"] > 0]
    losses = [t for t in trades if t["pnl_bnb"] <= 0]
    total = sum(t["pnl_bnb"] for t in trades)
    aw = sum(t["pnl_bnb"] for t in wins) / len(wins) if wins else 0
    al = sum(t["pnl_bnb"] for t in losses) / len(losses) if losses else 0
    wr = len(wins) / n
    ev = wr * aw + (1 - wr) * al
    rr = abs(aw / al) if al else 0
    return {"n": n, "wins": len(wins), "wr": wr, "total": total, "aw": aw, "al": al, "ev": ev, "rr": rr}

def fmt(s, label=""):
    if not s:
        return f"  {label:<24} — нет данных"
    return (f"  {label:<24} {s['n']:>4} сд | wr={s['wr']*100:4.0f}% |"
            f" P&L={s['total']:+7.4f} | EV={s['ev']:+.5f} | R:R={s['rr']:.2f}")

def project(ev, tpd, days):
    n = int(tpd * days)
    dry = ev * n
    real = dry * 0.45
    return n, dry, real

def main():
    sells = load_data()
    if not sells:
        print("Нет данных"); return

    dates = [parse_dt(t.get("opened_at") or t.get("closed_at")) for t in sells]
    valid_dates = [(t, d) for t, d in zip(sells, dates) if d]
    if not valid_dates:
        print("Нет дат"); return

    all_dates = [d for _, d in valid_dates]
    min_dt = min(all_dates)
    max_dt = max(all_dates)
    span = max(1.0, (max_dt - min_dt).total_seconds() / 86400)
    tpd = len(sells) / span

    print(f"Период данных: {min_dt.strftime('%Y-%m-%d')} → {max_dt.strftime('%Y-%m-%d')}"
          f" ({span:.1f} дн) | {len(sells)} сделок | {tpd:.2f} сд/день\n")

    s_all = st(sells)

    # === 1. Общая статистика ===
    print("=" * 75)
    print("1. ОБЩАЯ СТАТИСТИКА")
    print("=" * 75)
    print(fmt(s_all, "ВСЕ СДЕЛКИ"))
    print(f"  {'':24} avg_win={s_all['aw']:+.4f} | avg_loss={s_all['al']:+.4f}")

    # === 2. По размеру пула ===
    print("\n" + "=" * 75)
    print("2. ПО РАЗМЕРУ ПУЛА dev_bnb")
    print("=" * 75)
    for lo, hi in [(0,1),(1,2),(2,3),(3,5),(5,10),(10,20),(20,999)]:
        bucket = [t for t in sells if lo <= t.get("dev_bnb", 0) < hi]
        label = f"{lo}-{'∞' if hi==999 else hi} BNB"
        print(fmt(st(bucket), label))

    # === 3. Grid search: оптимальные границы пула ===
    print("\n" + "=" * 75)
    print("3. GRID SEARCH: оптимальные границы пула (топ-10 по EV)")
    print("=" * 75)
    results = []
    for lo in [0, 0.5, 1, 2, 3]:
        for hi in [3, 5, 7, 10, 15, 20]:
            if hi <= lo:
                continue
            bucket = [t for t in sells if lo <= t.get("dev_bnb", 0) < hi]
            s = st(bucket)
            if s and s["n"] >= 5:
                results.append((lo, hi, s))
    results.sort(key=lambda x: x[2]["ev"], reverse=True)
    print(f"  {'мин':>5} {'макс':>5} | {'N':>4} | {'WR':>6} | {'EV/сделку':>10} | {'P&L':>8} | {'R:R':>5}")
    print("  " + "-" * 55)
    for lo, hi, s in results[:10]:
        print(f"  {lo:5.1f} {hi:5.0f} | {s['n']:4} | {s['wr']*100:5.0f}% |"
              f" {s['ev']:+10.5f} | {s['total']:+8.4f} | {s['rr']:5.2f}")

    # === 4. По причинам выхода ===
    print("\n" + "=" * 75)
    print("4. ПО ПРИЧИНАМ ВЫХОДА")
    print("=" * 75)
    by_reason = defaultdict(list)
    for t in sells:
        by_reason[t.get("reason", "?")].append(t)
    for reason, trades in sorted(by_reason.items(), key=lambda x: sum(t["pnl_bnb"] for t in x[1]), reverse=True):
        print(fmt(st(trades), reason))

    # === 5. Grid search по min_price_chg при входе ===
    print("\n" + "=" * 75)
    print("5. GRID SEARCH: min_price_chg_pct при входе")
    print("=" * 75)
    print(f"  {'min_Δ%':>8} | {'N':>4} | {'WR':>6} | {'EV/сделку':>10} | {'P&L':>8}")
    print("  " + "-" * 48)
    for min_chg in [0, 1, 2, 3, 5, 8, 10, 15]:
        field = "price_chg_at_entry_pct"
        bucket = [t for t in sells if t.get(field, t.get("price_chg_at_entry", 0)) >= min_chg]
        s = st(bucket)
        if s and s["n"] >= 5:
            print(f"  {min_chg:>7}% | {s['n']:4} | {s['wr']*100:5.0f}% |"
                  f" {s['ev']:+10.5f} | {s['total']:+8.4f}")

    # === 6. Grid search по pool_bnb_entry ===
    print("\n" + "=" * 75)
    print("6. GRID SEARCH: по pool_bnb_entry (пул на момент входа)")
    print("=" * 75)
    pool_results = []
    for lo in [0, 1, 2, 3]:
        for hi in [5, 10, 15, 20, 30, 50]:
            if hi <= lo:
                continue
            bucket = [t for t in sells if lo <= t.get("pool_bnb_entry", t.get("pool_bnb_at_entry", 0)) < hi]
            s = st(bucket)
            if s and s["n"] >= 5:
                pool_results.append((lo, hi, s))
    pool_results.sort(key=lambda x: x[2]["ev"], reverse=True)
    print(f"  {'мин':>5} {'макс':>5} | {'N':>4} | {'WR':>6} | {'EV/сделку':>10} | {'P&L':>8}")
    print("  " + "-" * 50)
    for lo, hi, s in pool_results[:8]:
        print(f"  {lo:5.0f} {hi:5.0f} | {s['n']:4} | {s['wr']*100:5.0f}% |"
              f" {s['ev']:+10.5f} | {s['total']:+8.4f}")

    # === 7. По часам суток UTC ===
    print("\n" + "=" * 75)
    print("7. ПО ЧАСАМ СУТОК UTC")
    print("=" * 75)
    by_hour = defaultdict(list)
    for t, d in valid_dates:
        if d:
            by_hour[d.hour].append(t)
    hour_stats = [(h, st(trs)) for h, trs in by_hour.items() if trs and st(trs)]
    hour_stats.sort(key=lambda x: x[1]["ev"], reverse=True)
    print("  Лучшие часы:")
    for h, s in hour_stats[:5]:
        print(f"    {h:02d}:00 | {fmt(s).strip()}")
    print("  Худшие часы:")
    for h, s in hour_stats[-3:]:
        print(f"    {h:02d}:00 | {fmt(s).strip()}")

    # === 8. Динамика: первая vs вторая половина ===
    if span >= 4:
        mid_dt = min_dt + timedelta(days=span / 2)
        first = [t for t, d in valid_dates if d < mid_dt]
        second = [t for t, d in valid_dates if d >= mid_dt]
        s1, s2 = st(first), st(second)
        print("\n" + "=" * 75)
        print("8. ДИНАМИКА: первая vs вторая половина периода")
        print("=" * 75)
        print(fmt(s1, f"1-я пол ({span/2:.0f}д)"))
        print(fmt(s2, f"2-я пол ({span/2:.0f}д)"))
        if s1 and s2:
            trend = "улучшается ↑" if s2["ev"] > s1["ev"] else "ухудшается ↓"
            print(f"  → EV тренд: {trend}  ({s1['ev']:+.5f} → {s2['ev']:+.5f})")

    # === 9. Без early_stop: что было бы с улучшенными фильтрами ===
    no_es = [t for t in sells if t.get("reason") != "early_stop"]
    s_no_es = st(no_es)
    print("\n" + "=" * 75)
    print("9. СИМУЛЯЦИЯ: без early_stop (улучшенные pre-entry фильтры)")
    print("=" * 75)
    print(fmt(s_all,   "С early_stop (факт)"))
    print(fmt(s_no_es, "Без early_stop (цель)"))
    if s_all and s_no_es:
        ev_gain = (s_no_es["ev"] - s_all["ev"]) / abs(s_all["ev"]) * 100 if s_all["ev"] != 0 else 0
        print(f"  → EV улучшение: {ev_gain:+.1f}%  ({s_all['ev']:+.5f} → {s_no_es['ev']:+.5f})")

    # === 10. ПРОГНОЗ 3 и 6 месяцев ===
    print("\n" + "=" * 75)
    print("10. ПРОГНОЗ: 3 МЕСЯЦА (90 дней) И 6 МЕСЯЦЕВ (180 дней)")
    print("=" * 75)

    # Лучший конфиг пула из grid search
    best_lo, best_hi, best_s = results[0] if results else (0, 999, s_all)
    # Темп для лучшего конфига пропорционально доле сделок
    best_tpd = tpd * (best_s["n"] / s_all["n"]) if s_all else tpd
    no_es_tpd = tpd * (s_no_es["n"] / s_all["n"]) if s_all and s_no_es else tpd

    scenarios = [
        ("Базовый (факт)",               s_all["ev"],   tpd),
        (f"Оптим пул {best_lo}-{best_hi}BNB", best_s["ev"], best_tpd),
        ("Без early_stop",               s_no_es["ev"] if s_no_es else s_all["ev"],  no_es_tpd),
    ]

    print(f"  {'Сценарий':<28} | {'3м N':>6} | {'3м DRY':>8} | {'3м реал':>8} |"
          f" {'6м N':>6} | {'6м DRY':>8} | {'6м реал':>8}")
    print("  " + "-" * 85)
    for label, ev_val, rate in scenarios:
        n3, dry3, real3 = project(ev_val, rate, 90)
        n6, dry6, real6 = project(ev_val, rate, 180)
        print(f"  {label:<28} | {n3:>6} | {dry3:>+8.4f} | {real3:>+8.4f} |"
              f" {n6:>6} | {dry6:>+8.4f} | {real6:>+8.4f}")

    print()
    print("  Рост депозита 0.5 BNB (реалистичный ×0.45):")
    for label, ev_val, rate in scenarios:
        _, _, real3 = project(ev_val, rate, 90)
        _, _, real6 = project(ev_val, rate, 180)
        print(f"    {label:<28} | 3м: {0.5+real3:.4f} BNB  | 6м: {0.5+real6:.4f} BNB")

    print()
    print("  * реал = DRY × 0.45 (MEV, проскальзывание, пропущенные входы)")
    print("  * early_stop: 24 сд, -0.866 BNB — цель новых GoPlus LP-фильтров")

if __name__ == "__main__":
    main()
