"""Plan exact vault-scope revocations; --apply requires reviewed assignment IDs."""
from __future__ import annotations

import argparse
import json
import re
from uuid import UUID

from governedgraph.strategy_service.provision_auth import az


def revoke(vault_id: str, *, approved_assignment_ids: tuple[str, ...] = (),
           apply: bool = False) -> list[str]:
    vault = vault_id.rstrip("/")
    if not re.fullmatch(r"/subscriptions/[^/]+/resourceGroups/[^/]+/providers/"
                        r"Microsoft.KeyVault/vaults/[^/]+", vault, re.IGNORECASE):
        raise ValueError("an exact Key Vault resource ID is required")
    rows = az("role", "assignment", "list", "--scope", vault, "--include-inherited")
    if not isinstance(rows, list):
        raise TypeError("Azure CLI did not return a role-assignment list")
    direct = [row for row in rows if row["scope"].lower() == vault.lower()]
    targets = [row["id"] for row in direct]
    print(json.dumps({"apply": apply, "vault_id": vault, "vault_scope_assignments": direct,
                      "remove_exact_assignment_ids": targets}))
    if apply:
        prefix = vault.lower() + "/providers/microsoft.authorization/roleassignments/"
        for assignment in approved_assignment_ids:
            if not assignment.lower().startswith(prefix):
                raise ValueError("approved assignment must belong to this exact vault")
            UUID(assignment[len(prefix):])
        approved = {assignment.lower() for assignment in approved_assignment_ids}
        if not approved or {target.lower() for target in targets} - approved:
            raise ValueError("explicit reviewed IDs must cover every current vault-scope assignment")
        for target in targets:
            az("role", "assignment", "delete", "--ids", target)
    return targets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault-id", required=True)
    parser.add_argument("--assignment-id", action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    revoke(args.vault_id, approved_assignment_ids=tuple(args.assignment_id), apply=args.apply)


if __name__ == "__main__":
    main()
