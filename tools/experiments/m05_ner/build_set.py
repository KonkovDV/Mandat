# ruff: noqa
# Одноразовый скрипт эксперимента M-05 (#65): не часть пакета, правила стиля пакета к нему не применяются.
"""Размеченный набор: люди из Wikidata (CC0), отчество и падеж синтетические; FP-корпус — 14-ФЗ + шаблоны."""

import csv
import hashlib
import json
import os
import random
import re
from pathlib import Path

os.chdir(os.environ.get("M05_DATA", os.path.expanduser("~/.cache/mandat-m05")))
import sys

import pymorphy3

rng = random.Random(int(sys.argv[1]) if len(sys.argv) > 1 else 20261016)
OUT = sys.argv[2] if len(sys.argv) > 2 else "names_set.json"
morph = pymorphy3.MorphAnalyzer()
CYR = re.compile(r"^[А-ЯЁ][а-яё]+$")


def col(path):
    return [r[0] for r in csv.reader(open(path)) if r and CYR.match(r[0])][0:]


given_m, given_f, surn = set(col("g_Q12308941.csv")), set(col("g_Q11879590.csv")), set(col("surn.csv"))
half = lambda w: int(hashlib.sha1(w.encode()).hexdigest(), 16) % 2
dict_surn = sorted(w for w in surn if half(w) == 0)
json.dump({"given": sorted(given_m | given_f), "surnames": dict_surn}, open("dict.json", "w"), ensure_ascii=False)
people = [r for r in csv.DictReader(open("people.csv")) if CYR.match(r["fl"]) and CYR.match(r["gl"])]
FATHERS = {
    "Иван": "Иванов",
    "Пётр": "Петров",
    "Сергей": "Сергеев",
    "Андрей": "Андреев",
    "Николай": "Николаев",
    "Алексей": "Алексеев",
    "Дмитрий": "Дмитриев",
    "Михаил": "Михайлов",
    "Владимир": "Владимиров",
    "Юрий": "Юрьев",
    "Олег": "Олегов",
    "Игорь": "Игорев",
    "Виктор": "Викторов",
    "Анатолий": "Анатольев",
}


def fem_surname(s):
    for a, b in (("ский", "ская"), ("цкий", "цкая"), ("ой", "ая")):
        if s.endswith(a):
            return s[: -len(a)] + b
    if re.search(r"(ов|ев|ёв|ин|ын)$", s):
        return s + "а"
    return s


def inflect(word, case, gender, kind):
    for p in morph.parse(word):
        if kind in p.tag:
            f = p.inflect({case, gender} if gender else {case})
            if f:
                w = f.word
                return w[0].upper() + w[1:]
    return word


seen, unseen = [], []
for r in people:
    female = r["sex"].endswith("Q6581072")
    if female and r["gl"] not in given_f and r["gl"] in given_m:
        continue
    fam = fem_surname(r["fl"]) if female else r["fl"]
    (seen if r["fl"] in set(dict_surn) else unseen).append((fam, r["gl"], female))
rng.shuffle(seen)
rng.shuffle(unseen)
TEMPL = [
    ("nomn", "Генеральный директор {F} {I} {O} действует на основании Устава."),
    ("gent", "Доверенность выдана от имени {F} {I} {O}, генерального директора Общества."),
    ("datv", "Обществом поручено {F} {I} {O} подписывать платёжные документы."),
    ("ablt", "Договор подписан {F} {I} {O} в присутствии представителя банка."),
    ("nomn", "Представитель {F} {i}.{o}. вправе получать выписки по счёту."),
    ("nomn", "Подписант: {i}. {o}. {F}."),
    ("nomn", "{I} {O} {F} назначен на должность единоличного исполнительного органа."),
    ("gent", "Решение принято по заявлению {F}."),
    ("loct", "Сведения о {I} {O} внесены в ЕГРЮЛ."),
]


def make(person, k):
    fam, name, female = person
    father = rng.choice(list(FATHERS))
    pat = FATHERS[father] + ("на" if female else "ич")
    pat = pat.replace("овна", "овна").replace("евна", "евна")
    if not female:
        pat = FATHERS[father][:-2] + ("ович" if FATHERS[father].endswith("ов") else "евич")
    else:
        pat = FATHERS[father][:-2] + ("овна" if FATHERS[father].endswith("ов") else "евна")
    case, t = TEMPL[k % len(TEMPL)]
    g = "femn" if female else "masc"
    F, I, O = inflect(fam, case, g, "Surn"), inflect(name, case, g, "Name"), inflect(pat, case, g, "Patr")
    vals = {"F": F, "I": I, "O": O, "i": name[0], "o": pat[0]}
    text, gold, pos = "", [], 0
    for part in re.split(r"(\{[FIOio]\})", t):
        m = re.fullmatch(r"\{([FIOio])\}", part)
        if m:
            v = vals[m.group(1)]
            gold.append((len(text), len(text) + len(v), m.group(1)))
            text += v
        else:
            text += part
    return {"text": text, "gold": gold, "case": case, "template": k % len(TEMPL)}


cases = [dict(make(p, i), split="seen") for i, p in enumerate(seen[:270])] + [
    dict(make(p, i), split="unseen") for i, p in enumerate(unseen[:270])
]
json.dump(cases, open(OUT, "w"), ensure_ascii=False, indent=0)
print(
    len(given_m), len(given_f), len(surn), len(dict_surn), "seen", len(seen), "unseen", len(unseen), "set", len(cases)
)
for c in cases[:3] + cases[-3:]:
    print(c["text"])
