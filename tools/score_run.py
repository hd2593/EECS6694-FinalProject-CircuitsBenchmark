"""Propose scores for a filled run log and screen each written solution.

Q1, Q3 and Q4 are scored from the final message with the rules in
docs/protocol.md. Q2 also needs a reader: its settling direction is checked
here, and the reader's decision on the explanation comes from a judgments file.

The screen is an aid for judging the written solution. It solves the case
exactly, lists every quantity a correct solution could state (node voltages
before and after settling, branch currents, series and parallel resistor
combinations, time constants, for the original and the changed circuit) and
reports any number with a unit in the response that matches none of them. It
also compares each connection the response states for a part ("R3 connects X
to OUT") with the netlist, in Q1 answers and wherever a response says how it
read the schematic. A reader settles whatever the screen reports.

    python tools/score_run.py LOG [--judgments FILE]
"""
import argparse
from fractions import Fraction as F
from itertools import combinations
import json
from pathlib import Path
import re

from cc_trials import read_log, write_log


ROOT = Path(__file__).resolve().parents[1]
NUMBER = r"(?<![\w.])(\d+(?:\.\d+)?)(?:\s*/\s*(\d+(?:\.\d+)?))?"
OHM = ("\u03a9", "\u2126")  # Greek capital omega and the ohm sign look alike
MICRO = ("\u00b5", "\u03bc")  # so do the micro sign and Greek mu
UNITS = {"kOhm": "kohm", "kohm": "kohm", "k": "kohm", "mA": "mA", "mS": "mS", "ms": "ms",
         "us": "us", "uF": "uF", "mV": "mV", "V": "V"}
UNITS.update({"k" + o: "kohm" for o in OHM})
UNITS.update({m + "s": "us" for m in MICRO})
UNITS.update({m + "F": "uF" for m in MICRO})
TOKEN = re.compile(NUMBER + r"\s*(" + "|".join(sorted(UNITS, key=len, reverse=True))
                   + r")(?![A-Za-z0-9(_/])")
PART = re.compile(r"\b([RCV]\d)\b")
NODE = re.compile(r"\b(IN|OUT|X|ground|node 0|(?<![\d.])0(?![\d.]|\s*(?:V\b|→|->)))\b")


def parse(text, double=None):
    source, resistors, cap = None, {}, None
    for line in text.strip().splitlines()[1:]:
        part = line.split()
        if part[0] == "V1":
            source = F(re.search(r"PULSE\(0 ([\d.]+)", line).group(1))
        elif part[0].startswith("R"):
            resistors[part[0]] = (part[1], part[2], F(part[3][:-1]) * (2 if part[0] == double else 1))
        elif part[0] == "C1":
            cap = (part[1], part[2], F(part[3][:-1]))
    return source, resistors, cap


def gauss(a, b):
    n = len(b)
    for i in range(n):
        pivot = a[i][i]
        a[i] = [x / pivot for x in a[i]]
        b[i] /= pivot
        for j in range(n):
            if j != i and a[j][i]:
                f = a[j][i]
                a[j] = [x - f * y for x, y in zip(a[j], a[i])]
                b[j] -= f * b[i]
    return b


def nodes(source, resistors, tied=None, inject=None):
    """Node voltages. `tied` joins the capacitor's two nodes, as an uncharged capacitor does."""
    known = {"IN": source, "0": F(0)}
    free, alias = ["X", "OUT"], {}
    if tied:
        a, b = tied
        if a in known or b in known:
            pinned = b if a in known else a
            known[pinned] = known[a if a in known else b]
            free.remove(pinned)
        else:
            alias["OUT"] = "X"
            free.remove("OUT")
    idx = {n: i for i, n in enumerate(free)}
    a = [[F(0)] * len(free) for _ in free]
    b = [F(0)] * len(free)
    for p, q, r in resistors.values():
        p, q = alias.get(p, p), alias.get(q, q)
        for u, w in ((p, q), (q, p)):
            if u in idx and u != w:
                a[idx[u]][idx[u]] += 1 / r
                if w in idx:
                    a[idx[u]][idx[w]] -= 1 / r
                else:
                    b[idx[u]] += known[w] / r
    for node, current in (inject or {}).items():
        if node in idx:
            b[idx[node]] += current
    solved = dict(known, **dict(zip(free, gauss(a, b))))
    solved.update({k: solved[v] for k, v in alias.items()})
    return solved


