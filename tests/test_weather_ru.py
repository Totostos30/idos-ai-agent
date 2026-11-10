"""Weather providers that work in Russia: Open-Meteo (default), wttr.in (fallback), Yandex (with a key), and the fallback order."""
import asyncio
import datetime
import time
import unittest
from unittest import mock

from core import llm
from core.features import weather as weather_mod
from tests.test_features import BusCase


def hour0() -> int:
    return int(datetime.datetime.now(datetime.timezone.utc).replace(minute=0, second=0, microsecond=0).timestamp())


def open_meteo(start: int, hours: int = 48) -> dict:
    times = [start + 3600 * i for i in range(hours)]
    codes = [3, 61, 63] + [2] * (hours - 3)
    rain = [0.0, 0.0, 0.7, 1.4] + [0.0] * (hours - 4)
    return {"utc_offset_seconds": 10800, "hourly": {
        "time": times, "temperature_2m": [4.2 - i * 0.05 for i in range(hours)],
        "relative_humidity_2m": [85] * hours, "precipitation": rain, "weather_code": codes,
        "wind_speed_10m": [5.0] * hours, "is_day": [1] * hours}}


def wttr() -> dict:
    today = datetime.date.today()
    days = []
    for d in range(3):
        date = (today + datetime.timedelta(days=d)).isoformat()
        days.append({"date": date, "hourly": [{"time": str(h * 100), "tempC": str(2 + d), "humidity": "90",
                                               "windspeedKmph": "18", "weatherCode": "326", "precipMM": "0.9"}
                                              for h in range(0, 24, 3)]})
    return {"weather": days}


def yandex(start: int) -> dict:
    today = datetime.date.today()
    near = {"date": today.isoformat(), "hours": [{"hour_ts": start + 3600 * i, "temp": -1, "condition": "light-snow",
                                                  "prec_mm": 0.3, "wind_speed": 4, "humidity": 88} for i in range(24)]}
    far = {"date": (today + datetime.timedelta(days=3)).isoformat(), "hours": [],
           "parts": {"day": {"temp_avg": 2, "condition": "overcast"}, "night": {"temp_avg": -3, "condition": "clear"}}}
    return {"forecasts": [near, far]}


class ProvidersTest(unittest.TestCase):
    def test_open_meteo(self):
        start = hour0()
        state = weather_mod.parse_forecast(weather_mod.from_open_meteo(open_meteo(start)), start + 600)
        self.assertEqual(state["temp"], 4)
        self.assertEqual(state["text"], "облачно")
        self.assertEqual(state["wind"], 5.0)
        self.assertIsNotNone(state["rain_soon"])  # precipitation is shifted to the following hour
        self.assertGreaterEqual(len(state["days"]), 2)
        self.assertEqual(weather_mod.kind("partlycloudy_day"), "partly")

    def test_open_meteo_iso_times(self):
        data = open_meteo(hour0(), 3)
        data["hourly"]["time"] = ["2026-11-09T14:00", "2026-11-09T15:00", "2026-11-09T16:00"]
        series = weather_mod.from_open_meteo(data)["properties"]["timeseries"]
        self.assertEqual(series[0]["time"], "2026-11-09T11:00:00+00:00")

    def test_wttr(self):
        state = weather_mod.parse_forecast(weather_mod.from_wttr(wttr()))
        self.assertEqual(state["text"], "небольшой снег")
        self.assertEqual(state["wind"], 5.0)
        self.assertEqual(len(state["hours"]), 13)

    def test_yandex(self):
        start = hour0()
        state = weather_mod.parse_forecast(weather_mod.from_yandex(yandex(start)), start + 60)
        self.assertEqual((state["temp"], state["kind"]), (-1, "snow"))
        self.assertEqual(state["days"][-1]["max"], 2)

    def test_short_error_hides_html(self):
        exc = llm.HTTPError(404, "404 Not found #intro { margin-top: 58px; } Home Documentation")
        self.assertEqual(weather_mod.short_error(exc), "HTTP 404")

    def test_empty(self):
        with self.assertRaises(ValueError):
            weather_mod.parse_forecast(weather_mod.from_open_meteo({}))
        with self.assertRaises(ValueError):
            weather_mod.parse_forecast(weather_mod.from_wttr([]))


