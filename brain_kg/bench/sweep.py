"""Sweep the entity-hop scoring constants to check the KG win is robust, not a
knife-edge fit (DIFF-gate recommendation: the constants were unswept).

For each (support_decay, floor, comention_w, relation_w) in a grid, set the env
vars, re-import config + retrieve (so they pick up the new values), run the
bench, and record net_B on the discriminating queries. A win that holds across a
RANGE of settings is a real signal; one that only appears at the hand-picked
point is suspect.

Run:  python -m brain_kg.bench.sweep
"""
from __future__ import annotations

import importlib
import json
import os

DECAYS = [0.4, 0.5, 0.6, 0.7, 0.8]
FLOORS = [0.3, 0.5, 0.7]
# co_mention vs relation weight pairs
WEIGHTS = [(1.0, 0.5), (1.0, 0.0), (1.0, 1.0), (0.7, 0.3)]


def _run_once() -> dict:
    # Reload config + retrieve so the new env vars take effect.
    import brain_kg.config as cfg
    importlib.reload(cfg)
    import brain_kg.retrieve as retr
    importlib.reload(retr)
    from brain_kg.bench import harness
    importlib.reload(harness)
    rep = harness.run(query_files=["queries_v3.jsonl"])
    rows = rep["rows"]
    disc = [r for r in rows if r["class"] in ("state-pair", "multi-hop")]
    sp = [r for r in rows if r["class"] == "state-pair"]
    sh = [r for r in rows if r["class"] == "single-hop"]
    net = (sum(1 for r in disc if r["B_hit"] and not r["A_hit"])
           - sum(1 for r in disc if r["A_hit"] and not r["B_hit"]))
    return {
        "state_pair_B": sum(1 for r in sp if r["B_hit"]),
        "state_pair_A": sum(1 for r in sp if r["A_hit"]),
        "single_hop_B": sum(1 for r in sh if r["B_hit"]),
        "single_hop_A": sum(1 for r in sh if r["A_hit"]),
        "net_B": net,
    }


def sweep() -> list[dict]:
    results = []
    for decay in DECAYS:
        for floor in FLOORS:
            for cw, rw in WEIGHTS:
                os.environ["OPEN_BRAIN_KG_ENT_SUPPORT_DECAY"] = str(decay)
                os.environ["OPEN_BRAIN_KG_ENT_FLOOR"] = str(floor)
                os.environ["OPEN_BRAIN_KG_ENT_COMENTION_W"] = str(cw)
                os.environ["OPEN_BRAIN_KG_ENT_RELATION_W"] = str(rw)
                r = _run_once()
                r.update({"decay": decay, "floor": floor, "cw": cw, "rw": rw})
                results.append(r)
                print(f"decay={decay} floor={floor} cw={cw} rw={rw} -> "
                      f"state-pair B={r['state_pair_B']}/6 net_B={r['net_B']} "
                      f"single-hop B={r['single_hop_B']}/3")
    return results


if __name__ == "__main__":  # pragma: no cover
    res = sweep()
    wins = [r for r in res if r["net_B"] >= 3 and r["single_hop_B"] >= r["single_hop_A"]]
    print(f"\n=== {len(wins)}/{len(res)} settings meet the bar (net_B>=3, no single-hop regression) ===")
    nets = sorted(set(r["net_B"] for r in res))
    print("net_B range across all settings:", nets)
    with open(os.path.join(os.path.dirname(__file__), "sweep_results.json"), "w") as f:
        json.dump(res, f, indent=2)
