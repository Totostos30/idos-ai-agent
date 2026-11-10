"""Island features: weather (MET Norway format via provider=met, rain warning, the «weather» tool) and status (battery, Wi-Fi, VPN)."""
import asyncio
import collections
import datetime
import socket
import unittest
from unittest import mock

from core.features import status as status_mod
from core.features import weather as weather_mod
from core.tools import HINTS, ToolError
from tests.test_features import BusCase
from tests.test_island_info import FakeTools, tool_handler

BASE = datetime.datetime(2026, 11, 9, 11, 0, tzinfo=datetime.timezone.utc)
Stat = collections.namedtuple("Stat", "isup")
Addr = collections.namedtuple("Addr", "family address")


def forecast(base: datetime.datetime) -> dict:
    def entry(hours, temp, symbol, precip, six=False):
        moment = (base + datetime.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        details = {"air_temperature": temp, "wind_speed": 3.0, "relative_humidity": 80.0}
        ahead = {"summary": {"symbol_code": symbol}, "details": {"precipitation_amount": precip}}
        return {"time": moment, "data": {"instant": {"details": details}, ("next_6_hours" if six else "next_1_hours"): ahead}}

    series = [entry(0, 3.4, "cloudy", 0.0), entry(1, 3.0, "lightrain", 0.6), entry(2, 2.5, "rain", 1.2), entry(3, 2.0, "cloudy", 0.0)]
    series += [entry(h, 1.0 + h / 10, "partlycloudy_day", 0.0, six=True) for h in range(6, 60, 6)]
    return {"properties": {"timeseries": series}}


def this_hour() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(minute=0, second=0, microsecond=0)


class WeatherTest(BusCase):
    def test_parse_and_describe(self):
        state = weather_mod.parse_forecast(forecast(BASE), BASE.timestamp() + 600)
        self.assertEqual((state["temp"], state["feels"], state["text"], state["kind"]), (3, 1, "облачно", "cloudy"))
        self.assertEqual(state["rain_soon"], {"in_minutes": 50, "mm": 0.6, "kind": "rain"})
        self.assertEqual(len(state["hours"]), 4)
        self.assertGreaterEqual(len(state["days"]), 2)
        state["city"] = "Москва"
        text = weather_mod.summary_text(state)
        self.assertTrue(text.startswith("Москва: 3°C, облачно, ощущается как 1°C, ветер 3 м/с, влажность 80%"), text)
        self.assertIn("Через 50 мин", text)
        first_day = datetime.datetime.fromtimestamp(BASE.timestamp()).date()
        self.assertIn(f"{weather_mod.WEEKDAYS[first_day.weekday()]} {first_day:%d.%m}", weather_mod.summary_text(state, week=True))
        self.assertEqual(weather_mod.describe("heavyrainshowers_day"), "ливень")
        self.assertEqual(weather_mod.describe("lightssleetshowersandthunder"), "гроза")
        self.assertEqual((weather_mod.kind("partlycloudy_night"), weather_mod.kind("clearsky_day")), ("partly", "clear"))
        with self.assertRaises(ValueError):
            weather_mod.parse_forecast({})

    def test_refresh_and_rain_warning(self):
        self.app.settings.values.update({"weather.lat": 55.7558, "weather.lon": 37.6173, "weather.city": "Москва",
                                         "weather.provider": "met"})
        weather = weather_mod.create(self.app)
        fake = mock.AsyncMock(return_value=(forecast(this_hour()), {}))
        with mock.patch.object(weather_mod.llm, "request_json_async", fake):
            state = asyncio.run(weather.refresh())
            asyncio.run(weather.refresh())
        self.assertEqual(state["city"], "Москва")
        self.assertIn("lat=55.7558&lon=37.6173", fake.call_args.args[0])
        cards = [data for event, data in self.events if event == "island.card"]
        self.assertEqual(len(cards), 1)
        self.assertIn("ожидается дождь", cards[0]["text"])
        self.assertEqual(sum(1 for event, _ in self.events if event == "weather.state"), 2)

    def test_tool(self):
        tools = FakeTools()
        self.app.tools = tools
        weather_mod.create(self.app)
        handler = tool_handler(tools, "weather")
        self.app.settings.values["weather.provider"] = "met"
        place = {"results": [{"latitude": 55.7963, "longitude": 49.1088, "name": "Казань"}]}
        fake = mock.AsyncMock(side_effect=[(place, {}), (forecast(this_hour()), {})])
        with mock.patch.object(weather_mod.llm, "request_json_async", fake):
            text = asyncio.run(handler("now", {"city": "Казань"}, None))
        self.assertTrue(text.startswith("Казань: 3°C"), text)
        self.assertIn("погод", HINTS["weather"])
        with self.assertRaises(ToolError):
            asyncio.run(handler("week", {}, None))


class StatusTest(BusCase):
    def test_find_vpn_and_wifi(self):
        stats = {"Ethernet": Stat(True), "Teredo Tunneling Pseudo-Interface": Stat(True), "vEthernet (WSL)": Stat(True),
                 "AmneziaVPN": Stat(False), "WireGuard Tunnel": Stat(True)}
        addrs = {name: [Addr(socket.AF_INET, "10.0.0.2")] for name in stats}
        self.assertEqual(status_mod.find_vpn(stats, addrs), "WireGuard Tunnel")
        del stats["WireGuard Tunnel"]
        self.assertEqual(status_mod.find_vpn(stats, addrs), "")
        english = ("    State                  : connected\n    SSID                   : HomeNet 5G\n"
                   "    BSSID                  : aa:bb:cc:dd:ee:ff\n    Signal                 : 87%\n")
        russian = ("    Состояние              : подключено\n    SSID                   : Дача\n"
                   "    BSSID                  : aa:bb\n    Сигнал                 : 64%\n")
        self.assertEqual(status_mod.parse_wifi(english), {"ssid": "HomeNet 5G", "signal": 87})
        self.assertEqual(status_mod.parse_wifi(russian), {"ssid": "Дача", "signal": 64})
        self.assertEqual(status_mod.parse_wifi("    State : disconnected\n"), {"ssid": "", "signal": None})

    def test_refresh_events(self):
        status = status_mod.create(self.app)
        batteries = [{"percent": 50, "charging": False, "no_battery": False, "saver": False, "seconds_left": None},
                     {"percent": 19, "charging": False, "no_battery": False, "saver": False, "seconds_left": 3600},
                     {"percent": 19, "charging": True, "no_battery": False, "saver": False, "seconds_left": None}]
        with mock.patch.object(status_mod.config, "IS_WINDOWS", True), \
                mock.patch("core.win.battery", side_effect=batteries), \
                mock.patch.object(status_mod, "wifi_info", return_value={"ssid": "Home", "signal": 80}), \
                mock.patch.object(status_mod, "online", return_value=True), \
                mock.patch.object(status_mod, "find_vpn", side_effect=["", "WireGuard Tunnel", "WireGuard Tunnel"]):
            for _ in range(3):
                asyncio.run(status.refresh())
        names = [event for event, _ in self.events]
        self.assertEqual(names.count("status.state"), 3)
        self.assertIn("status.charging", names)
        cards = [data for event, data in self.events if event == "island.card"]
        self.assertEqual([c["title"] for c in cards], ["VPN", "Батарея"])
        self.assertIn("19%", cards[1]["text"])
        self.assertEqual(status.state["vpn"], {"on": True, "name": "WireGuard Tunnel"})
        self.assertEqual(status.state["wifi"], {"ssid": "Home", "signal": 80})


if __name__ == "__main__":
    unittest.main()
