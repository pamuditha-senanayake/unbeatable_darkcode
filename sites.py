"""Where an anonymous job is: a probability per zone, from who the customer can be.

Contract (the site reader stage):
    receives the real jobs in arrival order, each as a dict
        {"jid", "customer": directory name or None, "zone": confirmed zone or None,
         "trade": trade or None, "not_zones": zones the text names (decoys, never the site)}
    and the customer directory [{"name", "zone", ...}];
    returns {jid: {zone: probability}} for every job whose customer is None, each summing to 1.

What it knows about the game, all visible on practice shifts:
  * a job's trade is one of its customer's equipment trades, each equally likely (EQUIPMENT);
  * the first real jobs of a shift, one per directory customer, are every customer once, so an
    anonymous early job is one of the customers not yet seen early in the shift; after that the
    customer is any of them. Each early anonymous job is weighed on its own: exact when it is the
    only one, and a joint (Gibbs) solution measured worse on generated shifts.
A customer missing from EQUIPMENT (another city) is taken to need any trade.
"""

EQUIPMENT = {
    "Kista Logistics AB": ("refrigeration", "electrical", "IT"),
    "Solna Health Clinic": ("HVAC", "lift", "IT", "electrical"),
    "Sundbyberg Bakery": ("refrigeration", "electrical", "plumbing"),
    "Nacka Marina Hotel": ("HVAC", "plumbing", "lift", "electrical"),
    "Soder Data Center": ("HVAC", "IT", "electrical"),
    "Kista Science Tower": ("lift", "HVAC", "IT"),
    "Sundbyberg Municipal Pool": ("plumbing", "HVAC", "electrical"),
    "Nacka Retail Park": ("refrigeration", "electrical", "IT"),
    "Soder Brewery": ("refrigeration", "plumbing", "electrical"),
    "Solna Office Campus": ("IT", "electrical", "HVAC"),
    "Taby Centrum Mall": ("lift", "HVAC", "electrical", "refrigeration"),
    "Taby Riding Arena": ("plumbing", "electrical", "HVAC"),
    "Lidingo Care Home": ("HVAC", "lift", "plumbing", "electrical"),
    "Millesgarden Cafe": ("refrigeration", "plumbing", "electrical"),
    "Farsta Ice Rink": ("refrigeration", "electrical", "HVAC", "plumbing"),
    "Farsta Grocery Depot": ("refrigeration", "IT", "electrical"),
    "Bromma Airport Cargo": ("IT", "electrical", "lift", "HVAC"),
    "Bromma Blocks Cinema": ("HVAC", "electrical", "IT"),
    "Soder Harbour Restaurant": ("refrigeration", "plumbing", "electrical"),
    "Nacka Sports Centre": ("plumbing", "HVAC", "electrical", "lift"),
    "Solna Stadium Catering": ("refrigeration", "plumbing", "electrical"),
    "Sundbyberg Print Works": ("electrical", "HVAC", "IT"),
    "Kista Data Hall North": ("IT", "HVAC", "electrical"),
}
MISREAD = 0.03          # the trade may be misread: a customer without it keeps a little weight


def likelihood(trade, customer):
    eq = EQUIPMENT.get(customer)
    if trade is None or eq is None:
        return 1.0
    return (1 - MISREAD) * (trade in eq) / len(eq) + MISREAD / 6.0


def _weights(job, directory):
    """Unnormalised weight of each directory customer for one job."""
    out = []
    for c in directory:
        if job.get("zone") and c["zone"] != job["zone"]:
            out.append(0.0)
        elif c["zone"] in job.get("not_zones", ()):
            out.append(0.0)
        else:
            out.append(likelihood(job.get("trade"), c["name"]))
    return out


def customer_odds(seq, directory):
    """{jid: [p per directory customer]} for every job in seq without a customer."""
    names = [c["name"] for c in directory]
    K = len(names)
    out = {}
    prefix, rest = seq[:K], seq[K:]
    used = {j["customer"] for j in prefix if j.get("customer")}
    free = [k for k, n in enumerate(names) if n not in used]
    anon = [j for j in prefix if not j.get("customer")]
    if anon and len(anon) <= len(free):
        rows = [[_weights(j, directory)[k] for k in free] for j in anon]
        rows = [r if sum(r) > 0 else [1.0] * len(free) for r in rows]
        for j, r in zip(anon, rows):
            p = [0.0] * K
            s = sum(r) or 1.0
            for k, v in zip(free, r):
                p[k] = v / s
            out[j["jid"]] = p
    else:
        rest = anon + rest
    for j in rest:
        if j.get("customer"):
            continue
        w = _weights(j, directory)
        s = sum(w)
        out[j["jid"]] = [v / s for v in w] if s > 0 else [1.0 / K] * K
    return out


def zone_odds(seq, directory, sites):
    """{jid: {zone: probability}} over `sites` for every job in seq without a customer."""
    zone_of = [c["zone"] for c in directory]
    out = {}
    for jid, p in customer_odds(seq, directory).items():
        z = {}
        for k, v in enumerate(p):
            if v > 0 and zone_of[k] in sites:
                z[zone_of[k]] = z.get(zone_of[k], 0.0) + v
        s = sum(z.values())
        out[jid] = {k: v / s for k, v in z.items()} if s > 0 else {k: 1.0 / len(sites) for k in sites}
    return out
