"""Unit tests for the edge-case handlers (no harness needed)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import handlers  # noqa: E402

CUSTOMERS = {"sundbyberg bakery": "Sundbyberg Bakery", "the bakery": "Sundbyberg Bakery",
             "soder brewery": "Soder Brewery"}


def customer_of(blob):
    return next((name for key, name in CUSTOMERS.items() if key in blob), None)


def trade_of(blob):
    return "electrical" if "tripping" in blob or "sparking" in blob else \
        "refrigeration" if "cold room" in blob else None


class AmbiguousOriginal(unittest.TestCase):
    def history(self, *rows):
        return [{"jid": j, "minute": m, "blob": b} for j, m, b in rows]

    def test_two_same_topic_jobs_from_the_customer_is_ambiguous(self):
        h = self.history((("J2"), 10, "hassan, sundbyberg bakery: the breaker keeps tripping"),
                         ("J9", 120, "the night baker (the bakery): a socket is sparking"))
        dup = {"jid": "J14", "minute": 200}
        self.assertTrue(handlers.ambiguous_original(dup, "Sundbyberg Bakery", "electrical", h,
                                                    customer_of, trade_of))

    def test_one_matching_job_is_not_ambiguous(self):
        h = self.history(("J2", 10, "hassan, sundbyberg bakery: the breaker keeps tripping"),
                         ("J9", 120, "the night baker (the bakery): the cold room is warm"))
        dup = {"jid": "J14", "minute": 200}
        self.assertFalse(handlers.ambiguous_original(dup, "Sundbyberg Bakery", "electrical", h,
                                                     customer_of, trade_of))

    def test_later_tickets_and_other_customers_do_not_count(self):
        h = self.history(("J2", 10, "hassan, sundbyberg bakery: the breaker keeps tripping"),
                         ("J5", 50, "ola, soder brewery: the breaker keeps tripping"),
                         ("J20", 300, "the bakery: a socket is sparking"))
        dup = {"jid": "J14", "minute": 200}
        self.assertFalse(handlers.ambiguous_original(dup, "Sundbyberg Bakery", "electrical", h,
                                                     customer_of, trade_of))

    def test_unknown_customer_never_triggers(self):
        dup = {"jid": "J14", "minute": 200}
        self.assertFalse(handlers.ambiguous_original(dup, None, "electrical", [], customer_of, trade_of))


if __name__ == "__main__":
    unittest.main()
