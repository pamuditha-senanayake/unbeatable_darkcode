"""Contract and property tests for the site reader (sites.py).

Type 1 (rules that always hold): odds sum to 1, only listed sites, a decoy zone never gets
weight, a confirmed zone gets it all, a customer already seen early in the shift is not
claimed again by an early anonymous job. Type 3: hundreds of random shifts from the game's own
generator, where the reader must beat a flat guess on the true zone.
"""
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import sites  # noqa: E402

DIR = [{"name": n, "zone": z} for n, z in [
    ("Kista Logistics AB", "Kista"), ("Solna Health Clinic", "Solna"), ("Sundbyberg Bakery", "Sundbyberg"),
    ("Nacka Marina Hotel", "Nacka"), ("Soder Data Center", "Sodermalm"), ("Kista Science Tower", "Kista"),
    ("Sundbyberg Municipal Pool", "Sundbyberg"), ("Nacka Retail Park", "Nacka"), ("Soder Brewery", "Sodermalm"),
    ("Solna Office Campus", "Solna")]]
SITES = ["Kista", "Solna", "Sundbyberg", "Nacka", "Sodermalm"]


class Contract(unittest.TestCase):
    def test_sums_to_one_and_only_sites(self):
        seq = [{"jid": "J%d" % i, "customer": None, "trade": t} for i, t in
               enumerate(["IT", "lift", "plumbing", None, "HVAC"] * 3)]
        for jid, odds in sites.zone_odds(seq, DIR, SITES).items():
            self.assertAlmostEqual(sum(odds.values()), 1.0, places=6)
            self.assertTrue(set(odds) <= set(SITES))

    def test_decoy_zone_gets_nothing(self):
        seq = [{"jid": "J1", "customer": None, "trade": "IT", "not_zones": {"Kista"}}]
        self.assertNotIn("Kista", {z for z, p in sites.zone_odds(seq, DIR, SITES)["J1"].items() if p > 0})

    def test_confirmed_zone_takes_all(self):
        seq = [{"jid": "J1", "customer": None, "trade": "IT", "zone": "Solna"}]
        self.assertAlmostEqual(sites.zone_odds(seq, DIR, SITES)["J1"]["Solna"], 1.0)

    def test_lift_is_only_at_lift_sites(self):
        seq = [{"jid": "J1", "customer": None, "trade": "lift"}]
        odds = sites.zone_odds(seq, DIR, SITES)["J1"]
        # lift: Solna Health Clinic, Nacka Marina Hotel, Kista Science Tower
        self.assertGreater(odds["Solna"] + odds["Nacka"] + odds["Kista"], 0.9)

    def test_early_customer_seen_is_not_claimed_again(self):
        # the two lift customers in Kista and Solna are already seen early: lift -> Nacka
        seq = [{"jid": "J1", "customer": "Kista Science Tower", "trade": "lift"},
               {"jid": "J2", "customer": "Solna Health Clinic", "trade": "lift"},
               {"jid": "J3", "customer": None, "trade": "lift"}]
        self.assertGreater(sites.zone_odds(seq, DIR, SITES)["J3"]["Nacka"], 0.8)

    def test_late_jobs_can_be_any_customer(self):
        # after the first ten real jobs (one per customer) a seen customer counts again
        seq = [{"jid": "J%d" % i, "customer": c["name"], "trade": None} for i, c in enumerate(DIR)]
        seq[5] = dict(seq[5], customer="Nacka Marina Hotel")    # Kista Science Tower unseen early
        seq.append({"jid": "J99", "customer": None, "trade": "lift"})
        odds = sites.zone_odds(seq, DIR, SITES)["J99"]
        self.assertGreater(odds["Solna"], 0.2)

    def test_unknown_city_is_flat_over_its_customers(self):
        d = [{"name": "A", "zone": "X"}, {"name": "B", "zone": "Y"}]
        odds = sites.zone_odds([{"jid": "J1", "customer": None, "trade": "IT"}], d, ["X", "Y"])["J1"]
        self.assertAlmostEqual(odds["X"], 0.5)


class Property(unittest.TestCase):
    """Random shifts from the game's generator: the reader beats a flat guess."""

    def test_beats_flat_on_generated_shifts(self):
        try:
            from arena.games.dispatchdesk import scenario as sc
        except Exception:
            self.skipTest("harness not installed")
        for world, jobs in (("stockholm", 22), ("metro", 45)):
            w = sc.WORLDS[world]
            d = [{"name": c.name, "zone": c.zone} for c in w.customers]
            zs = sorted({c.zone for c in w.customers})
            ours, flat, n = 0.0, 0.0, 0
            for seed in range(200):
                s = sc.generate(random.Random(seed), num_jobs=jobs, world=world, num_techs=4)
                real = [j for j in s.jobs if j.kind == "real"]
                rng = random.Random(seed)
                seq = [{"jid": j.jid, "trade": j.skill,
                        "customer": j.customer.name if rng.random() < 4 / 7.0 else None} for j in real]
                for i, (j, q) in enumerate(zip(real, seq)):
                    if q["customer"] is None:      # read when it arrives: later jobs unseen
                        odds = sites.zone_odds(seq[:i + 1], d, zs)
                        ours += odds[j.jid].get(j.zone, 0.0)
                        flat += sum(c["zone"] == j.zone for c in d) / len(d)
                        n += 1
            print("%s: mean p(true zone) %.3f vs flat %.3f over %d jobs" % (world, ours / n, flat / n, n))
            self.assertGreater(ours / n, flat / n + 0.03)


if __name__ == "__main__":
    unittest.main()
