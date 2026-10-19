import re
import unittest

from open_slot import server
from tests.support import ROOT

WEB = ROOT / "web"


def flat(text):
    return " ".join(text.split())


INDEX = flat((WEB / "index.html").read_text(encoding="utf-8"))
SCRIPT = (WEB / "app.js").read_text(encoding="utf-8")
STYLE = (WEB / "app.css").read_text(encoding="utf-8")

SENTENCES = {
    "the public-performers hint": "Type public performers, podcasts or shows only. "
                                  "Each name is sent to Qloo to find a match.",
    "the plain-HTTP banner": "This page was loaded over plain HTTP, so your browser sends no label with its "
                             "requests, and Confirm, Assign and Approve cannot work here. "
                             "Open the HTTPS address.",
    "the no-label banner": "Your browser does not label requests, so you cannot confirm or approve here. "
                           "Use a current Chrome, Firefox or Safari.",
    "the synthetic banner": "Synthetic sample data. Qloo is not being called.",
    "the no-key sentence": "No Qloo key is set on this server.",
    "the live banner": "Live: Qloo is being called",
    "the spent-allowance banner": "Daily allowance spent: showing synthetic sample data",
    "the inquiry hint": "This app never sends anything: you copy the approved text or open it in your own "
                        "mail app, and you find the address yourself, because Qloo holds no contact details.",
    "the nights hint": "A proposal is only a proposal: you assign a night. Nothing here books an act.",
    "the workspace note": "It is forgotten after two hours of inactivity, and sooner if the server restarts. "
                          "A free host that sleeps restarts it: the first load then takes up to a minute and "
                          "the workspace is empty, and the sample programme button starts again.",
}


class TheFixedSentences(unittest.TestCase):
    def test_each_is_in_the_page_verbatim(self):
        for name, sentence in SENTENCES.items():
            self.assertIn(sentence, INDEX, name)

    def test_the_banners_exist_hidden_until_the_script_shows_them_and_are_announced(self):
        for banner in ("pending", "synthetic", "live", "spent", "http", "nolabel"):
            self.assertRegex(INDEX, rf'<p id="banner-{banner}" class="banner [a-z]+" role="(status|alert)"')
        for banner in ("synthetic", "live", "spent", "http", "nolabel"):
            self.assertRegex(INDEX, rf'id="banner-{banner}"[^>]*\bhidden\b')

    def test_the_script_shows_the_banners_from_what_the_server_says(self):
        for needle in ("state.server.mode", "isSecureContext", "no_fetch_metadata", "banner-http",
                       "banner-nolabel", "banner-synthetic", "banner-live", "banner-spent", "banner-nokey"):
            self.assertIn(needle, SCRIPT)


class TheWorkspaceIsNotPromisedForTwoHours(unittest.TestCase):
    def test_every_text_that_gives_the_two_hours_also_says_what_a_sleeping_host_does_to_it(self):
        for path in ("README.md", "docs/submission.md", "web/index.html"):
            text = flat((ROOT / path).read_text(encoding="utf-8"))
            self.assertIn("two hours of inactivity", text, path)
            self.assertIn("sooner", text, path)
            self.assertIn("up to a minute", text, path)
            self.assertIn("sample programme", text, path)

    def test_the_readme_and_the_copy_give_the_free_tiers_fifteen_minutes(self):
        for path in ("README.md", "docs/submission.md"):
            self.assertIn("15 minutes without traffic", flat((ROOT / path).read_text(encoding="utf-8")), path)


VERB_HANDLERS = {
    "confirm_anchor": "confirmMatch",
    "assign_slot": "assignNight",
    "approve_inquiry": "approveDraft",
    "drop_act": "dropAct",
    "raise_call_cap": "raiseCap",
}


def inside_a_person_button(handler):
    """Every use of `handler(` other than its definition sits in a personButton( call: the nearest
    control-making call before it is one."""
    uses = [m.start() for m in re.finditer(rf"\b{handler}\(", SCRIPT)
            if not SCRIPT[:m.start()].endswith("function ")]
    return uses, all(SCRIPT.rfind("personButton(", 0, at) > SCRIPT.rfind('h("button"', 0, at) for at in uses)


