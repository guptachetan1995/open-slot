# Qloo API notes

**Status: key not yet available.** Written on 19 October 2026, before any Qloo hackathon key had
arrived. Nothing in this repository has been run against Qloo, so every response shape below is
what Qloo's published examples show, and every constant in `open_slot/pins.py` is marked
unverified. This file holds shapes, thresholds and constants only, never a name, an id or a value
from a response: Qloo's developer guide says not to store Qloo response data in a public
repository.

**Live calibration has not been done.** It is a short run with the key, at most 40 calls, that
checks each shape in the tables below against what Qloo really sends. No key had arrived by 20
October, so nothing in this repository has made a call to Qloo, and what follows about the page
and the server was checked against a loopback stub of Qloo that answers from the synthetic
fixtures.

## What the code assumes until the probe has run

Each reader accepts exactly one shape and raises an error naming the JSON path (never a value)
when it meets another, so a wrong guess here stops at the first live call and is fixed from the
probe's printed paths.

| Call | Assumed answer | Basis |
|---|---|---|
| `GET /search` | `results[]` with `entity_id`, `name`, `type`; optional `popularity` (0 to 1) and `disambiguation` | Search Entities page; the response shape is not shown there |
| `GET /v2/tags` | `results.tags[]` with `tag_id`, `name`, `types[]` | the tag shape in the taste-analysis example; the container path is a guess |
| `GET /v2/insights` | `results.entities[]` with `entity_id`, `name`, `type`; optional `popularity`, `tags[].name`, `query.measurements.audience_growth` | the documented example response, a movie query |
| `feature.explainability=true` | `query.explainability["signal.interests.entities"][]` with `entity_id` and `score` (0 to 1) | the parameter is documented as scores between 0 and 1; the layout is a guess |
| `GET /v2/trending` | `results.trends[]` with `date`, `population_percentile` (0 to 100), optional `population_rank` | the row fields are documented; the container path is a guess |
| An empty answer | `{}`, or `results` empty, or an empty list under it | the developer guide's "empty 200" |

Error statuses are read as: 401 `qloo_auth`, 403 `qloo_unsupported_type`, 404 on `/search`
`qloo_no_results` (elsewhere `qloo_bad_request`), 429 `qloo_rate_limited`, 5xx `qloo_unavailable`,
anything else `qloo_bad_request`. The probe's last step sends an unsupported type and a nonsense
search to confirm these against what Qloo really sends.

Requests are built only from an allowlist per path and per `filter.type`, because Qloo silently
ignores a parameter it does not know. `types` on `/search` is sent comma-separated, the form the
reference shows; the repeated form is the fallback if the probe shows the comma form is refused.
`/entities` is not built: it is only needed if `/search` turns out to carry no popularity.

## Constants in `pins.py` and how the probe replaces them

| Constant | Now | Fixed by |
|---|---|---|
| `CONFIRMED_LENSES` | music, podcast, comedy | thresholds T1 and T2 |
| `LENS_DEFAULTS` | each lens's `filter.type`; `bias.trends` of `medium` (Qloo's default); comedy tag `None` | P3 and P5 |
| `TREND_EPS` | 0.5 points of `population_percentile`, a stand-in | P4: the median absolute week-over-week change over the rows it reads, rounded to 0.1 |
| `TREND_FIELDS`, `PERCENTILE_SCALE` | `date` and `population_percentile`; 100 | P4 |
| `TREND_WINDOW_DAYS` | 56, the window the probe tries first | P4 |

The comedy tag is `None` on purpose. A tag id is pinned only if Qloo's public documentation lists
it; until then the planner looks it up with `find_tag`, which costs one cached call.

## Running the probe

```bash
python3 -m open_slot probe --name "<a musician>" --name "<a comedian>" --name "<a podcast>" \
  --notes docs/api-notes.md
```

It makes at most 14 live calls and never retries one. P1 searches the three names; P2 asks for
artists anchored on the matches, with and without a popularity ceiling; P3 asks for podcasts and,
with a comedian tag found through `/v2/tags`, for people; P4 reads the weekly trend rows of two
different acts; P5 repeats P2 with `bias.trends=high`; P6 sends an unsupported type and a nonsense
search. It prints JSON paths with their types, counts, status codes, which of the five thresholds
pass, and a block of constants to paste into `pins.py`. With `--notes` it also writes this file's
replacement. The thresholds:

- T1: P2 returns at least 10 artist candidates. If not, the entry stops the same day.
- T2: the podcast lens needs 5 candidates; the comedy lens needs a comedian or stand-up tag and 5
  tagged people.
