import contextlib
import io
import os
import re
import shlex
import shutil
import subprocess
import unittest

from open_slot import __main__ as cli
from open_slot import config, server
from tests.rig import settings
from tests.support import ROOT, fake_key

SCRIPT_PATH = ROOT / "deploy" / "ec2" / "user-data.sh"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
GUIDE = (ROOT / "docs" / "deploy.md").read_text(encoding="utf-8")
EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")
BASH = shutil.which("bash")
HOST = "203-0-113-7.sslip.io"


def defaults(script):
    """The assignments at the top of the script, which are what a person edits."""
    head = script.split("\nsay() {")[0]
    return dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(\S*)", head, re.M))


def env_file(script, values):
    """The lines the script writes into /etc/open-slot.env, with the shell variables filled in."""
    lines = {}
    for found in re.finditer(r"printf '([A-Z_]+)=([^'\\]*)\\n'(?: \"\$([A-Z_]+)\")?", script):
        name, text, variable = found.groups()
        lines[name] = text.replace("%s", values[variable]) if variable else text
    return lines


def tables(markdown, heading):
    """{first header cell: {first cell: second cell}} for the Markdown tables under a heading."""
    section = markdown.split(heading, 1)[1].split("\n## ", 1)[0].split("\n### ", 1)[0]
    found, current = {}, None
    for line in section.splitlines():
        if not line.startswith("|"):
            current = None
            continue
        cells = [cell.strip().replace("`", "") for cell in line.strip().strip("|").split("|")]
        if set(cells[0]) <= set("-: "):
            continue
        if current is None:
            current = found.setdefault(cells[0], {})
            continue
        current[cells[0]] = cells[1]
    return found


def help_flags(command):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
        cli.main([command, "--help"], environ={})
    return set(re.findall(r"--[a-z][a-z-]*", out.getvalue()))


class TheScriptHoldsNoKey(unittest.TestCase):
    def test_the_key_line_is_empty_and_the_only_other_mentions_are_not_values(self):
        lines = [line for line in SCRIPT.splitlines() if re.match(r"QLOO_API_KEY=", line)]
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^QLOO_API_KEY=\s+# <<< ")
        self.assertEqual(defaults(SCRIPT)["QLOO_API_KEY"], "")
        for value in re.findall(r"QLOO_API_KEY=(\S*)", SCRIPT):
            self.assertIn(value, ("", "%s\\n'"))
        self.assertNotRegex(SCRIPT, r"QLOO_API_KEY=[A-Za-z0-9_-]{8,}")
        self.assertNotIn("X-Api-Key", SCRIPT)
        self.assertNotIn("set -x", SCRIPT)

    def test_the_key_goes_only_into_the_root_readable_environment_file(self):
        self.assertIn("( umask 077", SCRIPT)
        self.assertEqual(len(re.findall(r'"\$QLOO_API_KEY"', SCRIPT)), 2)
        for line in SCRIPT.splitlines():
            if "$QLOO_API_KEY" in line:
                self.assertTrue(line.lstrip().startswith(("printf 'QLOO_API_KEY=%s", "if [")), line)
        self.assertIn("> /etc/open-slot.env", SCRIPT)

    def test_the_guide_says_the_key_is_typed_by_the_person_and_shows_no_key(self):
        self.assertIn("you paste it", GUIDE)
        self.assertIn("QLOO_API_KEY=            # <<< the one line to edit", GUIDE)
        self.assertNotRegex(GUIDE, r"QLOO_API_KEY=[A-Za-z0-9_-]{8,}")


