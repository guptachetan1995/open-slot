# Open Slot demo video — script and shot list

The narration, shot list and on-screen sources for the optional demo video. It is rendered as a
frame-exact slideshow: each still is a real screenshot of the running app, reached by clicking
the same buttons a person clicks (or, for the agent's beats, by the page's own agent console),
and held for its scripted duration, with synthesized narration laid over it. Nothing on screen is
typed in by hand except what a person would type: the sender's name, and the agent console's
tool name and arguments.

## What the video shows, and what it does not

- **Synthetic sample data only.** The app runs locally with `OPEN_SLOT_PROVIDER=fake`, no Qloo
  key in its environment and no `.env` read. The performers are invented stand-ins for the names
  typed; the venue and the programmer are fictional. Qloo is not called.
- The page's own banner, "Synthetic sample data. Qloo is not being called. No Qloo key is set on
  this server.", is kept in view at the top of every app still. The narration says it in beat 1,
  and the end card says it again.
- The narration never describes a result as live Qloo data. Figures on screen carry the app's
  own labels ("Qloo's explanation score", "Qloo's popularity percentile"), which name the field
  the live app reads; on this run every value behind them is synthetic, as the banner says, and
  the tags read "(synthetic)". The drafted inquiry says so in its own text: "Sample taste data,
  standing in for Qloo's, connects …".
- No key, cookie or agent address is on screen: the "Connect your own agent" panel stays closed,
  and every still fails the render if anything shaped like the agent address's token is visible.

## Target runtime

**116.52 s (1:56.5) across 9 beats and 17 stills.** The Qloo hackathon asks for no video and sets
no cap; this plan's ceiling is 2:00. Each sentence is synthesised first and measured, and a still
lasts as long as its sentences plus fixed pauses (0.2 s lead, 0.3 s between sentences, 0.4 s
tail), rounded up to whole 40 ms frames, so the rendered file matches the shot list exactly.
Voice: the macOS system voice at `-r 140`, stretched to tempo 0.88 (the slowest that fits under
1:57). "Qloo" is spoken as "clue".

## Framing

Presentation only, never app state: the page is zoomed to 130 %, the data banner (and the status
line, when it says something) is held at the top, the page is scrolled to what the narration is
about, the element it names is outlined in orange, and a caption bar runs along the bottom.

## Beats

Times are from the start of the video.

| # | Time | Still(s): what is on screen | How it is reached | Caption | Narration |
|---|---|---|---|---|---|
| 1 | 0:00–0:18.5 | (a) The empty room form, "Try the sample programme" outlined. (b) The Lantern Room (fictional), its three open Tuesdays and the line "This programme runs on synthetic sample data: invented performers. Qloo is not called." outlined | Load the page; click **Try the sample programme** (the page searches each of the four names) | "Three dark Tuesdays. Who should we ask?" / "Sample data: invented performers. Qloo is not called." | Maya books a three-hundred-seat room. Three Tuesdays in November are dark. She knows four acts that sold it. She needs who to ask next, with evidence. This demo runs on built-in sample data: invented performers, and no call to Qloo. |
| 2 | 0:18.5–0:29.2 | (a) Confirm the acts: "The Milk Carton Kids" with two matches, one a look-alike, nothing chosen. (b) Four confirmed acts, **Run the agent** enabled | Click **Yes, this is the act I mean** on the first match of each of the four names | "The agent proposes a match. Only you confirm it." / "Four clicks by the person: four confirmed acts." | The agent searched each name. Only Maya confirms which match she means, and nothing is picked for her. Four clicks, and the agent can run. |
| 3 | 0:29.2–0:47.0 | (a) The trace: the three wide `/v2/insights` calls (music, podcast, comedy after the `/v2/tags` lookup) outlined. (b) The triage note, four `/v2/trending` calls and "stopped: 4 candidates are ready for 3 nights; 8 calls not spent" outlined | Click **Run the agent** | "One wide call per kind of act." / "Depth only where it can change the answer." | One wide call per kind of act: music, podcasts, and comedy, after a stand-up tag lookup. Triage costs no call. Trend checks go only to the strongest four, then it stops, eight calls unspent. Every line says why. |
| 4 | 0:47.0–1:06.0 | (a) The first card, Hollowmere Radio: which of the four acts it connects to, with scores. (b) Its popularity, the room's range and the trend, each with its source line. (c) The set-aside list, opened, each act with its reason | Scroll; open "Set aside, each with its reason" | "Evidence, not a score." / "Every number says whose it is: Qloo's, or this app's rule." / "Set aside, each with its reason. Nothing is hidden." | Each card shows evidence, not a score: which of her acts it connects to, and how strongly. Popularity against the room's range, and the weekly trend, each labelled Qloo's figure or the app's rule. Acts out of range, or weakly linked, are set aside with a reason. |
| 5 | 1:06.0–1:15.6 | (a) Nights: a band, a comedian and a podcast proposed, none assigned. (b) Tue 3 Nov: "Assigned … assigned by you. Nothing is booked." | Click **Propose nights**; click **Assign** on the first night | "The agent proposes. Only you assign." / "Assigned by the person. Nothing is booked." | The agent proposes a band, a comedian and a podcast for the three nights. Only Maya assigns one. Nothing is booked. |
| 6 | 1:15.6–1:27.0 | (a) The agent console: `draft_inquiry`, "draft_inquiry: ok (ok)." (b) The draft on the page, "DRAFT, not approved", its text reading "Sample taste data, standing in for Qloo's, connects …" | Open **Agent console**; type `draft_inquiry` and its arguments; click **Run as the agent** | "The agent drafts, through the same chokepoint as the page." / "Its draft lands on the page. It never sends." | The agent uses the same chokepoint as the page. From its console, it drafts an inquiry. The draft lands on Maya's page, marked DRAFT. |
| 7 | 1:27.0–1:37.0 | The agent console: "approve_inquiry: refused, not_a_tool. That name is not one of the tools this surface offers." | Click **Try approve_inquiry as the agent** | "The agent has no approve tool: not_a_tool." | Can the agent approve it? approve_inquiry is refused: not a tool. Human verbs are never registered, for any MCP agent. |
| 8 | 1:37.0–1:44.2 | (a) Sender name "Maya" typed, **Approve inquiry** outlined. (b) "Approved", "Approved by Maya …", the frozen text | Type the sender; click **Approve inquiry** | "Only you approve, after naming the sender." / "Approved. The app still sends nothing." | Maya names herself as the sender, and approves. Even then, the app sends nothing. |
| 9 | 1:44.2–1:56.5 | End card: Open Slot; the public repository (MIT); `python3 -m open_slot serve`, then "click Try the sample programme"; connect your own agent over MCP; "This video shows synthetic sample data: invented performers. Qloo was not called."; built for the Qloo Agentic Hackathon, not affiliated with or endorsed by Qloo | A local card | none | To try it, clone the repo, run one command, and click Try the sample programme. Or connect your own MCP agent. Links are in the description. |

## The file

- `final-narrated.mp4`: H.264 1920 × 1080 at 25 fps and AAC 44.1 kHz, both streams 116.520 s,
  loudness −16 LUFS. 3,963,139 bytes, sha256
  `c585e7396f09ab4ed3263a535897910ed5e0bc5c16142ba607bc272e047e36d7` (rendered 30 Oct 2026, after
  the sample-data wording of the drafted inquiry changed).
- A subtitle sidecar, `final-narrated.srt` (38 cues), is rendered with it for the YouTube upload.
- A re-render gives a different file: the approval still shows the time it was approved.
