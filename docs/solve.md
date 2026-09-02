# Solve: a problem at a place, planned first, checked at every step

`aquascope ask` answers a question. `aquascope solve` takes a *problem* at a
*location* ("design flow for a road crossing, 100-year return period", "what
flow can this ungauged stream give an irrigation scheme", "is the water table
under this well falling") and goes from there to a verified answer through a
plan you see before anything runs. The design contract is in
[solve-design.md](solve-design.md); this page is the user's guide.

## The flow

```
intake ──► recon ──► plan ──► review ──► execute ──► report
(the text,  (assess_  (a playbook  (you see   (aquascope   (answer + what it
 --intake)   site)     branch)      the plan)  run, a gate  does not establish
                                               per step,    + study.yaml)
                                               replan once)
```

1. **Intake.** The problem text and the coordinates. A playbook's intake
   fields (return period, what is being decided, ...) are read off the text
   where they are stated, or given with `--intake key=value`.
2. **Recon.** `assess_site(lat, lon)` says which records exist within reach,
   for how long, at what resolution, what the catchment looks like and how
   many donor gauges there are, and grades every method in the registry
   (`aquascope.methods`) as defensible, marginal or not defensible here.
3. **Plan.** The playbook's decision tree picks a branch for the data that
   exists and fills a **study** (version 2): steps with arguments, a
   rationale each, the gates each must pass, and a fallback for when a gate
   fails. No model is needed for this.
4. **Review.** The CLI prints the plan as a numbered checklist and asks
   `y/N`; the API gives it to a callback that may edit it or decline it.
5. **Execute.** `run_study` runs the steps in order and evaluates the gates
   after each one. A failed gate runs the step's fallback once, or stops the
   study with the reason. When a model is present, a Specialist may propose
   one more fallback step; the replan is bounded (`max_replans=1`).
6. **Report.** The answer, the plan and its rationale, every step with its
   gate outcomes, "what this answer does not establish", the playbook's
   caveats verbatim, Data and Methods assembled from the tool results, and
   the executed study, which `aquascope run study.yaml` reproduces with no
   model at all.

## A CLI example

```bash
aquascope playbooks                       # the playbooks and their branches
aquascope playbooks show flood_risk       # intake, branches, gates, declines, caveats

aquascope solve "Design flow for a road crossing, 100-year return period" \
    --lat 51.415 --lon -0.308 --playbook flood_risk \
    --out kingston.md --study kingston.yaml
```

The plan printed for the Thames at Kingston (39 years of daily discharge,
so the `at_site` branch):

```
Plan: playbook flood_risk, branch at_site, 3 step(s)
  39.5 years of daily discharge at Kingston (uk_ea 3400TH, 0.4 km from the point): an at-site
  frequency fit is defensible. ...
  1. describe_catchment(lat=51.415, lon=-0.308)
  2. analyze_station(source='uk_ea', station_id='3400TH')
     gate min_years 20 on years
     gate not_empty on trend
  3. flood_frequency(source='uk_ea', station_id='3400TH', bootstrap_ci=True)
     gate max_return_period_factor 3 on years
     gate ci_finite on ffa.fits.gev_bootstrap.ci
     gate spread_within 0.25 on ffa.fits.gev_lmoments.q, ffa.fits.lp3.q
     fallback: similar_basins
Run this plan? [y/N]
```

`--yes` skips the question (scripts, notebooks). `--intake return_period=200`
sets an intake field. `--quiet` hides the timeline. The report lands in
`--out`, the executed study in `--study`; `aquascope run kingston.yaml`
re-runs it and prints the same gate outcomes.

Without `--lat`/`--lon`, `aquascope solve` is the older challenge agent over a
data file, unchanged.

## The playbooks

A playbook is a YAML file under `aquascope/playbooks/`: data, not code. Each
has intake fields, branches over the reconnaissance (first match wins),
study-v2 steps with gates and fallbacks, the sentences it prints when it
declines, caveats printed verbatim in every report, and citations. The
thresholds (20 years, T at most three times the record, three donors, the
10,000 km2 ceiling for a lumped model) are the registry's, not the file's:
a step that names its `method` is checked against `aquascope.methods` when
the plan is filled, so a method the registry calls not defensible at this
site is refused before anything runs.

| Playbook | Branches | Declines |
| --- | --- | --- |
| `flood_risk` | `at_site` (20+ years of discharge: Mann-Kendall pre-test, GEV by L-moments and Log-Pearson III with a bootstrap band, the spread quoted; a stationary estimate with a climate caveat, never a nonstationary fit), `short_record` (8 to 20 years: marginal at-site numbers next to donors and regionalised signatures), `regional` (no gauge: donors, signatures, a GloFAS cross-check) | a return period beyond about three times the record with fewer than three donors; inundation extent (out of scope) |
| `ungauged_flow` | `at_gauge` (a gauge with 5+ years nearby: its flow-duration curve beside the regionalised signatures for the point), `regional` (donors, signatures with the band and the leave-one-out skill, GloFAS) | fewer than three donor gauges |
| `groundwater_decline` | `well` (10+ years of levels: Sen's slope with Mann-Kendall, the Standardised Groundwater Index, water-table-fluctuation recharge with a stated specific yield), `regional` (no well: the ERA5 water balance for the cell, labelled regional) | attributing the cause without pumping data |

### Writing a playbook

Copy one of the three and keep to the block-style YAML subset the browser
worker reads (nested mappings and lists, quoted or bare scalars, `>-` block
text; no anchors). The schema:

```yaml
id: my_problem                 # the file name without .yaml
title: ...
problem: my_problem            # the problem kind the registry knows
variable: discharge            # picks the station for {{ station.* }}
intake:
  - {name: return_period, label: Return period (years), type: int, default: 100}
branches:                      # first match wins; conditions over the recon dict
  - id: at_site
    when: [{path: context.years_by_variable.discharge, op: ">=", value: 20}]
    station_variable: discharge
    rationale: >-              # placeholders: {{ intake.x }}, {{ station.source }},
      ...                      #   {{ station.station_id }}, {{ site.lat }}, {{ site.lon }}, {{ derived.x }}
    steps:
      - id: s1
        tool: analyze_station  # any Analyst tool, workbench analysis, or assess_site
        method: at_site_flood_frequency   # optional: checked against aquascope.methods at plan time
        optional: false        # true: dropped with a note when the registry says not defensible
        arguments: {source: "{{ station.source }}", station_id: "{{ station.station_id }}"}
        expects:               # gates, see aquascope.gates.CHECKS
          - {check: min_years, value: 20, path: years}
        fallback: {step: {tool: similar_basins, arguments: {...}}}   # or {branch: regional} or stop
        depends_on: []
declines:
  - {when: [...], say: "the sentence printed verbatim"}
caveats: ["always", {say: "only when", when: [...]}]
citations: ["..."]
```

Conditions take `==`, `!=`, `>=`, `<=`, `>`, `<`, `in`, `exists`, and are
evaluated over the recon dict extended with `intake`, `station`, `site` and
`derived` (`discharge_years`, `groundwater_years`, `donors`, `dams`,
`return_period_cap`, `return_period_beyond_cap`, `area_km2`). A workbench
step takes `from_step: s2` to run on the series a previous `get_timeseries`
step fetched. `aquascope.playbooks.validate("my_problem")` lists every
authoring mistake; `tests/test_playbooks.py` shows the three fixtures
(gauged long, gauged short, ungauged) a new playbook should be exercised on.

## What keyless gives, what a key adds

With no key, no role calls a model: the Scout runs the reconnaissance, the
tree fills the plan, the Reviewer evaluates the gates and the deterministic
checks, and a template Narrator writes the answer from the results. That is
a complete, reproducible run, and it is what the MCP tools and the browser
do by default.

With `--provider` (or `--model`, `--api-key`, `--base-url`), three roles use
the model, each as a stateless subcall that sees only its own inputs, never a
transcript: the Coordinator writes a one-paragraph rationale for the plan
(and settles a problem the keyword rules cannot place), the Specialist
proposes one fallback step after a failed gate, and the Narrator writes the
prose under the analyst's rules (units, records named, no invented numbers).
The report's footer counts the calls and tokens per role. `solve` is keyless
unless you ask for a model, even when a key is in the environment.

## The honesty rules

- Every number in the report comes from a tool result; the gates checked it
  before it was quoted, and the deterministic checks of `aquascope ask`
  (numbers present in the results, units named, records named, intervals
  with return levels, significance wording matching the test) run on the
  prose as well. What fails is listed under "what this answer does not
  establish", never hidden.
- A playbook that declines prints its own sentence and stops. So does a plan
  whose method the registry calls not defensible at this site (the GR4J on a
  100,000 km2 catchment of #273 is refused before it runs).
- The caveats are the playbook's, verbatim, in every report: design-flood
  guidance under climate change is immature (Wasko et al. 2024), a transferred
  number without its band and skill is not an estimate, a trend says whether
  a level is changing and never why.
- The executed study is the answer's receipt and its plan at once:
  `aquascope run study.yaml` reproduces it with no model in the loop.

## The other faces

Over MCP (`aquascope mcp`): `list_playbooks()`, `describe_playbook(id)`,
`solve_plan(problem, lat, lon, playbook, intake)` returns the study to
review, `solve_run(study)` executes it. The Analyst (`aquascope ask`) has
the same four tools, so a question that turns out to be a problem at a place
can be handed to Solve. In Python:

```python
from aquascope.ai_engine.team import solve

result = solve("Design flow for a road crossing, 100-year return period",
               lat=51.415, lon=-0.308, playbook="flood_risk",
               review=lambda study: study)         # or edit it, or return None
print(result.answer)
print(result.to_markdown())
open("study.yaml", "w").write(result.study_yaml)   # aquascope run study.yaml
```
