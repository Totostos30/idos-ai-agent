"""Weather for the island with services that work in Russia: Open-Meteo (default, no key), wttr.in (fallback, no key),
optional Yandex Weather (with a key) and MET Norway. Open-Meteo geocoding with a Nominatim fallback, a rain warning
and a «weather» tool for the agent. Every provider is converted to one timeseries shape and parsed by parse_forecast."""
from __future__ import annotations

import asyncio
import datetime
import logging
import time
import urllib.parse

from .. import bus, llm
from . import Feature

log = logging.getLogger("idos.weather")
MET = "https:" + "//api.met" + ".no/weatherdata/locationforecast/2.0/compact"
NOMINATIM = "https:" + "//nominatim.openstreetmap" + ".org/search"
HEADERS = {"User-Agent": "IDOS/2.0 (desktop assistant)"}
OPEN_METEO = "https:" + "//api.open-meteo" + ".com/v1/forecast"
OM_GEO = "https:" + "//geocoding-api.open-meteo" + ".com/v1/search"
WTTR = "https:" + "//wttr" + ".in/"
YANDEX = "https:" + "//api.weather.yandex" + ".ru/v2/forecast"
PROVIDERS = ("open-meteo", "wttr", "yandex", "met")
PROVIDER_NAMES = {"open-meteo": "Open-Meteo", "wttr": "wttr.in", "yandex": "Яндекс Погода", "met": "MET Norway"}
# WMO weather codes (Open-Meteo) -> MET-style symbols
WMO = {0: "clearsky", 1: "fair", 2: "partlycloudy", 3: "cloudy", 45: "fog", 48: "fog", 51: "lightrain", 53: "lightrain",
       55: "rain", 56: "sleet", 57: "sleet", 61: "lightrain", 63: "rain", 65: "heavyrain", 66: "sleet", 67: "sleet",
       71: "lightsnow", 73: "snow", 75: "heavysnow", 77: "snow", 80: "lightrain", 81: "rain", 82: "heavyrain",
       85: "lightsnow", 86: "heavysnow", 95: "thunder", 96: "thunder", 99: "thunder"}
# World Weather Online codes (wttr.in) -> MET-style symbols
WWO = {113: "clearsky", 116: "partlycloudy", 119: "cloudy", 122: "cloudy", 143: "fog", 248: "fog", 260: "fog",
       176: "lightrain", 263: "lightrain", 266: "lightrain", 293: "lightrain", 296: "lightrain", 353: "lightrain",
       299: "rain", 302: "rain", 356: "rain", 305: "heavyrain", 308: "heavyrain", 359: "heavyrain",
       179: "lightsnow", 323: "lightsnow", 326: "lightsnow", 368: "lightsnow", 227: "snow", 329: "snow", 332: "snow",
       230: "heavysnow", 335: "heavysnow", 338: "heavysnow", 371: "heavysnow", 182: "sleet", 185: "sleet", 281: "sleet",
       284: "sleet", 311: "sleet", 314: "sleet", 317: "sleet", 320: "sleet", 350: "sleet", 362: "sleet", 365: "sleet",
       374: "sleet", 377: "sleet", 200: "thunder", 386: "thunder", 389: "thunder", 392: "thunder", 395: "thunder"}
# Yandex Weather conditions -> MET-style symbols
YA = {"clear": "clearsky", "partly-cloudy": "partlycloudy", "cloudy": "cloudy", "overcast": "cloudy",
      "drizzle": "lightrain", "light-rain": "lightrain", "rain": "rain", "moderate-rain": "rain", "heavy-rain": "heavyrain",
      "continuous-heavy-rain": "heavyrain", "showers": "heavyrain", "wet-snow": "sleet", "hail": "sleet",
      "light-snow": "lightsnow", "snow": "snow", "snow-showers": "heavysnow", "thunderstorm": "thunder",
      "thunderstorm-with-rain": "thunder", "thunderstorm-with-hail": "thunder"}
