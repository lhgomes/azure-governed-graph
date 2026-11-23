"""Keep the scheduled learning cycles from starting while a delivery changes what they depend on.

A cycle that overlaps a delivery can lose the Strategy Service or its fairness audit, and a failed cycle freezes
learning for every member until a person lifts it. The schedules are paused for the delivery and restored after it.
Nothing here touches the freeze, the learned order or any recovery path."""
from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager

from scripts.delivery import CommandError, DeliveryError

# The cadence of infra/modules/p2-jobs.bicep, used only when an interrupted delivery left a job paused.
JOBS = {"job-gg-p2-fairness": "0 */4 * * *", "job-gg-p2-pipeline": "2 */4 * * *"}
PAUSED = "0 0 1 1 *"
RUNNING = {"Pending", "Running", "Processing"}
WAIT_SECONDS = 1200


def _base(inputs) -> list[str]:
    return ["--subscription", inputs.subscription, "-g", inputs.resource_group]


def _schedules(runner, inputs) -> dict[str, str]:
    try:
        jobs = json.loads(runner.run(["az", "containerapp", "job", "list", *_base(inputs), "-o", "json"]))
    except CommandError as error:
        if "ResourceGroupNotFound" in error.stderr:
            return {}  # a new target has no schedules yet
        raise
    if not isinstance(jobs, list):
        raise DeliveryError("learning schedule observation is malformed")
    return {job["name"]: ((job["properties"]["configuration"].get("scheduleTriggerConfig") or {})
                          .get("cronExpression") or "") for job in jobs if job.get("name") in JOBS}


def _set(runner, inputs, job: str, cron: str) -> None:
    runner.run(["az", "containerapp", "job", "update", *_base(inputs), "-n", job, "--cron-expression", cron,
                "--only-show-errors", "-o", "none"])


def _wait_until_idle(runner, inputs, jobs, sleep, clock) -> None:
    deadline = clock() + WAIT_SECONDS
    while True:
        busy = [job for job in jobs if any(
            item.get("properties", {}).get("status") in RUNNING for item in json.loads(runner.run(
                ["az", "containerapp", "job", "execution", "list", *_base(inputs), "-n", job, "-o", "json"])))]
        if not busy:
            return
        if clock() > deadline:
            raise DeliveryError("a scheduled learning cycle is still running (" + ", ".join(busy)
                                + "); nothing was changed, rerun once it has finished")
        sleep(15)


def _restore(runner, inputs, held: dict[str, str]) -> None:
    current = _schedules(runner, inputs)
    for job, cron in held.items():
        # Otherwise the deployment already replaced it, possibly with a new cadence, which must be kept.
        if current.get(job) == PAUSED:
            _set(runner, inputs, job, cron)


@contextmanager
def paused(runner, inputs, *, sleep=time.sleep, clock=time.monotonic):
    held: dict[str, str] = {}
    try:
        for job, cron in _schedules(runner, inputs).items():
            held[job] = JOBS[job] if cron == PAUSED else cron
            if cron != PAUSED:
                _set(runner, inputs, job, PAUSED)
        if held:
            print("Learning schedules paused for the delivery; a running cycle is allowed to finish.", flush=True)
            _wait_until_idle(runner, inputs, held, sleep, clock)
        yield
    except BaseException:
        try:
            _restore(runner, inputs, held)
        except Exception as error:  # the delivery's own failure is the one to report
            print(f"Learning schedules were NOT restored ({type(error).__name__}); a full deployment or "
                  f"'az containerapp job update --cron-expression' on {', '.join(held)} restores them.",
                  file=sys.stderr, flush=True)
        raise
    else:
        _restore(runner, inputs, held)
        if held:
            print("Learning schedules restored.", flush=True)
