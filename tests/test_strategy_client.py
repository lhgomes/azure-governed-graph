"""ILLUSTRATIVE - synthetic data; bounded Strategy Service transport without changing ranking."""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx
import pytest

from governedgraph.agents.clients import HttpStrategyClient
from governedgraph.common.config import settings
from strategy_doubles import local_strategy_service

REQUEST = {
    "journey_id": "J-SYNTHETIC", "journey_state": "first_contact", "intent": "transition_impact_question",
    "channel": "voice", "accessibility_mode": "standard",
}


@pytest.fixture
def ranking(tmp_path):
    service = local_strategy_service(tmp_path, control_share=0)
    return service.get_ranked(REQUEST).to_dict()


def test_b3_b7_strategy_client_has_a_bounded_timeout_and_preserves_the_contract(monkeypatch, ranking):
    def respond(request):
        assert request.url.path == "/strategies/ranked"
        assert request.extensions["timeout"] == {"connect": 5, "read": 5, "write": 5, "pool": 5}
        assert json.loads(request.content) == REQUEST
        assert request.headers["X-Journey-ID"] == REQUEST["journey_id"]
        assert "Authorization" not in request.headers
        return httpx.Response(200, json=ranking)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr("governedgraph.agents.connections.http_client", lambda: client)
        monkeypatch.setattr(settings.__class__, "strategy_service_url", property(lambda self: "https://synthetic.example"))
        assert HttpStrategyClient().get_ranked(REQUEST).to_dict() == ranking


def test_b3_strategy_response_slower_than_one_second_is_not_aborted(monkeypatch, ranking):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            time.sleep(1.2)
            body = json.dumps(ranking).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            origin = f"http://127.0.0.1:{server.server_port}"
            monkeypatch.setattr(settings.__class__, "strategy_service_url", property(lambda self: origin))
            with httpx.Client(trust_env=False) as client:
                monkeypatch.setattr("governedgraph.agents.connections.http_client", lambda: client)
                assert HttpStrategyClient().get_ranked(REQUEST).to_dict() == ranking
            assert received == [REQUEST]
        finally:
            server.shutdown()
            thread.join()


@pytest.mark.parametrize("failure", ["timeout", "unavailable"])
def test_b3_b9_strategy_transport_failure_is_not_retried_or_hidden(monkeypatch, failure):
    calls = []

    def respond(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr("governedgraph.agents.connections.http_client", lambda: client)
        monkeypatch.setattr(settings.__class__, "strategy_service_url", property(lambda self: "https://synthetic.example"))
        expected = httpx.ReadTimeout if failure == "timeout" else httpx.HTTPStatusError
        with pytest.raises(expected):
            HttpStrategyClient().get_ranked(REQUEST)
    assert len(calls) == 1
