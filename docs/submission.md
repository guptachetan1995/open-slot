# Devpost submission copy — Open Slot

**Hackathon:** [Qloo Agentic Hackathon](https://qloo.devpost.com/) ("Agents, but with taste") ·
**Deadline:** Oct 30, 2026, 11:45 pm EDT (Sat 31 Oct, 09:15 IST).

**State: version 3, revised 30 Oct 2026 after an independent review against the judging criteria.
The app runs on its labelled synthetic sample data: the Qloo API key requested on 17 Oct had not
reached this build when it was published. No real MCP client session has been recorded.**

- This file is the copy for each field of the Devpost "Enter a Submission" form, then the
  checklist those fields answer and the split of who does what.
- Every sentence that describes behaviour is a claim the build must satisfy. A feature that is cut
  loses its sentences here, in the README and in the saved draft in the same change.
- A value that exists only once the demo is hosted (its address, the host's name) is an HTML
  comment naming what fills it, and is filled before the copy is pasted.
- The form's exact field list is read on the live form once the project is registered, and this
  file is updated to match. The fields below are Devpost's standard ones plus what the Official
  Rules require.
- Every link in this file is absolute, because Devpost can't follow a relative link.
- The hackathon asks for a hosted demo, a public repository and a text description. It does not
  ask for a video.

## Project name

Open Slot

## Elevator pitch

At most 200 characters. This one is 199:

> Fills a venue's dark nights: an agent finds musicians, comedians and podcasters whose audiences overlap your regulars, shows the evidence and drafts the inquiry. Only you confirm, assign and approve.

## About the project

The rules require that the text description "explain the features and functionality of your
Project", and the overview adds "what makes it 'Qloo-powered.'"

**Try it first:** <!-- hosted-url -->
Click **Try the sample programme**, confirm the acts, click **Run the agent**, then open any card.

The sample room has three dark Tuesdays and four acts that sold it: The Milk Carton Kids, Alvvays,
Maria Bamford and Judge John Hodgman. The agent proposes a music act, a comedian and a podcast for
those nights. The demo runs on labelled synthetic data, so the candidates are invented; the
planner, cards and gate are the code a key would run on Qloo's answers.

### Inspiration

Every venue, theatre and campus board has nights it can't fill. The person who programs them has
sold the room before, so they know a few acts that worked. What they lack is a short list of whom
to ask next, and some evidence for each name: whose audience overlaps theirs (including people
who aren't musicians), whether the act is within reach of the room, and whether it is on the way
up or on the way down.

A language model can't answer that from memory. It holds no audience-overlap figure, no popularity
percentile and no weekly trend, and it states a guess as a fact. Qloo's taste graph does hold
them, across music, podcasts and people, which is exactly the kind of thing the hackathon's brief
asks for: "the thing that only works because it knows what people love".

The other half is what an agent should be allowed to do. An inquiry to an act's management is a
message sent in the room's name. It can't be unsent, and a careless one costs a relationship the
room needs. So Open Slot drafts everything and sends nothing, and the three decisions that matter
are not things an agent has a tool for.

### What it does

**It finds the acts a room's regulars already love, in reach and on the way up, shows the
evidence for each, and drafts the inquiry. The programmer alone confirms an act, assigns a night
and approves an inquiry.**

You describe the room: a label, the open nights and two to six acts that sold well (public
performers, podcasts or shows: each name is sent to Qloo to find a match).
1. **Confirm your acts.** For each name, one Qloo entity search returns candidates. The agent can
   propose a match. Only you confirm it, which follows Qloo's own guidance to show resolved
   matches to the user before relying on them.
2. **One wide pass per kind of act.** One Qloo Insights call each for music, podcasts and (if a
   suitable tag exists) comedy, anchored on your confirmed acts, returns up to 50 candidates and
   Qloo's own account of which of your acts each connects to.
3. **Triage, with no further call.** Candidates connected to too few of your acts, or above the
   popularity range your room has proven, are set aside with the reason shown. Nothing is hidden.
4. **Look deeper only where it can change the answer.** For the best remaining candidates, one
   Qloo trending call each shows whether the act is rising, steady or fading. The agent stops as
   soon as it holds one more ready candidate than you have open nights, and says how many calls it
   saved.
5. **Evidence, not a score.** Each card shows which of your acts it connects to and by how much,
   where its popularity sits against your room's range, its weekly trend and its tags, each
   labelled as Qloo's figure or as this app's rule.
6. **Propose, then assign.** The agent proposes which candidate could fit which night. Only you
   assign a night.
7. **Draft, then approve.** The agent drafts an inquiry that reads DRAFT. To approve it you name
   the sender. An approved inquiry is text you copy or open in your own mail app. The app sends
   nothing, and Qloo holds no contact details, so you find the address yourself.

The three decisions (confirming an act, assigning a night, approving an inquiry), dropping an act
and raising the call cap are human-only verbs. They are never registered as tools, so no agent
can call them, whether it is the page's own agent console or a model-driven agent over MCP. No act
is ever shown as booked.

**What bookers use today**, by their makers' own pages:
[Opendate's Smart Suggestions](https://www.opendate.io/post/artist-discovery-smart-suggestions)
ranks "artists who've drawn well at venues comparable to yours";
[Chartmetric's Neighbor Artists](https://hmc.chartmetric.com/new-features-cities-page-neighbor-artists-instagram-follower-demographics-amazon-music/)
lists artists with similar Spotify or Deezer numbers; [Booking-Agent.io](https://booking-agent.io/)
helps artists and their agents find venues. All three describe artists; none describes finding a
comedian or a podcast whose audience overlaps a band's. Open Slot asks that cross-domain question,
adds reach and trend, and labels whose figure each number is. It has no draw history, the better
evidence of what a night takes at the door.

### How we built it

No language model is bundled, and that is deliberate. The agent is an auditable planner whose
every step is a rule applied to a Qloo response, and the rules are in the README. The same ten-tool
registry is served over the Model Context Protocol, so any tool-calling model can drive it, and
none can commit through it: one endpoint at the agent address speaks the stateless 2026-07-28
revision and the 2025-11-25 and 2025-06-18 `initialize` handshake, and
`python3 -m open_slot mcp --address ...` relays the same address to a client that only speaks
stdio.

Qloo is called through four endpoints, each for a different question: `/search` (turn a name into
an entity you confirm), `/v2/insights` (one call per kind of act, with Qloo's explainability, the
room's popularity range, and exclusions), `/v2/trending` (direction, only for candidates worth a
deeper look) and `/v2/tags` (a valid tag before any filter by one). Every request is built from a
per-type parameter allowlist, because Qloo silently ignores parameters it doesn't recognise. The
key travels only in the `X-Api-Key` header, from the server.

**One request, one result, redacted.** For the podcast lens, one `GET /v2/insights` with
`filter.type=urn:entity:podcast`, `signal.interests.entities` set to the four acts you confirmed,
`filter.exclude.entities` set to the same four, `feature.explainability=true`, `bias.trends` and
`take=50`, with the key in the `X-Api-Key` header and every id elided here. From each result the
app reads the name, id, type, popularity, tags and which of your acts it connects to. A card then
says: "Connects to 3 of your 4 acts (Qloo's explanation scores), popularity inside your room's
range, trend rising (this app's rule on Qloo's weekly rows)". The entity choice is yours: you
confirm each match before it is used. The comedy lens's tag id is one Qloo returned for a word such
as "stand-up", and the app refuses any other. No Qloo response is stored in the repository.

Everything goes through one `invoke(tool, args, actor)` chokepoint that the page's buttons, the
command line, the agent console and the MCP front end all call. It is plain Python 3.11 or newer
with no dependency and no build step: about 4,200 lines in 19 modules, a page of about 1,500 lines
of HTML, CSS and JavaScript, and 675 tests that run offline in a few seconds. It is hosted over
HTTPS, because a browser labels a person's click only on HTTPS pages, so a judge can try it
without installing anything. The public repository holds no Qloo response data, as Qloo's
developer guide asks: its tests and its keyless mode use invented data, labelled on every page.
Claude Code wrote the code.

What the planner does, measured on the synthetic sample (invented performers, so the counts show
the planner and say nothing about Qloo's data): for The Lantern Room, four acts and three open
nights, a plan spends 12 of the programme's 16 calls (four searches, one tag lookup, three wide
calls and four trend checks), stops because four candidates are ready for three nights, and says
that eight further trend checks were not needed. No live run has happened, so there are no live
counts.

**What this build has not done:** the Qloo API key requested on 17 Oct had not reached this build
when it was published, so the app runs on its labelled synthetic sample data and has made no call
to Qloo; every response shape in the code is the documented one, marked unverified. The live
provider and its key-day probe are in the code. No real MCP client has been run against the
endpoint or the relay; the tests use a client written from the specification.

### Challenges we ran into

- **Telling a person's click from an agent's call.** Over HTTP both are requests. The page
  therefore acts as the person only when the browser labels the request `Sec-Fetch-Site:
  same-origin`, a header page script cannot set, and browsers send it only on HTTPS pages and on
  `localhost`. On plain HTTP no Confirm button can work, so the page says why in a banner, and
  every hosted copy has to be HTTPS.
- **Qloo ignoring a parameter it does not know,** without an error. Every request is built from a
  per-type allowlist and a test refuses anything else.
- **Testing realistic shapes without Qloo data in a public repository.** The fixtures are
  invented performers with ids in a reserved range, and a test fails if any other id-shaped string
  appears anywhere in the tree.
- **MCP moved while this was built.** One endpoint serves both the stateless 2026-07-28 revision
  and the older `initialize` handshake, and the stdio relay rebuilds the new revision's headers
  from each message, because its stdio transport has no header layer.
- **Free hosts forget.** A free web service sleeps after 15 minutes without traffic and its
  in-memory workspaces go with it, so the copy says what a visitor sees after a cold start. Caddy
  gives a bare IP only a certificate no browser trusts, so the EC2 option uses an `sslip.io` name.

### Accomplishments that we're proud of

- The gate is structural and tested. The five human-only verbs are absent from the registry,
  `GET /api/tools`, the agent address and the MCP tool list, and the chokepoint refuses them for
  the agent even when called directly, in both MCP eras.
- Every figure on a card says whose it is: Qloo's, or this app's rule.
- The page runs from a keyboard alone, at 375 px, in light and dark, and has a sentence for every
  state: a name with several matches, no match, the cap reached, the daily ceiling spent, Qloo
  failing, plain HTTP and a browser that sends no label.
- It spends calls only where they can change the answer, and shows the count it did not spend.

### What we learned

- Put the decision of who may do what in the transport and the registry, not in a prompt: an agent
  that has no approve tool cannot be talked into approving.
- A deterministic planner whose steps are all written to a trace is easier to trust than a
  summary of one.
- Hosting is part of the design here. The person gate needs HTTPS, a free host forgets its
  workspaces, and a key has to live somewhere a stranger cannot read it.

### What's next for Open Slot

The venue pays: the programmer or talent buyer of an independent room, a theatre or a campus
board, the person this app is built around. The wedge is the dark weeknight, a Tuesday with nothing
on, where a comedy night or a podcast taping is a booking that a list of similar bands does not
suggest. Our working assumption on price, not yet tested with any buyer, is a flat US$49 a month
per room. Next come a support-act mode, which finds openers below a confirmed headliner whose
audiences overlap it, and a taste profile of the room from Qloo tags, shown beside the candidates.

After those, a comparison of any candidate's audience with the room's, through Qloo's analysis
endpoint.

## Built with

python, html, css, javascript, model-context-protocol, qloo, <!-- host: render|ec2 -->

(The host marker becomes `render`, or `amazon-ec2, caddy`, whichever the demo is hosted on.)

## Try it out links

- <!-- hosted-url --> (the hosted demo; it goes first)
- https://github.com/guptachetan1995/open-slot (the public repository)

## Video demo link

<!-- video-url: the YouTube link if the optional video is uploaded, else leave the field empty -->
The rules do not ask for a video.

## Image gallery (planned captions)

All images are real frames of the app running on its synthetic sample data, with the sample-data
banner visible, no key, cookie or agent address on screen, and no Qloo logo.

1. **Thumbnail (3:2):** the board: the open nights on one side, ready candidates with their
   evidence on the other.
2. **Confirm your acts:** the agent's proposed match for each name, and the person's confirm click.
3. **The agent's run:** the trace and the call strip, each call's path, live or cache and
   milliseconds, with "calls spent, calls not spent".
4. **A candidate card, opened:** which acts it connects to with Qloo's scores, the popularity
   range, the trend, and the rule that ranked it.
5. **The set-aside pile:** candidates above the room's range or connected to too few acts, each
   with its reason.
6. **Draft, approve:** an inquiry reading DRAFT, then approved with the sender named.
7. **The agent can't approve:** the agent console's refusal of `approve_inquiry`.

## Hackathon-specific fields (confirm on the live form)

| Field (expected) | Draft answer |
|---|---|
| Link to a functional demo application | <!-- hosted-url --> |
| Public code repository URL | https://github.com/guptachetan1995/open-slot, public since 19 Oct 2026, with the MIT licence visible in the About panel |
| Platform | A web app, in any current browser, on a phone or a desktop |
| Testing instructions | See [Testing instructions](#testing-instructions) below |
| New or existing project | New. Planning notes were written on 7 Oct. All application code was written from 19 Oct 2026, inside the Submission Period |

## Testing instructions

The rules require access "free of charge and without any restriction". Open Slot is a web app.

1. **No login, no key, nothing to install.** Open <!-- hosted-url --> in a current Chrome,
   Firefox or Safari. On a free host that sleeps after 15 minutes without traffic, the first load
   can take up to a minute (Render shows its own loading page while it wakes), and a workspace does
   not survive the sleep: after the wait the page is empty and **Try the sample programme** starts
   again. A page loaded over plain HTTP, or a browser that sends no request label (older than 2023,
   or behind a proxy or privacy tool that strips it), shows a banner and cannot confirm, assign or
   approve, because the page tells you apart from an agent by that label; the agent console and an
   MCP client still work. Click **Try the sample programme**, then: confirm the acts, run the agent,
   open a card, propose and assign a night, draft, name the sender and approve an inquiry, then try
   `approve_inquiry` in the agent console.
2. **What the demo runs on.** A banner on every page names the mode. The hosted demo serves
   synthetic sample data, invented performers through the real planner, tools and human gate,
   because the Qloo API key had not reached this build; with a key set on the host, the same server
   calls Qloo live. A programme has a call cap (16 by default; **Raise the call cap** takes it to
   40), and a live server has a daily ceiling that guards the key. These are abuse guards, not a
   login or a paywall. The ceiling and the agent's allowance live in the server's memory, so they
   bound one run of the server and not a calendar day, and they restart when a free host wakes from
   sleep. If the ceiling is ever spent, the page says so in a banner and serves synthetic sample
   data, labelled as such. Type public performers, podcasts or shows only: with a key set, each name
   is sent to Qloo.
3. **Locally, with no key:** clone the repository and run `python3 -m open_slot serve` (Python 3.11
   or newer, nothing to install). It runs on synthetic data and says so.
4. **Locally, live, with your own key:** put your Qloo hackathon key in `QLOO_API_KEY` and run the
   same command. The key only works against the hackathon base URL.
5. **Tests:** `python3 -m unittest discover -s tests -t . -q`. They run offline.
6. **Connect your own agent (optional):** the page shows an agent address. An MCP client that
   speaks Streamable HTTP can use `<agent address>/mcp` (Claude Code:
   `claude mcp add --transport http open-slot <agent address>/mcp`), and one that only speaks stdio
   can run `python3 -m open_slot mcp --address <agent address>` from a clone. The human verbs are
   not in the tool list, and asking for one returns `not_a_tool`. No real MCP client has been run
   against either yet; the tests drive both with a client written from the specification's text.

The hosted demo and the repository stay up through at least 18 Nov 2026, past the end of the
judging period.

## YouTube upload details (conditional; sent with the video file)

Only if the optional video is made.

| Field | Value |
|---|---|
| Title | Open Slot: an agent that finds acts for a venue's dark nights (Qloo Agentic Hackathon) |
| Description | The elevator pitch above; then "Qloo endpoints used: search, insights, trending, tags."; then the demo and repository links; then "Built for the Qloo Agentic Hackathon. An independent entry, not affiliated with or endorsed by Qloo. The venue and programmer are fictional." |
| Tags | qloo, taste graph, ai agent, mcp, venue booking, live events, hackathon |
| Category | Science & Technology |
| Visibility | **Public**. The rules set none, and Public avoids any access problem |

## Judging criteria — where to look

| Criterion (Official Rules §6) | Where the evidence is |
|---|---|
| Technological Implementation | The README's "Where Qloo comes in" table. The parameter allowlist and its tests. The trace and call strip on the demo page. The planner's calls spent and saved. The caps, the allowance and the ceiling. The gate tests |
| Design | The demo's single flow from room to approved inquiry, every state reachable (no key, ambiguous name, nothing found, cap reached, host asleep). The page at phone width. Keyboard operation and the palette's contrast test |
| Potential Impact | [What it does](#what-it-does), including what bookers use today, and the sample programme on the demo. The one irreversible act, sending an inquiry in the room's name, is the human's |
| Quality of the Idea | Live acts across domains for a room's regulars. Reach read from the room's own acts. Direction spent only where it matters. Evidence with a source on every figure. Two human gates that follow Qloo's own guidance |

## What this entry does not claim

- **No language model bundled.** The agent is an auditable planner of written rules. Any model
  that speaks MCP can drive the same tools, and none can commit through them.
- **No sales forecast.** Qloo reports audience overlap, popularity and trend, not tickets.
- **No price.** Popularity is Qloo's percentile, used as a stand-in for "in reach".
- **No assessment of any performer.** Qloo's figures describe how audiences overlap; they are not an assessment of any performer, and no card combines them into a rating of one.
- **No act is shown as booked, available or interested.** An inquiry is a draft until the
  programmer approves it, and the app never sends it.
- **A gate that holds against the agent's interfaces, not against everything.** A script that
  holds the workspace cookie and also sends a browser's `Sec-Fetch-Site: same-origin` header, a
  program that reads the browser's cookies, or an agent that drives the page by clicking its
  buttons acts as the person.
- **No real venue or programmer.** The room and the person are fictional. The performers are
  real public acts that Qloo resolves, shown only through what Qloo returns, with no endorsement.
- **No Qloo data in the repository.** Tests and the keyless mode use synthetic, invented data.
- **No live Qloo run.** The Qloo API key requested on 17 Oct had not reached this build when it
  was published, so every response shape in the code is the documented one, marked unverified, and
  the demo runs on labelled synthetic sample data.
- **No claim of a tested MCP client.** The endpoint and the stdio relay are tested with a client
  written for the tests; no real client's session has been recorded.
- **No persistence.** The hosted demo keeps a workspace in memory only. It is forgotten after two hours
  of inactivity, and sooner on a free host that sleeps: Render's free tier spins a service down after
  15 minutes without traffic and the memory goes with the process. After that a visitor waits up to a
  minute, then finds an empty workspace, and **Try the sample programme** starts again.

## Submission checklist — where each rule is answered

| Rule (Official Rules §4 and the overview) | Answered by |
|---|---|
| A working application that integrates the Qloo API and functions as an agentic tool (or wires Qloo into an agent) | Four Qloo endpoints behind an MCP-connectable tool registry and a planner. [What it does](#what-it-does) |
| A link to a functional demo application: a live, working app judges can try end to end | [Try it out links](#try-it-out-links) and [Testing instructions](#testing-instructions) |
| Hosted and fully published externally; not local-only, not private | The hosted demo. No login |
| A public repository with an open-source licence visible in the About section | [Try it out links](#try-it-out-links); the MIT licence is at the repository root |
| A text description of the features and what makes it Qloo-powered | [About the project](#about-the-project) |
| Cultural intelligence as the differentiator | Without Qloo there is no catalogue to rank: [Inspiration](#inspiration) |
| Free of charge and without restriction for judges until judging ends | [Testing instructions](#testing-instructions). The caps are stated abuse guards; no login; the demo stays up through at least 18 Nov |
| English | Everything is in English |
| New project, or significantly updated during the period | [Hackathon-specific fields](#hackathon-specific-fields-confirm-on-the-live-form) |
| Qloo's own submission guide: a problem statement, the Qloo workflow and why, a redacted request to result, a demo or screenshots without credentials, setup steps, known limitations | [Inspiration](#inspiration); [How we built it](#how-we-built-it) (the redacted request to result); the gallery; [Testing instructions](#testing-instructions); [What this entry does not claim](#what-this-entry-does-not-claim) |
| A video | Not required; optional |

## Who does what before submitting

**Agent,** in a live session in the owner's Chrome:
- joins the hackathon, requests the API key, and fills the registration and key forms, leaving
  every agreement box for the owner;
- builds and tests the app, publishes the repository and the hosted demo, then walks the demo on
  the live address in a logged-out window;
- reads the live submission form and updates this file to match;
- fills every Devpost field from this file, including links, gallery, thumbnail and built-with
  tags;
- fills each hosted-address marker in this file and in the README with the demo's address;
- runs the judge-panel review and fixes what it finds;
- verifies every checklist line against the live draft, the live demo and the public repository.

**Owner:**
1. Ticked the agreement boxes and clicked **Register** and the key form's **Submit** (done, 17 Oct).
2. In the deploy sitting, creates the host's account if it needs one and clicks the host's create
   button (Render's **Create Web Service**, or **Launch instance** on EC2); types the Qloo key into
   the host only if one has arrived. The guide is `docs/deploy.md` in the repository.
3. Uploads the video to YouTube as **Public** and pastes the link, only if the video is made.
4. Ticks the agreement boxes and clicks **Submit**, before Sat 31 Oct 2026, 09:15 IST.
