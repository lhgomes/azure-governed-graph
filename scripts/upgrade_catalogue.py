"""Explicit signed source-catalogue upgrade; never part of ordinary deployment."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts import deploy
from scripts.azd_env import EnvError
from scripts.delivery import Composition, DeliveryError, ROOT, Runner
from scripts.deployment_target import new_target, read_inputs, select_target
from scripts.delivery_evidence import Evidence

SEQUENCE = (*deploy.SEQUENCE[:-1], "signed catalogue upgrade", "readiness")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True, type=Path, help="explicit private delivery inputs JSON")
    parser.add_argument("--reuse", required=True, metavar="RESOURCE_GROUP", help="existing gg-dev target to upgrade")
    parser.add_argument("--plan", action="store_true", help="local sequence only; no cloud commands")
    args = parser.parse_args(argv)
    evidence = None
    try:
        raw = read_inputs(args.inputs)
        inputs = new_target(raw)
        composition = Composition.load(ROOT / inputs.composition)
        if args.plan:
            print(json.dumps({"environment": inputs.environment, "composition": composition.name,
                              "target": args.reuse, "sequence": SEQUENCE}))
            return 0
        evidence = Evidence(ROOT)
        evidence.data["selection"] = {"subscription": inputs.subscription, "reuse_requested": True,
                                      "create_new": False}
        evidence.write()
        runner = Runner(evidence=evidence)
        inputs = select_target(runner, args.inputs, raw, inputs, reuse=args.reuse, create_new=False)
        with deploy.paused(runner, inputs):
            receipt = deploy.deploy(runner, inputs, composition, catalogue_upgrade=True)
        evidence.finish("succeeded")
        print(json.dumps({"readiness": receipt["readiness"], "evaluation": "not_run",
                          "strategy_initialization": receipt["bootstrap"]["priors"]["initialize"]}))
        return 0
    except KeyboardInterrupt:
        if evidence is not None:
            evidence.failure("interrupted")
        print("Catalogue upgrade interrupted; submitted Azure operations may still be running. "
              "Inspect their status before rerunning.", file=sys.stderr)
        return 130
    except (DeliveryError, EnvError, OSError, ValueError, TypeError, KeyError) as error:
        if evidence is not None:
            evidence.failure("failed", error)
        print(f"Catalogue upgrade failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
