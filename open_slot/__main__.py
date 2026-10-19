import argparse
import json
import sys
from pathlib import Path

from . import config, invoke, mcp, probe, providers, server
from .providers import AGENT, PERSON

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache"
SAMPLE = ROOT / "scenarios" / "lantern-room.json"
MODES = {
    "fake": "synthetic sample data, Qloo is not being called",
    "live": "live: Qloo is being called",
}


def say(text=""):
    sys.stdout.write(text + "\n")


def describe(call):
    params, bits = call["params"], []
    if "filter.type" in params:
        bits.append(params["filter.type"].rsplit(":", 1)[-1])
    if "types" in params:
        bits.append("types " + ",".join(t.rsplit(":", 1)[-1] for t in params["types"].split(",")))
    if "query" in params or "filter.query" in params:
        bits.append(f"\"{params.get('query') or params['filter.query']}\"")
    if "filter.tags" in params:
        bits.append("tag filter")
    if "filter.popularity.min" in params:
        bits.append(f"popularity {params['filter.popularity.min']} to {params['filter.popularity.max']}")
    if "signal.interests.entities" in params:
        bits.append(f"{params['signal.interests.entities'].count(',') + 1} id(s)")
    return f"{call['path']} {' '.join(bits)}"


def trace_line(step):
    if step["kind"] != "call":
        return f"{step['step']:>3}  {step['kind']:<5} {step['because']}"
    where = f"[{step['cache']}] {describe(step['request'])}"
    return f"{step['step']:>3}  call  {step['tool']} ({step['actor']}) {where}: {step['because']}"


def refused(envelope):
    error = envelope["error"]
    sys.stderr.write(f"open-slot: refused: {error['code']}: {error['message']}\n")
    return 1


def summary(result):
    calls = result["calls"]
    say()
    say(f"calls: {calls['spent_in_total']} of {calls['cap']} spent in all ({calls['spent_by_this_plan']} by "
        f"this plan), {calls['not_spent']} not spent; stopped: {result['stopped_because']}")
    if result["range"]:
        say(f"room's range: Qloo popularity {result['range']['min']:.2f} to {result['range']['max']:.2f}")
    say("board, best first:")
    for place, card in enumerate(result["board"], 1):
        trend = (card["trend"] or {}).get("state", "not checked")
        say(f"  {place}. {card['name']} [{card['lens']}] {card['status']}: connects to {len(card['links'])} "
            f"acts, popularity {card['popularity']['value']}, trend {trend}")
    counts = result["counts"]
    say(f"also connected: {len(result['also_connected'])}; set aside: {len(result['set_aside'])} "
        f"({counts['outside_band']} outside the range, {counts['weak_link']} with too few links)")


