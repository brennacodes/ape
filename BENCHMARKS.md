# APE Benchmarks

This document describes how APE (Applied Primitive Expression) is being
benchmarked and how it has performed so far against alternative ways of
delivering the same workflow instructions to an LLM agent.

The numbers below are a snapshot of the runs collected to date. They are
early results from a single application, with deliberately uneven sampling
across some conditions. Read the [Limitations](#limitations) section before
drawing strong conclusions.

## What the benchmark measures

The benchmark does not test whether an agent can write code. It tests whether
a **structured workflow** changes how disciplined an agent is while doing so:
does it write a spec first, run the linter, run the tests, build, commit with
the right discipline, and recover correctly when a step fails.

The core question is: **does encoding a workflow as an APE document produce
more reliable, more complete execution than the same workflow written as
plain markdown, ad hoc XML, or plain text, or than no workflow at all?**

## Methodology

### The application under test

All runs to date target a single fixture app, **bivvy**, an interactive
development-environment setup CLI written in Rust. The app ships with a fixed
set of planted work items so every run is reproducible and gradable.

### Scenarios

Nine scenarios are drawn from three categories, each describing a realistic
piece of work a developer might hand to an agent:

| Category | Scenarios |
|----------|-----------|
| Bugs | `silent_yaml_failure`, `interactive_prompt_panic`, `race_condition_in_cache` |
| Architectural issues | `hardcoded_home_dir`, `tight_coupling_ui_runner`, `inconsistent_error_handling` |
| New features | `toml_config_support`, `dry_run_mode`, `github_actions_formatter` |

Each scenario is delivered to the agent as a natural-language request phrased
the way a user would actually phrase it (for example, "the app falls back to
default config without any warning when my .bivvy.yml is invalid").

### Conditions: format and source

Every scenario is run under a matrix of conditions. The two dimensions are the
**format** the workflow is written in and the **source** through which it
reaches the agent.

**Format** (the workflow markup):

- `ape` - the workflow written as an APE document.
- `markdown` - the same workflow as conventional markdown instructions.
- `adhoc-xml` - the same workflow as loosely structured XML.
- `plain-text` - the same workflow as unstructured prose.
- `no-workflow` - a baseline with no workflow instructions at all; the agent
  is given only the task.

**Source** (how the workflow is delivered):

- `claude-md` - the workflow is placed in a `CLAUDE.md` file the agent picks up
  as project context.
- `prompt` - the workflow is handed to the agent inline in the prompt.

The `no-workflow` baseline has no source dimension because there is no workflow
to deliver.

### Execution environment

- Each case runs in an isolated workspace with a scrubbed environment, so runs
  do not contaminate each other.
- All runs to date use the same model, `claude-opus-4-6`, so format and source
  are the only variables.
- Runs are graded automatically against a per-app rubric of checks.

### Evaluation: phases and checks

Grading is structural. The rubric groups checks into ordered execution
**phases** that mirror the workflow the agent is supposed to follow:

`specification -> implementation -> documentation -> linting -> testing ->
build -> commit -> post-commit`, plus a floating `failure_recovery` phase and
a `workflow` phase.

Phase detection is trace-based: it inspects the tool calls the agent actually
made (file edits, `cargo test`, `cargo build`, git commits, and so on) and
scores whether the required actions for a phase actually clustered together in
the right order, rather than trusting the agent's narration. Gate routing
(what the workflow says to do when a step fails) is checked against the
workflow's declared `on-fail` and `on-pass` targets.

A run's **pass rate** is the fraction of rubric checks it passed. Runs that
exceed the per-case time budget are recorded as timeouts and have no check
data.

### Reproducing the numbers

The summary tool reads every `summary.json` under `benchmark/output/`:

```bash
python3 benchmark/summary.py            # per-scenario and aggregate tables
python3 benchmark/summary.py --phase    # per-phase pass rates
python3 benchmark/summary.py --checks   # per-check pass rates
python3 benchmark/summary.py --timeouts # which runs timed out
```

## Results

Snapshot of runs collected between 2026-03-15 and 2026-05-16: **331 runs**
across the nine scenarios, all on `claude-opus-4-6`, totalling roughly \$1,150
in API cost.

Two pass-rate metrics are reported because they answer different questions:

- **Pass rate (completed runs)** - of the runs that finished within the time
  budget, what fraction of checks passed. This measures quality of work when
  the agent gets to finish.
- **Pass rate (all runs)** - the same average, but computed over every run
  including the ones that timed out. A timed-out run contributes only partial
  credit for the checks it managed to pass before the budget ran out (about
  0.21 on average), so this metric pulls down conditions that fail to finish.

### Fully-sampled conditions

Five conditions have a full run count (roughly 62-64 runs each across all nine
scenarios). These are the only conditions with enough data to discuss. Note
the **Source** column: four of the five were delivered via `CLAUDE.md`, but
`plain-text` was delivered via `prompt`. That difference is a confound, covered
right after the table.

| Condition | Source | Runs | Completed | Timeout rate | Pass rate (completed) | Pass rate (all runs) | Avg turns | Avg time |
|-----------|--------|------|-----------|--------------|-----------------------|-------------------------|-----------|----------|
| `plain-text` | prompt | 62 | 36 | 42% | 80% | 56% | 86 | 27m |
| `ape` | claude-md | 64 | 45 | 30% | 79% | 62% | 72 | 23m |
| `markdown` | claude-md | 63 | 57 | 10% | 64% | 60% | 48 | 18m |
| `adhoc-xml` | claude-md | 64 | 64 | 0% | 50% | 50% | 41 | 12m |
| `no-workflow` | (none) | 64 | 63 | 2% | 48% | 47% | 49 | 12m |

Reading the table:

- On **completed runs, the top of the table is a near-tie**: `plain-text`
  (prompt) at 80% and `ape` (claude-md) at 79%, both well above markdown (64%),
  ad hoc XML (50%), and no workflow (48%). Having a real workflow of any richer
  form clearly helps over the baseline.
- The completed-run number **flatters high-timeout conditions**, because it only
  averages the runs that finished. `plain-text` (prompt) times out on 42% of
  runs, the highest of any condition, so its 80% is drawn from the surviving
  58%. On the **all-runs metric** (timed-out runs included at partial credit),
  APE leads at 62%, with markdown at 60% and plain-text at 56%. Which metric is
  fairer depends on whether a caller counts a run that ran out of time as a
  near-failure.
- APE and plain-text both trade speed for thoroughness (72-86 turns, 23-27
  minutes, versus ~12 minutes for the baseline and ad hoc XML), which is why
  both time out more under the per-case budget.
- Ad hoc XML performs no better than no workflow at all. Structure alone is not
  the win; the semantics a format attaches to that structure are.

### Format and source are confounded

The comparison above cannot cleanly isolate the **format** because the
fully-sampled conditions do not hold the **delivery source** constant. APE,
markdown, and ad hoc XML were run at scale via `CLAUDE.md`; plain-text was run
at scale via `prompt`.

The two sources are not interchangeable across every format. `CLAUDE.md` is a
markdown file, so delivering the `plain-text` format through it is not
meaningful by definition: unstructured prose placed in a markdown file is just
markdown delivery, and the plain-text-versus-markdown distinction collapses.
That is why plain-text was sampled via `prompt`. The `prompt` source, by
contrast, applies to every format, so it is the only delivery path on which all
formats can be compared head to head.

So "APE beats plain-text" is not something this data supports, because moving
from APE to plain-text also moves the delivery from `CLAUDE.md` to `prompt` at
the same time. Any difference could be the format, the source, or both. The
one comparison that does hold source constant is APE, markdown, ad hoc XML, and
the baseline, all via `CLAUDE.md`; within that same-source group, APE is ahead
on both metrics. The complementary comparison, all formats via `prompt`, has
not yet been measured at scale (only plain-text has a full `prompt` sample),
and it is what would settle the format ranking.

### Where APE's advantage comes from

This comparison holds the delivery source constant: both `ape` and the
`no-workflow` baseline below are `CLAUDE.md`-delivered, so the difference is
attributable to the workflow. The per-phase breakdown (`--phase`) shows the
gains are concentrated in the discipline phases that APE encodes as explicit
gates and steps:

| Phase | APE (claude-md) | No workflow |
|-------|-----------------|-------------|
| Specification | 80% | 19% |
| Testing | 76% | 41% |
| Build | 81% | 31% |
| Commit | 87% | 17% |
| Linting | 95% | 0% |

Without a workflow, the agent tends to jump straight to implementation, skip
writing a spec, skip the linter, and commit without discipline. APE's
structural gates are what pull those phases back into the run. Implementation
and documentation, which agents do well unprompted, are near-ceiling for every
condition, so there is little room for a workflow to help there.

### Under-sampled conditions

A handful of runs exist in the other cells of the format-by-source matrix. Each
has too few runs to read as a result; they are listed only for transparency:

| Condition | Runs | Completed | Pass rate (completed) |
|-----------|------|-----------|-----------------------|
| `ape` (prompt) | 4 | 3 | 75% |
| `plain-text` (claude-md) | 4 | 3 | 93% |
| `adhoc-xml` (prompt) | 3 | 2 | 72% |
| `markdown` (prompt) | 3 | 3 | 76% |

With three or four runs apiece, these numbers are noise. The `ape` (prompt),
`adhoc-xml` (prompt), and `markdown` (prompt) cells are the ones worth filling
in: a full `prompt` sample for the structured formats would let them be
compared to plain-text with the delivery source held constant. The
`plain-text` (claude-md) cell is not a meaningful target, for the reason given
above (a markdown file cannot deliver the plain-text format), and those four
runs should be disregarded.

## Conclusions

From the runs collected so far on the bivvy app:

1. **A richer workflow helps.** The two strongest conditions, APE via
   `CLAUDE.md` and plain-text via `prompt`, both land near 80% on completed
   runs, well above markdown (64%), ad hoc XML (50%), and no workflow (48%).
2. **Among same-source conditions, APE leads.** Holding delivery constant at
   `CLAUDE.md`, APE tops both metrics: 79% completed and 62% across all runs,
   ahead of markdown, ad hoc XML, and the baseline.
3. **Format and source are not yet separable.** APE was sampled at scale via
   `CLAUDE.md` and plain-text via `prompt`, so the APE-versus-plain-text
   comparison changes two things at once. Plain-text cannot be delivered via
   `CLAUDE.md` at all (a markdown file cannot carry a non-markdown format), so
   the only way to compare all formats on equal footing is an all-via-`prompt`
   sweep, which has not been run. This benchmark cannot yet say the format alone
   is responsible for APE's showing.
4. **Structure without semantics does nothing.** Ad hoc XML matches the
   no-workflow baseline. The gain comes from enforced gates and steps, not from
   markup for its own sake.
5. **The wins land where discipline is hard.** Against the same-source
   baseline, APE's largest margins are in specification, linting, testing,
   build, and commit, the phases agents skip when left to their own devices.
6. **Thoroughness has a cost.** APE and plain-text both run longer and time out
   more often (30% and 42% under the current budget). The completed-run metric
   rewards them for the runs that finished; the all-runs metric docks them for
   the ones that did not. Which is fairer depends on how a caller values
   completeness against wall-clock time.

## Limitations

- **One application.** Every result comes from a single Rust CLI fixture
  (bivvy). Nothing here has been shown to generalize to other codebases,
  languages, or task types.
- **One model, and a moving target.** Every run here uses `claude-opus-4-6`.
  Version tracking was only added recently, so there is not yet a meaningful
  body of results from any other model. Format sensitivity may differ on other
  models, and as models and the harnesses that run them change, these results
  are likely to shift. They may also hold; that has not been tested. Treat this
  as a point-in-time reading, not a fixed property of the formats.
- **Format and source are confounded.** Of the five fully-sampled conditions,
  four are `CLAUDE.md`-delivered and one (`plain-text`) is `prompt`-delivered.
  Plain-text cannot be delivered via `CLAUDE.md` (a markdown file cannot carry a
  non-markdown format), and the structured formats have only three or four
  `prompt` runs each, so the benchmark cannot yet separate the effect of the
  workflow format from the effect of the delivery source. The fix is an
  all-via-`prompt` sweep, which has not been run.
- **Timeout confound.** APE's high timeout rate under a fixed time budget
  depresses its all-runs score. A longer budget would likely raise APE's
  completion rate and widen its lead; this has not yet been tested.
- **Automated grading.** Pass rates come from a trace-based rubric, not human
  review. The rubric measures workflow adherence, not the ultimate correctness
  or quality of the code produced.

## Next steps

- Run every format via the `prompt` source at scale. It is the only delivery
  path that applies to all formats, so an all-via-`prompt` sweep would compare
  APE, markdown, ad hoc XML, and plain-text with the source held constant and
  separate the format effect from the delivery effect. This has not been
  measured yet.
- Add a second and third fixture app to test whether the APE advantage
  generalizes beyond bivvy.
- Re-run APE under a larger time budget to separate the thoroughness signal
  from the timeout penalty.
