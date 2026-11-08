"""Start a synthetic acceptance proof as an execution-only run of the existing dev-issuer job.

Everything environment-specific is read from the azd environment (`scripts/azd_env.py`):

    python -m scripts.run_dev_proof channel                       # --probe-channel through the orchestrator (+ gateway)
    python -m scripts.run_dev_proof voice --image <registry>/<repo>@sha256:<digest>   # explicit extension probe image

The job's stored template, identity and permissions are never changed: the image/command/arguments are overridden for
this single execution only, and no member token exists on this machine (the job mints it in process memory).
Output is the execution name; read the PASS/FAIL lines with --logs or your Log Analytics access.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from scripts.azd_env import ORCHESTRATOR_HOST, EnvError, load, matches_endpoint, problems

JOB = "job-gg-dev-member-issuer"
IMMUTABLE_IMAGE = re.compile(r"[a-z0-9.-]+\.azurecr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}")


def az(*args: str) -> str:
    return subprocess.check_output(["az", *args], text=True)


def build_template(template: dict, mode: str, orchestrator_url: str, apim_url: str, image: str | None) -> dict:
    container = template["containers"][0]
    if image:
        container["image"] = image
    container["command"] = ["python", "-m", "governedgraph.dev.issue_member_token"]
    container["args"] = (["--probe-voice", "--orchestrator-url", orchestrator_url] if mode == "voice"
                         else ["--probe-channel", "--orchestrator-url", orchestrator_url, "--apim-url", apim_url])
    return template


def validate(values: dict[str, str], mode: str, image: str | None) -> None:
    issues = problems("voice-proof", values)
    if issues:
        raise EnvError("; ".join(issues))
    if values["AZURE_ENV_NAME"] != "gg-dev":
        raise EnvError("synthetic proofs run only in the gg-dev environment")
    if mode == "voice" and not (image and IMMUTABLE_IMAGE.fullmatch(image)):
        raise EnvError("--image must be an immutable registry digest reference built from the extension probe target")
    if image and not IMMUTABLE_IMAGE.fullmatch(image):
        raise EnvError("--image must be an immutable registry digest reference")
    if image and image.split("/", 1)[0] != values["AZURE_CONTAINER_REGISTRY_ENDPOINT"]:
        raise EnvError("--image must come from this deployment's registry (AZURE_CONTAINER_REGISTRY_ENDPOINT)")


def orchestrator_endpoint(values: dict[str, str]) -> str:
    endpoint = values["ORCHESTRATOR_URL"].rstrip("/")
    if matches_endpoint(endpoint, ORCHESTRATOR_HOST):
        return endpoint
    query = ('[?tags."azd-service-name" == \'orchestrator\' && tags."azd-env-name" == \'gg-dev\']'
             '.properties.configuration.ingress.fqdn')
    output = az("containerapp", "list", "-g", values["AZURE_RESOURCE_GROUP"],
                "--subscription", values["AZURE_SUBSCRIPTION_ID"], "--query", query, "-o", "json")
    try:
        endpoints = json.loads(output)
    except json.JSONDecodeError:
        raise EnvError("could not read the orchestrator endpoint inventory as JSON") from None
    if not isinstance(endpoints, list) or len(endpoints) != 1 or not isinstance(endpoints[0], str):
        raise EnvError("expected exactly one gg-dev orchestrator Container App endpoint in the saved resource group")
    endpoint = "https://" + endpoints[0]
    if not matches_endpoint(endpoint, ORCHESTRATOR_HOST):
        raise EnvError("the tagged orchestrator does not provide an approved private Container App endpoint")
    return endpoint


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("channel", "voice"))
    parser.add_argument("--image")
    parser.add_argument("--env-file", help="read values from a dotenv file instead of `azd env get-values`")
    parser.add_argument("--environment", help="explicit azd environment; otherwise use the selected environment")
    parser.add_argument("--logs", action="store_true", help="print the last log lines of the started execution")
    args = parser.parse_args(argv)
    try:
        values = load(args.env_file, environment=args.environment)
        validate(values, args.mode, args.image)
        endpoint = orchestrator_endpoint(values)
        group = values["AZURE_RESOURCE_GROUP"]
        job = ["-g", group, "-n", JOB, "--subscription", values["AZURE_SUBSCRIPTION_ID"]]
        template = json.loads(az("containerapp", "job", "show", *job, "--query", "properties.template", "-o", "json"))
        template = build_template(template, args.mode, endpoint,
                                  values["APIM_GATEWAY_URL"].rstrip("/"), args.image)
        with tempfile.TemporaryDirectory(prefix="gg-proof-") as directory:
            path = Path(directory) / "execution.json"
            path.write_text(json.dumps(template))  # configuration only; no member token exists here
            execution = az("containerapp", "job", "start", *job, "--yaml", str(path),
                           "--query", "name", "-o", "tsv").strip()
        print(execution)
        if args.logs:
            print(az("containerapp", "job", "logs", "show", *job, "--execution", execution,
                     "--container", "issuer", "--tail", "30"))
    except EnvError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as error:
        print(f"error: az failed with exit code {error.returncode}; are you signed in (az login) with access to the job?",
              file=sys.stderr)
        return error.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