class FallbackTest(BusCase):
    def make(self, **values):
        self.app.settings.values.update({"weather.lat": 59.9386, "weather.lon": 30.3141, "weather.city": "Санкт-Петербург",
                                         **values})
        return weather_mod.create(self.app)

    def test_order(self):
        self.assertEqual(self.make().providers(), ["open-meteo", "wttr"])
        self.assertEqual(self.make(**{"weather.yandex_key": "k"}).providers(), ["yandex", "open-meteo", "wttr"])
        self.assertEqual(self.make(**{"weather.provider": "wttr", "weather.yandex_key": ""}).providers(),
                         ["wttr", "open-meteo"])
        self.assertEqual(self.make(**{"weather.provider": "met"}).providers(), ["met", "open-meteo", "wttr"])

    def test_open_meteo_down_uses_wttr(self):
        weather = self.make()
        fake = mock.AsyncMock(side_effect=[llm.HTTPError(404, "<html>big page</html>"), (wttr(), {})])
        with mock.patch.object(weather_mod.llm, "request_json_async", fake):
            state = asyncio.run(weather.refresh())
        self.assertEqual(state["source"], "wttr.in")
        self.assertIn("api.open-meteo.com", fake.call_args_list[0].args[0])
        self.assertIn("latitude=59.9386", fake.call_args_list[0].args[0])
        self.assertIn("wttr.in/59.9386,30.3141", fake.call_args_list[1].args[0])
        self.assertIn("Источник: wttr.in", weather_mod.summary_text(state))

    def test_all_down_short_message(self):
        weather = self.make()
        fake = mock.AsyncMock(side_effect=[llm.HTTPError(404, "<html>" + "x" * 500), llm.NetworkError("timed out")])
        with mock.patch.object(weather_mod.llm, "request_json_async", fake):
            with self.assertRaises(ValueError) as caught:
                asyncio.run(weather.forecast(59.9, 30.3))
        self.assertEqual(str(caught.exception), "Open-Meteo — HTTP 404; wttr.in — timed out")

    def test_yandex_key_header(self):
        weather = self.make(**{"weather.yandex_key": "secret"})
        start = hour0()
        fake = mock.AsyncMock(return_value=(yandex(start), {}))
        with mock.patch.object(weather_mod.llm, "request_json_async", fake):
            state = asyncio.run(weather.refresh())
        self.assertEqual(state["source"], "Яндекс Погода")
        self.assertEqual(fake.call_args.args[2]["X-Yandex-Weather-Key"], "secret")

    def test_geocode_open_meteo_then_nominatim(self):
        weather = self.make()
        found = {"results": [{"latitude": 59.93863, "longitude": 30.31413, "name": "Санкт-Петербург"}]}
        fake = mock.AsyncMock(return_value=(found, {}))
        with mock.patch.object(weather_mod.llm, "request_json_async", fake):
            place = asyncio.run(weather.geocode("Питер"))
        self.assertEqual(place, {"lat": 59.9386, "lon": 30.3141, "city": "Санкт-Петербург"})
        nominatim = [{"lat": "55.75", "lon": "37.61", "display_name": "Москва, Россия"}]
        fake = mock.AsyncMock(side_effect=[llm.NetworkError("blocked"), (nominatim, {})])
        with mock.patch.object(weather_mod.llm, "request_json_async", fake):
            place = asyncio.run(weather.geocode("Москва"))
        self.assertEqual(place["city"], "Москва")
        fake = mock.AsyncMock(side_effect=[({"results": []}, {}), ([], {})])
        with mock.patch.object(weather_mod.llm, "request_json_async", fake):
            with self.assertRaises(ValueError) as caught:
                asyncio.run(weather.geocode("Нигдеград"))
        self.assertIn("не нашёл город", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
