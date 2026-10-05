"""Chart Guy Data: official US macro data, rebuilt from public-domain sources.

Sources (all US Government works, public domain):
  BLS  - Employment Situation (NFP, unemployment, earnings), CPI, release schedule
  CFTC - Commitments of Traders (legacy, futures only)
  Fed  - FOMC meeting calendar

Writes JSON into data/. Standard library only, so it runs anywhere.
Values from BLS are the latest revised numbers. The first print of each new
release is captured from the day this pipeline sees it and never overwritten.
Forecasts / consensus are not published by these sources and are not included.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import traceback
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data")
UA = "ChartGuyData/1.0 (+https://github.com/muzamil-glitch/chartguy-terminal-releases)"
ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc

BLS_SERIES = {
    "CES0000000001": "nfp_level",        # total nonfarm payrolls, thousands, SA
    "LNS14000000": "unemployment_rate",  # %, SA
    "CES0500000003": "ahe",              # average hourly earnings, $, SA
    "CUSR0000SA0": "cpi_sa",             # CPI-U all items, SA
    "CUUR0000SA0": "cpi_nsa",            # CPI-U all items, NSA (for y/y)
    "CUSR0000SA0L1E": "core_sa",         # core CPI, SA
    "CUUR0000SA0L1E": "core_nsa",        # core CPI, NSA
}
# CFTC contract market codes (legacy report)
COT_MARKETS = {
    "099741": "EUR", "096742": "GBP", "097741": "JPY", "092741": "CHF",
    "090741": "CAD", "232741": "AUD", "112741": "NZD", "095741": "MXN",
    "098662": "USD_INDEX", "088691": "GOLD", "084691": "SILVER",
    "067651": "WTI_CRUDE", "133741": "BITCOIN",
}
# BLS schedule titles we keep, with impact
BLS_EVENTS = {
    "Employment Situation": ("Non-Farm Payrolls (Employment Situation)", "high"),
    "Consumer Price Index": ("CPI", "high"),
    "Producer Price Index": ("PPI", "medium"),
    "Job Openings and Labor Turnover Survey": ("JOLTS Job Openings", "medium"),
    "Employment Cost Index": ("Employment Cost Index", "medium"),
    "Real Earnings": ("Real Earnings", "low"),
    "Import and Export Price Indexes": ("Import/Export Prices", "low"),
}


def http(url: str, data: bytes | None = None, headers: dict | None = None, timeout=60) -> bytes:
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def load(name: str, default):
    try:
        with open(os.path.join(OUT, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(name: str, obj) -> None:
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)


def now_iso() -> str:
    return dt.datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def iso_utc(t: dt.datetime) -> str:
    return t.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------- BLS data
def parse_bls(payload: dict) -> dict[str, dict[str, float]]:
    """{series_key: {"YYYY-MM": value}} from a BLS API response."""
    if payload.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS: {payload.get('status')} {payload.get('message')}")
    out: dict[str, dict[str, float]] = {}
    for s in payload.get("Results", {}).get("series", []):
        key = BLS_SERIES.get(s.get("seriesID"))
        if not key:
            continue
        col = out.setdefault(key, {})
        for row in s.get("data", []):
            p = row.get("period", "")
            if not re.fullmatch(r"M(0[1-9]|1[0-2])", p):
                continue
            try:
                v = float(str(row.get("value", "")).replace(",", ""))
            except ValueError:
                continue
            col[f"{row['year']}-{p[1:]}"] = v
    return out


def fetch_bls(years_back=20) -> dict[str, dict[str, float]]:
    end = dt.date.today().year
    merged: dict[str, dict[str, float]] = {}
    start = end - years_back + 1
    # v1 API: max 10 years per query
    for a in range(start, end + 1, 10):
        b = min(a + 9, end)
        body = json.dumps({"seriesid": list(BLS_SERIES), "startyear": str(a), "endyear": str(b)}).encode()
        payload = json.loads(http("https://api.bls.gov/publicAPI/v1/timeseries/data/", body,
                                  {"Content-Type": "application/json"}))
        for k, col in parse_bls(payload).items():
            merged.setdefault(k, {}).update(col)
    return merged


def prev_month(p: str, n=1) -> str:
    y, m = map(int, p.split("-"))
    m -= n
    while m <= 0:
        m += 12
        y -= 1
    return f"{y}-{m:02d}"


def pct(a, b):
    return None if a is None or b in (None, 0) else round((a / b - 1) * 100, 2)


def build_nfp(s: dict) -> list[dict]:
    lvl, ur, ahe = s.get("nfp_level", {}), s.get("unemployment_rate", {}), s.get("ahe", {})
    rows = []
    for p in sorted(lvl):
        pp = prev_month(p)
        rows.append({
            "period": p,
            "nfp_change_k": round(lvl[p] - lvl[pp], 1) if pp in lvl else None,
            "nfp_level_k": lvl[p],
            "unemployment_rate": ur.get(p),
            "ahe_mom_pct": pct(ahe.get(p), ahe.get(pp)),
            "ahe_yoy_pct": pct(ahe.get(p), ahe.get(prev_month(p, 12))),
        })
    return rows


def build_cpi(s: dict) -> list[dict]:
    sa, nsa, csa, cnsa = (s.get(k, {}) for k in ("cpi_sa", "cpi_nsa", "core_sa", "core_nsa"))
    rows = []
    for p in sorted(set(sa) | set(nsa)):
        pp, py = prev_month(p), prev_month(p, 12)
        rows.append({
            "period": p,
            "cpi_mom_pct": pct(sa.get(p), sa.get(pp)),
            "cpi_yoy_pct": pct(nsa.get(p), nsa.get(py)),
            "core_mom_pct": pct(csa.get(p), csa.get(pp)),
            "core_yoy_pct": pct(cnsa.get(p), cnsa.get(py)),
        })
    return rows


def keep_first_prints(new_rows: list[dict], old: dict, fields: list[str]) -> list[dict]:
    """Attach first_print to periods this pipeline sees appear for the first time."""
    old_rows = {r["period"]: r for r in old.get("rows", [])}
    seeded = bool(old_rows)
    for r in new_rows:
        o = old_rows.get(r["period"])
        if o and o.get("first_print"):
            r["first_print"] = o["first_print"]
        elif seeded and o is None:
            r["first_print"] = {"captured": now_iso(), **{f: r.get(f) for f in fields}}
    return new_rows


# ---------------------------------------------------------- BLS schedule
def parse_ics(text: str) -> list[dict]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n[ \t]", "", text)  # unfold lines
    events = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        summ = re.search(r"^SUMMARY[^:]*:(.*)$", block, re.M)
        start = re.search(r"^DTSTART([^:]*):(\d{8}T\d{4,6}Z?)$", block, re.M)
        if not (summ and start):
            continue
        title = summ.group(1).strip().replace("\\,", ",")
        params, stamp = start.group(1), start.group(2)
        fmt = "%Y%m%dT%H%M%S" if len(stamp.rstrip("Z")) == 15 else "%Y%m%dT%H%M"
        t = dt.datetime.strptime(stamp.rstrip("Z"), fmt)
        t = t.replace(tzinfo=UTC) if stamp.endswith("Z") else t.replace(tzinfo=ET)
        for key, (name, impact) in BLS_EVENTS.items():
            if title.startswith(key):
                ref = re.search(r"for ([A-Z][a-z]+ \d{4})", title)
                events.append({"title": name, "reference": ref.group(1) if ref else None,
                               "time_utc": iso_utc(t), "currency": "USD", "impact": impact,
                               "source": "BLS"})
                break
    return events


BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"),
    "Accept": "text/calendar,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_bls_schedule() -> list[dict]:
    # www.bls.gov refuses some automated clients; try plain, then browser headers.
    errors = []
    for headers in ({}, BROWSER_HEADERS):
        try:
            raw = http("https://www.bls.gov/schedule/news_release/bls.ics", headers=headers).decode("utf-8", "replace")
            ev = parse_ics(raw)
            if ev:
                return ev
            errors.append("parsed 0 events")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}")
    raise RuntimeError("; ".join(errors))


# ------------------------------------------------------------- FOMC
MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September",
     "October", "November", "December"], 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})


def parse_fomc(html: str) -> list[dict]:
    events = []
    heads = list(re.finditer(r"(\d{4})\s+FOMC Meetings", html))
    for i, h in enumerate(heads):
        year = int(h.group(1))
        seg = html[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(html)]
        pairs = re.findall(
            r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?fomc-meeting__date[^>]*>\s*([^<]+)<',
            seg, re.S)
        for month_txt, date_txt in pairs:
            month_name = month_txt.strip().split("/")[-1].strip()
            days = re.findall(r"\d+", date_txt)
            if month_name not in MONTHS or not days:
                continue
            m = MONTHS[month_name]
            day = int(days[-1])
            # "Dec/Jan" style meetings that roll into next year
            y = year + 1 if "/" in month_txt and m < MONTHS.get(month_txt.split("/")[0].strip(), m) else year
            try:
                t = dt.datetime(y, m, day, 14, 0, tzinfo=ET)
            except ValueError:
                continue
            events.append({"title": "FOMC Rate Decision", "reference": None, "time_utc": iso_utc(t),
                           "currency": "USD", "impact": "high", "source": "Federal Reserve",
                           "projections": "*" in date_txt,
                           "unscheduled": "unscheduled" in date_txt.lower()})
    return events


def fetch_fomc() -> list[dict]:
    html = http("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm").decode("utf-8", "replace")
    return parse_fomc(html)


# --------------------------------------------------------------- COT
def fetch_cot(since="2006-01-01") -> dict:
    codes = ",".join(f"'{c}'" for c in COT_MARKETS)
    q = {
        "$where": f"cftc_contract_market_code in({codes}) AND report_date_as_yyyy_mm_dd >= '{since}T00:00:00'",
        "$order": "report_date_as_yyyy_mm_dd",
        "$limit": "50000",
        "$select": ("report_date_as_yyyy_mm_dd,cftc_contract_market_code,open_interest_all,"
                    "noncomm_positions_long_all,noncomm_positions_short_all,"
                    "comm_positions_long_all,comm_positions_short_all"),
    }
    url = "https://publicreporting.cftc.gov/resource/6dca-aqww.json?" + urllib.parse.urlencode(q)
    rows = json.loads(http(url))
    return build_cot(rows)


def _i(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def build_cot(rows: list[dict]) -> dict:
    out: dict[str, list] = {}
    for r in rows:
        name = COT_MARKETS.get(str(r.get("cftc_contract_market_code", "")).strip())
        if not name:
            continue
        nl, ns = _i(r.get("noncomm_positions_long_all")), _i(r.get("noncomm_positions_short_all"))
        cl, cs = _i(r.get("comm_positions_long_all")), _i(r.get("comm_positions_short_all"))
        out.setdefault(name, []).append({
            "date": str(r.get("report_date_as_yyyy_mm_dd", ""))[:10],
            "noncomm_long": nl, "noncomm_short": ns,
            "noncomm_net": None if nl is None or ns is None else nl - ns,
            "comm_net": None if cl is None or cs is None else cl - cs,
            "open_interest": _i(r.get("open_interest_all")),
        })
    for v in out.values():
        v.sort(key=lambda x: x["date"])
    return out


# --------------------------------------------------------------- main
def attach_release_times(rows: list[dict], events: list[dict], title: str) -> None:
    """Map 'Employment Situation for August 2026' -> period 2026-08."""
    by_ref = {}
    for e in events:
        if e["title"] == title and e.get("reference"):
            try:
                d = dt.datetime.strptime(e["reference"], "%B %Y")
                by_ref[f"{d.year}-{d.month:02d}"] = e["time_utc"]
            except ValueError:
                pass
    old = {r["period"]: r.get("release_time_utc") for r in rows}
    for r in rows:
        r["release_time_utc"] = by_ref.get(r["period"]) or old.get(r["period"])


def main() -> int:
    status = {"updated": now_iso(), "sources": {}}
    licence = "US Government work, public domain. Compiled by Chart Guy Data."

    def run(name, fn):
        try:
            res = fn()
            status["sources"][name] = {"ok": True}
            return res
        except Exception as e:  # noqa: BLE001 - report every failure, keep going
            status["sources"][name] = {"ok": False, "error": f"{type(e).__name__}: {e}",
                                       "trace": traceback.format_exc()[-800:]}
            return None

    old_cal = load("calendar.json", {"events": []})
    bls_events = run("bls_schedule", fetch_bls_schedule)
    fomc_events = run("fomc", fetch_fomc)
    # keep previously seen events, replace by source when a fresh fetch succeeded
    keep = [e for e in old_cal.get("events", [])
            if (e["source"] == "BLS" and bls_events is None) or
               (e["source"] == "Federal Reserve" and fomc_events is None)]
    events = keep + (bls_events or []) + (fomc_events or [])
    seen, uniq = set(), []
    for e in sorted(events, key=lambda e: e["time_utc"]):
        k = (e["title"], e["time_utc"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    save("calendar.json", {"updated": status["updated"], "licence": licence, "events": uniq})
    status["sources"].setdefault("bls_schedule", {})["count"] = len(bls_events or [])
    status["sources"].setdefault("fomc", {})["count"] = len(fomc_events or [])

    series = run("bls_data", fetch_bls)
    if series:
        nfp = keep_first_prints(build_nfp(series), load("nfp.json", {}),
                                ["nfp_change_k", "unemployment_rate", "ahe_mom_pct"])
        attach_release_times(nfp, uniq, "Non-Farm Payrolls (Employment Situation)")
        cpi = keep_first_prints(build_cpi(series), load("cpi.json", {}),
                                ["cpi_mom_pct", "cpi_yoy_pct", "core_mom_pct", "core_yoy_pct"])
        attach_release_times(cpi, uniq, "CPI")
        note = "Latest revised values. first_print is recorded from the first time this pipeline saw a release."
        save("nfp.json", {"updated": status["updated"], "licence": licence, "note": note, "rows": nfp})
        save("cpi.json", {"updated": status["updated"], "licence": licence, "note": note, "rows": cpi})
        status["sources"]["bls_data"]["rows"] = {"nfp": len(nfp), "cpi": len(cpi)}

    cot = run("cftc_cot", fetch_cot)
    if cot:
        save("cot.json", {"updated": status["updated"], "licence": licence,
                          "note": "Legacy futures-only report. Net = long - short.", "markets": cot})
        status["sources"]["cftc_cot"]["markets"] = {k: len(v) for k, v in cot.items()}

    save("_status.json", status)
    print(json.dumps(status, indent=1))
    return 0 if all(s.get("ok") for s in status["sources"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
