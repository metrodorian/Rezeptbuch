#!/usr/bin/env python3
"""
aus_naehrwerte.py — holt Rezepte aus der Nährwerte-App ins Rezeptbuch.

In der App gibt es unter jedem Gerichtvorschlag „📖 Ins Rezeptbuch“. Der Pi
schreibt das Rezept dann im Aufbau dieses Buchs und schlägt Fotos von
Wikimedia Commons vor. Dieses Werkzeug

  * holt die fertigen Rezepte vom Pi (nur im Heimnetz erreichbar),
  * sucht das Foto: zuerst die Vorschläge, sonst eine Commons-Suche; es nimmt
    nur freie Lizenzen (CC0, gemeinfrei, CC BY, CC BY-SA) und lädt das Bild
    nach assets/bilder/,
  * legt NN_slug.md an und trägt das Rezept in _data/rezepte.yml, in CHAPTERS
    (tools/generate_pdfs.py) und in die Inhaltstabelle der README ein,
  * meldet dem Pi, dass es übernommen ist.

Ohne passendes Foto wird ein Rezept nicht übernommen, sondern gemeldet.
Danach Rezept lesen, PDFs erzeugen, committen, pushen.

    python3 tools/aus_naehrwerte.py            # holen und eintragen
    python3 tools/aus_naehrwerte.py --probe    # nur zeigen, was käme
    python3 tools/aus_naehrwerte.py --foto "File:Name.jpg" SLUG
                                               # Foto prüfen und laden, Felder ausgeben

Adresse des Pi: Umgebungsvariable NAEHRWERTE_URL (Vorgabe http://192.168.178.20:8082).
"""
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).parent.parent
PI = os.environ.get("NAEHRWERTE_URL", "http://192.168.178.20:8082").rstrip("/")
COMMONS = "https://commons.wikimedia.org/w/api.php"
KENNUNG = {"User-Agent": "Rezeptbuch-Werkzeug/1.0 (privates Rezeptbuch, Bilder mit Nachweis)"}
FREI = re.compile(r"^(CC0|Public domain|PD.*|CC BY(-SA)? \d\.\d.*)$", re.I)
NICHT_FREI = re.compile(r"\b(NC|ND)\b")


def _json(url, daten=None):
    kopf = dict(KENNUNG)
    if daten is not None:
        daten = json.dumps(daten).encode()
        kopf["Content-Type"] = "application/json"
    with urllib.request.urlopen(urllib.request.Request(url, daten, kopf), timeout=60) as a:
        return json.load(a)


def _commons(**werte):
    werte.update(action="query", format="json")
    return _json(COMMONS + "?" + urllib.parse.urlencode(werte))


def _ohne_html(text):
    return " ".join(re.sub(r"<[^>]+>", "", str(text or "")).split())


def foto_pruefen(titel):
    """Infos zu einer Commons-Datei oder None, wenn sie nicht taugt."""
    seiten = _commons(titles=titel, prop="imageinfo", iiprop="url|size|extmetadata|mime",
                      iiurlwidth=1600)["query"]["pages"]
    seite = next(iter(seiten.values()))
    if "imageinfo" not in seite:
        return None                                   # gibt es nicht
    info = seite["imageinfo"][0]
    meta = info.get("extmetadata", {})
    lizenz = _ohne_html(meta.get("LicenseShortName", {}).get("value"))
    if not FREI.match(lizenz) or NICHT_FREI.search(lizenz):
        return None
    if info.get("mime") not in ("image/jpeg", "image/png") or info["width"] < 1000:
        return None
    return {"titel": seite["title"], "url": info.get("thumburl") or info["url"],
            "quer": info["width"] >= info["height"] * 1.1,
            "foto": _ohne_html(meta.get("Artist", {}).get("value"))[:60] or "unbekannt",
            "lizenz": lizenz,
            "lizenz_url": meta.get("LicenseUrl", {}).get("value") or info["descriptionurl"],
            "quelle": info["descriptionurl"]}


def foto_suchen(vorschlaege, suche):
    """Erst die Vorschläge des Pi, dann die Commons-Suche. Querformat zuerst."""
    gefunden = []
    for titel in vorschlaege or []:
        f = foto_pruefen(titel)
        if f:
            gefunden.append(f)
    if not any(f["quer"] for f in gefunden) and suche:
        treffer = _commons(list="search", srnamespace=6, srlimit=10,
                           srsearch="%s filetype:bitmap" % suche)["query"]["search"]
        for t in treffer:
            f = foto_pruefen(t["title"])
            if f:
                gefunden.append(f)
            if any(g["quer"] for g in gefunden):
                break
    gefunden.sort(key=lambda f: not f["quer"])
    return gefunden[0] if gefunden else None


