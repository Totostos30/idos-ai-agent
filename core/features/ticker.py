"""Currency ticker for the island: USD/EUR rates from the Central Bank of Russia (cbr-xml-daily.ru),
refreshed twice a day — the feed itself publishes the previous value for the ↑/↓ arrow."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request

from . import Feature

log = logging.getLogger("idos.ticker")
URL = "https://cbr-xml-daily.ru/daily_json.js"
POLL = 6 * 3600.0
UA = {"User-Agent": "IDOS/1.0 (island ticker)"}


def fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    out = {}
    for code in ("USD", "EUR"):
        v = (data.get("Valute") or {}).get(code)
        if v:
            out[code.lower()] = {"value": round(float(v.get("Value") or 0), 2),
                                 "prev": round(float(v.get("Previous") or 0), 2)}
    return out


class Ticker(Feature):
    name = "ticker"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.rates: dict = {}
        self.updated = 0.0
        self.error = ""

    def state(self) -> dict:
        rates = {k: {**v, "diff": round(v["value"] - v["prev"], 2)} for k, v in self.rates.items()}
        return {"rates": rates, "updated": self.updated, "error": self.error}

    async def refresh(self) -> None:
        try:
            self.rates = await asyncio.to_thread(fetch, URL)
            self.updated = time.time()
            self.error = ""
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            log.debug("ticker refresh: %s", exc)
            return
        from .. import bus
        bus.emit("ticker.changed", self.state())

    async def _loop(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(POLL)

    async def start(self) -> None:
        self.spawn(self._loop(), "poll")

    def methods(self) -> dict:
        async def get_state() -> dict:
            if not self.rates:
                await self.refresh()
            return self.state()

        async def refresh() -> dict:
            await self.refresh()
            return self.state()

        return {"ticker.state": get_state, "ticker.refresh": refresh}


def create(app) -> Ticker:
    return Ticker(app)
