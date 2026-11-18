export AZURE_ENDPOINT="https://ggbm623f4b51.services.ai.azure.com"
export DEPLOYMENT_NAME="decision-bench-v1"

export AZURE_ENTRA_TOKEN=$(az account get-access-token \
  --resource https://cognitiveservices.azure.com \
  --query accessToken \
  --output tsv)

 curl "$AZURE_ENDPOINT/providers/microsoft/v1/systemone" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $AZURE_ENTRA_TOKEN" \
  -d '{
    "model": "'"$DEPLOYMENT_NAME"'",
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
  }' 