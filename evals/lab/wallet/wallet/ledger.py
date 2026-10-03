import json
import os
import urllib.request
from decimal import Decimal

from wallet.accounts import Account


def record_transfer(source: Account, target: Account, amount: Decimal) -> str:
    url = os.environ["WALLET_LEDGER_URL"]
    body = json.dumps({"from": source.owner, "to": target.owner, "amount": str(amount)})
    request = urllib.request.Request(
        url, data=body.encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=0.5) as response:
        entry_id: str = json.load(response)["id"]
    return entry_id
