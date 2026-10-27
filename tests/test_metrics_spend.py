import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PRICES = {
    "date": "2026-10-06",
    "source": "test",
    "per_million": {
        "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_write_5m": 5.0, "cache_write_1h": 8.0, "cache_read": 0.2},
        "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_write_5m": 2.5, "cache_write_1h": 4.0, "cache_read": 0.2},
        "claude-haiku-5-5": {"input": 0.1, "output": 0.5, "cache_write_5m": 0.125, "cache_write_1h": 0.2, "cache_read": 0.01},
    },
}

ZERO = {"input": 0, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 0, "output": 0}


def at(clock):
    return datetime.datetime.fromisoformat("2026-10-26T%s+00:00" % clock)


SPAN = (at("10:00:00"), at("10:05:00"))


def assistant(message_id, model, usage, clock="10:01:00"):
    return {
        "type": "assistant",
        "uuid": "uuid-" + message_id,
        "timestamp": "2026-10-26T%s.000Z" % clock,
        "message": {"id": message_id, "model": model, "usage": usage},
    }


def tokens(**kinds):
    return {**ZERO, **kinds}


class TranscriptHome(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home)

    def write_transcript(self, relative, lines):
        path = os.path.join(self.home, ".claude", "projects", "scratch", relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("".join(json.dumps(line) + "\n" for line in lines))

    def spend_of(self, calls, caller=None, children=()):
        import spend

        return spend.as_dict(spend.run_spend(self.home, calls, caller, SPAN, children, PRICES))


class TokensByKind(TranscriptHome):
    def test_tokens_by_kind_and_model(self):
        first_opus = assistant(
            "msg-opus-1",
            "claude-opus-5-5",
            {
                "input_tokens": 10,
                "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 200},
                "cache_creation_input_tokens": 300,
                "cache_read_input_tokens": 1000,
                "output_tokens": 50,
            },
        )
        self.write_transcript(
            "worker-1.jsonl",
            [
                first_opus,
                first_opus,
                assistant(
                    "msg-opus-2",
                    "claude-opus-5-5",
                    {
                        "input_tokens": 20,
                        "cache_creation": {"ephemeral_5m_input_tokens": 300, "ephemeral_1h_input_tokens": 0},
                        "cache_read_input_tokens": 2000,
                        "output_tokens": 70,
                    },
                ),
                assistant(
                    "msg-sonnet-1",
                    "claude-sonnet-5-5",
                    {
                        "input_tokens": 5,
                        "cache_creation_input_tokens": 40,
                        "cache_read_input_tokens": 500,
                        "output_tokens": 30,
                    },
                ),
            ],
        )
        result = self.spend_of([{"source": "worker", "session": "worker-1"}])
        self.assertEqual(
            [(row["source"], row["model"], row["tokens"]) for row in result["spend"]],
            [
                (
                    "worker",
                    "claude-opus-5-5",
                    {"input": 30, "cache_write_5m": 400, "cache_write_1h": 200, "cache_read": 3000, "output": 120},
                ),
                (
                    "worker",
                    "claude-sonnet-5-5",
                    {"input": 5, "cache_write_5m": 40, "cache_write_1h": 0, "cache_read": 500, "output": 30},
                ),
            ],
        )
        self.assertEqual(
            result["tokens"],
            {"input": 35, "cache_write_5m": 440, "cache_write_1h": 200, "cache_read": 3500, "output": 150},
        )


class Pricing(TranscriptHome):
    def test_priced_with_the_table(self):
        self.write_transcript(
            "worker-1.jsonl",
            [assistant("msg-1", "claude-opus-5-5", {"input_tokens": 1000000, "output_tokens": 500000})],
        )
        result = self.spend_of([{"source": "worker", "session": "worker-1"}])
        self.assertEqual(
            result["spend"],
            [
                {
                    "source": "worker",
                    "model": "claude-opus-5-5",
                    "tokens": tokens(input=1000000, output=500000),
                    "usd": 14.0,
                }
            ],
        )
        self.assertEqual(result["usd"], 14.0)
        self.assertEqual(result["prices_date"], "2026-10-06")
        self.assertEqual(result["unpriced_models"], [])
        self.assertEqual(result["sessions_missing"], 0)

    def test_unknown_model_unpriced(self):
        self.write_transcript(
            "worker-1.jsonl",
            [
                assistant("msg-1", "claude-unknown-1", {"input_tokens": 700, "output_tokens": 300}),
                assistant("msg-2", "claude-opus-5-5", {"input_tokens": 1000, "output_tokens": 2000}),
            ],
        )
        result = self.spend_of([{"source": "worker", "session": "worker-1"}])
        self.assertEqual(
            result["spend"],
            [
                {"source": "worker", "model": "claude-opus-5-5", "tokens": tokens(input=1000, output=2000), "usd": 0.044},
                {"source": "worker", "model": "claude-unknown-1", "tokens": tokens(input=700, output=300), "usd": None},
            ],
        )
        self.assertEqual(result["unpriced_models"], ["claude-unknown-1"])
        self.assertEqual(result["usd"], 0.044)

    def test_synthetic_messages_skipped(self):
        self.write_transcript(
            "worker-1.jsonl",
            [
                assistant("msg-1", "claude-opus-5-5", {"input_tokens": 10, "output_tokens": 20}),
                assistant("msg-2", "<synthetic>", {"input_tokens": 0, "output_tokens": 0}),
            ],
        )
        result = self.spend_of([{"source": "worker", "session": "worker-1"}])
        self.assertEqual([row["model"] for row in result["spend"]], ["claude-opus-5-5"])
        self.assertEqual(result["unpriced_models"], [])


class Sessions(TranscriptHome):
    def test_subagent_transcripts_counted(self):
        self.write_transcript("worker-1.jsonl", [])
        self.write_transcript(
            os.path.join("worker-1", "subagents", "agent-a.jsonl"),
            [assistant("msg-1", "claude-haiku-5-5", {"input_tokens": 400, "cache_read_input_tokens": 60, "output_tokens": 90})],
        )
        result = self.spend_of([{"source": "worker", "session": "worker-1"}])
        self.assertEqual(
            result["spend"],
            [
                {
                    "source": "worker",
                    "model": "claude-haiku-5-5",
                    "tokens": tokens(input=400, cache_read=60, output=90),
                    "usd": 0.000086,
                }
            ],
        )

    def test_missing_sessions_counted(self):
        self.write_transcript(
            "worker-present.jsonl",
            [assistant("msg-1", "claude-sonnet-5-5", {"input_tokens": 3, "output_tokens": 4})],
        )
        result = self.spend_of(
            [
                {"source": "worker", "session": None},
                {"source": "structure", "session": "worker-gone"},
                {"source": "brief", "session": "worker-present"},
            ]
        )
        self.assertEqual(result["sessions_missing"], 2)
        self.assertEqual(
            [(row["source"], row["model"], row["tokens"]) for row in result["spend"]],
            [("brief", "claude-sonnet-5-5", tokens(input=3, output=4))],
        )


class Windows(TranscriptHome):
    def write_worker_messages(self):
        self.write_transcript(
            "worker-1.jsonl",
            [
                assistant("msg-1", "claude-sonnet-5-5", {"output_tokens": 1}, "10:01:00"),
                assistant("msg-2", "claude-sonnet-5-5", {"output_tokens": 10}, "10:03:00"),
                assistant("msg-3", "claude-sonnet-5-5", {"output_tokens": 100}, "10:05:00"),
            ],
        )

    def test_caller_messages_inside_span_only(self):
        self.write_transcript(
            "caller-1.jsonl",
            [
                assistant("msg-1", "claude-opus-5-5", {"output_tokens": 1}, "09:59:00"),
                assistant("msg-2", "claude-opus-5-5", {"output_tokens": 10}, "10:01:00"),
                assistant("msg-3", "claude-opus-5-5", {"output_tokens": 100}, "10:09:00"),
            ],
        )
        result = self.spend_of([], caller="caller-1")
        self.assertEqual(
            [(row["source"], row["model"], row["tokens"]) for row in result["spend"]],
            [("caller", "claude-opus-5-5", tokens(output=10))],
        )
        self.assertEqual(result["sessions_missing"], 0)

    def test_span_ends_included(self):
        self.write_transcript(
            "caller-1.jsonl",
            [
                assistant("msg-1", "claude-opus-5-5", {"output_tokens": 1}, "10:00:00"),
                assistant("msg-2", "claude-opus-5-5", {"output_tokens": 10}, "10:05:00"),
            ],
        )
        self.write_worker_messages()
        result = self.spend_of(
            [{"source": "worker", "session": "worker-1"}],
            caller="caller-1",
            children=[{"session": "worker-1", "start": at("10:01:00"), "end": at("10:03:00")}],
        )
        self.assertEqual(
            [(row["source"], row["tokens"]) for row in result["spend"]],
            [("caller", tokens(output=11)), ("worker", tokens(output=100))],
        )

    def test_child_span_left_out(self):
        self.write_worker_messages()
        result = self.spend_of(
            [{"source": "worker", "session": "worker-1"}],
            children=[{"session": "worker-1", "start": at("10:02:00"), "end": at("10:04:00")}],
        )
        self.assertEqual(
            [(row["source"], row["tokens"]) for row in result["spend"]],
            [("worker", tokens(output=101))],
        )

    def test_child_of_another_session_ignored(self):
        self.write_worker_messages()
        result = self.spend_of(
            [{"source": "worker", "session": "worker-1"}],
            children=[{"session": "worker-2", "start": at("10:02:00"), "end": at("10:04:00")}],
        )
        self.assertEqual(
            [(row["source"], row["tokens"]) for row in result["spend"]],
            [("worker", tokens(output=111))],
        )


if __name__ == "__main__":
    unittest.main()