class TheGateInThePage(unittest.TestCase):
    def test_every_button_that_runs_a_human_verb_carries_the_attribute_the_gate_disables_it_by(self):
        self.assertIn('"data-needs-person": true', SCRIPT)
        self.assertIn("button.disabled = busy || blocked !== null", SCRIPT)
        self.assertEqual(SCRIPT.count('h("button"'), len(re.findall(r'h\("button"', SCRIPT)))
        for verb, handler in VERB_HANDLERS.items():
            calls = [line for line in SCRIPT.splitlines() if f'"{verb}"' in line]
            self.assertTrue(calls and all("invoke(" in line or "runConsole(" in line for line in calls), verb)
            uses, guarded = inside_a_person_button(handler)
            self.assertTrue(uses, f"{handler} is not used by any control")
            self.assertTrue(guarded, f"{handler} is reachable from a control that is not a personButton")

    def test_each_human_verb_is_invoked_in_one_place_and_the_agent_console_cannot_reach_it_as_a_person(self):
        """Assigning and clearing a night are one verb, as are dropping an act and bringing it back."""
        for verb in VERB_HANDLERS:
            people = [line for line in SCRIPT.splitlines() if f'invoke("{verb}"' in line]
            self.assertEqual(len(people), 2 if verb in ("assign_slot", "drop_act") else 1, verb)
        self.assertEqual(SCRIPT.count("runConsole("), 3, "its definition, the form and the approve preset")
        self.assertIn("invoke(tool, args, true)", SCRIPT)

    def test_the_human_controls_are_the_five_verbs_plus_clearing_a_night_and_bringing_an_act_back(self):
        defined = SCRIPT.count("personButton(") - 1
        self.assertEqual(defined, 7, "confirm, assign, clear, approve, drop, bring back, raise the cap")

    def test_the_page_never_says_who_is_asking_except_that_the_console_asks_to_be_the_agent(self):
        for header in ("Sec-Fetch-Site", "Origin", "Cookie"):
            self.assertNotIn(header, SCRIPT)
        self.assertEqual(re.findall(r"X-Open-Slot-As[^;]*", SCRIPT), ['X-Open-Slot-As"] = "agent"'])
        self.assertEqual(SCRIPT.count("asAgent"), 2, "the parameter and its one use")

    def test_the_agent_address_is_shown_inside_a_closed_details_and_from_the_servers_say_so(self):
        self.assertRegex(INDEX, r'<details id="agent-details" hidden>')
        self.assertIn("state.agentPath", SCRIPT)

    def test_the_agent_console_offers_the_approve_refusal_and_names_no_second_way_in(self):
        self.assertIn('id="console-try"', INDEX)
        self.assertIn("Try approve_inquiry as the agent", INDEX)
        self.assertIn("A person's verbs are not tools, so asking for one here is refused.", flat(INDEX))
        self.assertEqual(SCRIPT.count('"/api/invoke"'), 1)


class NothingRemoteNothingInline(unittest.TestCase):
    def test_no_page_file_names_another_host(self):
        for name in ("index.html", "app.js", "app.css"):
            text = (WEB / name).read_text(encoding="utf-8")
            self.assertNotRegex(text, r"https?://|//[a-z0-9-]+\.[a-z]{2,}", name)
            self.assertNotIn("@import", text)
            self.assertNotIn("@font-face", text)

    def test_the_page_loads_exactly_its_own_script_and_stylesheet(self):
        self.assertEqual(re.findall(r'<script[^>]*>', INDEX), ['<script src="/app.js" defer>'])
        self.assertEqual(re.findall(r'<link[^>]*rel="stylesheet"[^>]*>', INDEX),
                         ['<link rel="stylesheet" href="/app.css">'])
        self.assertEqual(re.findall(r"<script[^>]*>(.*?)</script>", INDEX), [""])
        self.assertNotIn("<style", INDEX)

    def test_nothing_the_content_security_policy_forbids_is_in_the_page(self):
        self.assertNotRegex(INDEX, r'\sstyle="')
        self.assertNotRegex(INDEX, r"\son[a-z]+=")
        self.assertNotIn("javascript:", INDEX)
        self.assertIn("script-src 'self'", server.SECURITY_HEADERS["Content-Security-Policy"])

    def test_text_from_a_response_is_never_parsed_as_markup(self):
        for word in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                     "new Function", "setAttribute(\"style\"", "srcdoc"):
            self.assertNotIn(word, SCRIPT, word)

    def test_the_script_talks_only_to_this_server(self):
        for path in re.findall(r'(?:request|fetch)\(\s*[`"\']([^`"\']+)', SCRIPT):
            self.assertTrue(path.startswith("/api/"), path)


