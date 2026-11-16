"""Collect pilot trials through Claude Code subagents when no API key is available.

One trial is one fresh subagent that receives a fixed harness line, then the
netlist text or the path of one schematic PNG, then the common instruction and
one question. The harness cannot switch a subagent's tools off, so `collect`
audits every transcript and marks a trial eligible only when the prompt matches
the manifest byte for byte, the model and effort are the expected ones, the
subagent started in the neutral staging folder, and the tool calls are exactly
the ones the format allows (none for a netlist, one read of the staged PNG for
an image). A subagent returns its answer through the harness's hand-back call;
that message is the scored response. Text it wrote before that is kept beside it.

    python tools/pilot.py new LOG --all --models SLOT
    python tools/cc_trials.py stage LOG MANIFEST --stage-dir DIR
    python tools/cc_trials.py show MANIFEST T001 T002 ...
    python tools/cc_trials.py collect LOG MANIFEST TRANSCRIPT_DIR --raw RAW.jsonl
"""
import argparse
import base64
import csv
import difflib
import hashlib
import json
from pathlib import Path
import random
import shutil

from designs import COMMON_PROMPT, DESIGNS, questions
from pilot import FIELDS


ROOT = Path(__file__).resolve().parents[1]
EXTRA_FIELDS = ["trial", "working_text", "solution_validity", "validity_note"]
HANDBACK = "SubagentHandback"

NETLIST_HARNESS = (
    "This is a single self-contained question. Answer it from your own reasoning only: "
    "do not use any tools (no file access, no code execution, no web or simulators). "
    "Your final message is recorded verbatim as your answer."
)
IMAGE_HARNESS = (
    "This is a single self-contained question. Answer it from your own reasoning only. "
    "The circuit is supplied as one image file: open {path} once with the Read tool, "
    "and do not use any other tools (no other file access, no code execution, no web "
    "or simulators). Your final message is recorded verbatim as your answer."
)


def build_prompt(fmt, netlist, image_path, question):
    """The harness line is the only text outside the benchmark's own wording."""
    task = COMMON_PROMPT + "\n\n" + question
    if fmt == "netlist":
        return NETLIST_HARNESS + "\n\n" + netlist.strip("\n") + "\n\n" + task
    return IMAGE_HARNESS.format(path=image_path) + "\n\n" + task


def read_log(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as fp:
        reader = csv.DictReader(fp)
        return list(reader), list(reader.fieldnames or [])


def write_log(path, rows, fieldnames):
    with Path(path).open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def stage(log, manifest_path, stage_dir):
    rows, fieldnames = read_log(log)
    stage_dir = Path(stage_dir).resolve()
    by_id = {d.id: d for d in DESIGNS}
    # Opaque folder names keep the case ID and the repository path out of the
    # image request. The netlist request still shows its own title line.
    tags = random.Random(6694).sample(range(0x1000, 0xFFFF), len(DESIGNS))
    folder = {d.id: f"{t:04x}" for d, t in zip(DESIGNS, tags)}
    trials = []
    for n, row in enumerate(rows, 1):
        case, q, fmt = row["case"], row["question"], row["format"]
        source = ROOT / "inputs" / case
        sheet = (source / "questions.md").read_text(encoding="utf-8")
        question = questions(by_id[case])[q]
        # The committed question sheet is what a teammate would copy from.
        assert COMMON_PROMPT in sheet and f"**{q}.** {question}" in sheet, (case, q)
        trial = dict(trial=f"T{n:03d}", case=case, question=q, format=fmt)
        if fmt == "image":
            target = stage_dir / folder[case] / "schematic.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / "schematic.png", target)
            trial["image_path"] = str(target)
            trial["image_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
            netlist = None
        else:
            netlist = (source / "circuit.cir").read_text(encoding="utf-8")
        trial["prompt"] = build_prompt(fmt, netlist, trial.get("image_path"), question)
        trial["prompt_sha256"] = hashlib.sha256(trial["prompt"].encode()).hexdigest()
        trials.append(trial)
        row["trial"] = trial["trial"]
    Path(manifest_path).write_text(json.dumps(trials, indent=2, ensure_ascii=False) + "\n",
                                   encoding="utf-8")
    write_log(log, rows, fieldnames + [f for f in EXTRA_FIELDS if f not in fieldnames])
    print(f"{len(trials)} trials staged; images under {stage_dir}")


def show(manifest_path, wanted):
    trials = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    for t in trials:
        if t["trial"] in wanted:
            print(f"===== {t['trial']} =====\n{t['prompt']}\n")


def blocks(message):
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)]


