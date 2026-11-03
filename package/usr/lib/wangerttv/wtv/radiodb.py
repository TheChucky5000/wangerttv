"""Lokale Radiosender-Datenbank (alle deutschen Sender aus radio-browser.info, aufbereitet).

Wird mit WangertTV ausgeliefert (radio/stations-de.json.gz) und auf der Box wöchentlich im Hintergrund
aktualisiert – so funktionieren Liste, Genres, Bundesländer und Suche auch, wenn der Radio-Server hakt.
"""
import re

BUNDESLAENDER = ["Baden-Württemberg", "Bayern", "Berlin", "Brandenburg", "Bremen", "Hamburg", "Hessen",
                 "Mecklenburg-Vorpommern", "Niedersachsen", "Nordrhein-Westfalen", "Rheinland-Pfalz", "Saarland",
                 "Sachsen", "Sachsen-Anhalt", "Schleswig-Holstein", "Thüringen"]
_STATE_WORDS = {
    "Baden-Württemberg": ["baden-württemberg", "baden-wuerttemberg", "baden wurttemberg", "baden-wurttemberg", "bw", "stuttgart", "freiburg", "karlsruhe", "mannheim", "ulm", "heidelberg"],
    "Bayern": ["bayern", "bavaria", "münchen", "munich", "nürnberg", "nuremberg", "augsburg", "regensburg", "würzburg", "franken", "ingolstadt"],
    "Berlin": ["berlin"],
    "Brandenburg": ["brandenburg", "potsdam", "cottbus"],
    "Bremen": ["bremen", "bremerhaven"],
    "Hamburg": ["hamburg"],
    "Hessen": ["hessen", "hesse", "frankfurt", "wiesbaden", "kassel", "darmstadt"],
    "Mecklenburg-Vorpommern": ["mecklenburg", "vorpommern", "rostock", "schwerin"],
    "Niedersachsen": ["niedersachsen", "lower saxony", "hannover", "hanover", "braunschweig", "osnabrück", "oldenburg", "göttingen"],
    "Nordrhein-Westfalen": ["nordrhein", "north rhine", "nrw", "köln", "cologne", "düsseldorf", "dortmund", "essen", "bochum", "münster", "ruhr", "bielefeld", "aachen", "duisburg", "wuppertal", "bonn"],
    "Rheinland-Pfalz": ["rheinland-pfalz", "rhineland", "mainz", "koblenz", "trier", "kaiserslautern"],
    "Saarland": ["saarland", "saarbrücken"],
    "Sachsen": ["sachsen", "saxony", "dresden", "leipzig", "chemnitz"],
    "Sachsen-Anhalt": ["sachsen-anhalt", "saxony-anhalt", "magdeburg", "halle"],
    "Schleswig-Holstein": ["schleswig", "holstein", "kiel", "lübeck", "flensburg"],
    "Thüringen": ["thüringen", "thuringia", "thueringen", "erfurt", "jena", "weimar"],
}
# Reihenfolge wichtig: "Sachsen-Anhalt" vor "Sachsen", "Brandenburg" nach "Berlin-Brandenburg" egal (Berlin zuerst)
_ORDER = ["Sachsen-Anhalt", "Mecklenburg-Vorpommern", "Schleswig-Holstein", "Baden-Württemberg", "Rheinland-Pfalz",
          "Nordrhein-Westfalen", "Niedersachsen", "Berlin", "Brandenburg", "Bremen", "Hamburg", "Hessen", "Saarland",
          "Sachsen", "Thüringen", "Bayern"]
# bekannte Sender ohne Ortsangabe
_KNOWN = {"swr": "Baden-Württemberg", "br ": "Bayern", "bayern 1": "Bayern", "bayern 3": "Bayern", "antenne bayern": "Bayern",
          "wdr": "Nordrhein-Westfalen", "1live": "Nordrhein-Westfalen", "ndr": "Niedersachsen", "hr1": "Hessen", "hr3": "Hessen",
          "hit radio ffh": "Hessen", "ffh": "Hessen", "mdr": "Sachsen", "rbb": "Berlin", "radioeins": "Berlin", "fritz": "Berlin",
          "sr 1": "Saarland", "sr1": "Saarland", "radio bremen": "Bremen", "bremen eins": "Bremen", "rsh": "Schleswig-Holstein",
          "r.sh": "Schleswig-Holstein", "bigfm": "Baden-Württemberg", "radio hamburg": "Hamburg", "antenne thüringen": "Thüringen",
          "landeswelle": "Thüringen", "antenne mv": "Mecklenburg-Vorpommern", "ostseewelle": "Mecklenburg-Vorpommern",
          "radio psr": "Sachsen", "r.sa": "Sachsen", "radio brocken": "Sachsen-Anhalt", "radio ssr": "Sachsen-Anhalt",
          "rpr1": "Rheinland-Pfalz", "bb radio": "Brandenburg", "antenne niedersachsen": "Niedersachsen", "ffn": "Niedersachsen"}

