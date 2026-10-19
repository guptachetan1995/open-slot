#!/usr/bin/env bash
# Checks for the Open Slot entry.
# Before the Python code lands, it checks the documents that a judge's clone and the planning
# tree both carry. Once open_slot/__main__.py exists, it also compiles the package and runs the
# tests. The entry has no third-party dependency, so there is nothing to install first.
# Written for the bash 3.2 that ships with macOS: no mapfile, no associative arrays, no
# negative array indices, and no expansion of an empty array.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

fail() {
  echo "verify: FAIL: $1" >&2
  exit 1
}
ok() { echo "  ok: $1"; }
section() { echo "== $1"; }

# The plan (SPEC.md) is never published, so the checks that read it run only in the planning
# tree, recognised by the publishing and verification scripts two levels up.
in_monorepo=false
if [ -f ../../bin/publish.sh ] && [ -f ../../bin/verify.sh ]; then in_monorepo=true; fi

# Prints the body of one "## <title>" section of a Markdown file, without the blank lines
# around it: exactly what gets pasted into a form field.
section_body() {
  awk -v want="## $2" '
    $0 == want { on = 1; next }
    on && /^## / { on = 0 }
    on { lines[++n] = $0 }
    END {
      first = 1
      while (first <= n && lines[first] ~ /^[[:space:]]*$/) first++
      last = n
      while (last >= first && lines[last] ~ /^[[:space:]]*$/) last--
      for (i = first; i <= last; i++) print lines[i]
    }' "$1"
}

char_count() { printf '%s' "$1" | LC_ALL=en_US.UTF-8 wc -m | tr -d ' '; }

# True when a phrase appears in a file even if the Markdown wraps it across lines.
has_phrase() { tr '\n' ' ' < "$1" | tr -s ' ' | grep -qF -- "$2"; }

section "entry root files"
for f in README.md LICENSE .gitignore .env.example docs/submission.md .python-version; do
  [ -f "$f" ] || fail "$f is missing"
  ok "$f"
done
[ "$(cat .python-version)" = "3.13" ] || fail ".python-version must read 3.13, the version hosts and the tests are pinned to"
ok ".python-version is 3.13"
[ -x verify.sh ] || fail "verify.sh is not executable"
ok "verify.sh is executable"

section "the licence is MIT and names the copyright holder"
[ "$(sed -n 1p LICENSE)" = "MIT License" ] || fail "LICENSE does not start with the MIT License title"
grep -qx 'Copyright (c) 2026 Chetan Gupta' LICENSE || fail "LICENSE lost its copyright line"
ok "MIT, Copyright (c) 2026 Chetan Gupta"

section "the key stays out of the repository"
# The published copy is a fresh git repository in which only this directory's .gitignore
# applies, so it alone keeps a local .env and the private cache out.
grep -qx '\.env' .gitignore || fail ".gitignore does not ignore .env"
grep -qx '\.cache/' .gitignore || fail ".gitignore does not ignore .cache/ (the private cache and ledger)"
grep -qx 'QLOO_API_KEY=' .env.example || fail ".env.example must carry an empty QLOO_API_KEY="
for setting in OPEN_SLOT_PORT PORT OPEN_SLOT_BIND OPEN_SLOT_ALLOWED_HOSTS OPEN_SLOT_TRUST_PROXY; do
  grep -qx "$setting=" .env.example || fail ".env.example must carry an empty $setting="
done
if grep -nE '^[A-Za-z_]+=.+' .env.example; then fail ".env.example carries a value; every setting must stay empty"; fi
if grep -rnIE 'QLOO_API_KEY=[A-Za-z0-9_-]{8,}' . \
  --exclude-dir=.git --exclude-dir=.cache --exclude-dir=out --exclude='.env' --exclude='.env.*' \
  --exclude='verify.sh'; then
  fail "a QLOO_API_KEY with a value is in the tree"
