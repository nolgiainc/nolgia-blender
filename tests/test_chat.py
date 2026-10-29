# SPDX-License-Identifier: GPL-3.0-or-later
"""The chat in the NOLGIA panel, against the mock API's agent routes."""

import os
import tempfile
import time
import unittest

import support

from core import AGENT_URL, BILLING_URL, COST_LINE
from core.api import ApiClient
from core.chat import (
    MAX_CHARS,
    AgentChat,
    ChatStore,
    Link,
    failure_text,
    format_reply,
    session_name,
    time_key,
    turn_rate,
)


class Owner:
    """The parts of the Controller the chat uses."""

    def __init__(self):
        self.bumps = 0
        self.logs = []
        self.auth_failures = []

    def bump(self):
        self.bumps += 1

    def log(self, text):
        self.logs.append(text)

    def auth_failed(self, text):
        self.auth_failures.append(text)


class FormatTest(unittest.TestCase):
    def test_replies_are_paragraphs_list_items_bold_text_and_https_links_only(self):
        blocks = format_reply(
            "## Done\n\nI made **five objects** in `Scene`.\n\n- A mountain\n2. A camera and a sun\n\n"
            "See [your library](https://nolgia.ai/library) or https://nolgia.ai/agent. "
            "Not http://x.test or [this](javascript:alert(1)).\n```py\ncode\n```"
        )
        self.assertEqual(blocks[0], {"type": "p", "parts": [{"text": "Done", "bold": True}]})
        self.assertEqual(blocks[1]["parts"], [{"text": "I made "}, {"text": "five objects", "bold": True},
                                              {"text": " in Scene."}])
        self.assertEqual(blocks[2], {"type": "li", "parts": [{"text": "A mountain"}]})
        self.assertEqual(blocks[3], {"type": "li", "parts": [{"text": "A camera and a sun"}]})
        links = [p for p in blocks[4]["parts"] if p.get("href")]
        self.assertEqual(links, [{"text": "your library", "href": "https://nolgia.ai/library"},
                                 {"text": "https://nolgia.ai/agent", "href": "https://nolgia.ai/agent"}])
        self.assertEqual(len(blocks), 6, "the code fence lines are dropped, its body kept as text")

    def test_a_chats_name_on_nolgia_ai_says_it_came_from_blender(self):
        self.assertEqual(session_name("Add a low poly mountain scene with a camera and a sun"),
                         "Blender: Add a low poly mountain scene")
        self.assertEqual(session_name("   "), "Blender: New chat")
        self.assertLessEqual(len(session_name("x" * 200)), 80)

    def test_times_sort_to_the_microsecond_whatever_their_fraction(self):
        order = ["2026-10-29T00:15:01Z", "2026-10-29T00:15:01.000001Z", "2026-10-29T00:15:01.5Z",
                 "2026-10-29T00:15:02Z"]
        self.assertEqual(sorted(reversed(order), key=time_key), order)

    def test_turn_rate(self):
        models = [{"model": "a", "turn_credits": 10, "default": True},
                  {"model": "claude-opus-5-5", "turn_credits": 70, "default": False}]
        self.assertEqual(turn_rate({"model": None}, models), 10)
        self.assertEqual(turn_rate({"model": "claude-opus-5-5"}, models), 70)
        self.assertIsNone(turn_rate({"model": "retired-brain"}, models))
        self.assertIsNone(turn_rate(None, models))


class ChatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = support.start_mock()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.server.state.reset()
        self.dir = tempfile.TemporaryDirectory()
        self.store_path = os.path.join(self.dir.name, "chat.json")
        self.chats = []

    def tearDown(self):
        for chat in self.chats:
            chat.close()
        self.dir.cleanup()

    def link(self, token=support.TOKEN, instance_id="unit-instance", email="test@nolgia.ai"):
        return Link(ApiClient(self.server.base_url, token), instance_id, self.server.base_url, email,
                    ChatStore(self.store_path))

    def chat(self, owner=None, link=None, threaded=False):
        chat = AgentChat(owner or Owner(), threaded=threaded)
        self.chats.append(chat)
        chat.begin(link or self.link())
        return chat

    def mock_agent(self, **body):
        status, _ = support.call(self.server, "POST", "/mock/agent", body, token=None)
        self.assertEqual(status, 204)

    def reply(self, **body):
        status, data = support.call(self.server, "POST", "/mock/agent/reply", body, token=None)
        self.assertEqual(status, 200, data)

    def state(self):
        status, data = support.call(self.server, "GET", "/mock/state", token=None)
        self.assertEqual(status, 200)
        return data

    def remembered(self):
        return ChatStore(self.store_path).recall()

    def test_the_first_message_starts_a_chat_pinned_to_this_blender_and_the_reply_comes_back(self):
        chat = self.chat()
        self.assertTrue(chat.view()["empty"])
        self.assertTrue(chat.send("Add a low poly mountain scene with a camera and a sun"))
        view = chat.view()
        self.assertTrue(view["busy"])
        self.assertRegex(view["working"], "working")
        state = self.state()
        self.assertEqual(state["agent_creates"], [{"name": "Blender: Add a low poly mountain scene",
                                                   "app": {"app": "blender", "instance_id": "unit-instance"}}])
        session_id = state["agent_sessions"][0]["id"]
        self.assertEqual(state["agent_submits"][0], {"content": "Add a low poly mountain scene with a camera and a sun",
                                                     "session_id": session_id})
        self.assertEqual(self.remembered(), {"id": session_id, "api": self.server.base_url, "email": "test@nolgia.ai"})

        self.reply(content="I added **Mountains** with a camera and a sun.\n\n- Terrain\n- Camera")
        chat.tick()
        view = chat.view()
        self.assertFalse(view["busy"])
        self.assertEqual([r["role"] for r in view["rows"]], ["user", "agent"])
        self.assertEqual(view["rows"][1]["blocks"][0]["parts"][1], {"text": "Mountains", "bold": True})
        self.assertEqual(view["rows"][1]["credits"], 10)
        self.assertEqual(view["session_url"], AGENT_URL + "/" + session_id)
        self.assertTrue(all(not r.get("local") for r in chat.messages()), "NOLGIA's own rows replaced the drawn ones")

        # The next message goes to the same chat, as a plain turn.
        self.assertTrue(chat.send("Make the sun warmer."))
        state = self.state()
        self.assertEqual(len(state["agent_creates"]), 1)
        self.assertEqual(state["agent_submits"][1], {"content": "Make the sun warmer.", "session_id": session_id})
        self.reply(content="Warmer now.")
        chat.tick()
        self.assertEqual(len(chat.view()["rows"]), 4)

    def test_an_api_without_the_app_pin_never_gets_an_unpinned_chat(self):
        self.mock_agent(app_pin=False)
        chat = self.chat()
        chat.send("hello")
        state = self.state()
        self.assertEqual(len(state["agent_creates"]), 1, "no second try without the pin")
        self.assertEqual(state["agent_submits"], [])
        self.assertIsNone(chat.session_id)
        self.assertIsNone(self.remembered())
        self.assertRegex(chat.view()["notice"]["text"], "unknown field")
        self.assertEqual(chat.take_restore(), "hello", "the text goes back in the box")
        self.assertIsNone(chat.take_restore())

    def test_one_message_is_one_turn_and_a_turn_started_on_nolgia_ai_is_followed(self):
        chat = self.chat()
        self.assertTrue(chat.send("first"))
        self.assertTrue(chat.busy)
        self.assertFalse(chat.send("and one more thing"), "held while the turn runs")
        self.assertEqual(len(self.state()["agent_submits"]), 1)
        self.reply(content="First done.")
        chat.tick()

        # The same chat continued on nolgia.ai: its turn is running when the panel sends.
        status, _ = support.call(self.server, "POST", "/v1/agent/messages",
                                 {"content": "from the website", "session_id": chat.session_id})
        self.assertEqual(status, 202)
        chat.send("from the panel")
        self.assertRegex(chat.view()["notice"]["text"], "still working on this chat")
        self.assertEqual(chat.take_restore(), "from the panel")
        self.assertTrue(chat.busy, "the panel follows the running turn")
        self.reply(content="Website turn done.")
        chat.tick()
        self.assertEqual([r["role"] + ":" + (r.get("content") or "") for r in chat.messages()],
                         ["user:first", "agent:First done.", "user:from the website", "agent:Website turn done."])
        self.assertTrue(chat.send("now the panel"))
        self.assertIsNone(chat.view()["notice"])

    def test_no_nolgia_agent_yet_says_how_to_start_one(self):
        self.mock_agent(provisioned=False)
        chat = self.chat()
        self.assertIsNone(chat.agent)
        chat.send("hi")
        notice = chat.view()["notice"]
        self.assertEqual(notice["kind"], "no_agent")
        self.assertEqual(notice["action"], {"label": "Open NOLGIA Agent", "url": AGENT_URL})
        self.assertEqual(self.state()["agent_creates"], [], "no empty chat is left on nolgia.ai")
        self.assertIsNone(chat.view()["session_url"])
        # Set up on nolgia.ai meanwhile: the next send reads it again and goes through.
        self.mock_agent(provisioned=True)
        self.assertTrue(chat.send("hi again"))
        self.assertEqual(len(self.state()["agent_submits"]), 1)

    def test_out_of_credits_on_the_send_or_on_the_turn_offers_add_credits(self):
        self.mock_agent(submit_error={"status": 402, "title": "Payment Required", "detail": "not enough credits",
                                      "code": "insufficient_credits"})
        chat = self.chat()
        chat.send("hi")
        self.assertEqual(chat.view()["notice"]["action"], {"label": "Add credits", "url": BILLING_URL})
        chat.close()

        self.mock_agent(submit_error=None)
        chat = self.chat()
        chat.send("hi")
        self.reply(status="failed", error="The wallet cannot pay for the turn.", error_code="insufficient_credits")
        chat.tick()
        view = chat.view()
        self.assertEqual(len(view["rows"]), 1, "the failed reply is not drawn in the thread as well")
        self.assertEqual(view["notice"], {"kind": "credits",
                                          "text": failure_text({"status": "failed", "error_code": "insufficient_credits"}),
                                          "action": {"label": "Add credits", "url": BILLING_URL}})
        # Once a later turn settles, the old failure is a reply like any other and the card goes.
        chat.send("try again")
        self.reply(content="Done.")
        chat.tick()
        view = chat.view()
        self.assertIsNone(view["notice"])
        self.assertEqual(view["rows"][1]["failure"],
                         failure_text({"status": "failed", "error_code": "insufficient_credits"}))

    def test_the_chat_is_remembered_and_a_chat_deleted_on_nolgia_ai_starts_a_new_one(self):
        chat = self.chat()
        chat.send("remember me")
        self.reply(content="Got it.")
        chat.tick()
        session_id = chat.session_id
        chat.close()

        chat = self.chat()
        self.assertEqual(chat.session_id, session_id)
        self.assertEqual([r.get("content") for r in chat.messages()], ["remember me", "Got it."])
        chat.close()

        # Another account on this computer does not see it.
        chat = self.chat(link=self.link(email="someone@else.test"))
        self.assertIsNone(chat.session_id)
        chat.close()

        # A remembered chat pinned to another install (or to none) is never used here.
        chat = self.chat(link=self.link(instance_id="another-install"))
        self.assertIsNone(chat.session_id)
        self.assertIsNone(self.remembered(), "forgotten once found pinned elsewhere")
        chat.close()

        ChatStore(self.store_path).remember({"id": "00000000-0000-4000-8000-000000000000",
                                             "api": self.server.base_url, "email": "test@nolgia.ai"})
        chat = self.chat()
        self.assertIsNone(chat.session_id, "a chat deleted on nolgia.ai is forgotten")
        self.assertIsNone(self.remembered())
        self.assertTrue(chat.send("still there?"))
        self.assertEqual(len(self.state()["agent_creates"]), 2, "carried on in a new chat")

    def test_new_chat_and_sign_out(self):
        chat = self.chat()
        chat.send("one")
        self.reply(content="ok")
        chat.tick()
        first = chat.session_id
        chat.new_chat()
        self.assertTrue(chat.view()["empty"])
        self.assertIsNone(self.remembered())
        chat.send("two")
        self.assertNotEqual(chat.session_id, first)
        self.assertEqual(self.remembered()["id"], chat.session_id)
        chat.reset()
        self.assertIsNone(self.remembered(), "signing out forgets the chat on this computer")
        self.assertIsNone(chat.link)
        self.assertFalse(chat.send("after sign out"))

    def test_stop_ends_the_running_turn(self):
        chat = self.chat()
        chat.send("build something big")
        self.assertTrue(chat.view()["can_stop"])
        chat.stop()
        view = chat.view()
        self.assertFalse(view["busy"])
        self.assertEqual(view["rows"][1]["failure"], "Stopped.")

    def test_only_the_newest_replys_question_takes_answers(self):
        chat = self.chat()
        chat.send("make art")
        self.reply(content="In Blender or as a new image?",
                   question={"text": "In Blender or as a new image?",
                             "options": [{"label": "In Blender"}, {"label": "A new image on NOLGIA"}]})
        chat.tick()
        self.assertEqual([o["label"] for o in chat.view()["rows"][1]["question"]["options"]],
                         ["In Blender", "A new image on NOLGIA"])
        chat.send("In Blender")
        self.reply(content="On it.")
        chat.tick()
        self.assertIsNone(chat.view()["rows"][1]["question"])

    def test_the_cost_line_names_this_accounts_own_turn_rate(self):
        chat = self.chat()
        self.assertEqual(chat.view()["cost_line"],
                         "NOLGIA Agent works right here, each turn from 10 credits on your model. "
                         "You can also use Claude, Cursor, Codex or another agent with NOLGIA connected.")
        chat.close()
        self.mock_agent(model="claude-opus-5-5")
        chat = self.chat()
        self.assertIn("each turn from 70 credits on your model", chat.view()["cost_line"])
        chat.new_chat()
        self.assertIn("from 70 credits", chat.view()["cost_line"], "New chat keeps it")
        chat.close()
        self.mock_agent(models_error=True)
        chat = self.chat()
        self.assertEqual(chat.view()["cost_line"], COST_LINE)
        self.assertNotIn("free", COST_LINE)

    def test_a_refused_sign_in_goes_back_to_the_controller_and_a_long_message_stays_in_the_box(self):
        owner = Owner()
        chat = self.chat(owner=owner, link=self.link(token="not-a-token"))
        self.assertEqual(owner.auth_failures, ["Your NOLGIA sign in expired. Sign in again."])
        chat.send("hi")
        self.assertEqual(owner.auth_failures[-1], "Your NOLGIA sign in expired. Sign in again.")
        self.assertEqual(self.state()["agent_creates"], [])
        ok = self.chat()
        self.assertFalse(ok.send("x" * (MAX_CHARS + 1)))
        self.assertRegex(ok.view()["notice"]["text"], "too long")

    def test_the_chat_thread_reads_the_reply_by_itself(self):
        owner = Owner()
        chat = self.chat(owner=owner, threaded=True)
        deadline = time.time() + 10
        while not chat.view()["loaded"] and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(chat.send("hello from the thread"))
        while not chat.pending() and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(chat.busy)
        self.reply(content="Hello back.")
        deadline = time.time() + 15
        while chat.busy and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(chat.busy)
        self.assertEqual(chat.view()["rows"][-1]["text"], "Hello back.")
        self.assertGreater(owner.bumps, 2)


if __name__ == "__main__":
    unittest.main()