class UsableByKeyboardAndOnAPhone(unittest.TestCase):
    def test_every_action_is_a_real_button_or_form_control(self):
        self.assertNotRegex(SCRIPT, r'h\("(div|span|li|p|a|h[1-6])",\s*\{[^}]*\bon[a-z]+:')
        self.assertNotRegex(INDEX, r"<(div|span|a)[^>]*role=\"button\"")

    def test_the_page_declares_its_language_viewport_and_both_colour_schemes(self):
        self.assertIn('<html lang="en">', INDEX)
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1">', INDEX)
        self.assertIn("@media (prefers-color-scheme: dark)", STYLE)
        self.assertIn("color-scheme: light dark", STYLE)

    def test_focus_is_visible_targets_are_at_least_44_px_and_status_is_in_words(self):
        self.assertIn(":focus-visible", STYLE)
        self.assertNotRegex(STYLE, r"outline:\s*(none|0)")
        self.assertRegex(STYLE, r"button,\s*a\.button\s*\{[^}]*min-height:\s*2\.75rem")
        self.assertRegex(STYLE, r"input,\s*select,\s*textarea\s*\{[^}]*min-height:\s*2\.75rem")
        self.assertRegex(INDEX, r'id="status"[^>]*aria-live="polite"')
        self.assertIn("STATUS[evidence.status]", SCRIPT)

    def test_the_layout_has_one_column_with_a_16_px_gutter_and_relative_text(self):
        self.assertRegex(STYLE, r"header,\s*main,\s*footer\s*\{[^}]*padding:\s*0 1rem")
        self.assertNotRegex(STYLE, r"font-size:\s*[0-9.]+px")

    def test_every_form_control_has_a_visible_label_or_a_name(self):
        self.assertNotRegex(SCRIPT, r'h\("(input|select|textarea)"')
        for control in re.findall(r"<(?:input|select|textarea)\b[^>]*>", INDEX):
            self.assertRegex(control, r'id="[a-z-]+"')
        for found in re.finditer(r"<label>(.*?)</label>", INDEX, re.S):
            self.assertRegex(found[1], r"<(input|select|textarea)\b")
        for needed in ("console-tool", "console-args"):
            self.assertRegex(INDEX, rf"<label>[^<]*\s*<(input|textarea) id=\"{needed}\"")

    def test_the_tab_order_is_the_reading_order_and_nothing_jumps_the_queue(self):
        self.assertNotRegex(INDEX + SCRIPT, r"tabindex[=:]\s*[\"']?[1-9]")
        self.assertRegex(INDEX, r'<a class="skip" href="#main">')
        self.assertLess(INDEX.index('class="skip"'), INDEX.index("<header>"))
        self.assertRegex(INDEX, r'<main id="main" tabindex="-1">')
        order = [INDEX.index(f'id="{name}"') for name in (
            "banner-pending", "problem", "room", "programme", "run-button", "trace-details", "results",
            "nights-title", "inquiries-title", "console-details", "agent-details")]
        self.assertEqual(order, sorted(order))

    def test_what_changes_is_announced_by_live_regions_in_words(self):
        self.assertRegex(INDEX, r'id="status"[^>]*aria-live="polite"')
        self.assertRegex(INDEX, r'id="trace"[^>]*aria-live="polite"[^>]*aria-relevant="additions"')
        self.assertRegex(INDEX, r'id="console-line"[^>]*aria-live="polite"')
        self.assertRegex(INDEX, r'id="problem"[^>]*role="alert"')
        for need in ('say(`Confirmed', 'say(`Assigned', 'say(`Approved', 'say(`Dropped', "say(`Drafted",
                     "say(`The call cap is now", 'say("The agent proposed'):
            self.assertIn(need, SCRIPT)

    def test_a_disabled_person_button_names_its_reason_to_a_screen_reader_too(self):
        self.assertIn('"aria-describedby", `gate-${gates}`', SCRIPT)
        self.assertIn('"Needs the HTTPS address."', SCRIPT)
        self.assertIn('"Needs a browser that labels its requests."', SCRIPT)

    def test_the_approved_inquiry_can_be_copied_opened_in_a_mail_app_or_downloaded(self):
        self.assertIn("navigator.clipboard.writeText", SCRIPT)
        self.assertIn("box.select()", SCRIPT, "the fallback when the browser will not copy")
        self.assertIn("mailto:?subject=", SCRIPT)
        self.assertNotRegex(SCRIPT, r"mailto:[^?`\"']")
        self.assertIn("/api/inquiry/${programme.id}-${draft.draftId}.txt", SCRIPT)

    def test_a_draft_shows_its_text_with_the_draft_line_and_says_it_is_not_approved(self):
        self.assertIn('"DRAFT, not approved"', SCRIPT)
        self.assertIn("draft.text", SCRIPT)
        self.assertNotIn("DRAFT. Not approved", SCRIPT, "the line is the server's text, never the page's")


if __name__ == "__main__":
    unittest.main()