def foto_laden(f, name):
    ziel = REPO / "assets" / "bilder" / ("%s.jpg" % name)
    with urllib.request.urlopen(urllib.request.Request(f["url"], None, KENNUNG), timeout=120) as a:
        ziel.write_bytes(a.read())
    # wie die übrigen Fotos: 1200 px breit, JPEG Qualität 80 (sips gehört zu macOS)
    subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "80",
                    "--resampleWidth", "1200", str(ziel), "--out", str(ziel)],
                   check=True, capture_output=True)
    return "assets/bilder/%s.jpg" % name


def _yaml_text(text):
    return json.dumps(str(text), ensure_ascii=False)       # JSON-Zeichenkette ist gültiges YAML


def eintragen(rezept, foto, bild):
    """Datei, rezepte.yml, CHAPTERS und README. Gibt den Slug zurück."""
    nummern = [int(p.name[:2]) for p in REPO.glob("[0-9][0-9]_*.md")]
    slug = "%02d_%s" % (max(nummern, default=0) + 1, rezept["slug"])
    (REPO / ("%s.md" % slug)).write_text(rezept["markdown"].strip() + "\n", encoding="utf-8")

    with open(REPO / "_data" / "rezepte.yml", "a", encoding="utf-8") as f:
        f.write("\n- slug: %s\n  titel: %s\n  emoji: %s\n  kurz: %s\n  bild: %s\n  bildtext: %s\n"
                "  foto: %s\n  lizenz: %s\n  lizenz_url: %s\n  quelle: %s\n"
                % (slug, _yaml_text(rezept["titel"]), _yaml_text(rezept["emoji"]),
                   _yaml_text(rezept["kurz"]), bild, _yaml_text(rezept["bildtext"] or rezept["titel"]),
                   _yaml_text(foto["foto"]), _yaml_text(foto["lizenz"]), foto["lizenz_url"], foto["quelle"]))

    pdf = REPO / "tools" / "generate_pdfs.py"
    text = pdf.read_text(encoding="utf-8")
    text = re.sub(r"(CHAPTERS = \[\n(?:    \"[^\"]+\",\n)*)\]", lambda m: m.group(1) + '    "%s",\n]' % slug, text, 1)
    pdf.write_text(text, encoding="utf-8")

    readme = REPO / "README.md"
    zeilen = readme.read_text(encoding="utf-8").split("\n")
    letzte = max(i for i, z in enumerate(zeilen) if re.match(r"\| \d+ \| \[", z))
    nr = int(slug[:2])
    zeilen.insert(letzte + 1, "| %d | [%s](%s.md) | %s |" % (nr, rezept["titel"], slug, rezept["kurz"]))
    readme.write_text("\n".join(zeilen), encoding="utf-8")
    return slug


def main():
    if sys.argv[1:2] == ["--foto"]:
        f = foto_pruefen(sys.argv[2])
        if not f:
            sys.exit("Taugt nicht (Lizenz, Größe oder Dateiname).")
        f["bild"] = foto_laden(f, sys.argv[3])
        print(json.dumps(f, ensure_ascii=False, indent=1))
        return
    probe = "--probe" in sys.argv
    try:
        rezepte = _json(PI + "/api/rezeptbuch?stand=fertig")["rezepte"]
    except OSError as fehler:
        sys.exit("Pi nicht erreichbar (%s) – nur im Heimnetz." % fehler)
    if not rezepte:
        print("Nichts Neues aus der Nährwerte-App.")
    for r in rezepte:
        foto = foto_suchen(r["fotos"], r["foto_suche"])
        if not foto:
            print("✗ %s: kein Foto mit freier Lizenz gefunden – nicht übernommen" % r["titel"])
            continue
        if probe:
            print("· %s – Foto %s (%s, %s)" % (r["titel"], foto["titel"], foto["foto"], foto["lizenz"]))
            continue
        bild = foto_laden(foto, r["slug"])
        slug = eintragen(r, foto, bild)
        _json(PI + "/api/rezeptbuch/uebernommen", {"id": r["id"]})
        print("✓ %s → %s.md, Foto %s (%s)" % (r["titel"], slug, foto["titel"], foto["lizenz"]))


if __name__ == "__main__":
    main()
