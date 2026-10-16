"""Скачать исходные данные эксперимента M-05 в $M05_DATA (по умолчанию ~/.cache/mandat-m05, в репозиторий не входят).

- Wikidata (CC0): имена (Q12308941, Q11879590) и фамилии (Q101352) с кириллической записью;
  люди с гражданством России 1960–1995 г. р. — пары «фамилия, имя» для размеченного набора.
- Викитека: главы I–IV Федерального закона № 14-ФЗ — тексты о полномочиях органов общества без ФИО
  (официальный документ, не объект авторского права, ГК РФ ст. 1259 п. 6).
"""

from __future__ import annotations

import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

DATA = Path(os.environ.get("M05_DATA", os.path.expanduser("~/.cache/mandat-m05")))
UA = {"User-Agent": "mandat-research/0.1 (offline NER evaluation)", "Accept": "text/csv"}
QUERIES = {
    "g_Q12308941.csv": 'SELECT ?l WHERE { ?i wdt:P31 wd:Q12308941; wdt:P282 wd:Q8209; rdfs:label ?l. FILTER(lang(?l)="ru") }',  # noqa: E501
    "g_Q11879590.csv": 'SELECT ?l WHERE { ?i wdt:P31 wd:Q11879590; wdt:P282 wd:Q8209; rdfs:label ?l. FILTER(lang(?l)="ru") }',  # noqa: E501
    "surn.csv": (
        'SELECT DISTINCT ?l WHERE { ?i wdt:P31 wd:Q101352; wdt:P282 wd:Q8209; rdfs:label ?l. FILTER(lang(?l)="ru") '
        'FILTER(REGEX(?l,"^[А-ЯЁ][а-яё]+$")) }'
    ),
    "people.csv": (
        "SELECT ?fl ?gl ?sex WHERE { ?p wdt:P31 wd:Q5; wdt:P27 wd:Q159; wdt:P569 ?b; wdt:P734 ?f; wdt:P735 ?g; "
        'wdt:P21 ?sex. FILTER(YEAR(?b) >= 1960 && YEAR(?b) <= 1995) ?f rdfs:label ?fl. FILTER(lang(?fl)="ru") '
        '?g rdfs:label ?gl. FILTER(lang(?gl)="ru") } LIMIT 6000'
    ),
}


def sparql(query: str) -> bytes:
    url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": query})
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120).read()


def law14() -> str:
    parts = []
    for chapter in ("I", "II", "III", "IV"):
        title = f"Федеральный закон от 08.02.1998 № 14-ФЗ/Глава {chapter}"
        url = "https://ru.wikisource.org/w/index.php?" + urllib.parse.urlencode({"title": title, "action": "raw"})
        parts.append(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read().decode())
    text = "\n".join(parts)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)
    return re.sub(r"<[^>]+>|'''?|==+", "", text)


def main() -> None:
    DATA.mkdir(exist_ok=True)
    for name, query in QUERIES.items():
        (DATA / name).write_bytes(sparql(query))
        time.sleep(2)
    (DATA / "law14.txt").write_text(law14(), encoding="utf-8")


if __name__ == "__main__":
    main()