fi
if grep -rnIiE 'x-api-key"?[:=] *["'"'"']?[A-Za-z0-9_-]{20,}' . \
  --exclude-dir=.git --exclude-dir=.cache --exclude-dir=out --exclude='.env' --exclude='.env.*' \
  --exclude='verify.sh'; then
  fail "an X-Api-Key header with a key-shaped value is in the tree"
fi
ok ".env and .cache/ ignored, every example setting empty, no key-shaped value in the tree"

section "no Qloo response data in the public repository"
# Qloo's developer guide: "Do not store Qloo response data in a public repository". Real entity
# ids are UUIDs, so any UUID outside the reserved synthetic range fails.
real_ids="$(grep -rIiEho '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' . \
  --exclude-dir=.git --exclude-dir=.cache --exclude-dir=out --exclude='.env' --exclude='.env.*' \
  --exclude='verify.sh' | grep -viE '^00000000-0000-4000-8000-' || true)"
[ -z "$real_ids" ] || fail "a UUID outside the synthetic range 00000000-0000-4000-8000-… is in the tree: $real_ids"
ok "every id in the tree is in the synthetic range"

section "the published documents stand on their own"
published=(README.md .env.example)
for f in docs/*.md; do published+=("$f"); done
if grep -nwE 'SPEC(\.md)?' "${published[@]}"; then
  fail "a published file mentions the plan file, which is never published"
fi
private_name="hackathons"'-2026'
private_paths='(^|[^A-Za-z0-9_./-])(\.\./)*(entries/[a-z0-9-]+|bin/(publish|pages|ready|verify)\.sh|bin/video/)'
if grep -nE "$private_paths|$private_name" "${published[@]}"; then
  fail "a published file points into the private planning tree"
fi
ok "no plan-file or private-tree references in ${published[*]}"

section "no unresolved placeholders"
placeholder_files=("${published[@]}")
if [ "$in_monorepo" = true ]; then placeholder_files+=(SPEC.md); fi
if grep -nE '(^|[^A-Za-z])(TODO|TBD)([^A-Za-z]|$)|\[owner\]|[Oo]wner fills in|<placeholder>|PLACEHOLDER' \
  "${placeholder_files[@]}"; then
  fail "a placeholder is left in: ${placeholder_files[*]}"
fi
ok "none in ${placeholder_files[*]}"

section "the README states what a judge needs"
grep -q 'never registered as a tool' README.md ||
  fail "README.md no longer says the human verbs are never registered as a tool"
grep -q '^## What leaves your machine, exactly' README.md ||
  fail "README.md lost its exact statement of what is sent to Qloo"
grep -q 'not affiliated with or endorsed by Qloo' README.md ||
  fail "README.md lost the independence line"
grep -q 'No Qloo response is stored in this repository' README.md ||
  fail "README.md lost the statement that no Qloo response is stored here"
for verb in confirm_anchor assign_slot approve_inquiry drop_act raise_call_cap; do
  grep -q "\`$verb\`" README.md || fail "README.md does not name the human verb $verb"
done
has_phrase README.md 'Sec-Fetch-Site' ||
  fail "README.md lost the statement of what the cookie gate does and does not hold against"
has_phrase README.md 'does **not** hold against' ||
  fail "README.md no longer says where the human gate does not hold"
has_phrase README.md 'public performers, podcasts or shows only' ||
  fail "README.md lost the line telling visitors to type public performers only"
has_phrase docs/submission.md 'public performers, podcasts or shows only' ||
  fail "docs/submission.md lost the line telling visitors to type public performers only"
has_phrase README.md 'only on HTTPS pages' ||
  fail "README.md lost the statement that the person label exists only on HTTPS pages"
has_phrase README.md 'python3 -m open_slot mcp --address' ||
  fail "README.md lost the command that connects a client that speaks stdio"
has_phrase README.md 'docs/deploy.md' ||
  fail "README.md no longer points at the deploy guide"
has_phrase README.md 'One request, one result, redacted' ||
  fail "README.md lost its redacted request-to-result section"
has_phrase docs/submission.md 'One request, one result, redacted' ||
  fail "docs/submission.md lost its redacted request-to-result paragraph"
has_phrase README.md 'not an assessment of any performer' ||
  fail "README.md lost the sentence that Qloo's figures are not an assessment of any performer"
has_phrase docs/submission.md 'not an assessment of any performer' ||
  fail "docs/submission.md lost the sentence that Qloo's figures are not an assessment of any performer"
has_phrase docs/submission.md 'sends no request label' ||
  fail "docs/submission.md testing instructions lost the sentence about a browser that sends no request label"
has_phrase README.md 'not a calendar day' ||
  fail "README.md no longer says the call ceiling and allowance live in memory"
has_phrase docs/submission.md 'not a calendar day' ||
  fail "docs/submission.md testing instructions no longer say the ceiling lives in memory"
if grep -n 'three or four acts' README.md docs/submission.md; then
  fail "the sample programme is four acts; a published file still says three or four"
fi
if [ -f open_slot/__main__.py ]; then
  if grep -q 'Status: a plan, not yet code' README.md; then
    fail "open_slot/__main__.py exists, but README.md still says the entry is a plan, not yet code"
  fi
else
  grep -q 'Status: a plan, not yet code' README.md ||
    fail "there is no code yet, so README.md must say it is a plan, not yet code"
fi
ok "gate, privacy, independence, no-Qloo-data and status lines present; the five verbs are named"

section "no price, availability or sales claim in the published copy"
claim_files=(README.md)
for f in docs/*.md; do claim_files+=("$f"); done
if grep -niE 'costs less|sells better|affordable|cheaper|more available|price has moved|before it peaks' \
  "${claim_files[@]}"; then
  fail "a published file makes a price, availability or sales claim the honesty rules forbid"
fi
# "will sell" is allowed only where it is disclaimed ("Whether an act will sell", "never", "no ...")
if grep -niE 'will sell' "${claim_files[@]}" | grep -viE 'whether|never|not |no |nothing'; then
  fail "a published file says an act will sell"
fi
ok "none of: costs less, sells better, affordable, cheaper, more available, price has moved, before it peaks"

section "the submission copy fits the form"
name="$(section_body docs/submission.md "Project name")"
[ -n "$name" ] || fail "docs/submission.md has no '## Project name' section"
n="$(char_count "$name")"
[ "$n" -le 60 ] || fail "the project name is $n characters; Devpost's standard limit is 60"
ok "Project name: $n of 60"
pitch="$(awk '/^## Elevator pitch/{f=1;next} /^## /{f=0} f && /^> /{sub(/^> /,""); print; exit}' docs/submission.md)"
[ -n "$pitch" ] || fail "docs/submission.md has no quoted elevator pitch"
pitch_len="$(char_count "$pitch")"
[ "$pitch_len" -le 200 ] || fail "the elevator pitch is $pitch_len characters; Devpost allows 200"
section_body docs/submission.md "Elevator pitch" | grep -q "This one is $pitch_len:" ||
  fail "docs/submission.md must state the pitch length as 'This one is $pitch_len:'"
ok "Elevator pitch: $pitch_len of 200, and its stated length is current"
about="$(section_body docs/submission.md "About the project")"
[ -n "$about" ] || fail "docs/submission.md has no '## About the project' section"
n="$(char_count "$about")"
# Devpost states no limit for the story; 12,000 is this entry's own ceiling.
[ "$n" -le 12000 ] || fail "'About the project' is $n characters; this entry keeps it under 12000"
ok "About the project: $n of 12000 (this entry's own ceiling)"
for title in "Built with" "Try it out links" "Video demo link" "Testing instructions"; do
  [ -n "$(section_body docs/submission.md "$title")" ] || fail "docs/submission.md has no '## $title' section"
  ok "$title"
done
if grep -nE '\]\((\.\.?/|[A-Za-z0-9_./-]+\.md)' docs/submission.md; then
  fail "docs/submission.md links a relative path; the form can't follow it"
fi
ok "no relative links"

if [ "$in_monorepo" = true ]; then
  section "SPEC.md sections, in the fixed order"
  [ -f SPEC.md ] || fail "SPEC.md is missing"
  expected=(
    "1. Concept"
    "2. User and problem"
    "3. Why this wins"
    "4. What the agent does, what only a human does"
    "5. Tool list"
    "6. Qloo API contract"
    "7. Architecture and stack pin"
    "8. Data model"
    "9. Demo script"
    "10. Build plan"
    "11. Owner steps"
    "12. Risks"
    "13. Submission checklist"
  )
  found=()
  while IFS= read -r line; do found+=("$line"); done < <(grep -E '^## ' SPEC.md)
  [ "${#found[@]}" -eq "${#expected[@]}" ] ||
    fail "SPEC.md has ${#found[@]} top-level sections, expected ${#expected[@]}"
  i=0
  while [ "$i" -lt "${#expected[@]}" ]; do
    case "${found[$i]}" in
      "## ${expected[$i]}"*) ok "${expected[$i]}" ;;
      *) fail "section $((i + 1)) is '${found[$i]}', expected '## ${expected[$i]}…'" ;;
    esac
    i=$((i + 1))
  done
  [ "${found[$((${#found[@]} - 1))]}" = "## 13. Submission checklist" ] ||
    fail "SPEC.md does not end with the submission checklist"
  ok "the submission checklist is the last section"

  section "the submission-count clause is quoted verbatim, with its caps (read 7 Oct 2026)"
  for clause in \
    "An Entrant may submit more than one Submission, however, each Submission must be unique and substantially different from each of the Entrant’s other Submissions, as determined by the Sponsor and Devpost in their sole discretion." \
    "Each Project is eligible to win one (1) prize." \
    "Verdict: multiple entries are allowed, with no numeric cap in the Rules."; do
    grep -qF -- "$clause" SPEC.md || fail "SPEC.md does not carry: $clause"
    ok "${clause:0:72}"
  done

  section "three candidate concepts are scored and the chosen one is justified"
  for concept in "A. Open Slot" "B. Common Ground" "C. Landing Kit" "**Why A.**"; do
    grep -qF -- "$concept" SPEC.md || fail "SPEC.md section 1 does not carry: $concept"
    ok "$concept"
  done

  section "every judging criterion is quoted verbatim (Rules §6, read 7 Oct 2026)"
  for criterion in \
    "**Technological Implementation:** How thoroughly and skillfully does the project use Qloo? Does the code reflect genuine effort and a working, non-trivial implementation?" \
    "**Design:** Does the project deliver a complete, coherent product experience, not just a technical proof of concept?" \
    "**Potential Impact:** Does the project make a credible, specific case for solving a real problem for a real audience and does the solution actually address that problem based on what's demonstrated?" \
    "**Quality of the Idea:** Is this a creative, non-obvious use of Qloo and does the team demonstrate genuine understanding of the problem space they're working in?" \
    "The first stage will determine via pass/fail whether the ideas meet a baseline level of viability, in that the Project reasonably fits the theme and reasonably applies the required APIs/SDKs featured in the Hackathon."; do
    grep -qF -- "$criterion" SPEC.md || fail "section 3 does not quote: $criterion"
    ok "${criterion:0:64}"
  done

  section "the checklist carries the rules' own words (read 7 Oct 2026)"
  checklist="$(awk '/^## 13\. Submission checklist/ { on = 1 } on' SPEC.md)"
  for item in \
    "Registration and Submission Period: September 30, 2026 (9:00 am Eastern Time) – October 30, 2026 (11:45 pm Eastern Time)" \
    "Judging Period: November 2, 2026 (12:00 pm Eastern Time) – November 16, 2026 (11:45 pm Eastern Time)" \
    "Register for the Hackathon on the Hackathon Website by clicking the “Join Hackathon” button." \
    "Entrants will be given access to the Qloo API by filling out the Google Form here:" \
    "Functionality: The Project must be capable of being successfully installed and running consistently on the platform for which it is intended and must function as depicted in the video and/or expressed in the text description." \
    "Provide a link to a functional demo application" \
    "The repository must be public and open source by including an open source license file." \
    "Include a text description that should explain the features and functionality of your Project." \
    "The Entrant must make the Project available free of charge and without any restriction, for testing, evaluation and use by the Sponsor, Administrator and Judges until the Judging Period ends." \
    "Once the Submission Period has ended, you may not make any changes or alterations to your Submission" \
    "A live, working app participants can try end-to-end." \
    "Submissions that only run locally or rely on private access will not qualify." \
    "An open-source license, with the file visible in About section of your repo." \
    "NOTE: Demo videos are not required." \
    "FINAL CHECK"; do
    printf '%s\n' "$checklist" | grep -qF -- "$item" || fail "the checklist does not carry: $item"
    ok "${item:0:72}"
  done

  section "the deadline, the host check, the key hygiene and the demo's honesty are pinned"
  grep -qF 'Sat 31 Oct 2026, 09:15 IST' SPEC.md || fail "SPEC.md no longer pins the deadline in IST"
  grep -qF 'https://hackathon.api.qloo.com' SPEC.md || fail "SPEC.md no longer pins the hackathon base URL"
  grep -qF 'X-Api-Key' SPEC.md || fail "SPEC.md no longer pins the key header"
  grep -qF 'Do not store Qloo response data in a public repository' SPEC.md ||
    fail "SPEC.md no longer quotes the developer guide's storage rule"
  grep -qF '**Storage: no Qloo data in the public repository.**' SPEC.md ||
    fail "SPEC.md no longer pins the no-Qloo-data design"
  grep -qF 'G1, hosting' SPEC.md || fail "SPEC.md lost the hosting go/no-go test"
  grep -qF 'hard ceiling is 120 s' SPEC.md || fail "SPEC.md no longer pins the 120 s video ceiling"
  grep -qF 'bin/video/out/open-slot/final-narrated.mp4' SPEC.md ||
    fail "SPEC.md no longer pins where the finished video lives"
  for verb in confirm_anchor assign_slot approve_inquiry drop_act raise_call_cap; do
    grep -qF "\`$verb\`" SPEC.md || fail "SPEC.md does not name the human verb $verb"
  done
  grep -qF '`OPEN_SLOT_MAX_CALLS`, default 16' SPEC.md || fail "SPEC.md no longer pins the call cap of 16"
  ok "deadline; base URL; header; storage rule; hosting test; 120 s ceiling; video path; five verbs; call cap"

  section "the fix-round decisions stay pinned (review of 7 Oct 2026)"
  for pin in \
    'G1b, the last resort' \
    'G1c, the first fallback' \
    'Render, EC2 and Vercel all fail' \
    'sslip.io' \
    '`OPEN_SLOT_PORT`' \
    '`RENDER_EXTERNAL_HOSTNAME`' \
    '`no_fetch_metadata`' \
    'potentially trustworthy' \
    'The person gate needs HTTPS' \
    'redacted request-to-result explanation, including any entity/tag choices' \
    'other high-impact decisions' \
    'is on the **form**, not in the developer guide' \
    'a real throwaway deploy' \
    'python3 -m open_slot probe' \
    '`TREND_MIN_MOVES`' \
    '`population_percentile`' \
    '`limiterKey`' \
    '`open_slot/pins.py`' \
    '**Server routes and their guards.**' \
    'the hosted Streamable-HTTP MCP endpoint' \
    '**candidate B in §1 (`common-ground`, a group-consensus planner) is the' \
    'Compute'; do
    has_phrase SPEC.md "$pin" || fail "SPEC.md lost a fix-round decision: $pin"
    ok "${pin:0:64}"
  done
  # Each stale phrase below was replaced in the 7 Oct fix round; none may come back.
  for stale in \
    'TREND_MIN_WEEKS' \
    'acceleration is not negative' \
    'which is the recommendation' \
    'Nothing past S2 runs before the answer' \
    'per client address on `/api/invoke`' \
    'Instance Type | Free' \
    'three or four acts' \
    'G1b, the fallback host' \
    'more available and more interesting' \
    'only after the price has moved' \
    'The page says "waking the server' \
    'It also says, in its header,' \
    'create the Render and Vercel accounts'; do
    if grep -nF -- "$stale" SPEC.md; then fail "SPEC.md carries a phrase the fix round removed: $stale"; fi
  done
  ok "none of the replaced phrases is back"

  section "the plan itself makes no price or sales claim, and carries the kit's six items"
  if grep -nE 'costs less|sells better|cheaper|more available|price has moved|before it peaks' SPEC.md; then
    fail "SPEC.md makes a price, availability or sales claim the honesty rules forbid"
  fi
  kit_block="$(awk '/^\*\*Qloo.s own submission guide/ { on = 1 } /^\*\*This entry.s own final check/ { on = 0 } on' SPEC.md)"
  [ -n "$kit_block" ] || fail "SPEC.md section 13 lost the block mapping the kit's submission guide"
  for n in 1 2 3 4 5 6; do
    printf '%s\n' "$kit_block" | grep -q -- "^- \[ \] $n\. " || fail "the kit block lacks its item $n"
  done
  ok "no forbidden claim; the kit's six items are each mapped in section 13"
fi

if [ -f open_slot/__main__.py ]; then
  PY="${PY:-python3}"
  section "compile and test (no third-party packages, nothing to install)"
  "$PY" -W error -m compileall -q open_slot tests || fail "compileall found a problem"
  "$PY" -m unittest discover -s tests -t . -q || fail "the tests failed"

  section "the sample programme's plan runs on the synthetic provider"
  # No key is read or needed: the fake provider is named, and the three settings that could point
  # elsewhere are removed from this one command's environment.
  plan_out="$(env -u QLOO_API_KEY -u OPEN_SLOT_ENV_FILE -u OPEN_SLOT_PROVIDER \
    "$PY" -m open_slot plan scenarios/lantern-room.json --provider fake)" ||
    fail "plan scenarios/lantern-room.json --provider fake failed"
  printf '%s\n' "$plan_out" |
    grep -qF 'calls: 12 of 16 spent in all (8 by this plan), 8 not spent; stopped: enough_ready' ||
    fail "the sample plan no longer spends 12 of 16 calls and stops because enough candidates are ready"
  ok "12 of 16 calls spent, 8 not spent, stopped: enough_ready"

  section "the server starts on the synthetic provider and answers"
  # A real process on a free loopback port: the page, the health check and a refused foreign Host.
  # The same three settings are removed, so no key can be read.
  port="$("$PY" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
  env -u QLOO_API_KEY -u OPEN_SLOT_ENV_FILE -u OPEN_SLOT_PROVIDER -u PORT OPEN_SLOT_PORT="$port" \
    "$PY" -m open_slot serve --provider fake > /dev/null 2>&1 &
  server_pid=$!
  trap 'kill "$server_pid" 2> /dev/null || true' EXIT
  "$PY" - "$port" << 'PYTHON' || fail "the server did not start or did not answer as it should"
import http.client
import sys
import time

port = int(sys.argv[1])


def get(path, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("GET", path, headers=headers or {})
    reply = connection.getresponse()
    return reply.status, reply.getheader("Set-Cookie"), reply.read()


for _ in range(50):
    try:
        status, _, body = get("/healthz")
        break
    except OSError:
        time.sleep(0.1)
else:
    sys.exit("no answer on /healthz")
assert (status, body) == (200, b"ok\n"), (status, body)
status, cookie, page = get("/")
assert status == 200 and b"<title>Open Slot</title>" in page, status
assert cookie and cookie.startswith("os_w=ws_") and "HttpOnly" in cookie and "SameSite=Strict" in cookie, cookie
assert get("/", {"Host": "evil.example"})[0] == 403
assert get("/api/state")[0] == 401
PYTHON
  kill "$server_pid" 2> /dev/null || true
  wait "$server_pid" 2> /dev/null || true
  ok "/healthz answers, / serves the page with a workspace cookie, a foreign Host and no cookie are refused"
fi

echo "open-slot: all checks passed."
