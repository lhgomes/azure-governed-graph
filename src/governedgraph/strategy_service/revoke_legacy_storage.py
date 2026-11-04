"""Remove only the two superseded account-wide Blob Contributor assignments."""
from __future__ import annotations

import argparse
import json

from governedgraph.strategy_service.provision_auth import az


def revoke(account_id: str, runtime: str, pipeline: str, *, apply: bool = False) -> list[str]:
    assignments = az("role", "assignment", "list", "--scope", account_id)
    targets = [
        row["id"] for row in assignments
        if row["scope"].lower() == account_id.lower()
        and row["principalId"] in {runtime, pipeline}
        and row["roleDefinitionId"].split("/")[-1] == "ba92f5b4-2d11-453d-a403-e96b0029c9fe"
    ]
    print(json.dumps({"apply": apply, "remove_exact_assignment_ids": targets}))
    if apply:
        # Incremental ARM deployment does not remove assignments omitted from Bicep.
        for target in targets:
            az("role", "assignment", "delete", "--ids", target)
    return targets


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--pipeline", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    revoke(args.account_id, args.runtime, args.pipeline, apply=args.apply)
