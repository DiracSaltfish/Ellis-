import concurrent.futures
import datetime as dt
import unittest

from tgw_macos import GetTaskID
from tgw_macos._task_id import MAX_TASK_SEQUENCE, TaskIdGenerator, compose_task_id


class _Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class GetTaskIdTests(unittest.TestCase):
    def test_public_call_is_an_int_with_documented_local_time_shape(self):
        before = dt.datetime.now().replace(microsecond=0)
        value = GetTaskID()
        after = dt.datetime.now().replace(microsecond=0)

        self.assertIs(type(value), int)
        digits = str(value)
        self.assertTrue(digits.isdigit())
        self.assertGreaterEqual(len(digits), 15)
        self.assertLessEqual(len(digits), 17)

        possible_prefixes = set()
        current = before - dt.timedelta(seconds=1)
        while current <= after + dt.timedelta(seconds=1):
            possible_prefixes.add(current.strftime("%m%d%H%M%S").lstrip("0"))
            current += dt.timedelta(seconds=1)
        matches = []
        for prefix in possible_prefixes:
            if not digits.startswith(prefix):
                continue
            suffix_text = digits[len(prefix):]
            if suffix_text and suffix_text == f"{int(suffix_text):06d}":
                matches.append(int(suffix_text))
        self.assertTrue(matches)
        self.assertTrue(all(1 <= sequence <= MAX_TASK_SEQUENCE for sequence in matches))

    def test_manual_example_and_sequence_bounds(self):
        second = dt.datetime(2026, 5, 24, 15, 30, 30)
        self.assertEqual(compose_task_id(second, 1), 524153030000001)
        self.assertEqual(
            compose_task_id(second, MAX_TASK_SEQUENCE),
            5241530301000000,
        )
        with self.assertRaises(ValueError):
            compose_task_id(second, 0)
        with self.assertRaises(ValueError):
            compose_task_id(second, MAX_TASK_SEQUENCE + 1)

    def test_cross_second_resets_sequence(self):
        clock = _Clock(dt.datetime(2026, 8, 29, 23, 15, 9, 999999))
        generator = TaskIdGenerator(clock)

        self.assertEqual(generator.next(), compose_task_id(clock.value, 1))
        self.assertEqual(generator.next(), compose_task_id(clock.value, 2))
        clock.value = clock.value + dt.timedelta(seconds=1)
        self.assertEqual(generator.next(), compose_task_id(clock.value, 1))

    def test_sequence_ceiling_and_next_second_rollover(self):
        clock = _Clock(dt.datetime(2026, 8, 29, 23, 15, 9))
        generator = TaskIdGenerator(clock)
        generator._second = clock.value
        generator._sequence = MAX_TASK_SEQUENCE - 1

        self.assertEqual(
            generator.next(), compose_task_id(clock.value, MAX_TASK_SEQUENCE)
        )
        with self.assertRaisesRegex(RuntimeError, "documented per-second sequence"):
            generator.next()
        clock.value = clock.value + dt.timedelta(seconds=1)
        self.assertEqual(generator.next(), compose_task_id(clock.value, 1))

    def test_concurrent_calls_are_unique_and_strictly_orderable(self):
        second = dt.datetime(2026, 8, 29, 23, 15, 9)
        generator = TaskIdGenerator(_Clock(second))

        with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
            values = list(executor.map(lambda _: generator.next(), range(4096)))

        self.assertTrue(all(type(value) is int for value in values))
        self.assertEqual(len(set(values)), len(values))
        self.assertEqual(min(values), compose_task_id(second, 1))
        self.assertEqual(max(values), compose_task_id(second, len(values)))
        self.assertTrue(all(a < b for a, b in zip(sorted(values), sorted(values)[1:])))


if __name__ == "__main__":
    unittest.main()
