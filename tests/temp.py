
import json
import urllib.request
import urllib.error

from azure.identity import AzureCliCredential

ENDPOINT = (
    "https://ggbm623f4b51.services.ai.azure.com"
    "/providers/microsoft/v1/systemone"
)

DEPLOYMENT_NAME = "decision-bench-v1"
SCOPE = "https://cognitiveservices.azure.com/.default"

payload = {
    "model": DEPLOYMENT_NAME,
    "state": "The API returns 500 on every call.",
    "questions": {
        "team": {
            "type": "choice",
            "instructions": "Which team should handle this ticket?",
            "criteria": {
                "billing": "Charges, invoices, and refunds",
                "engineering": "Bugs, errors, and outages",
                "support": "How-to and account questions"
            }
        }
    }
}

with AzureCliCredential() as credential:
    token = credential.get_token(SCOPE).token

request = urllib.request.Request(
    ENDPOINT,
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    },
    method="POST"
)

try:
    response = urllib.request.urlopen(request, timeout=60)
except urllib.error.HTTPError as error:
    response = error

with response:
    print(f"HTTP Status: {response.status}")
    print("Request ID:", response.headers.get("apim-request-id"))

    result = json.loads(response.read().decode("utf-8"))
    print(json.dumps(result, indent=2))

    if response.status is not None and response.status >= 400:
        raise SystemExit(1)
