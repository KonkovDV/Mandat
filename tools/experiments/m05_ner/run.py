# ruff: noqa
# Одноразовый скрипт эксперимента M-05 (#65): не часть пакета, правила стиля пакета к нему не применяются.
import json
import os
import re
import sys
import time
from pathlib import Path

os.chdir(os.environ.get("M05_DATA", os.path.expanduser("~/.cache/mandat-m05")))
SET = json.load(open(os.environ.get("NAMES_SET", "names_set.json")))
D = json.load(open("dict.json"))
FP_TEXT = open("law14.txt").read().split("\n") + open(Path(__file__).resolve().parent / "fp_extra.txt").read().split(
    "\n"
)
FP_TEXT = [t for t in FP_TEXT if t.strip()]
WORD = re.compile(r"[А-ЯЁA-Z][а-яёa-z]+(?:-[А-ЯЁ][а-яё]+)?")
END = r"(?:а|я|у|ю|ом|ем|ой|ей|ою|е|ы|и|ым|им|ых|их|ого|его|ому|ему)?"
PATR = re.compile(r"^[А-ЯЁ][а-яё]+(?:ович|евич|ич|овна|евна|ична|инична)" + END + "$")
SURN_SUFFIX = re.compile(
    r"^[А-ЯЁ][а-яё]+(?:ов|ев|ёв|ин|ын|ск|цк|енко|ук|юк|ян|дзе|швили|их|ых)"
    + r"(?:ий|ой|ая|ого|ому|им|ом|ой|ую|ая|ей|а|у|ым|е|ы|их|ими)?"
    + "$"
)


def stem(w):
    w = w.lower()
    for e in sorted(
        [
            "а",
            "я",
            "у",
            "ю",
            "ом",
            "ем",
            "ой",
            "ей",
            "ою",
            "е",
            "ы",
            "и",
            "ым",
            "им",
            "ь",
            "й",
            "ий",
            "ого",
            "ему",
            "ому",
            "ая",
            "ую",
            "ов",
            "ев",
            "ёв",
            "ова",
            "ева",
            "ин",
            "ина",
            "ым",
        ],
        key=len,
        reverse=True,
    ):
        if w.endswith(e) and len(w) - len(e) >= 3:
            return w[: -len(e)]
    return w


GIVEN = {stem(w) for w in D["given"]}
SURN = {stem(w) for w in D["surnames"]}


def dictionary(text):
    toks = [(m.start(), m.end(), m.group(0)) for m in WORD.finditer(text)]
    init = [(m.start(), m.end()) for m in re.finditer(r"\b[А-ЯЁ]\.\s?(?:[А-ЯЁ]\.)?", text)]
    spans = list(init)
    kind = []
    for s, e, w in toks:
        k = set()
        if PATR.match(w):
            k.add("P")
        if stem(w) in GIVEN:
            k.add("G")
        if stem(w) in SURN:
            k.add("S")
        if SURN_SUFFIX.match(w):
            k.add("s")
        kind.append(k)

    def near_init(s, e):
        return any(abs(a - e) <= 2 or abs(s - b) <= 2 for a, b in init)

    for i, (s, e, w) in enumerate(toks):
        k = kind[i]
        nb = set().union(*(kind[j] for j in (i - 1, i + 1) if 0 <= j < len(toks)))
        # Рядом пара «имя + отчество» — соседнее слово с заглавной считается фамилией (fail-closed).
        pair = any(j + 1 < len(toks) and "G" in kind[j] and "P" in kind[j + 1] for j in (i + 1, i - 2) if 0 <= j)
        if (
            (pair and not (s == 0))
            or "P" in k
            or "S" in k
            or ("G" in k and nb & {"P", "S", "s"})
            or ("s" in k and (nb & {"P", "G"} or near_init(s, e)))
            or (near_init(s, e) and ("G" in k or "s" in k))
        ):
            spans.append((s, e))
    return spans


def natasha_factory():
    from natasha import Doc, NewsEmbedding, NewsNERTagger, Segmenter

    seg, ner = Segmenter(), NewsNERTagger(NewsEmbedding())

    def f(text):
        d = Doc(text)
        d.segment(seg)
        d.tag_ner(ner)
        return [(x.start, x.stop) for x in d.spans if x.type == "PER"]

    return f


def covered(a, b, spans):
    return any(s <= a and b <= e for s, e in spans) or sum(max(0, min(b, e) - max(a, s)) for s, e in spans) >= (b - a)


def evaluate(name, fn):
    t0 = time.time()
    res = {}
    for split in ("seen", "unseen"):
        rows = [c for c in SET if c["split"] == split]
        tok = hit = per = per_hit = 0
        for c in rows:
            sp = fn(c["text"])
            ok = True
            for a, b, part in c["gold"]:
                if part in "io":
                    continue
                tok += 1
                h = covered(a, b, sp)
                hit += h
                ok &= h
            per += 1
            per_hit += ok
        res[split] = {
            "token_recall": round(hit / tok, 4),
            "person_recall": round(per_hit / per, 4),
            "tokens": tok,
            "persons": per,
        }
    fp_tokens = cap = 0
    examples = []
    for t in FP_TEXT:
        sp = fn(t)
        for m in WORD.finditer(t):
            cap += 1
            if covered(m.start(), m.end(), sp):
                fp_tokens += 1
                examples.append(m.group(0))
    res["fp"] = {
        "capitalised_tokens": cap,
        "masked": fp_tokens,
        "rate": round(fp_tokens / cap, 4),
        "examples": sorted(set(examples))[:25],
    }
    res["seconds"] = round(time.time() - t0, 2)
    return res


methods = {"dictionary": dictionary}
if "--v1" in sys.argv:
    methods = {}
if "--natasha" in sys.argv:
    nat = natasha_factory()
    methods["natasha"] = nat
    methods["union"] = lambda t: nat(t) + dictionary(t)
out = {k: evaluate(k, f) for k, f in methods.items()}
print(json.dumps(out, ensure_ascii=False, indent=1))
