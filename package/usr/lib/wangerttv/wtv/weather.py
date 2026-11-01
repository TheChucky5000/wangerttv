"""Wetter über Open-Meteo (kostenlos, ohne API-Schlüssel)."""
import threading
import time
import urllib.parse

from . import config, mock
from .util import http_get_json, log

_cache = {"key": None, "ts": 0, "data": None}
_lock = threading.Lock()


def _geocode(city, lang):
    q = urllib.parse.urlencode({"name": city, "count": 1, "language": lang, "format": "json"})
    res = http_get_json(f"https://geocoding-api.open-meteo.com/v1/search?{q}")
    hits = res.get("results") or []
    if not hits:
        return None
    h = hits[0]
    return h["latitude"], h["longitude"], h.get("name", city)


def get(city, lang="de"):
    city = (city or "").strip()
    if config.DEV:
        data = mock.weather()
        data["place"] = city or data["place"]
        return data
    if not city:
        return {"ok": False, "message": "no_city"}
    key = (city.lower(), lang)
    with _lock:
        if _cache["key"] == key and time.time() - _cache["ts"] < 20 * 60 and _cache["data"]:
            return _cache["data"]
    try:
        geo = _geocode(city, lang)
        if not geo:
            return {"ok": False, "message": "city_not_found"}
        lat, lon, name = geo
        q = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 3,
            "current": "temperature_2m,weather_code,is_day",
            "daily": "temperature_2m_max,temperature_2m_min,weather_code",
        })
        res = http_get_json(f"https://api.open-meteo.com/v1/forecast?{q}")
        cur = res.get("current") or {}
        daily = res.get("daily") or {}
        days = []
        for i, date in enumerate(daily.get("time") or []):
            days.append({
                "date": date,
                "max": round(daily["temperature_2m_max"][i]),
                "min": round(daily["temperature_2m_min"][i]),
                "code": daily["weather_code"][i],
            })
        data = {"ok": True, "place": name, "temp": round(cur.get("temperature_2m", 0)),
                "code": cur.get("weather_code", 0), "is_day": cur.get("is_day", 1), "daily": days}
    except Exception as exc:  # noqa: BLE001
        log.info("Wetter nicht verfügbar: %s", exc)
        return {"ok": False, "message": "offline"}
    with _lock:
        _cache.update(key=key, ts=time.time(), data=data)
    return data
