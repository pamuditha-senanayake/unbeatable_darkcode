"""Edge-case handlers: small rules for single patterns the general layers get wrong.

Each handler has one trigger and may only override the layer below it inside that
trigger. They were chosen by ranking lost value on level 2 practice shifts by cause; each
has a unit test in tests/test_handlers.py.
"""


def ambiguous_original(dup, customer, topic, history, customer_of, trade_of):
    """Two jobs from the same customer that are not repeats of each other.

    Trigger: a follow-up from `customer` about `topic` (a trade or None), when the history
    holds two or more earlier tickets from that customer on that topic, open or finished.
    The core would merge the follow-up into the one such job still open, but if the other
    one was the original (often already finished), that merge is wrong and costs 15.
    Override: do not merge. The follow-up is left alone (never dispatched either), which
    costs nothing: a correct merge earns nothing.

    `history` is every ticket seen so far: [{"jid", "minute", "blob"}]; `customer_of(blob)`
    and `trade_of(blob)` are the regex readers.
    """
    if customer is None:
        return False
    same = [h for h in history
            if h["jid"] != dup["jid"] and h["minute"] <= dup["minute"]
            and customer_of(h["blob"]) == customer
            and (topic is None or trade_of(h["blob"]) == topic)]
    return len(same) >= 2