GENRES = {   # Anzeige -> Schlagworte
    "pop": ["pop", "hits", "top 40", "top40", "charts", "top charts", "top hits"], "rock": ["rock", "classic rock", "hard rock", "alternative"],
    "news": ["news", "nachrichten", "info", "information", "talk", "politics"], "schlager": ["schlager", "volksmusik", "discofox", "deutsche schlager"],
    "80s": ["80s", "80er", "eighties"], "90s": ["90s", "90er", "nineties"], "oldies": ["oldies", "60s", "70s", "60er", "70er", "oldie"],
    "dance": ["dance", "edm", "party", "disco", "dj"], "electronic": ["electronic", "electro", "techno", "house", "trance", "deep house"],
    "hip hop": ["hiphop", "hip hop", "hip-hop", "rap", "rnb", "r&b", "urban", "black"], "jazz": ["jazz", "smooth jazz", "swing"],
    "classical": ["classical", "klassik", "classic", "orchestra", "opera", "oper"], "chillout": ["chillout", "lounge", "relax", "ambient", "easy listening", "chill"],
    "metal": ["metal", "heavy metal", "hard rock"], "country": ["country", "americana", "folk"], "kids": ["kinder", "kids", "children", "kinderradio"],
    "sport": ["sport", "fussball", "fußball", "bundesliga", "football"], "christmas": ["weihnachten", "christmas", "xmas"],
    "religion": ["christian", "christlich", "gospel", "kirche", "religion"], "regional": ["regional", "local", "lokal"],
}


def normalize_state(st):
    text = " ".join([st.get("state") or "", st.get("name") or "", (st.get("tags") or "").replace(",", " ")]).lower()
    raw = (st.get("state") or "").lower()
    for land in _ORDER:                                   # 1. Angabe "state" der Datenbank
        if any(w in raw for w in _STATE_WORDS[land]):
            return land
    name = (st.get("name") or "").lower() + " "
    for key, land in _KNOWN.items():                      # 2. bekannte Senderfamilien
        if name.startswith(key) or f" {key}" in f" {name}":
            return land
    for land in _ORDER:                                   # 3. Stadt/Land im Namen oder in den Schlagworten
        if any(re.search(rf"(^|[^\w]){re.escape(w)}([^\w]|$)", text) for w in _STATE_WORDS[land] if len(w) > 3):
            return land
    return ""


def slim(st):
    tags = [t.strip().lower() for t in (st.get("tags") or "").split(",") if t.strip() and len(t.strip()) < 30]
    url = st.get("url_resolved") or st.get("url") or ""
    fav = st.get("favicon") or ""
    return {"id": st.get("stationuuid") or st.get("id") or "", "name": (st.get("name") or "").strip()[:80], "url": url,
            "favicon": fav if fav.startswith(("https://", "http://")) else "", "tags": ", ".join(tags[:5]),
            "state": normalize_state(st), "codec": (st.get("codec") or "")[:10], "bitrate": int(st.get("bitrate") or 0),
            "pop": int(st.get("clickcount") or 0) * 3 + int(st.get("votes") or 0) // 20, "country": "Germany"}


def build(raw):
    """Rohdaten (Liste von radio-browser-Stationen) -> bereinigte Liste, beliebteste zuerst, ohne Doppelte."""
    best = {}
    for st in raw:
        s = slim(st)
        if not s["id"] or not s["name"] or not s["url"].startswith(("http://", "https://")):
            continue
        key = re.sub(r"[^a-z0-9äöüß]+", "", s["name"].lower())
        if not key:
            continue
        cur = best.get(key)
        # gleiche Station mehrfach eingetragen: beliebteste/beste Qualität behalten
        if cur is None or (s["pop"], s["bitrate"] >= 96, bool(s["favicon"])) > (cur["pop"], cur["bitrate"] >= 96, bool(cur["favicon"])):
            if cur and not s["favicon"]:
                s["favicon"] = cur["favicon"]
            best[key] = s
    return sorted(best.values(), key=lambda s: -s["pop"])
