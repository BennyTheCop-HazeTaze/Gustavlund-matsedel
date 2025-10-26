#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Matilda (embed eller week-URL) → iCal (matsedel.ics)

Logik:
- Mån–fre: hämta NUVARANDE vecka (mån–sön)
- Lör–sön: hämta NÄSTA vecka (mån–sön)

Miljövariabler:
  MATILDA_URL  (din embed- ELLER week-URL, t.ex.
                https://menu.matildaplatform.com/sv/embed/?displayMode=Week&distributorId=68f9fc37bf545da84ec60b23)
  CAL_NAME     (t.ex. "Gustavlundsskolan matsedel")
  OUT_ICS      (default: matsedel.ics)

Felsökning (frivilligt):
  FORCE_START=YYYY-MM-DD
  FORCE_END=YYYY-MM-DD
"""

import os, sys, re, json, datetime as dt
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import requests
from bs4 import BeautifulSoup

TZ = "Europe/Stockholm"

def week_bounds_mo_su(d: dt.date):
    monday = d - dt.timedelta(days=d.weekday())  # 0=mån
    sunday = monday + dt.timedelta(days=6)
    return monday, sunday

def target_week_bounds(today: dt.date):
    wd = today.weekday()  # 0=mån ... 5=lör, 6=sön
    if wd >= 5:
        # Lör–sön: visa NÄSTA vecka
        base = today + dt.timedelta(days=(7 - wd))  # nästa måndag
    else:
        # Mån–fre: visa nuvarande vecka
        base = today
    return week_bounds_mo_su(base)

def add_week_query_to_week_url(url: str, start: dt.date, end: dt.date) -> str:
    """
    Lägg till/uppdatera ?startDate=YYYY-MM-DD&endDate=YYYY-MM-DD på URL:en.
    Fungerar för både /meals/week/... och embed-länkar.
    """
    u = urlparse(url)
    q = parse_qs(u.query)
    q["startDate"] = [start.isoformat()]
    q["endDate"] = [end.isoformat()]
    new_query = urlencode({k: v[0] for k, v in q.items()})
    return urlunparse((u.scheme, u.netloc, u.path, u.params, new_query, u.fragment))

def fetch_html(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Matilda-ICS/1.3)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text

def find_next_data(html: str):
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except Exception:
        return None

def walk(obj):
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, list) and re.search(r"(meal|menu|dish|course)", k, re.I):
                found.append(v)
            else:
                found += walk(v)
    elif isinstance(obj, list):
        for it in obj:
            found += walk(it)
    return found

def extract_entries(next_data):
    meal_lists = walk(next_data)
    entries = []
    for lst in meal_lists:
        for item in lst:
            if not isinstance(item, dict):
                continue
            # datum
            d = None
            for dk in ("date","day","servedDate","menuDate"):
                if dk in item and item[dk]:
                    try:
                        d = dt.date.fromisoformat(str(item[dk])[:10])
                        break
                    except Exception:
                        pass
            # texter
            texts = []
            for mk in ("meals","dishes","courses","menuRows","items"):
                if mk in item and isinstance(item[mk], list):
                    for m in item[mk]:
                        if isinstance(m, dict):
                            for nk in ("name","title","dishName","courseName","label","description","text"):
                                if nk in m and m[nk]:
                                    s = str(m[nk]).strip()
                                    if s and s not in texts:
                                        texts.append(s)
                        elif isinstance(m, str):
                            s = m.strip()
                            if s and s not in texts:
                                texts.append(s)
            for nk in ("name","title","label","description","text"):
                if nk in item and item[nk]:
                    s = str(item[nk]).strip()
                    if s and s not in texts:
                        texts.append(s)

            if d and texts:
                entries.append((d, texts))

    merged = {}
    for d, lst in entries:
        merged.setdefault(d, [])
        for t in lst:
            t = re.sub(r"\s+", " ", t)
            if t and t not in merged[d]:
                merged[d].append(t)
    return sorted(merged.items(), key=lambda x: x[0])

def guess_name(next_data, default_name):
    if default_name:
        return default_name
    txt = json.dumps(next_data, ensure_ascii=False)
    m = re.search(r'"(school|kitchen|distributor|title|name)"\s*:\s*"([^"]{3,})"', txt)
    if m:
        return m.group(2)
    return "Skolmatsedel"

def build_ics(cal_name, daily_meals):
    def dtstamp():
        return dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    def fmtdate(d: dt.date):
        return d.strftime("%Y%m%d")

    now = dtstamp()
    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:-//matilda2ics//github.com//",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{cal_name}",
        f"X-WR-TIMEZONE:{TZ}",
    ]
    for d, meals in daily_meals:
        clean = []
        seen = set()
        for m in meals:
            m2 = m.strip()
            if not m2:
                continue
            key = m2.lower()
            if key in seen:
                continue
            seen.add(key)
            clean.append(m2)
        if not clean:
            continue
        description = "\\n".join(clean).replace("\n", "\\n")
        uid = f"{d.isoformat()}-{abs(hash(description))}@matilda2ics"
        lines += [
            "BEGIN:VEVENT",
            f"DTSTAMP:{now}",
            f"UID:{uid}",
            f"DTSTART;VALUE=DATE:{fmtdate(d)}",
            f"DTEND;VALUE=DATE:{fmtdate(d + dt.timedelta(days=1))}",
            "SUMMARY:Matsedel",
            f"DESCRIPTION:{description}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\n".join(lines)

def main():
    base_url = os.environ.get("MATILDA_URL") or (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if not base_url:
        print("ERROR: Ange MATILDA_URL (embed eller week-URL).", file=sys.stderr)
        sys.exit(2)

    cal_name_env = os.environ.get("CAL_NAME", "").strip()
    out_ics = os.environ.get("OUT_ICS", "matsedel.ics")

    # Välj vecka enligt lör–sön = nästa vecka
    today = dt.date.today()
    if os.environ.get("FORCE_START") and os.environ.get("FORCE_END"):
        start = dt.date.fromisoformat(os.environ["FORCE_START"])
        end = dt.date.fromisoformat(os.environ["FORCE_END"])
    else:
        start, end = target_week_bounds(today)

    url = add_week_query_to_week_url(base_url, start, end)
    try:
        html = fetch_html(url)
    except Exception as e:
        print(f"ERROR: Kunde inte hämta URL ({url}): {e}", file=sys.stderr)
        sys.exit(3)

    data = find_next_data(html)
    if not data:
        print("ERROR: Hittade ingen __NEXT_DATA__ – är URL:en korrekt och är det en Matilda/Next.js-sida?", file=sys.stderr)
        sys.exit(4)

    entries = extract_entries(data)
    # Filtrera på den valda veckan (om sidan råkar returnera mer)
    entries = [(d, meals) for d, meals in entries if start <= d <= end]

    cal_name = guess_name(data, cal_name_env)
    ics = build_ics(cal_name, entries)
    with open(out_ics, "w", encoding="utf-8") as f:
        f.write(ics)

    print(f"Period: {start}–{end} | Dagar: {len(entries)} | Kalender: {cal_name} → {out_ics}")

if __name__ == "__main__":
    main()