YA_PARTS = (("night", 3), ("morning", 9), ("day", 15), ("evening", 21))


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _daynight(symbol: str, day: bool) -> str:
    if symbol in ("clearsky", "fair", "partlycloudy"):
        return symbol + ("_day" if day else "_night")
    return symbol


def _entry(moment: float, temp, wind, humidity, symbol: str, precip) -> dict:
    return {"time": datetime.datetime.fromtimestamp(moment, datetime.timezone.utc).isoformat(),
            "data": {"instant": {"details": {"air_temperature": temp, "wind_speed": wind, "relative_humidity": humidity}},
                     "next_1_hours": {"summary": {"symbol_code": symbol},
                                      "details": {"precipitation_amount": precip or 0.0}}}}


def _series(entries: list) -> dict:
    entries.sort(key=lambda e: e["time"])
    return {"properties": {"timeseries": entries}}


def from_open_meteo(data: dict) -> dict:
    data = data if isinstance(data, dict) else {}
    hourly = data.get("hourly") or {}
    offset = datetime.timezone(datetime.timedelta(seconds=int(data.get("utc_offset_seconds") or 0)))

    def col(name: str, i: int):
        values = hourly.get(name) or []
        return values[i] if 0 <= i < len(values) else None

    entries = []
    for i, moment in enumerate(hourly.get("time") or []):
        if isinstance(moment, str):
            try:
                moment = datetime.datetime.fromisoformat(moment).replace(tzinfo=offset).timestamp()
            except ValueError:
                continue
        code = col("weather_code", i)
        symbol = WMO.get(int(code), "cloudy") if code is not None else ""
        # Open-Meteo precipitation is the sum of the preceding hour; MET's next_1_hours is the following hour
        precip = _num(col("precipitation", i + 1))
        if precip is None:
            precip = _num(col("precipitation", i))
        entries.append(_entry(float(moment), _num(col("temperature_2m", i)), _num(col("wind_speed_10m", i)),
                              _num(col("relative_humidity_2m", i)), _daynight(symbol, col("is_day", i) != 0), precip))
    return _series(entries)