@unittest.skipUnless(BASH, "no bash on this machine")
class TheScriptRuns(unittest.TestCase):
    def guard(self, key="", provider=None):
        """The script as far as its guard, run for real, with the owner's edit made in the text."""
        text = SCRIPT.split('say "installing Python')[0].replace("/dev/console", "/dev/null")
        text = re.sub(r"^QLOO_API_KEY=\s+#", f"QLOO_API_KEY={key}            #", text, flags=re.M)
        if provider:
            text = re.sub(r"^PROVIDER=\S+", f"PROVIDER={provider}", text, flags=re.M)
        return subprocess.run([BASH, "-c", text + "\necho reached\n"], capture_output=True, text=True,
                              timeout=20)

    def test_the_syntax_is_valid(self):
        done = subprocess.run([BASH, "-n", str(SCRIPT_PATH)], capture_output=True, text=True, timeout=20)
        self.assertEqual((done.returncode, done.stderr), (0, ""))

    def test_the_script_is_executable_and_starts_as_a_bash_script(self):
        self.assertTrue(os.access(SCRIPT_PATH, os.X_OK))
        self.assertEqual(SCRIPT.splitlines()[0], "#!/bin/bash")
        self.assertIn("\nset -euo pipefail\n", SCRIPT)

    def test_live_with_the_key_line_empty_stops_before_installing_anything(self):
        done = self.guard()
        self.assertEqual(done.returncode, 1)
        self.assertNotIn("reached", done.stdout)
        self.assertIn("the QLOO_API_KEY line is empty; nothing was installed", done.stdout)

    def test_a_key_typed_after_the_equals_sign_gets_past_the_guard_and_is_never_echoed(self):
        key = fake_key()
        done = self.guard(key)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "reached"))
        self.assertNotIn(key, done.stdout + done.stderr)

    def test_fake_needs_no_key_and_anything_else_is_refused(self):
        done = self.guard(provider="fake")
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "reached"))
        done = self.guard(provider="both")
        self.assertEqual(done.returncode, 1)
        self.assertIn("PROVIDER must be live or fake", done.stdout)


class TheEnvironmentTheScriptWrites(unittest.TestCase):
    def values(self, **changes):
        return {**defaults(SCRIPT), "QLOO_API_KEY": fake_key(), "HOST": HOST, **changes}

    def test_it_loads_as_the_settings_of_a_server_behind_caddy(self):
        lines = env_file(SCRIPT, self.values())
        self.assertEqual(set(lines), {"QLOO_API_KEY", "OPEN_SLOT_PROVIDER", "OPEN_SLOT_ALLOWED_HOSTS",
                                      "OPEN_SLOT_TRUST_PROXY", "OPEN_SLOT_DAILY_LIVE_CALLS"})
        loaded = config.load(environ=lines, cwd="/nonexistent", serving=True)
        self.assertEqual(loaded.bind, "127.0.0.1")
        self.assertEqual(loaded.port, config.DEFAULT_PORT)
        self.assertEqual(loaded.extra_hosts, (HOST,))
        self.assertTrue(loaded.trust_proxy)
        self.assertEqual((loaded.provider, loaded.daily_live_calls), ("live", 600))
        self.assertEqual(loaded.key, fake_key())

    def test_it_sets_neither_a_bind_nor_a_port_so_the_server_stays_on_loopback_behind_caddy(self):
        names = set(env_file(SCRIPT, self.values()))
        self.assertFalse(names & {"OPEN_SLOT_BIND", "OPEN_SLOT_PORT", "PORT", "RENDER_EXTERNAL_HOSTNAME"})

    def test_every_name_it_writes_is_one_the_example_settings_list(self):
        listed = set(re.findall(r"^([A-Z][A-Z_]*)=", EXAMPLE, re.M))
        self.assertLessEqual(set(env_file(SCRIPT, self.values())), listed)

    def test_the_rehearsal_provider_loads_without_a_key(self):
        lines = env_file(SCRIPT, self.values(QLOO_API_KEY="", PROVIDER="fake"))
        loaded = config.load(environ=lines, cwd="/nonexistent", serving=True)
        self.assertEqual((loaded.provider, loaded.key), ("fake", None))

    def test_the_allowed_host_is_the_name_caddy_serves_and_the_page_is_reached_through_it(self):
        self.assertIn('HOST="${IP//./-}.$DNS_NAME"', SCRIPT)
        self.assertEqual(defaults(SCRIPT)["DNS_NAME"], "sslip.io")
        caddyfile = re.search(r"printf '(%s \{\\n[^']*)' \"\$HOST\"", SCRIPT).group(1)
        self.assertEqual(caddyfile.replace("\\n", "\n").replace("%s", HOST),
                         f"{HOST} {{\n    reverse_proxy 127.0.0.1:{config.DEFAULT_PORT}\n}}\n")
        shown = settings(extra_hosts=(HOST,))
        app = server.App(shown, None)
        answer = app.handle(server.Request("GET", "/healthz", {"host": "anything.example"}))
        self.assertEqual((answer.status, answer.body), (200, b"ok\n"))
        self.assertEqual(app._host(server.Request("GET", "/", {"host": HOST})).origin, f"https://{HOST}")


