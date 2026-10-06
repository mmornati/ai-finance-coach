"""Regional qualifiers of organisation names are quasi-identifiers ("NEXITY NORMANDIE", "CAISSE D EPARGNE BRETAGNE PAYS
DE LOIRE", "CAF DE LA MANCHE" -> where the household lives). :func:`strip_regions` removes them; French regions are removed wherever
they stand, departments only as a trailing qualifier (many are ordinary words)."""
from __future__ import annotations

import re
import unicodedata

REGIONS = """AUVERGNE RHONE ALPES|AUVERGNE-RHONE-ALPES|RHONE ALPES|AUVERGNE|BOURGOGNE FRANCHE COMTE|BOURGOGNE|FRANCHE COMTE|BRETAGNE|
CENTRE VAL DE LOIRE|CENTRE EST|CORSE|GRAND EST|ALSACE|LORRAINE|CHAMPAGNE ARDENNE|HAUTS DE FRANCE|NORD PAS DE CALAIS|PICARDIE|
NORD DE FRANCE|ILE DE FRANCE|NORMANDIE|BASSE NORMANDIE|HAUTE NORMANDIE|NORMANDIE SEINE|NOUVELLE AQUITAINE|AQUITAINE|LIMOUSIN|
POITOU CHARENTES|OCCITANIE|LANGUEDOC ROUSSILLON|MIDI PYRENEES|SUD OUEST|NORD EST|PAYS DE LOIRE|PAYS DE LA LOIRE|
PROVENCE ALPES COTE D AZUR|PROVENCE ALPES COTE D'AZUR|PACA|COTE D AZUR|GUADELOUPE|MARTINIQUE|GUYANE|REUNION|MAYOTTE""".replace("\n", "").split("|")
DEPARTMENTS = """AIN|AISNE|ALLIER|ALPES DE HAUTE PROVENCE|HAUTES ALPES|ALPES MARITIMES|ARDECHE|ARDENNES|ARIEGE|AUBE|AUDE|AVEYRON|
BOUCHES DU RHONE|CALVADOS|CANTAL|CHARENTE|CHARENTE MARITIME|CHER|CORREZE|COTE D OR|COTES D ARMOR|CREUSE|DORDOGNE|DOUBS|DROME|EURE|
EURE ET LOIR|FINISTERE|GARD|HAUTE GARONNE|GERS|GIRONDE|HERAULT|ILLE ET VILAINE|INDRE|INDRE ET LOIRE|ISERE|JURA|LANDES|LOIR ET CHER|
LOIRE|HAUTE LOIRE|LOIRE ATLANTIQUE|LOIRET|LOT|LOT ET GARONNE|LOZERE|MAINE ET LOIRE|MANCHE|MARNE|HAUTE MARNE|MAYENNE|
MEURTHE ET MOSELLE|MEUSE|MORBIHAN|MOSELLE|NIEVRE|NORD|OISE|ORNE|PAS DE CALAIS|PUY DE DOME|PYRENEES ATLANTIQUES|HAUTES PYRENEES|
PYRENEES ORIENTALES|BAS RHIN|HAUT RHIN|RHONE|HAUTE SAONE|SAONE ET LOIRE|SARTHE|SAVOIE|HAUTE SAVOIE|SEINE MARITIME|SEINE ET MARNE|
YVELINES|DEUX SEVRES|SOMME|TARN|TARN ET GARONNE|VAR|VAUCLUSE|VENDEE|VIENNE|HAUTE VIENNE|VOSGES|YONNE|TERRITOIRE DE BELFORT|ESSONNE|
HAUTS DE SEINE|SEINE SAINT DENIS|VAL DE MARNE|VAL D OISE|PARIS""".replace("\n", "").split("|")
CONNECT = r"(?:(?:DE|DU|DES|D|LA|LE|LES|EN|ET|AU|AUX)\s+)*"


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.upper()) if unicodedata.category(c) != "Mn")


def _pat(names) -> str:
    return "|".join(r"[\s'-]+".join(re.escape(w) for w in re.split(r"[\s'-]+", n.strip()) if w)
                    for n in sorted({n for n in names if n.strip()}, key=len, reverse=True))


_REGION = re.compile(r"(?:^|[\s'-]+)" + CONNECT.replace("(?:(?:", "(?:(?:") + r"(?:" + _pat(REGIONS) + r")(?=$|[\s'-])")
_DEPT_TAIL = re.compile(r"[\s'-]+" + CONNECT + r"(?:" + _pat(DEPARTMENTS) + r")\s*$")
_TAIL_CONNECT = re.compile(r"(?:[\s'-]+(?:DE|DU|DES|D|LA|LE|LES|EN|ET|AU|AUX))+\s*$")


def strip_regions(name: str) -> str:
    """The name without its regional qualifier, in the original letters ("NEXITY NORMANDIE" -> "Nexity"). A name that is only a
    region is returned unchanged."""
    f = _fold(name)
    if len(f) != len(name):                       # accents that fold to several characters: work on the folded form's length only
        f = "".join(c if unicodedata.category(c) != "Mn" else "" for c in unicodedata.normalize("NFD", name)).upper()
        name = unicodedata.normalize("NFC", "".join(c for c in unicodedata.normalize("NFD", name) if unicodedata.category(c) != "Mn"))
    out, cur = name, f
    for rx in (_REGION, _DEPT_TAIL):
        m = rx.search(cur)
        while m and m.start() > 0:
            out, cur = out[:m.start()] + out[m.end():], cur[:m.start()] + cur[m.end():]
            m = rx.search(cur)
    t = _TAIL_CONNECT.search(cur)
    if t and t.start() > 0:
        out = out[:t.start()]
    out = out.strip(" -'")
    return out if out else name