def from_wttr(data: dict) -> dict:
    data = data if isinstance(data, dict) else {}
    entries = []
    for day in data.get("weather") or []:
        for hour in day.get("hourly") or []:
            try:
                hhmm = int(hour.get("time") or 0)
                start = datetime.datetime.fromisoformat(str(day.get("date"))).replace(hour=hhmm // 100, minute=hhmm % 100)
                code = int(hour.get("weatherCode") or 0)
            except (TypeError, ValueError):
                continue
            temp, hum = _num(hour.get("tempC")), _num(hour.get("humidity"))
            wind = _num(hour.get("windspeedKmph"))
            wind = round(wind / 3.6, 1) if wind is not None else None
            precip = (_num(hour.get("precipMM")) or 0.0) / 3
            for step in range(3):  # wttr.in gives 3-hour steps; spread them into hours
                local = start + datetime.timedelta(hours=step)
                entries.append(_entry(local.timestamp(), temp, wind, hum,
                                      _daynight(WWO.get(code, "cloudy"), 7 <= local.hour < 19), precip))
    return _series(entries)


def from_yandex(data: dict) -> dict:
    data = data if isinstance(data, dict) else {}
    entries = []
    for day in data.get("forecasts") or []:
        hours = day.get("hours") or []
        for hour in hours:
            moment = _num(hour.get("hour_ts"))
            if moment is None:
                continue
            local = datetime.datetime.fromtimestamp(moment)
            entries.append(_entry(moment, _num(hour.get("temp")), _num(hour.get("wind_speed")), _num(hour.get("humidity")),
                                  _daynight(YA.get(str(hour.get("condition") or ""), "cloudy"), 7 <= local.hour < 19),
                                  _num(hour.get("prec_mm"))))
        if hours or not day.get("date"):
            continue
        try:
            date = datetime.datetime.fromisoformat(str(day["date"]))
        except ValueError:
            continue
        for name, at in YA_PARTS:  # far days only have parts of the day
            part = (day.get("parts") or {}).get(name) or {}
            temp = _num(part.get("temp_avg", part.get("temp")))
            if temp is None:
                continue
            entries.append(_entry(date.replace(hour=at).timestamp(), temp, _num(part.get("wind_speed")),
                                  _num(part.get("humidity")),
                                  _daynight(YA.get(str(part.get("condition") or ""), "cloudy"), name in ("morning", "day")),
                                  (_num(part.get("prec_mm")) or 0.0) / 6))
    return _series(entries)


def short_error(exc: Exception) -> str:
    if isinstance(exc, llm.HTTPError):
        return f"HTTP {exc.status}"
    text = " ".join(str(exc).split())
    return (text[:117] + "…") if len(text) > 120 else (text or type(exc).__name__)
REFRESH = 1800
SYMBOLS = (("thunder", "гроза"), ("heavysleet", "сильный мокрый снег"), ("sleet", "мокрый снег"), ("heavysnow", "сильный снег"),
           ("lightsnow", "небольшой снег"), ("snow", "снег"), ("heavyrain", "ливень"), ("lightrain", "небольшой дождь"),
           ("rain", "дождь"), ("fog", "туман"), ("partlycloudy", "переменная облачность"), ("fair", "малооблачно"),
           ("cloudy", "облачно"), ("clearsky", "ясно"))
KINDS = ("thunder", "sleet", "snow", "rain", "fog", "partlycloudy", "fair", "cloudy", "clearsky")
WET = {"rain", "sleet", "snow", "thunder"}
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def describe(symbol: str) -> str:
    base = (symbol or "").split("_")[0]
    return next((text for key, text in SYMBOLS if key in base), "")


def kind(symbol: str) -> str:
    base = (symbol or "").split("_")[0]
    found = next((key for key in KINDS if key in base), "cloudy")
    return {"partlycloudy": "partly", "clearsky": "clear"}.get(found, found)


def feels_like(temp: float | None, wind: float | None) -> int | None:
    if temp is None:
        return None
    speed = (wind or 0.0) * 3.6
    if temp <= 10 and speed > 4.8:
        v = speed ** 0.16
        return round(13.12 + 0.6215 * temp - 11.37 * v + 0.3965 * temp * v)
    return round(temp)


def parse_forecast(data: dict, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    points = []
    for entry in ((data or {}).get("properties") or {}).get("timeseries") or []:
        try:
            moment = datetime.datetime.fromisoformat(str(entry["time"]).replace("Z", "+00:00")).timestamp()
        except (KeyError, ValueError):
            continue
        body = entry.get("data") or {}
        details = (body.get("instant") or {}).get("details") or {}
        ahead = body.get("next_1_hours") or body.get("next_6_hours") or {}
        points.append({"time": moment, "temp": details.get("air_temperature"), "wind": details.get("wind_speed"),
                       "humidity": details.get("relative_humidity"),
                       "symbol": (ahead.get("summary") or {}).get("symbol_code", ""),
                       "precip": float((ahead.get("details") or {}).get("precipitation_amount") or 0.0),
                       "hourly": "next_1_hours" in body})
    if not points:
        raise ValueError("пустой прогноз")
    current = next((p for p in points if p["time"] + 3600 > now), points[-1])
    upcoming = [p for p in points if p["time"] >= current["time"]]
    hours = [p for p in upcoming if p["hourly"]][:13]
    rain_soon = None
    if kind(current["symbol"]) not in WET:
        for point in hours[1:3]:
            if point["precip"] >= 0.2 and kind(point["symbol"]) in WET:
                rain_soon = {"in_minutes": max(5, int((point["time"] - now) / 60)), "mm": point["precip"],
                             "kind": kind(point["symbol"])}
                break
    days: dict[str, dict] = {}
    for point in upcoming:
        if point["temp"] is None:
            continue
        local = datetime.datetime.fromtimestamp(point["time"])
        key = local.date().isoformat()
        day = days.setdefault(key, {"date": key, "min": point["temp"], "max": point["temp"], "symbols": []})
        day["min"], day["max"] = min(day["min"], point["temp"]), max(day["max"], point["temp"])
        if 9 <= local.hour <= 18 and point["symbol"]:
            day["symbols"].append(point["symbol"])
    day_list = []
    for day in list(days.values())[:7]:
        symbols = day.pop("symbols")
        symbol = max(sorted(set(symbols)), key=symbols.count) if symbols else ""
        day_list.append({**day, "min": round(day["min"]), "max": round(day["max"]), "symbol": symbol,
                         "kind": kind(symbol), "text": describe(symbol)})
    temp = current["temp"]
    return {"ok": True, "temp": round(temp) if temp is not None else None, "feels": feels_like(temp, current["wind"]),
            "wind": current["wind"], "humidity": current["humidity"], "symbol": current["symbol"],
            "kind": kind(current["symbol"]), "text": describe(current["symbol"]),
            "night": current["symbol"].endswith("_night"), "precip_hour": current["precip"], "rain_soon": rain_soon,
            "hours": [{"time": p["time"], "temp": round(p["temp"]) if p["temp"] is not None else None,
                       "symbol": p["symbol"], "kind": kind(p["symbol"]), "precip": p["precip"]} for p in hours],
            "days": day_list, "updated": now}


def summary_text(state: dict, week: bool = False) -> str:
    head = f"{state.get('city') or 'Сейчас'}: {state.get('temp')}°C, {state.get('text') or 'без осадков'}"
    if state.get("feels") is not None and state.get("feels") != state.get("temp"):
        head += f", ощущается как {state['feels']}°C"
    head += f", ветер {float(state.get('wind') or 0):.0f} м/с"
    if state.get("humidity") is not None:
        head += f", влажность {float(state['humidity']):.0f}%"
    lines = [head]
    if week:
        for day in state.get("days", []):
            weekday = WEEKDAYS[datetime.date.fromisoformat(day["date"]).weekday()]
            lines.append(f"{weekday} {day['date'][8:10]}.{day['date'][5:7]}: {day['min']}…{day['max']}°C, {day['text'] or '—'}")
        return "\n".join(lines)
    parts = []
    for hour in state.get("hours", [])[1:7]:
        rain = f", {hour['precip']:.1f} мм" if hour.get("precip") else ""
        parts.append(f"{datetime.datetime.fromtimestamp(hour['time']).strftime('%H:%M')} {hour['temp']}°{rain}")
    if parts:
        lines.append("Ближайшие часы: " + "; ".join(parts))
    soon = state.get("rain_soon")
    if soon:
        lines.append(f"Через {soon['in_minutes']} мин ожидаются осадки ({soon['mm']:.1f} мм)")
    if state.get("source"):
        lines.append(f"Источник: {state['source']}")
    return "\n".join(lines)


class Weather(Feature):
    name = "weather"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.state: dict = {"ok": False, "reason": "укажите город в настройках погоды"}
        self._warned = 0.0
        if getattr(app, "tools", None) is not None:
            register_tool(app.tools, self)

    def location(self) -> tuple[float, float, str] | None:
        lat, lon = self.settings.get("weather.lat"), self.settings.get("weather.lon")
        if lat in (None, "") or lon in (None, ""):
            return None
        return float(lat), float(lon), str(self.settings.get("weather.city", "") or "")

    async def geocode(self, city: str) -> dict:
        errors = []
        try:  # Open-Meteo geocoding works from Russia and knows Russian names
            query = urllib.parse.urlencode({"name": city, "count": 1, "language": "ru", "format": "json"})
            found, _ = await llm.request_json_async(f"{OM_GEO}?{query}", None, HEADERS, "GET", 15)
            results = (found or {}).get("results") if isinstance(found, dict) else None
            if results:
                top = results[0]
                return {"lat": round(float(top["latitude"]), 4), "lon": round(float(top["longitude"]), 4),
                        "city": str(top.get("name") or city)}
        except (llm.HTTPError, llm.NetworkError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"Open-Meteo — {short_error(exc)}")
        try:
            query = urllib.parse.urlencode({"q": city, "format": "json", "limit": 1, "accept-language": "ru"})
            found, _ = await llm.request_json_async(f"{NOMINATIM}?{query}", None, HEADERS, "GET", 15)
            if found and isinstance(found, list):
                top = found[0]
                name = str(top.get("display_name") or city).split(",")[0].strip() or city
                return {"lat": round(float(top["lat"]), 4), "lon": round(float(top["lon"]), 4), "city": name}
        except (llm.HTTPError, llm.NetworkError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"Nominatim — {short_error(exc)}")
        if len(errors) == 2:
            raise ValueError("не удалось найти город: " + "; ".join(errors))
        raise ValueError(f"не нашёл город «{city}»")

    def providers(self) -> list[str]:
        """Chosen provider first, then the rest. Yandex is used only with a key, MET Norway only when chosen."""
        choice = str(self.settings.get("weather.provider", "auto") or "auto").strip().lower()
        key = str(self.settings.get("weather.yandex_key", "") or "").strip()
        order = (["yandex"] if key else []) + ["open-meteo", "wttr"]
        if choice in PROVIDERS:
            order = [choice] + [p for p in order if p != choice]
        return order

    async def fetch(self, provider: str, lat: float, lon: float) -> dict:
        if provider == "open-meteo":
            query = urllib.parse.urlencode({"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
                                            "hourly": "temperature_2m,relative_humidity_2m,precipitation,weather_code,"
                                                      "wind_speed_10m,is_day",
                                            "wind_speed_unit": "ms", "timezone": "auto", "forecast_days": 7,
                                            "timeformat": "unixtime"})
            data, _ = await llm.request_json_async(f"{OPEN_METEO}?{query}", None, HEADERS, "GET", 20)
            return parse_forecast(from_open_meteo(data))
        if provider == "wttr":
            data, _ = await llm.request_json_async(f"{WTTR}{lat:.4f},{lon:.4f}?format=j1&lang=ru", None, HEADERS, "GET", 25)
            return parse_forecast(from_wttr(data))
        if provider == "yandex":
            key = str(self.settings.get("weather.yandex_key", "") or "").strip()
            if not key:
                raise ValueError("нет ключа (Настройки → Погода → yandex_key)")
            query = urllib.parse.urlencode({"lat": f"{lat:.4f}", "lon": f"{lon:.4f}", "lang": "ru_RU", "limit": 7,
                                            "hours": "true"})
            data, _ = await llm.request_json_async(f"{YANDEX}?{query}", None, {**HEADERS, "X-Yandex-Weather-Key": key},
                                                   "GET", 20)
            return parse_forecast(from_yandex(data))
        if provider == "met":
            query = urllib.parse.urlencode({"lat": f"{lat:.4f}", "lon": f"{lon:.4f}"})
            data, _ = await llm.request_json_async(f"{MET}?{query}", None, HEADERS, "GET", 20)
            return parse_forecast(data)
        raise ValueError(f"неизвестный источник «{provider}»")

    async def forecast(self, lat: float, lon: float) -> dict:
        errors = []
        for provider in self.providers():
            try:
                state = await self.fetch(provider, lat, lon)
            except (llm.HTTPError, llm.NetworkError, ValueError, KeyError, TypeError, AttributeError) as exc:
                errors.append(f"{PROVIDER_NAMES.get(provider, provider)} — {short_error(exc)}")
                log.info("weather provider %s failed: %s", provider, short_error(exc))
                continue
            state["source"] = PROVIDER_NAMES.get(provider, provider)
            return state
        raise ValueError("; ".join(errors) or "нет источников погоды")

    async def set_city(self, city: str) -> dict:
        place = await self.geocode(city.strip())
        for key, value in (("weather.city", place["city"]), ("weather.lat", place["lat"]), ("weather.lon", place["lon"]),
                           ("weather.enabled", True)):
            self.settings.set(key, value)
        return await self.refresh()

    async def refresh(self) -> dict:
        place = self.location()
        if place is None:
            city = str(self.settings.get("weather.city", "") or "").strip()
            if not city:
                self.state = {"ok": False, "reason": "укажите город в настройках погоды"}
                return self.state
            return await self.set_city(city)
        lat, lon, city = place
        try:
            state = await self.forecast(lat, lon)
        except (llm.HTTPError, llm.NetworkError, ValueError) as exc:
            self.state = {**self.state, "error": str(exc)}
            return self.state
        state["city"] = city
        self.state = state
        bus.emit("weather.state", state)
        self.warn(state)
        return state

    def warn(self, state: dict) -> None:
        soon = state.get("rain_soon")
        if not soon or not self.settings.get("weather.rain_warning", True) or time.time() - self._warned < 3 * 3600:
            return
        self._warned = time.time()
        what = {"snow": "снег", "sleet": "мокрый снег", "thunder": "гроза"}.get(soon["kind"], "дождь")
        bus.card("Погода", f"Через {soon['in_minutes']} мин ожидается {what}", kind="warning", icon="umbrella",
                 timeout=12)

    async def start(self) -> None:
        self._unsubscribe.append(bus.on("settings.changed", self.on_settings))
        self.spawn(self._loop(), "loop")

    def on_settings(self, data) -> None:
        if str((data or {}).get("key", "")).startswith("weather.") and self.settings.get("weather.enabled", False):
            self.spawn(self.refresh(), "refresh")

    async def _loop(self) -> None:
        while True:
            if self.settings.get("weather.enabled", False):
                try:
                    await self.refresh()
                except Exception:  # noqa: BLE001
                    log.warning("weather refresh failed", exc_info=True)
            await asyncio.sleep(REFRESH)

    def methods(self) -> dict:
        async def get_state() -> dict:
            return self.state

        async def refresh() -> dict:
            return await self.refresh()

        async def set_city(city: str) -> dict:
            return await self.set_city(city)

        return {"weather.state": get_state, "weather.refresh": refresh, "weather.set_city": set_city}


def register_tool(tools, feature: Weather) -> None:
    from ..tools import HINTS, SUMMARY, A, S, Tool, ToolError, get_str

    async def handler(action: str, args: dict, ctx) -> str:
        city = get_str(args, "city").strip()
        try:
            if city:
                place = await feature.geocode(city)
                state = {**await feature.forecast(place["lat"], place["lon"]), "city": place["city"]}
            else:
                state = await feature.refresh()
        except (llm.HTTPError, llm.NetworkError, ValueError) as exc:
            raise ToolError(f"погода недоступна: {exc}") from None
        if not state.get("ok"):
            raise ToolError("город не задан — назовите его (city) или укажите в настройках погоды")
        return summary_text(state, week=action == "week")

    tools.add(Tool("weather", "Погода (Open-Meteo / wttr.in / Яндекс, работает в РФ). now — сейчас и ближайшие часы; week — прогноз на неделю. "
                   "city — другой город (по умолчанию — город из настроек).",
                   {"now": A("Смотрю погоду", "read"), "week": A("Смотрю прогноз на неделю", "read")},
                   {"city": S("город, например «Москва»; по умолчанию — из настроек")}, handler, timeout=40))
    HINTS["weather"] = ("погод", "дожд", "зонт", "температур", "прогноз", "снег", "weather")
    SUMMARY["weather"] = "погода и прогноз"


def create(app) -> Weather:
    return Weather(app)