class TheServices(unittest.TestCase):
    def test_the_app_unit_starts_serve_with_a_python_that_is_new_enough_and_no_flag_that_does_not_exist(self):
        exec_start = re.search(r"^ExecStart=(/usr/bin/python\$PYV .*)$", SCRIPT, re.M).group(1)
        words = shlex.split(exec_start)
        self.assertEqual(words[1:4], ["-m", "open_slot", "serve"])
        self.assertLessEqual(set(a for a in words[4:] if a.startswith("-")), help_flags("serve"))
        versions = re.search(r"for v in ([0-9. ]+); do", SCRIPT).group(1).split()
        self.assertEqual(versions, ["3.13", "3.12", "3.11"])
        for version in versions:
            self.assertGreaterEqual(tuple(map(int, version.split("."))), (3, 11))
        self.assertIn('say "$("/usr/bin/python$PYV" --version) at /usr/bin/python$PYV"', SCRIPT)

    def test_the_app_unit_runs_as_its_own_user_in_the_directory_whose_cache_it_may_write(self):
        self.assertIn("User=openslot", SCRIPT)
        self.assertIn("WorkingDirectory=/opt/open-slot", SCRIPT)
        self.assertIn("EnvironmentFile=/etc/open-slot.env", SCRIPT)
        self.assertEqual(cli.CACHE_DIR.name, ".cache")
        self.assertIn("chown openslot:openslot /opt/open-slot/.cache", SCRIPT)

    def test_caddy_is_pinned_and_checked_before_it_is_run_and_is_the_only_thing_on_80_and_443(self):
        values = defaults(SCRIPT)
        self.assertRegex(values["CADDY_VERSION"], r"^2\.\d+\.\d+$")
        self.assertRegex(values["CADDY_SHA512"], r"^[0-9a-f]{128}$")
        check = SCRIPT.index('sha512sum -c -')
        self.assertLess(check, SCRIPT.index("tar -xzf /tmp/caddy.tar.gz"))
        self.assertLess(check, SCRIPT.index("systemctl enable"))
        self.assertIn("CAP_NET_BIND_SERVICE", SCRIPT)
        self.assertIn(values["CADDY_VERSION"], GUIDE)

    def test_the_metadata_token_is_asked_for_before_the_address_is_read(self):
        token = SCRIPT.index("-X PUT http://169.254.169.254/latest/api/token")
        address = SCRIPT.index("meta-data/public-ipv4")
        self.assertLess(token, address)
        self.assertIn('-H "X-aws-ec2-metadata-token: $TOKEN"', SCRIPT)

    def test_the_services_report_to_the_console_because_there_is_no_other_way_to_read_them(self):
        self.assertEqual(SCRIPT.count("StandardOutput=journal+console"), 2)
        self.assertEqual(SCRIPT.count("StandardError=journal+console"), 2)
        self.assertIn("certificate ok after", SCRIPT)


