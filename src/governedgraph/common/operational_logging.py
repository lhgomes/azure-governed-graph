"""Content-free operational signals used by the Azure Monitor alert rules."""
import json
import logging
import re


class OperationalFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        from governedgraph.common.contracts import REVIEW_ISSUES
        from governedgraph.observability import FIELDS

        fields = {"event": record.getMessage(), "data": "ILLUSTRATIVE - synthetic data"}
        names = FIELDS | {"queue", "age_seconds", "error_type", "failure_domain",
                     "snapshot_id", "arm", "source", "frozen",
                     "expected_responses", "matched_responses", "coverage_percent", "pending_records", "published_count", "refused_count"}
        for name in names:
            if hasattr(record, name):
                fields[name] = getattr(record, name)
        if record.getMessage() in {"decision_model_failed", "decision_choice_unclear"}:
            status = getattr(record, "http_status", None)
            question = getattr(record, "decision_question", None)
            if type(status) is int and (status == 0 or 100 <= status <= 599):
                fields["http_status"] = status
            if isinstance(question, str) and re.fullmatch(r"[a-z][a-z_0-9]{0,63}", question):
                fields["decision_question"] = question
            for label in ("top_choice", "runner_up"):
                value = getattr(record, label, None)
                if isinstance(value, str) and re.fullmatch(r"[a-z][a-z_0-9]{0,63}", value):
                    fields[label] = value
            for score in ("top_probability", "runner_up_probability"):
                value = getattr(record, score, None)
                if type(value) in (int, float) and 0 <= value <= 1:
                    fields[score] = round(value, 3)
        verdict, issues = getattr(record, "verdict", None), getattr(record, "issues", None)
        if record.getMessage() == "llm_call":
            for name in ("call_id", "kind", "part", "outcome"):
                value = getattr(record, name, None)
                if isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9_]{0,63}", value):
                    fields[name] = value
            for name in ("chunk", "chunks"):
                value = getattr(record, name, None)
                if type(value) is int and 0 < value < 10_000:
                    fields[name] = value
            if isinstance(getattr(record, "text", None), str):
                fields["text"] = record.text
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