def digest(path):
    """Reduce one subagent transcript to the fields a run log needs."""
    out = dict(transcript=Path(path).name, prompt=None, models=[], efforts=[], cwds=[],
               tool_calls=[], images_seen=[], texts=[], final=None, output_tokens=0,
               started=None, ended=None, harness_version=None)
    seen_message_ids = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = event.get("message")
        if not isinstance(message, dict) or event.get("type") not in ("user", "assistant"):
            continue
        if out["final"] is not None:
            continue  # the hand-back ended the run; later events are harness bookkeeping
        stamp = event.get("timestamp")
        out["started"] = out["started"] or stamp
        out["ended"] = stamp or out["ended"]
        out["harness_version"] = event.get("version") or out["harness_version"]
        if event.get("cwd") and event["cwd"] not in out["cwds"]:
            out["cwds"].append(event["cwd"])
        if event["type"] == "user":
            for block in blocks(message):
                if block["type"] == "text" and out["prompt"] is None:
                    out["prompt"] = block["text"]
                elif block["type"] == "tool_result":
                    inner = block.get("content")
                    for item in inner if isinstance(inner, list) else []:
                        if isinstance(item, dict) and item.get("type") == "image":
                            raw = base64.b64decode(item["source"]["data"])
                            out["images_seen"].append(hashlib.sha256(raw).hexdigest())
            continue
        for key, field in (("models", message.get("model")), ("efforts", event.get("effort"))):
            if field and field not in out[key]:
                out[key].append(field)
        # One API message can be logged as several events, one per content block.
        if message.get("id") not in seen_message_ids:
            seen_message_ids.add(message.get("id"))
            out["output_tokens"] += (message.get("usage") or {}).get("output_tokens", 0)
        for block in blocks(message):
            if block["type"] == "text" and block.get("text", "").strip():
                out["texts"].append(block["text"])
            elif block["type"] == "tool_use" and block.get("name") == HANDBACK:
                out["final"] = (block.get("input") or {}).get("message")
            elif block["type"] == "tool_use":
                out["tool_calls"].append(dict(name=block.get("name"), input=block.get("input")))
    return out


def judge(trial, d, model, effort, stage_dir):
    """Return the reasons a transcript cannot count as the trial's response."""
    problems = []
    if d["models"] != [model]:
        problems.append(f"model {d['models']} instead of {model}")
    if d["efforts"] != [effort]:
        problems.append(f"effort {d['efforts']} instead of {effort}")
    if d["cwds"] != [stage_dir]:
        problems.append(f"started in {d['cwds']}, not the neutral staging folder")
    if not (d["final"] or "").strip():
        problems.append("no final message")
    calls = d["tool_calls"]
    if trial["format"] == "netlist":
        if calls:
            problems.append("tool use in a netlist trial: " + ", ".join(c["name"] for c in calls))
    else:
        reads = [c for c in calls if c["name"] == "Read"
                 and (c["input"] or {}).get("file_path") == trial["image_path"]]
        if len(calls) != 1 or len(reads) != 1:
            problems.append("tool calls differ from one Read of the staged PNG: "
                            + json.dumps(calls, ensure_ascii=False))
        if d["images_seen"] != [trial["image_sha256"]]:
            problems.append("the image delivered is not the committed PNG, byte for byte")
    return problems


def nearest(prompt, trials):
    """Name the trial an unmatched prompt was probably meant for, and where it departs."""
    best = max(trials, key=lambda t: difflib.SequenceMatcher(None, prompt, t["prompt"]).ratio())
    ratio = difflib.SequenceMatcher(None, prompt, best["prompt"]).ratio()
    at = next((i for i, (a, b) in enumerate(zip(prompt, best["prompt"])) if a != b),
              min(len(prompt), len(best["prompt"])))
    return best["trial"], ratio, at