class TheRenderValues(unittest.TestCase):
    FORM = tables(GUIDE, "### The form, field by field")

    def test_the_form_is_the_free_python_service_with_the_health_check_the_server_answers(self):
        form = self.FORM["Field"]
        self.assertEqual(form["Language"], "Python 3")
        self.assertEqual(form["Compute"], "Free")
        self.assertEqual(form["Build Command"], "true")
        self.assertEqual(form["Branch"], "main")
        self.assertEqual(form["Root Directory"], "empty")
        self.assertEqual(form["Name"], "open-slot-qloo")
        self.assertEqual(form["Health Check Path (Advanced)"], "/healthz")
        self.assertTrue(form["Auto-Deploy"].startswith("On Commit"))
        app = server.App(settings(), None)
        for method in ("GET", "HEAD"):
            self.assertEqual(app.handle(server.Request(method, "/healthz", {})).status, 200)

    def test_the_start_command_is_serve_with_no_port_and_only_flags_that_exist(self):
        words = shlex.split(self.FORM["Field"]["Start Command"])
        self.assertIn(words[0], ("python", "python3"))
        self.assertEqual(words[1:4], ["-m", "open_slot", "serve"])
        self.assertLessEqual(set(a for a in words[4:] if a.startswith("-")), help_flags("serve"))
        self.assertNotIn("--port", words)

    def test_the_variables_load_as_the_settings_of_a_server_bound_for_the_hosts_proxy(self):
        rows = self.FORM["Key"]
        self.assertEqual(set(rows), {"QLOO_API_KEY", "OPEN_SLOT_PROVIDER", "OPEN_SLOT_BIND", "PORT",
                                     "OPEN_SLOT_TRUST_PROXY", "OPEN_SLOT_DAILY_LIVE_CALLS"})
        self.assertTrue(rows["QLOO_API_KEY"].startswith("you paste it"))
        environ = {**rows, "QLOO_API_KEY": fake_key(), "RENDER_EXTERNAL_HOSTNAME": "open-slot.onrender.com"}
        loaded = config.load(environ=environ, cwd="/nonexistent", serving=True)
        self.assertEqual((loaded.bind, loaded.port), ("0.0.0.0", 10000))
        self.assertEqual(loaded.extra_hosts, ("open-slot.onrender.com",))
        self.assertTrue(loaded.trust_proxy)
        self.assertEqual((loaded.provider, loaded.daily_live_calls), ("live", 600))

    def test_without_the_hosts_own_name_the_server_will_not_start_and_names_the_setting(self):
        environ = {**self.FORM["Key"], "QLOO_API_KEY": fake_key()}
        with self.assertRaises(config.ConfigError) as caught:
            config.load(environ=environ, cwd="/nonexistent", serving=True)
        self.assertEqual(caught.exception.setting, "OPEN_SLOT_ALLOWED_HOSTS")

    def test_the_python_the_host_is_told_to_use_is_one_the_app_supports(self):
        pinned = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
        self.assertGreaterEqual(tuple(map(int, pinned.split("."))), (3, 11))
        self.assertIn("`.python-version`", GUIDE)
        self.assertNotIn("PYTHON_VERSION", self.FORM["Key"])

    def test_what_is_left_unset_on_purpose_is_said(self):
        self.assertIn("Left unset on purpose: `OPEN_SLOT_ALLOWED_HOSTS`", GUIDE)
        self.assertIn("RENDER_EXTERNAL_HOSTNAME", GUIDE)


class TheGuideAndTheServerAgree(unittest.TestCase):
    def test_the_security_group_rules_are_the_two_ports_caddy_needs(self):
        wizard = tables(GUIDE, "### The launch wizard, field by field")["Field"]
        self.assertEqual(wizard["Security group"],
                         "new, open-slot-sg; inbound TCP 80 and TCP 443 from 0.0.0.0/0; no SSH")
        self.assertEqual(wizard["Key pair"], "Proceed without a key pair")
        self.assertEqual(wizard["Advanced details, Metadata version"], "V2 only (token required)")
        self.assertIn("ap-southeast-2", GUIDE)

    def test_every_setting_the_guide_names_is_one_the_server_reads(self):
        named = set(re.findall(r"`((?:OPEN_SLOT|QLOO|RENDER)_[A-Z_]+|PORT)`", GUIDE))
        known = set(re.findall(r"^[# ]*([A-Z][A-Z_]*)=", EXAMPLE, re.M))
        known |= {"RENDER_EXTERNAL_HOSTNAME", "PORT"}
        self.assertLessEqual(named, known)

    def test_the_state_fields_the_guide_tells_the_reader_to_check_exist(self):
        source = (ROOT / "open_slot" / "server.py").read_text(encoding="utf-8")
        for field in ("actorWhy", "limiterKey", "actor", "no_fetch_metadata", "typed_address", "same_origin"):
            self.assertIn(field, source)
            self.assertIn(field, GUIDE)

    def test_the_guide_names_no_file_the_published_copy_lacks(self):
        for found in re.findall(r"\]\((\.\./[^)#]+)", GUIDE):
            self.assertTrue((ROOT / "docs" / found).resolve().exists(), found)
        self.assertTrue(SCRIPT_PATH.exists())


if __name__ == "__main__":
    unittest.main()