- T3: the trend rule stands if both acts return rows, the rows differ, the percentile varies and
  the rank falls as the percentile rises in at least 80% of the weeks where both change.
- T4: explainability scores come back for artists, else links need one call per anchor.
- T5: entities carry a popularity, else the room's range goes.

## The sample programme's names

The Lantern Room is anchored on four public acts, one per kind: a folk duo, an indie band, a
stand-up and a live-taped podcast. They were named from general knowledge by a rule fixed before
any Qloo response was read: public performers or shows, never a private person; each must resolve
as the type its kind implies (artist, artist, person, podcast) with a Qloo popularity from 0.40 to
0.90, so that a 300-seat room is not anchored on a stadium act and something stays above the
range to be set aside. The probe's first search is where that is checked. Until it has run the
names are provisional; if one fails the rule, the next name of its kind below is tried, and the
choice never looks at the candidates Qloo returns.

| Kind | Chosen | Next, in order |
|---|---|---|
| Folk duo | The Milk Carton Kids | Watchhouse, First Aid Kit |
| Indie band | Alvvays | Hop Along, Real Estate |
| Stand-up | Maria Bamford | Mike Birbiglia, Tig Notaro |
| Live-taped podcast | Judge John Hodgman | My Brother, My Brother and Me; Doughboys |

In synthetic mode these typed names resolve to invented performers whose names and ids are
labelled as synthetic; no figure about a real act is invented.

## What the planner assumes while the shapes are unverified (20 October)

The planner reads Qloo only through the readers above, so every guess listed in the table at the
top is still a guess, and the planner fails loudly where a guess is wrong instead of widening a
reader:

- A reader that meets a shape it does not know raises an error naming the JSON path, and the call
  that produced it is still counted. The tool answers `qloo_unknown_shape` with that path and no
  value from the response.
- An Insights answer that carries entities and no explanation scores at all is refused the same way,
  at `results.entities[].query.explainability`. The documented fallback for that case (one Insights
  call per anchor, with the default cap raised to 24) is not built: it waits for the probe's
  threshold T4 to say it is needed.
- Trend checks stay behind one switch, `TREND_CHECKS` in `planner.py`. If threshold T3 fails it is
  set to false: nothing is then `ready`, and a plan makes its wide calls only.

## Planner choices that change the expected call counts

The traces first written for the planner (scenario A: 11 calls, B: 6, C: 7) assumed the comedy tag was
pinned. `pins.py`
pins no tag, on purpose: a tag id is pinned only if Qloo's public documentation lists it. So:

- The comedy lens costs one `find_tag` call of its own. **Scenario A is 12 of 16 calls and B is 7
  of 16**; C has no comedy lens and stays 7. With a documented tag pinned the counts are 11 and 6 (a
  test shows it), so the extra call is exactly the lookup and nothing else.
- The lookup is one call per word, `stand-up` first. `comedian` is searched only if the first answer
  holds no tag whose name says stand-up or comedian. One call cannot cover both words: the tag
  search takes one query. A tag the agent already looked up, or one given to `plan_acts`, makes the
  lookup free.
- If neither word finds a tag the comedy lens is left out, with a skip line in the trace, as
  threshold T2 says.
- "No lens comes up short" in scenario A means at least `open nights + 1` = 4 candidates in range
  with enough links in every lens. The synthetic data gives exactly 4, 4 and 4, and the check is a
  strict "fewer than", so the boundary case makes no range-filtered call. With a fourth night it
  would: each lens is then short and each costs one filtered call.
- The range-filtered call is judged per lens, as written. After a person drops an act, a lens can
  fall one short, and a re-run then spends one filtered call on it.

## How the live provider treats Qloo's answers

- A 429 or a 5xx is retried twice, after one second and then three; every attempt counts against the
  programme's cap, the agent's allowance and the server's ceiling, and a retry stops where any of
  the three would be passed. A transport failure is not retried.
