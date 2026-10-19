import dataclasses
import json
from datetime import datetime, timedelta, timezone

from open_slot import config, invoke, providers
from tests.support import ROOT, fake_key

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
TAG = "urn:tag:synthetic:stand-up"
GONE = "00000000-0000-4000-8000-0000000fffff"


def scenario(name):
    return json.loads((ROOT / "scenarios" / f"{name}.json").read_text(encoding="utf-8"))


THIN = {
    "label": "The Thin Lens (fictional)",
    "nights": ["Fri 6 Nov", "Fri 13 Nov"],
    "anchors": ["Fallowmere Strings", "Greywether Bell"],
    "lenses": ["music"],
}


FAKE = providers.FakeProvider()


def serve_fake(request):
    """A loopback stub's answer: the synthetic fixtures, as Qloo would send them."""
    status, body = FAKE.respond(request["path"], request["query"])
    return status, body, {}


def settings(**changes):
    return dataclasses.replace(config.load(environ={}, cwd="/nonexistent"), **changes)


class Rig:
    """A runtime with one workspace, on the fake provider unless given another."""

    def __init__(self, engine=None, ledger=None, fallback=None, **changes):
        self.settings = settings(**changes)
        self.ledger = ledger or providers.Ledger(self.settings.daily_live_calls,
                                                 self.settings.agent_calls_per_day,
                                                 self.settings.cache_ttl_s)
        self.runtime = invoke.Runtime(self.settings, engine or providers.FakeProvider(), self.ledger,
                                      clock=lambda: NOW, fallback=fallback)
        self.workspace = self.runtime.new_workspace()

    @classmethod
    def live(cls, stub, fallback=None, **changes):
        """A rig whose provider is the live one, talking to a loopback stub, with sleeps recorded."""
        changes = {"key": fake_key(), "provider": "live", **changes}
        chosen = settings(**changes)
        ledger = providers.Ledger(chosen.daily_live_calls, chosen.agent_calls_per_day, chosen.cache_ttl_s)
        sleeps = []
        provider = providers.LiveProvider(chosen.key, ledger, stub.base_url, sleep=sleeps.append)
        rig = cls(provider, ledger, fallback, **changes)
        rig.sleeps = sleeps
        return rig

    def call(self, tool, args=None, actor="agent", surface="page"):
        return invoke.invoke(self.runtime, self.workspace, tool, args or {}, actor, surface)

    def ok(self, tool, args=None, actor="agent"):
        envelope = self.call(tool, args, actor)
        assert envelope["ok"], envelope
        return envelope["result"]

    def refused(self, tool, args=None, actor="agent", surface="page"):
        envelope = self.call(tool, args, actor, surface)
        assert not envelope["ok"], envelope
        assert envelope["outcome"] == envelope["error"]["code"]
        assert envelope["error"]["message"]
        return envelope["error"]["code"]

    def open(self, args):
        return self.ok("open_programme", args)["programme_id"]

    def search(self, programme_id, actor="agent"):
        names = [a["name"] for a in self.programme(programme_id)["anchors"]]
        return {name: self.ok("find_anchor", {"programme_id": programme_id, "name": name},
                              actor)["candidates"] for name in names}

    def confirm_top(self, programme_id):
        for name, candidates in self.search(programme_id).items():
            if candidates:
                self.ok("confirm_anchor", {"programme_id": programme_id, "name": name,
                                           "entity_id": candidates[0]["entityId"]}, "person")

    def ready(self, args=None):
        """A programme with every anchor found and confirmed, ready for plan_acts."""
        programme_id = self.open(args or scenario("lantern-room"))
        self.confirm_top(programme_id)
        return programme_id

    def planned(self, args=None):
        programme_id = self.ready(args)
        self.ok("plan_acts", {"programme_id": programme_id})
        return programme_id

    def programme(self, programme_id):
        return self.ok("get_programme", {"programme_id": programme_id})

    def held(self, programme_id, name):
        return next(c for c in self.programme(programme_id)["candidates"] if c["name"] == name)

    def ready_ids(self, programme_id):
        return [c["entityId"] for c in self.stored(programme_id)["candidates"] if c["status"] == "ready"]

    def stored(self, programme_id):
        """The programme without going through invoke, so reading it adds nothing to any log."""
        from open_slot import store
        return store.programme(self.runtime.doc, self.workspace, programme_id)

    def activity(self):
        from open_slot import store
        return store.workspace(self.runtime.doc, self.workspace)["activityLog"]