def collect(log, manifest_path, transcript_dir, raw_path, model, effort, interface):
    trials = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    by_prompt = {t["prompt"]: t for t in trials}
    stage_dir = str(Path(next(t["image_path"] for t in trials if "image_path" in t)).parents[1])
    attempts, strays = {}, []
    for path in sorted(Path(transcript_dir).glob("*.output")):
        d = digest(path)
        trial = by_prompt.get(d["prompt"])
        if trial is None:
            strays.append(d)
            continue
        d["problems"] = judge(trial, d, model, effort, stage_dir)
        attempts.setdefault(trial["trial"], []).append(d)
    rows, fieldnames = read_log(log)
    records, done = [], 0
    for row, trial in zip(rows, trials):
        assert row["trial"] == trial["trial"]
        tries = sorted(attempts.get(trial["trial"], []), key=lambda d: d["started"] or "")
        # The first eligible transcript is the scored response. Later attempts are logged only.
        first = next((d for d in tries if not d["problems"]), None)
        for d in tries:
            # The transcript's file name is a session-internal ID, so it stays out of the record.
            records.append(dict(trial=trial["trial"], case=trial["case"],
                                question=trial["question"], format=trial["format"],
                                counted=d is first,
                                **{k: v for k, v in d.items() if k != "transcript"}))
        if first is None:
            continue
        done += 1
        row["model_version"] = first["models"][0]
        row["date_time_zone"] = first["ended"]
        row["interface_settings"] = (
            f"{interface}, harness {first['harness_version']}; fresh subagent per trial; model and "
            f"effort as recorded in the transcript: {first['models'][0]}, {first['efforts'][0]}; "
            "tools not disabled by the harness, instructed off and audited: "
            + ("one Read of the supplied PNG" if first["tool_calls"] else "no tool call")
            + "; sampling settings and response budget unknown")
        row["raw_response"] = first["final"]
        row["working_text"] = "\n\n".join(first["texts"])
        earlier = [d for d in tries if d is not first and (d["started"] or "") < first["started"]]
        if earlier and not row["evidence_or_retry_note"].startswith("retry"):
            row["evidence_or_retry_note"] = (
                f"retry: {len(earlier)} earlier ineligible attempt(s): "
                + " | ".join("; ".join(d["problems"]) for d in earlier)
                + (". " + row["evidence_or_retry_note"] if row["evidence_or_retry_note"] else ""))
    write_log(log, rows, fieldnames)
    if raw_path:
        with Path(raw_path).open("w", encoding="utf-8") as fp:
            for record in records:
                fp.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"{done} of {len(trials)} trials have an eligible response")
    for name, tries in sorted(attempts.items()):
        for d in tries:
            if d["problems"]:
                print(f"  {name} {d['transcript']}: " + "; ".join(d["problems"]))
    pending = [t["trial"] for t in trials if t["trial"] not in attempts]
    if pending:
        print("  no transcript yet:", " ".join(pending))
    for d in strays:
        trial, ratio, at = nearest(d["prompt"] or "", trials)
        hint = f"closest {trial}, differs at character {at}" if ratio > 0.9 else "not a trial prompt"
        print(f"  unmatched transcript {d['transcript']}: {hint}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    s = commands.add_parser("stage")
    s.add_argument("log", type=Path)
    s.add_argument("manifest", type=Path)
    s.add_argument("--stage-dir", type=Path, required=True)
    w = commands.add_parser("show")
    w.add_argument("manifest", type=Path)
    w.add_argument("trials", nargs="+")
    c = commands.add_parser("collect")
    c.add_argument("log", type=Path)
    c.add_argument("manifest", type=Path)
    c.add_argument("transcripts", type=Path)
    c.add_argument("--raw", type=Path)
    c.add_argument("--model", default="claude-fable-5-2")
    c.add_argument("--effort", default="max")
    c.add_argument("--interface", default="Claude Code cloud session, Agent tool, agent type claude")
    args = parser.parse_args()
    if args.command == "stage":
        stage(args.log, args.manifest, args.stage_dir)
    elif args.command == "show":
        show(args.manifest, set(args.trials))
    else:
        collect(args.log, args.manifest, args.transcripts, args.raw, args.model, args.effort,
                args.interface)