def run_plan(args, environ=None):
    settings = config.load_or_exit(environ=environ)
    name = args.provider or settings.provider
    if name == "live" and not settings.key:
        sys.stderr.write("open-slot: key: missing. Set QLOO_API_KEY or OPEN_SLOT_ENV_FILE, "
                         "or run with --provider fake\n")
        return 2
    try:
        scenario = json.loads(Path(args.scenario).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sys.stderr.write("open-slot: the scenario file cannot be read as JSON\n")
        return 2
    runtime = invoke.Runtime.build(settings, name, directory=CACHE_DIR if name == "live" else None)
    workspace = runtime.new_workspace()
    actor = AGENT if args.actor == AGENT else PERSON
    surface = "agent" if actor == AGENT else "page"

    def call(tool, tool_args):
        return invoke.invoke(runtime, workspace, tool, tool_args, actor, surface)

    opened = call("open_programme", scenario)
    if not opened["ok"]:
        return refused(opened)
    programme_id = opened["result"]["programme_id"]
    say(f"open-slot plan: {scenario['label']} ({MODES[name]}); acting as the {actor}")
    for typed in scenario["anchors"]:
        found = call("find_anchor", {"programme_id": programme_id, "name": typed})
        if not found["ok"]:
            return refused(found)
        top = found["result"]["candidates"][:1]
        say(f"  {typed}: {len(found['result']['candidates'])} candidate(s)"
            + (f", top match {top[0]['name']}" if top else ""))
        if top and actor == PERSON:
            confirmed = call("confirm_anchor", {"programme_id": programme_id, "name": typed,
                                                "entity_id": top[0]["entityId"]})
            say("    confirmed by you at this terminal" if confirmed["ok"] else "    not confirmed")
    planned = call("plan_acts", {"programme_id": programme_id})
    if not planned["ok"]:
        return refused(planned)
    say("trace:")
    for step in call("get_programme", {"programme_id": programme_id})["result"]["trace"]:
        say(trace_line(step))
    summary(planned["result"])
    return 0


def run_serve(args, environ=None):
    settings = config.load_or_exit(serving=True, environ=environ)
    name = args.provider or settings.provider
    if name == "live" and not settings.key:
        sys.stderr.write("open-slot: key: missing. Set QLOO_API_KEY or OPEN_SLOT_ENV_FILE, "
                         "or run with --provider fake\n")
        return 2
    runtime = invoke.Runtime.build(settings, name, directory=CACHE_DIR if name == "live" else None,
                                   fallback=providers.FakeProvider() if name == "live" else None)
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    app = server.App(settings, runtime, sample)
    try:
        httpd = server.make_server(app, settings.bind, settings.port)
    except OSError:
        sys.stderr.write(f"open-slot: cannot listen on {settings.bind} port {settings.port}; "
                         "is something else using it?\n")
        return 2
    say(f"open-slot: {MODES[name]}")
    if settings.bind in config.LOOPBACK_BINDS:
        host = "[::1]" if ":" in settings.bind else settings.bind
        say(f"open-slot: open http://{host}:{app.port}/ (Ctrl-C stops the server)")
    else:
        say(f"open-slot: listening on {settings.bind}:{app.port}, for these hosts: "
            f"{', '.join(settings.extra_hosts)}; TLS must be terminated outside")
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        say("open-slot: stopped")
    finally:
        httpd.server_close()
    return 0


def run_budget(args, environ=None):
    settings = config.load_or_exit(environ=environ)
    shown = providers.Ledger(settings.daily_live_calls, settings.agent_calls_per_day, settings.cache_ttl_s,
                             directory=CACHE_DIR).snapshot()
    say(f"key: {settings.key_status}")
    say(f"provider: {settings.provider} ({MODES[settings.provider]})")
    say(f"per-programme cap: {settings.max_calls} live calls; only a person can raise it, to "
        f"{config.MAX_CALLS_RAISED}")
    say(f"agent allowance: {settings.agent_calls_per_day} live calls in any 24 hours for each workspace; "
        "kept in memory by a running server")
    daily = shown["daily"]
    say(f"server ceiling for {daily['day']} (UTC): {daily['used']} of {daily['limit']} live calls "
        "made from this directory")
    say(f"cache: {shown['cache']['entries']} responses kept for up to {shown['cache']['ttlSeconds']} seconds")
    return 0


def run_mcp(args):
    try:
        endpoint = mcp.agent_endpoint(args.address)
    except ValueError as problem:
        sys.stderr.write(f"open-slot: {problem}\n")
        return 2
    try:
        return mcp.relay(endpoint, sys.stdin.buffer, sys.stdout.buffer)
    except (BrokenPipeError, KeyboardInterrupt):
        return 0


def run_probe(args):
    if len(args.name) != 3:
        sys.stderr.write("open-slot: give exactly three --name values: a musician, a comedian, a podcast\n")
        return 2
    settings = config.load_or_exit()
    if not settings.key:
        sys.stderr.write("open-slot: the probe needs a key: set QLOO_API_KEY or OPEN_SLOT_ENV_FILE\n")
        return 2
    report = probe.run(settings.key, args.name)
    sys.stdout.write(probe.format_report(report))
    if args.notes:
        Path(args.notes).write_text(probe.render_notes(report), encoding="utf-8")
    return 0


def main(argv=None, environ=None):
    parser = argparse.ArgumentParser(prog="open_slot")
    commands = parser.add_subparsers(dest="command", required=True)
    shapes = commands.add_parser(
        "probe",
        help="make at most 14 live calls and print response shapes, counts and thresholds, never a value",
    )
    shapes.add_argument("--name", action="append", default=[],
                        help="a public performer: first a musician, then a comedian, then a podcast")
    shapes.add_argument("--notes", metavar="PATH", help="also write the report as Markdown to this file")
    planning = commands.add_parser(
        "plan", help="open a sample programme, search its acts, and run the planner; prints the trace")
    planning.add_argument("scenario", help="a scenario file such as scenarios/lantern-room.json")
    planning.add_argument("--provider", choices=sorted(MODES), help="fake: synthetic data; live: needs a key")
    planning.add_argument(
        "--as", dest="actor", choices=(PERSON, AGENT), default=PERSON,
        help="person (default): you confirm each name's top match at this terminal, then the planner runs; "
             "agent: only the agent's tools run, so the plan is refused until a person confirms")
    serving = commands.add_parser(
        "serve", help="serve the page, the agent address and the MCP endpoint; prints the address to open")
    serving.add_argument("--provider", choices=sorted(MODES), help="fake: synthetic data; live: needs a key")
    commands.add_parser("budget", help="the limits, the key's status and the calls made from this directory")
    relaying = commands.add_parser(
        "mcp", help="relay an agent address to a client that speaks MCP over stdio: "
                    "one JSON-RPC message per line")
    relaying.add_argument(
        "--address", required=True, metavar="URL",
        help="https://<host>/agent/<token>, as the page shows it under 'Connect your own agent'; "
             "http://127.0.0.1:<port>/agent/<token> for a local server")
    args = parser.parse_args(argv)
    if args.command == "plan":
        return run_plan(args, environ)
    if args.command == "serve":
        return run_serve(args, environ)
    if args.command == "budget":
        return run_budget(args, environ)
    if args.command == "mcp":
        return run_mcp(args)
    return run_probe(args)


if __name__ == "__main__":
    raise SystemExit(main())
