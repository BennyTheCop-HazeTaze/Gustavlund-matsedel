#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hämtar veckomeny från Matilda (även embed-länk) och skriver ut en iCal-fil (matsedel.ics).

Användning:
  - Sätt env-variabler:
      MATILDA_URL="https://menu.matildaplatform.com/sv/embed/?displayMode=Week&distributorId=68ac1bbbdbb15510595ff42d"
      CAL_NAME="Gustavlundsskolan matsedel"
      OUT_ICS="matsedel.ics"   (valfritt, default matsedel.ics)
  - Eller skicka MATILDA_URL som första argv.

Scriptet letar efter __NEXT_DATA__ (Next.js) och plockar ut dagar och rätter generiskt.
"""

import os, sys, re, json, datetime as dt
from urllib.parse import urlparse, parse_qs
import requests
from bs4 import BeautifulSoup

TZ = "Europe/Stockholm"

def fetch_html(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Matilda-ICS/1.1)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
    """Rekursiv sökning efter menyblock."""
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
    """
    Försöker få ut [(date, [rätt1, rätt2,...]), ...].
    Robust mot namnskiftningar genom att leta generiskt.
    """
    meal_lists = walk(next_data)
    entries = []
    for lst in meal_lists:
        for item in lst:
            if not isinstance(item, dict):
                continue
            # Datum
            d = None
            for dk in ("date","day","servedDate","menuDate"):
                if dk in item and item[dk]:
                    try:
                        d = dt.date.fromisoformat(str(item[dk])[:10])
                        break
                    except Exception:
                        pass
            # Rätter
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
            # Fallback (text direkt på item)
            for nk in ("name","title","label","description","text"):
                if nk in item and item[nk]:
                    s = str(item[nk]).strip()
                    if s and s not in texts:
                        texts.append(s)

            if d and texts:
                entries.append((d, texts))

    # slå ihop per datum
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
        if not meals:
            continue
        # Rensa triviala dubblett-rader som ibland finns
        clean = []
        seen = set()
        for m in meals:
            m2 = m.strip()
            if not m2 or m2.lower() in seen:
                continue
            seen.add(m2.lower())
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
    url = os.environ.get("MATILDA_URL") or (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if not url:
        print("ERROR: Ange MATILDA_URL (embed eller week-URL).", file=sys.stderr)
        sys.exit(2)

    cal_name_env = os.environ.get("CAL_NAME", "").strip()
    out_ics = os.environ.get("OUT_ICS", "matsedel.ics")

    html = fetch_html(url)
    data = find_next_data(html)
    if not data:
        print("ERROR: Hittade ingen __NEXT_DATA__ – är URL:en korrekt?", file=sys.stderr)
        sys.exit(3)

    entries = extract_entries(data)
    cal_name = guess_name(data, cal_name_env)

    ics = build_ics(cal_name, entries)
    with open(out_ics, "w", encoding="utf-8") as f:
        f.write(ics)

    print(f"Skrev {out_ics} med {len(entries)} dagar. Kalender: {cal_name}")

if __name__ == "__main__":
    main()