- A 200, and a 404 from `/search` (Qloo's "no results"), are cached for 24 hours; nothing else is.
  The cache is shared by every workspace and its key is the path and the parameters, never the key.
- The call log keeps the time, the path, `hit`, `miss` or `fake`, milliseconds, the HTTP status
  (so a 429 shows), the attempts, who started the action, and an opaque label for the workspace. It
  holds no name, no id and no query value.
- With `.cache/` given to the ledger (the command line does this; it is git-ignored) the attempt
  times and the cache survive between runs, so `budget` can say what a day has cost.

## What the server does with threads and locks (20 October)

- A workspace's requests run one at a time under that workspace's own lock. A plan holds its lock
  for as long as Qloo takes to answer, so the same workspace's `/api/state` waits for it, and
  nobody else's request does. `/api/calls` takes no lock and answers while a plan runs.
- The document is replaced, never changed in place. Every replacement goes through one function
  that holds a lock only for the swap, and the ledger counts each live attempt under its own lock,
  so the programme's cap, the agent's allowance and the server's ceiling are exact when two
  workspaces call at once. A test runs two plans at once against a stub that answers each first
  Insights request only when both have arrived (so a shared lock would hang it), another that
  holds one plan inside a slow call while a second workspace opens a programme and reads its
  state, and others that count the stub's requests against a ceiling, an allowance and a cap.
- When the last call that a plan's check allowed is taken by another workspace before the plan
  asks, the plan stops with the same named reason (`ceiling` or `agent_allowance`) instead of
  failing.
- The server runs at most 64 handler threads. A connection that arrives when all are taken is
  answered `503` with `Retry-After: 5` on the thread that accepts it, so a flood cannot start more.
  An idle connection holds its slot until the 15 second socket timeout.

## Two providers on a live server (20 October)

A live server also holds the synthetic provider. Each programme records the provider it was
opened on and keeps it. When the server's daily ceiling is spent, a programme opened after that
runs on the synthetic provider and the state says `mode: "spent"`; a programme opened before it
stops at the ceiling with `stopped_because: "ceiling"` and is never moved. The command line's
`plan` has no fallback: with `--provider live` and a spent ceiling it is refused.

## What a real browser showed (20 October)

The browser was the desktop app's own pane, which reports Chrome 152. The synthetic provider
served every walk except those that say a stub of Qloo.

- **Loopback, `http://127.0.0.1`.** The page is a secure context, the browser labels its requests
  `same-origin`, `/api/state` says `actor: person`, and Confirm, Assign and Approve work.
- **Plain HTTP at the machine's LAN address (a non-loopback origin).** `isSecureContext` is false
  and the plain-HTTP banner shows. The browser does not keep the `Secure` cookie, so `/api/state`
  answers 401 `no_workspace` and the page cannot start a workspace. A POST sent from the page's
  own script answers 403 `bad_origin`, not `no_workspace` or `wrong_actor`: the page's `Origin` is
  `http://<address>` and the server's own origin for a name that is not loopback is `https://<address>`,
  and that check comes before the cookie is looked at. Either way no Confirm button can exist, and
  nothing a person clicks could run. The page leaves out its "Reload the page" hint there, because
  reloading cannot help.
- **A browser that sends no request label.** A loopback proxy that removes every `Sec-Fetch-*`
  header stood in for a privacy tool. `/api/state` said `actor: agent`, `actorWhy: no_fetch_metadata`;
  the no-label banner showed; every button that needs a person was disabled with "Needs a browser
  that labels its requests." and linked to that reason; the same call sent from the page's script
  was refused `wrong_actor`.
- **Keyboard only, on a fresh workspace.** From the skip link: the room form, the sample, each
  Confirm (focus moves to the next act that waits, then to Run), Run, Draft inquiry (focus moves to
  the sender field), the typed sender, Approve (focus moves to Copy), Propose nights, and Assign.
  Tab order was reading order and skipped the contents of a closed `details`.
- **Copy.** The pane denied `navigator.clipboard.writeText`, so the page selected the text and
  said to press Ctrl+C or Cmd+C. That fallback is also what a plain-HTTP origin gets.
- **375 px, light and dark.** `scrollWidth` equalled the 375 px viewport with every section open.
  The worst text pair is 6.3 to 1 (light) and 6.7 to 1 (dark); borders are at least 3.4 to 1 (light)
  and 4.7 to 1 (dark) against the page surfaces; a test computes both, not the eye.
- **Against a loopback stub of Qloo, with a made-up key and a small ceiling.** The live banner and
  the trace filling while the plan ran (4, 8, 13 then 15 steps over three seconds); a ceiling of 10
  gave the stop "the server's daily ceiling of live Qloo calls is spent", the spent banner, and a
  new programme on synthetic data; a stub that answered every Insights request 503 gave three
  attempts per call, `qloo_unavailable` in the trace and "Qloo refused twice running", with no
  candidate shown; a cap of 8 gave the cap box, and raising it to 16 let the plan finish.
- **Too many requests.** Past 30 in a minute the page showed the server's sentence in the red box.
- **Console.** `approve_inquiry` run as the agent from the page's console came back `not_a_tool`.
