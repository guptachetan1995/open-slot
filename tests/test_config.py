import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from open_slot import config
from tests.support import fake_key


class Layers(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.cwd = Path(self.dir.name)
        self.addCleanup(self.dir.cleanup)

    def load(self, environ, **kwargs):
        return config.load(environ=environ, cwd=self.cwd, **kwargs)

    def test_defaults_with_an_empty_environment(self):
        cfg = self.load({})
        self.assertEqual((cfg.bind, cfg.port, cfg.provider, cfg.key), ("127.0.0.1", 8760, "fake", None))
        self.assertEqual((cfg.max_calls, cfg.agent_calls_per_day, cfg.daily_live_calls), (16, 48, 600))
        self.assertEqual(cfg.key_status, "missing")
        self.assertFalse(cfg.trust_proxy)

    def test_the_named_file_beats_dot_env_beats_the_environment(self):
        (self.cwd / ".env").write_text("OPEN_SLOT_MAX_CALLS=20\nOPEN_SLOT_DAILY_LIVE_CALLS=300\n")
        named = self.cwd / "named.env"
        named.write_text("OPEN_SLOT_MAX_CALLS=30\n")
        environ = {"OPEN_SLOT_MAX_CALLS": "10", "OPEN_SLOT_DAILY_LIVE_CALLS": "100", "OPEN_SLOT_PORT": "9001"}
        self.assertEqual(self.load(environ).max_calls, 20)
        self.assertEqual(self.load(environ).daily_live_calls, 300)
        pointed = self.load({**environ, "OPEN_SLOT_ENV_FILE": str(named)})
        self.assertEqual(pointed.max_calls, 30)
        self.assertEqual(pointed.daily_live_calls, 100, "dot env is not read when a file is named")
        self.assertEqual(pointed.port, 9001, "the environment fills what the file leaves out")

    def test_an_empty_value_counts_as_unset(self):
        (self.cwd / ".env").write_text("QLOO_API_KEY=\nOPEN_SLOT_MAX_CALLS=\n")
        cfg = self.load({"QLOO_API_KEY": fake_key()})
        self.assertEqual(cfg.key, fake_key())
        self.assertEqual(cfg.max_calls, 16)

    def test_file_syntax(self):
        (self.cwd / ".env").write_text(
            "# a comment\n\nexport OPEN_SLOT_BIND = 127.0.0.1\nOPEN_SLOT_MAX_CALLS='12'\nnot a setting\n"
        )
        self.assertEqual(self.load({}).max_calls, 12)

    def test_a_named_file_that_is_missing_is_refused(self):
        with self.assertRaises(config.ConfigError) as caught:
            self.load({"OPEN_SLOT_ENV_FILE": str(self.cwd / "absent.env")})
        self.assertEqual(caught.exception.setting, "OPEN_SLOT_ENV_FILE")

    def test_the_provider_follows_the_key_unless_set(self):
        self.assertEqual(self.load({"QLOO_API_KEY": fake_key()}).provider, "live")
        keyed_but_fake = {"QLOO_API_KEY": fake_key(), "OPEN_SLOT_PROVIDER": "fake"}
        self.assertEqual(self.load(keyed_but_fake).provider, "fake")
        self.assertEqual(self.load({"OPEN_SLOT_PROVIDER": "live"}).provider, "live")
        with self.assertRaises(config.ConfigError):
            self.load({"OPEN_SLOT_PROVIDER": "both"})


class Ports(unittest.TestCase):
    def load(self, environ, **kwargs):
        return config.load(environ=environ, cwd="/nonexistent", **kwargs)

    def test_port_wins_over_open_slot_port_which_wins_over_the_default(self):
        self.assertEqual(self.load({}).port, 8760)
        self.assertEqual(self.load({"OPEN_SLOT_PORT": "9001"}).port, 9001)
        self.assertEqual(self.load({"OPEN_SLOT_PORT": "9001", "PORT": "10000"}).port, 10000)

    def test_a_bad_value_is_refused_naming_the_setting(self):
        for name in ("PORT", "OPEN_SLOT_PORT"):
            for bad in ("abc", "0", "65536", "-1", "80.5"):
                with self.assertRaises(config.ConfigError, msg=f"{name}={bad}") as caught:
                    self.load({name: bad})
                self.assertEqual(caught.exception.setting, name)
                self.assertIn(name, str(caught.exception))

    def test_a_bad_port_is_refused_even_when_the_other_one_is_fine(self):
        with self.assertRaises(config.ConfigError) as caught:
            self.load({"OPEN_SLOT_PORT": "9001", "PORT": "x"})
        self.assertEqual(caught.exception.setting, "PORT")

    def test_other_numbers_have_bounds(self):
        with self.assertRaises(config.ConfigError):
            self.load({"OPEN_SLOT_MAX_CALLS": "41"})
        with self.assertRaises(config.ConfigError):
            self.load({"OPEN_SLOT_MAX_CALLS": "0"})
        self.assertEqual(self.load({"OPEN_SLOT_MAX_CALLS": "40"}).max_calls, 40)


class Hosts(unittest.TestCase):
    def load(self, environ, **kwargs):
        return config.load(environ=environ, cwd="/nonexistent", **kwargs)

    def test_the_loopback_names_always_pass_and_the_rest_are_added(self):
        cfg = self.load({"OPEN_SLOT_ALLOWED_HOSTS": "Demo.Example, other.example",
                         "RENDER_EXTERNAL_HOSTNAME": "open-slot.onrender.com"})
        self.assertEqual(
            cfg.allowed_hosts,
            ("127.0.0.1", "localhost", "[::1]", "demo.example", "other.example", "open-slot.onrender.com"),
        )

    def test_the_allowed_set_does_not_depend_on_the_bind(self):
        for bind in ("127.0.0.1", "0.0.0.0"):
            cfg = self.load({"OPEN_SLOT_BIND": bind, "OPEN_SLOT_ALLOWED_HOSTS": "demo.example"})
            self.assertIn("demo.example", cfg.allowed_hosts)

    def test_a_host_with_a_scheme_or_port_is_refused(self):
        for bad in ("https://demo.example", "demo.example:8760", "demo example", "a/b"):
            with self.assertRaises(config.ConfigError, msg=bad):
                self.load({"OPEN_SLOT_ALLOWED_HOSTS": bad})

    def test_serving_on_a_public_bind_without_an_allowed_host_exits_with_status_2(self):
        environ = {"OPEN_SLOT_BIND": "0.0.0.0"}
        self.load(environ)
        with self.assertRaises(config.ConfigError) as caught:
            self.load(environ, serving=True)
        self.assertEqual(caught.exception.setting, "OPEN_SLOT_ALLOWED_HOSTS")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_:
            config.load_or_exit(serving=True, environ=environ, cwd="/nonexistent")
        self.assertEqual(exit_.exception.code, 2)
        self.assertIn("OPEN_SLOT_ALLOWED_HOSTS", stderr.getvalue())

    def test_a_public_bind_is_fine_with_either_source_of_a_host(self):
        for extra in ({"OPEN_SLOT_ALLOWED_HOSTS": "demo.example"},
                      {"RENDER_EXTERNAL_HOSTNAME": "x.onrender.com"}):
            self.load({"OPEN_SLOT_BIND": "0.0.0.0", **extra}, serving=True)

    def test_a_loopback_bind_needs_no_host(self):
        for bind in ("127.0.0.1", "localhost", "::1"):
            self.load({"OPEN_SLOT_BIND": bind}, serving=True)

    def test_trust_proxy_is_one_or_zero(self):
        self.assertTrue(self.load({"OPEN_SLOT_TRUST_PROXY": "1"}).trust_proxy)
        self.assertFalse(self.load({"OPEN_SLOT_TRUST_PROXY": "0"}).trust_proxy)
        with self.assertRaises(config.ConfigError):
            self.load({"OPEN_SLOT_TRUST_PROXY": "yes"})


class ExampleSettings(unittest.TestCase):
    def test_every_setting_in_the_example_file_is_read_by_config(self):
        from tests.support import ROOT

        names = [line.split("=")[0] for line in (ROOT / ".env.example").read_text().splitlines()
                 if line and not line.startswith("#")]
        source = (ROOT / "open_slot" / "config.py").read_text()
        self.assertGreaterEqual(len(names), 12)
        for name in names:
            self.assertIn(f'"{name}"', source, f"{name} is in .env.example and config.py does not read it")


class KeyNeverEchoed(unittest.TestCase):
    def test_a_malformed_key_is_refused_without_quoting_it(self):
        bad = fake_key() + " with a space"
        with self.assertRaises(config.ConfigError) as caught:
            config.load(environ={"QLOO_API_KEY": bad}, cwd="/nonexistent")
        self.assertEqual(caught.exception.setting, "QLOO_API_KEY")
        self.assertNotIn(fake_key(), str(caught.exception))

    def test_no_error_quotes_the_key(self):
        key = fake_key()
        for environ in ({"OPEN_SLOT_PORT": "x"}, {"OPEN_SLOT_PROVIDER": "bad"}, {"OPEN_SLOT_MAX_CALLS": "x"}):
            with self.assertRaises(config.ConfigError) as caught:
                config.load(environ={**environ, "QLOO_API_KEY": key}, cwd="/nonexistent")
            self.assertNotIn(key, str(caught.exception))

    def test_the_key_is_not_in_the_repr_and_the_status_is_words(self):
        cfg = config.load(environ={"QLOO_API_KEY": fake_key()}, cwd="/nonexistent")
        self.assertNotIn(fake_key(), repr(cfg))
        self.assertEqual(cfg.key_status, "set")


if __name__ == "__main__":
    unittest.main()
