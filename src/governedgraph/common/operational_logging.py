"""Content-free operational signals used by the Azure Monitor alert rules."""
import json
import logging


class OperationalFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        from governedgraph.common.contracts import REVIEW_ISSUES
        from governedgraph.observability import FIELDS

        fields = {"event": record.getMessage(), "data": "ILLUSTRATIVE - synthetic data"}
        names = FIELDS | {"queue", "age_seconds", "error_type", "failure_domain",
                     "snapshot_id", "arm", "source", "frozen",
                     "expected_responses", "matched_responses", "coverage_percent", "pending_records"}
        for name in names:
            if hasattr(record, name):
                fields[name] = getattr(record, name)
        verdict, issues = getattr(record, "verdict", None), getattr(record, "issues", None)
        if (record.getMessage() in {"answer_review_repair", "answer_review_nonpass"}
                and isinstance(verdict, str) and verdict in {"revise", "unassessable", "fail"}
                and isinstance(issues, list) and 0 < len(issues) <= len(REVIEW_ISSUES)
                and all(isinstance(issue, str) and issue in REVIEW_ISSUES for issue in issues)):
            fields.update(verdict=verdict, issues=issues)
        return json.dumps(fields)


def configure_operational_logging() -> None:
    logger = logging.getLogger("governedgraph")
    if not any(isinstance(handler.formatter, OperationalFormatter) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(OperationalFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
