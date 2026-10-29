"""Deutsche Rahmentexte für argparse: Aufrufzeile, Abschnittsüberschriften, Fehlermeldungen."""
from __future__ import annotations

import argparse

_DE = {
    "usage: ": "Aufruf: ",
    "positional arguments": "Argumente",
    "options": "Optionen",
    "optional arguments": "Optionen",
    "show this help message and exit": "diese Hilfe anzeigen und beenden",
    "show program's version number and exit": "Versionsnummer anzeigen und beenden",
    "the following arguments are required: %s": "folgende Argumente fehlen: %s",
    "unrecognized arguments: %s": "unbekannte Argumente: %s",
    "invalid choice: %(value)r (choose from %(choices)s)": "ungültige Angabe %(value)r (möglich: %(choices)s)",
    "expected one argument": "ein Wert wird erwartet",
    "expected at most one argument": "höchstens ein Wert wird erwartet",
    "expected at least one argument": "mindestens ein Wert wird erwartet",
    "expected %s argument": "%s Wert wird erwartet",
    "expected %s arguments": "%s Werte werden erwartet",
    "argument %(argument_name)s: %(message)s": "Argument %(argument_name)s: %(message)s",
    "invalid %(type)s value: %(value)r": "ungültiger Wert %(value)r (erwartet: %(type)s)",
    "%(prog)s: error: %(message)s\n": "%(prog)s: Fehler: %(message)s\n",
    "ambiguous option: %(option)s could match %(matches)s": "mehrdeutige Option %(option)s, passt auf %(matches)s",
    "not allowed with argument %s": "nicht zusammen mit %s erlaubt",
    "ignored explicit argument %r": "Argument %r wird ignoriert",
    "one of the arguments %s is required": "eines der Argumente %s ist erforderlich",
    "unknown parser %(parser_name)r (choices: %(choices)s)": "unbekannter Befehl %(parser_name)r (möglich: %(choices)s)",
    "can't open '%(filename)s': %(error)s": "'%(filename)s' lässt sich nicht öffnen: %(error)s",
    "%(prog)s: warning: option '%(option)s' is deprecated": "%(prog)s: Hinweis: Option '%(option)s' ist veraltet",
}


def _tr(msg: str) -> str:
    return _DE.get(msg, msg)


def _ntr(singular: str, plural: str, n: int) -> str:
    return _tr(singular if n == 1 else plural)


def install() -> None:
    """argparse holt sich gettext als Modulvariablen; die ersetzen wir durch die deutsche Tabelle."""
    argparse._ = _tr               # type: ignore[attr-defined]
    argparse.ngettext = _ntr       # type: ignore[attr-defined]
