# Claude Fable 5.2 at effort max · all 12 cases

**Result.** All 96 responses were scored correct: every question on every case, from the schematic image and from the netlist. Q2–Q4 combined is 72 of 72. That is above the 30–80% planning target in [the protocol](../../../docs/protocol.md), so for this model in this setting the 12 cases neither separate the two input formats nor expose an error to analyze.

**This is not the API condition in the team plan.** No API key was available, so each request ran as a fresh Claude Code subagent. [How this differs from an API run](#how-this-differs-from-an-api-run) lists what that changes. The scores are also a first pass by an AI scorer and need a teammate's confirmation; see [Scoring](#scoring).

## Results

| Question | Image | Netlist | Both right | Image only | Netlist only | Both wrong |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Q1 · Connectivity | 12 / 12 | 12 / 12 | 12 | 0 | 0 | 0 |
| Q2 · Function | 12 / 12 | 12 / 12 | 12 | 0 | 0 | 0 |
| Q3 · DC output | 12 / 12 | 12 / 12 | 12 | 0 | 0 | 0 |
| Q4 · Resistor change | 12 / 12 | 12 / 12 | 12 | 0 | 0 | 0 |

`python tools/pilot.py summary team/runs/claude-fable-5-2_max_cc-subagent/run-log.csv` prints the same counts.

- **Numeric answers.** Across the 48 Q3 and Q4 answers the largest error is 31% of the allowed tolerance. Every difference from the key is rounding to three significant digits.
- **Written solutions.** All 96 were judged valid. The check is described under [Scoring](#scoring).
- **Reading the image.** 47 of the 48 image responses restated connections they had read from the schematic, 241 statements in total, and every one matches the netlist. 19 responses restated all five passive parts.
- **Cost of the image.** Image requests took about twice the output tokens and time of netlist requests. The run used 193,206 output tokens in total, as reported by the harness.

| Median per request | Image tokens | Netlist tokens | Image seconds | Netlist seconds |
| --- | ---: | ---: | ---: | ---: |
| Q1 | 1,263 | 627 | 13.5 | 6.6 |
| Q2 | 3,900 | 2,998 | 35.5 | 27.2 |
| Q3 | 2,138 | 1,194 | 19.1 | 11.3 |
| Q4 | 2,411 | 1,244 | 22.5 | 11.4 |

## Conditions

| Item | Value | How it was checked |
| --- | --- | --- |
| Date | 2026-11-16, 06:51 to 07:02 UTC | Transcript timestamps |
| Interface | Claude Code cloud session, harness 2.1.325, Agent tool, agent type `claude` | Transcripts |
| Model | `claude-fable-5-2` | Requested and served model in every transcript |
| Reasoning setting | Effort `max` | Effort recorded in every transcript |
| Session | One fresh subagent per case, question and format. No shared history or memory | Each transcript starts with the request text |
| Starting folder | A neutral folder outside the repository | Transcripts. A separate probe subagent found no repository name, project instructions or memory in its starting context |
| Tools | Present, because the harness cannot switch them off. The request tells the model not to use them | Audit: the 48 netlist requests made no tool call. The 48 image requests made exactly one, the read of the supplied PNG |
| Image | The committed PNG, 1996 × 1120, byte for byte | SHA-256 of the image inside each transcript |
| System prompt | Claude Code's subagent prompt and tool definitions | Cannot be removed |
| Sampling and response budget | Not shown by the harness | Unknown |
| Attempts | 96 first attempts, all eligible. No retry and no technical failure | Audit |

## Request text

Each request is one line of harness text followed by the benchmark's own text, unchanged.

Netlist request:

```text
This is a single self-contained question. Answer it from your own reasoning only: do not use any tools (no file access, no code execution, no web or simulators). Your final message is recorded verbatim as your answer.

<the full contents of inputs/Pnn/circuit.cir>

<the common instruction>

<one question>
```

Image request:

```text
This is a single self-contained question. Answer it from your own reasoning only. The circuit is supplied as one image file: open <folder>/schematic.png once with the Read tool, and do not use any other tools (no other file access, no code execution, no web or simulators). Your final message is recorded verbatim as your answer.

<the common instruction>

<one question>
```

The question is the sentence from the case's `questions.md` without its `Q1.` label. [manifest.json](manifest.json) holds the exact text of all 96 requests. Each image was copied to a folder with an opaque name, so an image request carries no case ID. A netlist request shows the ID in the netlist's own title line.

## How this differs from an API run

- **A system prompt and tools are present.** A stateless API request would carry neither. Here the model sees Claude Code's subagent prompt and about 50 tool definitions, and is asked in words to leave the tools alone.
- **The image arrives through one tool call.** The model opens the PNG itself and receives it as a tool result. An API request would attach it to the user message.
- **The response is the subagent's hand-back message.** That message is `raw_response`. Text the model wrote before it, usually one line stating its approach, is in `working_text`.
- **The answer key was on the same machine.** The audit shows no file access other than the one image read, so no request could have seen it.
- **Sampling settings and the response budget are unknown.**
- **The harness line changes the wording.** Many responses add a remark such as "no tools were used".

To match the plan, rerun through the API with adaptive thinking and `output_config.effort = "max"` when a key is available. The requests in `manifest.json` can be reused without the harness line.

## Scoring

**Who scored.** Claude, in the Claude Code session that ran the requests. That is an AI scorer from the same model family as the model under test, and it had the key in view. Treat every score here as a first pass.

- **Q1, Q3 and Q4.** `tools/score_run.py` applies the protocol's rules to the final message. Q1 compares the labels in the first paragraph with the key. Q3 and Q4 take the first value in volts or milliseconds and allow the larger of 0.01 V or 1%, and of 0.005 ms or 1%.
- **Q2.** The script checks the settling direction. The scorer read each explanation, and the decisions are in [judgments.json](judgments.json). All 24 were accepted: each ties the direction to where C1 sits and gives the correct voltages just after the edge and after settling. None looked ambiguous. The protocol asks for a second teammate on ambiguous Q2 responses. Because the first reader was an AI, a teammate should read all 24.
- **Validity of the written solution.** The `solution_validity` column records it. The script solves each case exactly and compares every number with a unit in a response against the values a correct solution could state: node voltages, branch currents, resistor combinations and time constants, for the original and the changed circuit. It also compares each stated connection with the netlist. It checked 2,256 numbers and 357 connections. It reported one phrase, in T088, which reading showed to be a correct statement about two resistors in series. This check catches wrong numbers and wrong wiring. It does not judge the reasoning in prose.
- **Key.** The script's exact solution agrees with `team/keys.json` on all 96 key values to within 2 × 10⁻¹⁵.

## Notes for the team

- **Difficulty.** At 72 of 72 on Q2–Q4, the protocol's rule for this model is to keep these results and raise the shared reasoning burden in the next development batch. The other providers' results decide whether that applies to the whole set.
- **Q2 length.** Q2 asks for one sentence. All 24 responses give a labeled one-sentence explanation and then add supporting values. The rubric does not say whether that matters.
- **P09.** OUT does not jump at the edge in this case. Both P09 responses said so and answered "upward", which matches the key.
- **Rehearsal.** Before the 96 requests, the warm-up circuit C001 was asked Q3 in both formats to test the procedure. Both answers were 3 V, which is correct. They are not part of this log.

## Not done

- No API run, for lack of a key.
- The smaller-junction images under `ablations/` were not run.
- Each request was sent once, so there is no estimate of run-to-run variation.

## Files

| File | Contents |
| --- | --- |
| [run-log.csv](run-log.csv) | The 96 rows in `tools/pilot.py` format, with full responses, scores and scoring evidence. Extra columns: `trial`, `working_text`, `solution_validity`, `validity_note` |
| [manifest.json](manifest.json) | Request ID, case, question, format, exact request text and image hash for each of the 96 requests |
| [raw/trials.jsonl](raw/trials.jsonl) | One record per request taken from its transcript: request text, model, effort, tool calls, image hash, all text written, timestamps and output tokens |
| [judgments.json](judgments.json) | The reader's decisions that the scoring script cannot make |

## Repeat the procedure

```sh
python tools/pilot.py new LOG --all --models SLOT
python tools/cc_trials.py stage LOG MANIFEST --stage-dir NEUTRAL_FOLDER
python tools/cc_trials.py show MANIFEST T001 T002
python tools/cc_trials.py collect LOG MANIFEST TRANSCRIPT_FOLDER --raw RAW.jsonl
python tools/score_run.py LOG --judgments JUDGMENTS.json
```

`show` prints the request text to give each fresh subagent. `collect` reads the subagent transcripts, checks each one against the manifest and fills the log only from transcripts that pass.
