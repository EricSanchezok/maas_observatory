import unittest

from tooluse_bench.benchmarks.probe import (
    _check_exact_add,
    _check_irrelevant,
    _check_missing_info,
    _check_parallel,
    _check_selection,
)


def message_with_calls(*calls: tuple[str, str]) -> dict:
    return {
        "content": None,
        "tool_calls": [
            {
                "id": f"call_{index}",
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
            for index, (name, arguments) in enumerate(calls)
        ],
    }


class ProbeScoringTests(unittest.TestCase):
    def test_exact_arguments(self) -> None:
        message = message_with_calls(("add", '{"x": 17, "y": 25}'))
        self.assertTrue(_check_exact_add(message)[0])

    def test_rejects_text_encoded_tool_call(self) -> None:
        message = {"content": 'add({"x": 17, "y": 25})'}
        self.assertFalse(_check_exact_add(message)[0])

    def test_irrelevance(self) -> None:
        message = {"content": "Chlorophyll reflects green light."}
        self.assertTrue(_check_irrelevant(message)[0])

    def test_tool_selection(self) -> None:
        message = message_with_calls(
            ("get_exchange_rate", '{"base": "usd", "quote": "cny"}')
        )
        self.assertTrue(_check_selection(message)[0])

    def test_parallel_calls(self) -> None:
        message = message_with_calls(
            ("get_weather", '{"location": "Shanghai", "unit": "celsius"}'),
            ("get_weather", '{"location": "Beijing", "unit": "celsius"}'),
        )
        self.assertTrue(_check_parallel(message)[0])

    def test_missing_information_requires_clarification(self) -> None:
        self.assertTrue(
            _check_missing_info({"content": "What city are you departing from?"})[0]
        )
        self.assertFalse(
            _check_missing_info({"content": "I cannot help with that request."})[0]
        )
        self.assertFalse(
            _check_missing_info({"content": "Your flight has been booked."})[0]
        )


if __name__ == "__main__":
    unittest.main()
