# HydroGym benchmark: hydrology agents on the playbooks

Phase 1 of [#175](https://github.com/Rekin226/aquascope/issues/175): a
verifiable benchmark of hydrology agents on real sites. The
[playbooks](solve.md#the-playbooks) already say, for a problem at a place and
the data that exists there, which method chain is defensible, which gates it
must pass, and when to refuse. That makes them a scoring key: generate tasks
from them, play an agent on each task, and check the agent against what the
tree says. No synthetic truth, no judge model; the key is the same
method-selection scaffold the [plan-first Analyst](solve.md) runs on.

```bash
aquascope gym tasks --n 60 --seed 0 --out tasks.jsonl
aquascope gym bench --tasks tasks.jsonl --agent tree --out results/tree.jsonl
aquascope gym bench --tasks tasks.jsonl --agent team --provider anthropic --model claude-sonnet-5 \
    --limit 10 --unsolvable 3 --out results/team-sonnet-5.jsonl
aquascope gym bench --tasks tasks.jsonl --agent ask --provider anthropic --model claude-sonnet-5 \
    --limit 10 --unsolvable 3 --out results/ask-sonnet-5.jsonl
aquascope gym leaderboard results/*.jsonl --out leaderboard.md
```

The Phase 0 calibration environment (GR4J on one basin, the gymnasium API)
is documented in [gym.md](gym.md); `aquascope gym leaderboard` without
result files still plays those baselines.

## Tasks

A task (`aquascope.gym.tasks.Task`) is a playbook at a site with an intake:

| field | what |
| --- | --- |
| `site` | a catalog gauge (`source`, `station_id`, `name`, `lat`, `lon`, `kind`, `country`, `continent`, `years`) or a bare point |
| `intake` | the playbook's intake fields with defaults applied (`return_period`, `decision`, `purpose`, `attribute_cause`, ...) |
| `problem` | the problem in plain language, what an agent is given ("Design flow for a road crossing at this point, 100-year return period.") |
| `recon` | the reconnaissance snapshot, `assess_site(lat, lon)` at generation time: the gauges within 50 km with their catalog spans, the BasinATLAS catchment, the donor count, the sufficiency table |
| `expected` | the key: the `branch` the tree selects, its `gates` (`{step, check, path}`), its `tools`, or `declined: true` with the sentence and the kind (`declined`, `no_branch`, `refused`) |
| `split` | `train` or `test`: one site in four is held out by a hash of the site (the gauge id, or the rounded position) |
| `probe` | which decline rule the task probes, when it is one |

**Sites.** `suggest_sites(n, seed=...)` samples the published station catalog
(no agency call): gauges with 20 years and more of discharge, gauges with 5
to 20 years, wells with 10 years of levels, drawn round robin over kind,
continent and source so a suite spans the seven sources rather than the one
with the most rows; and bare points, offset 0.5 to 0.9 degrees from a gauge
in a sparse part of the catalog, so they sit on land near measured rivers but
usually beyond any gauge's reach. Reproducible for a seed.

**Keys.** `tasks_from_playbooks(sites, playbooks)` runs the reconnaissance
once per site and, for every playbook, the tree alone (`playbooks.plan`) on
that snapshot. Every task therefore carries the same catalog view the key was
computed on, and the `tree` agent replays it offline.

**Unsolvable tasks.** A task is unsolvable when the playbook declines: the
right answer is to refuse, and an agent that quotes a number is wrong. Two
kinds exist. Data-driven declines arise on their own (an ungauged point with
fewer than three donor gauges; a lumped method the registry refuses at this
catchment size). *Probes* are read off each playbook's own decline rules:
every rule whose conditions are all over `intake.*` fields becomes an intake
that triggers it (flood risk asked as an inundation map; a groundwater
decline asked with its cause). By default each site gets one probe, rotating
over the rules across the suite (`--probes all` gives every probe at every
site, `--probes 0` none), so about a quarter of a suite is unsolvable.

## Agents

| agent | what it is | needs a model |
| --- | --- | --- |
| `tree` | the playbook alone on the task's reconnaissance snapshot; the key's own baseline, 100 percent by construction; proves the harness and times it | no |
| `team` | `aquascope.ai_engine.team.solve`: the plan-first Analyst, given the problem text, the coordinates, the intake and the reconnaissance snapshot, review auto-approved, one replan; the Coordinator, Specialist and Narrator use the model when one is named, otherwise it runs keyless | optional |
| `ask` | `aquascope.ai_engine.analyst.ask`: the older tool loop, given only the problem text and the coordinates; no playbook, no gates, it calls the tools it likes and writes an answer | yes |

The `team` agent gets the snapshot so the key and the run see the same
catalog; the `ask` agent calls `assess_site` and the agencies live, as it
would for a user. Tool calls fetch records like any other run; a per-task
timeout (`--timeout`, 900 s) keeps one slow agency from stalling the suite.
The `ask` agent's conversation is capped (`--context-chars`, 40,000
characters, about 10k tokens; `--max-steps` 8) so a run's cost is bounded.

Your own agent is a function `(task, config) -> outcome` registered in
`aquascope.gym.bench._AGENT_FUNCS`; the outcome names the `playbook`,
`branch`, `gates` (`{step, check, passed}`), `tools`, `answer`, `declined`
and `usage` it produced.

## Scoring

Per task (`aquascope.gym.bench.Result`):

| score | meaning |
| --- | --- |
| `branch_match` | the agent's branch is the key's (and its playbook the task's); for `ask`, the branch is inferred as the playbook branch whose tools its calls cover best |
| `gates_respected` | the fraction of the key's `(step, check)` gates the run evaluated, pass or fail (the `tree` plans them, the `team` evaluates them, the `ask` loop has none, so it scores 0) |
| `tools_matched` | the fraction of the key's tools the agent called |
| `declined_correctly` | on an unsolvable task: did the agent decline. The team's decline is exact; the `ask` agent's is read off its answer by a list of refusal phrases, a heuristic: on an unsolvable task a refusal anywhere counts (the playbook's own decline for "why is the well falling" is to report the trend and refuse the cause), on a solvable one only a refusal in the opening of the answer, or anywhere when the loop called no tool of any branch, so a caveat after the numbers is not a decline |
| `answer_present` | prose came back and the agent did not decline |
| `prompt_tokens`, `completion_tokens`, `calls`, `cost_usd` | from the provider's usage fields (per role for the team, in `detail.cost_by_role`); the cost is a list-price estimate, see below |
| `seconds`, `error` | wall time; the exception or `TimeoutError` when the task did not finish |

`correct` is `declined_correctly` on an unsolvable task and `branch_match`
without a decline on a solvable one, so a keyless team that cannot place a
problem counts as wrong, and so does an agent that answers an out-of-scope
ask. The aggregate (`summarize`, `leaderboard`) per agent and model:
accuracy on the solvable tasks (and on the `test` split), the decline rate
on the unsolvable ones, the false-decline rate, mean gates and tools
respected, tokens and seconds per task, the total cost, errors and timeouts,
and correct-per-expected-branch.

**Cost.** `PRICES_USD_PER_MTOK` in `aquascope/gym/bench.py` is a small
table of list prices (USD per million input and output tokens; mid-2026:
Claude Sonnet 5 at 2 and 10, Opus 5 at 5 and 25, Haiku 4.5 at 1 and 5).
Prices change, cache and batch discounts are not modelled, and a model that
is not in the table gets no estimate rather than a guess.

## Running it

```bash
aquascope gym tasks --n 60 --seed 0 --out tasks.jsonl          # ~30 s per site: catalog, BasinATLAS, donors
aquascope gym tasks --n 12 --source usgs --probes all --out usgs.jsonl
aquascope gym bench --tasks tasks.jsonl --agent tree              # offline, seconds
aquascope gym bench --tasks tasks.jsonl --agent team              # keyless team, agency fetches, no model
aquascope gym bench --tasks tasks.jsonl --agent team --provider anthropic --model claude-sonnet-5 \
    --limit 8 --unsolvable 2 --out results/team.jsonl
aquascope gym bench --tasks tasks.jsonl --agent ask --provider anthropic --model claude-sonnet-5 \
    --task flood_risk-1a2b3c4d --out results/ask.jsonl
aquascope gym leaderboard results/*.jsonl --out leaderboard.md   # any agents, any models
```

`--limit N --unsolvable K` plays the first N tasks with at most K unsolvable
among them; `--task ID` picks tasks by id. Results are appended to `--out`
as they come, so an interrupted run keeps what it did and a leaderboard can
be built from several partial files (a `tasks.jsonl` in the same folder is skipped). In Python:

```python
from aquascope.gym import tasks_from_playbooks, suggest_sites, run_bench, leaderboard

tasks = tasks_from_playbooks(suggest_sites(12, seed=7), ["flood_risk", "ungauged_flow", "groundwater_decline"])
results = run_bench(tasks, "team", provider="anthropic", model="claude-sonnet-5", limit=8, unsolvable=2)
print(leaderboard(results))
```

## Leaderboard

The first run, 2026-09-02: 12 tasks from three sites (`aquascope gym tasks
--n 12 --seed 7`: the Loup River near Genoa, Nebraska, with 97 years of USGS
discharge; Bishops Stortford Castle on the Stort, 11 years of Environment
Agency discharge with a 62-year borehole nearby; a bare point in the
Colorado Front Range within reach of a century-long USGS gauge), three of
them probes. The tree played all 12 offline; the team and the ask loop
played the same six solvable tasks and two probes on Claude Sonnet 5
(`--limit 8 --unsolvable 2`). The tasks, the result rows (with the answers)
and this table are under `aquascope/gym/results/2026-09-02/`.

| agent | model | tasks (solvable + unsolvable) | accuracy | accuracy on test | declined unsolvable | false declines | gates | tools | tokens/task | s/task | cost USD | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ask | claude-sonnet-5 | 8 (6 + 2) | 67 % | - (0) | 100 % | 0 % | 0 % | 31 % | 62,314 | 58.4 | 1.113 | 0 |
| team | claude-sonnet-5 | 8 (6 + 2) | 100 % | - (0) | 100 % | 0 % | 100 % | 100 % | 6,437 | 25.9 | 0.154 | 0 |
| tree | none | 12 (9 + 3) | 100 % | - (0) | 100 % | 0 % | 100 % | 100 % | 0 | 0.0 | 0.000 | 0 |

Correct on solvable tasks by expected branch (correct / n):

| agent | model | at_gauge | at_site | regional | short_record | well |
|---|---|---|---|---|---|---|
| ask | claude-sonnet-5 | 2 / 2 | 1 / 1 | 0 / 1 | 0 / 1 | 1 / 1 |
| team | claude-sonnet-5 | 2 / 2 | 1 / 1 | 1 / 1 | 1 / 1 | 1 / 1 |
| tree | none | 3 / 3 | 2 / 2 | 2 / 2 | 1 / 1 | 1 / 1 |

**What the numbers say, and what they do not.** Eight tasks on three sites
is a smoke test of the harness, not a result about the agents: one more miss
moves the ask row by 17 points, no site fell in the test split, and the
sites came from one seed. Within that: the plan-first team picked the key's
branch on every solvable task, evaluated every gate the key expected,
declined both probes before any model call (a probe is refused by the tree,
so it costs nothing), and spent about 6,400 tokens and 26 seconds per task,
most of it the Narrator's prose. The ask loop got four of the six solvable
tasks and declined both probes in its own words, at ten times the tokens
(62,000 per task; 1.11 USD for the eight tasks against 0.15) and twice the
wall time. Its two misses are instructive rather than damning. At the Loup
River point it read the sufficiency table, saw that every groundwater method
was not defensible without a well, and stopped, where the playbook goes
regional and says the ERA5 water balance is all that can be said; both are
defensible, and the key is the playbook's. On the short-record flood task it
ran out of its eight steps after eleven tool calls (three flood-frequency
fits, a regionalisation, a Python snippet) without writing an answer, where
the plan did the work in four steps. The gates column is the difference in
kind: the team's numbers passed the gates the playbook set before they were
quoted; the ask loop's were checked only after the fact by the answer checks.
The scores lean on the key being right: a branch is "correct" because the
tree chose it, and the ask loop's decline is read off wording. A run that
means something needs the 60-task suite across the sources and continents,
several seeds, a held-out split with sites in it, a keyless team row, and
small models next to the frontier one; the harness is built for that and
the cost table says what it will cost.