def describe(text, double=None):
    source, resistors, cap = parse(text, double)
    start = nodes(source, resistors, tied=cap[:2])
    end = nodes(source, resistors)
    # 1 mA into one capacitor node and out of the other, source set to zero.
    probe = nodes(F(0), resistors, inject={cap[0]: F(1), cap[1]: F(-1)})
    r_th = probe[cap[0]] - probe[cap[1]]
    return dict(source=source, resistors=resistors, cap=cap, start=start, end=end,
                r_th=r_th, tau=r_th * cap[2])


def combos(values):
    """Every series or parallel combination that uses each resistor at most once."""
    table = {}
    for mask in range(1, 1 << len(values)):
        members = [i for i in range(len(values)) if mask >> i & 1]
        if len(members) == 1:
            table[mask] = {values[members[0]]}
            continue
        found, part = set(), (mask - 1) & mask
        while part:
            rest = mask ^ part
            if part < rest:
                for a in table[part]:
                    for b in table[rest]:
                        found.update((a + b, a * b / (a + b)))
            part = (part - 1) & mask
        table[mask] = found
    return set().union(*table.values())


def quantities(case, double):
    """What a correct solution of this case may state, by unit."""
    text = (ROOT / "inputs" / case / "circuit.cir").read_text()
    allowed = {u: {F(0)} for u in ("V", "kohm", "mA", "mS", "ms", "uF")}
    for circuit in (describe(text), describe(text, double)):
        allowed["uF"].add(circuit["cap"][2])
        allowed["ms"].add(circuit["tau"])
        ohms = combos([r for _, _, r in circuit["resistors"].values()])
        allowed["kohm"] |= ohms
        allowed["mS"] |= {1 / r for r in ohms}
        for state in (circuit["start"], circuit["end"]):
            levels = set(state.values())
            allowed["V"] |= levels | {abs(a - b) for a, b in combinations(levels, 2)}
            branch = [abs(state[p] - state[q]) / r for p, q, r in circuit["resistors"].values()]
            allowed["mA"] |= set(branch) | {a + b for a, b in combinations(branch, 2)}
        # The size of the settling step at each node.
        allowed["V"] |= {abs(circuit["start"][n] - circuit["end"][n]) for n in ("X", "OUT")}
    return {u: sorted(float(v) for v in vs) for u, vs in allowed.items()}


def stated(text):
    """Numbers with a unit: (value, tolerance from the digits shown, unit, original, divisor)."""
    text = text.replace("\u2212", "-").replace("\u202f", " ")
    for m in TOKEN.finditer(text):
        if text[:m.start()].rstrip().endswith("/"):
            continue  # the divisor of a longer expression, such as (54 - 7 v)/13 V
        top, bottom, unit = m.groups()
        value = float(top) / float(bottom) if bottom else float(top)
        digits = 0 if bottom or "." not in top else len(top.split(".")[1])
        tolerance = 1e-6 * max(1, value) if bottom else 0.6 * 10 ** -digits if digits else 0.02
        unit = UNITS[unit]
        scale = {"mV": 1e-3, "us": 1e-3}.get(unit, 1)
        yield (value * scale, tolerance * scale, {"mV": "V", "us": "ms"}.get(unit, unit),
               m.group(0), float(bottom) if bottom else None)


def connections(text):
    """Yield (part, node pair) where a part is followed by two node names before the next part."""
    marks = list(PART.finditer(text))
    for mark, following in zip(marks, marks[1:] + [None]):
        seen = []
        for node in NODE.findall(text[mark.end():following.start() if following else len(text)]):
            node = "0" if node in ("ground", "node 0") else node
            if node not in seen:
                seen.append(node)
        if len(seen) >= 2:
            yield mark.group(1), frozenset(seen[:2])


def screen(row, double):
    """Count the checkable statements in a response and list those that fit the circuit badly."""
    case, text = row["case"], row["working_text"] + "\n\n" + row["raw_response"]
    allowed = quantities(case, double)
    odd, count = [], 0
    for value, tol, unit, raw, bottom in stated(text):
        count += 1
        fits = any(abs(value - v) <= tol for v in allowed[unit])
        # "2/6 kΩ" and "1/4k" divide a voltage or 1 by a resistance; they are not resistances.
        over = unit == "kohm" and bottom and any(abs(bottom - v) <= 0.02 for v in allowed["kohm"])
        if not (fits or over):
            odd.append(raw)
    _, resistors, cap = parse((ROOT / "inputs" / case / "circuit.cir").read_text())
    wiring = {name: frozenset(v[:2]) for name, v in resistors.items()}
    wiring.update(C1=frozenset(cap[:2]), V1=frozenset(("IN", "0")))
    # Outside Q1, prose such as "R4 = 2 kΩ, so V(X) = 4 V" is not a statement about wiring.
    where = text if row["question"] == "Q1" else "\n\n".join(
        p for p in text.split("\n\n") if re.search(r"schematic|as read", p))
    links = 0
    for part, pair in connections(where):
        links += 1
        if wiring.get(part) != pair:
            odd.append(f"{part} between {' and '.join(sorted(pair))}")
    return count, links, odd


def first(text, unit):
    return next(((v, raw) for v, _, u, raw, _ in stated(text) if u == unit), None)


def score(row, key, judgment):
    """Return (score or None, evidence) for one response."""
    answer, q = row["raw_response"], row["question"]
    lead = answer.split("\n\n")[0]
    if q == "Q1":
        named = sorted(set(re.findall(r"\b[RCV]\d\b", lead.upper())))
        return int(named == key["Q1"]), f"lists {', '.join(named) or 'nothing'}; key {', '.join(key['Q1'])}"
    if q == "Q2":
        said = re.search(r"upward|downward", answer.lower())
        said = said.group(0) if said else "no direction"
        others = set(re.findall(r"upward|downward", answer.lower())) - {said}
        note = f"says {said}; key {key['Q2']['direction']}"
        if others:
            return None, note + "; both directions appear, needs a reader"
        if said != key["Q2"]["direction"]:
            return 0, note
        if "explains" not in judgment:
            return None, note + "; explanation not yet judged"
        return int(judgment["explains"]), note + "; " + judgment["note"]
    unit, reference, floor = ("V", key["Q3_V"], 0.01) if q == "Q3" else ("ms", key["Q4_ms"], 0.005)
    found = first(answer, unit)
    if found is None:
        return 0, f"no value in {unit}; key {reference:.4g} {unit}"
    tolerance = max(floor, 0.01 * abs(reference))
    error = abs(found[0] - reference)
    return int(error <= tolerance), (f"answers {found[1]}; key {reference:.4f} {unit}; "
                                     f"off by {error:.4f}, allowed {tolerance:.4f}")


def main(log, judgments_path):
    keys = json.loads((ROOT / "team" / "keys.json").read_text())
    judgments = json.loads(Path(judgments_path).read_text()) if judgments_path else {}
    rows, fieldnames = read_log(log)
    pending, flagged = [], []
    for row in rows:
        if not row["raw_response"].strip():
            continue
        key, judgment = keys[row["case"]], judgments.get(row["trial"], {})
        points, evidence = score(row, key, judgment)
        retry = re.match(r"retry: .*?\. ", row["evidence_or_retry_note"])
        row["score"] = "" if points is None else str(points)
        row["evidence_or_retry_note"] = (retry.group(0) if retry else "") + evidence
        if points is None:
            pending.append(row["trial"])
        count, links, odd = screen(row, key["double"])
        checked = f"{count} stated quantities and {links} stated connections checked against the circuit"
        if "validity" in judgment:
            row["solution_validity"] = judgment["validity"]
            row["validity_note"] = judgment["validity_note"] + f" ({checked}" + (
                f"; unmatched: {'; '.join(odd)})" if odd else ")")
        elif odd:
            row["solution_validity"] = ""
            row["validity_note"] = f"{checked}; unmatched, needs a reader: {'; '.join(odd)}"
            flagged.append((row["trial"], row["case"], row["question"], odd))
        else:
            row["solution_validity"] = "valid" if count + links else "answer only"
            row["validity_note"] = checked + ("; all consistent" if count + links else "; none stated")
    write_log(log, rows, fieldnames)
    done = [r for r in rows if r["score"]]
    print(f"{len(done)} scored, {sum(int(r['score']) for r in done)} correct")
    if pending:
        print("need a judgment:", " ".join(pending))
    for trial, case, q, odd in flagged:
        print(f"screen {trial} {case} {q}: {odd}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log", type=Path)
    parser.add_argument("--judgments", type=Path)
    args = parser.parse_args()
    main(args.log, args.judgments)
