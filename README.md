# Agentic Negotiation Prototyp

Zwei Agenten verhandeln den Preis eines Produkts. Jeder besitzt einen privaten
Zielpreis und eine feste Preisgrenze. Ein austauschbares Strategiemodul entscheidet
über Angebote, Gegenangebote, Annahme oder Abbruch. Ein unabhängiges Regelmodul
prüft jeden Vorschlag, bevor er beim anderen Agenten ankommt.

**Version 0.1: lokale Python-Konsolenanwendung.** Zwei logisch getrennte Agenten
laufen abwechselnd in einem Prozess. Es sind weder zwei Server noch ein externes
Agentenframework erforderlich. Ein Netzwerkprotokoll wie A2A ist noch nicht implementiert.

Die Arbeitskopie liegt unter `C:\Users\darre\Documents\Agentic Negotiation Prototyp`.

## In Visual Studio Code starten

Voraussetzung: **Python 3.10 oder neuer**. Die Offline-Demo braucht keine externen
Python-Pakete; nur der Claude-Modus benötigt das offizielle Anthropic SDK (siehe unten).

1. Diesen Ordner in VS Code öffnen oder `Agentic-Negotiation.code-workspace` öffnen.
2. Für F5 die empfohlenen Erweiterungen **Python** und **Python Debugger** installieren.
3. Über `Strg+Umschalt+P` → `Python: Select Interpreter` einen Python-Interpreter auswählen.
4. Unter **Ausführen und Debuggen** `1 - Offline-Demo: Einigung` auswählen und **F5** drücken.

Ohne Erweiterungen: **Terminal → Aufgabe ausführen → Demo: Einigung** wählen.
Unter **Alle Tests** ist auch die automatische Prüfung als Aufgabe hinterlegt.
Diese Aufgaben verwenden den über `python` erreichbaren Interpreter.

Alternativ im integrierten Terminal, aus diesem Ordner:

```powershell
python main.py
```

Die Offline-Demo ist **kein LLM**. Sie nutzt eine deterministische lineare
Zugeständnisstrategie, damit Regeln und Ablauf ohne API-Zugang untersucht werden können.
Im Standardszenario endet sie nach 10 Zügen mit einer Einigung auf **112,00 EUR**.

Abbruch bei unvereinbaren Preisgrenzen ausprobieren:

```powershell
python main.py --config config/no_deal.json
```

Dieser Lauf endet nach 12 Zügen ohne Einigung.

## Zwei echte LLM-Agenten verwenden

Der Adapter verwendet die Claude Messages API über das offizielle Python-SDK
`anthropic` mit strukturiertem JSON-Output (`output_config.format`).
Beide Agenten können dasselbe Modell nutzen; jeder Aufruf erhält ausschließlich
das eigene Profil und die öffentliche Historie. Es gibt keine gemeinsame
LLM-Conversation und keine Weitergabe der privaten Entscheidungszusammenfassungen.

1. SDK installieren: `python -m pip install -r requirements.txt`
2. `.env.example` nach `.env` kopieren.
3. In `.env` den eigenen Anthropic-API-Schlüssel (`ANTHROPIC_API_KEY`) eintragen.
   Den Schlüssel nicht in den Chat schreiben.
4. Optional `NEGOTIATION_MODEL` auf ein anderes Claude-Modell setzen.
5. Starten:

```powershell
python main.py --mode claude
```

Oder in VS Code `3 - Zwei LLM-Agenten (Claude)` wählen und F5 drücken.
Vorhandene Umgebungsvariablen haben Vorrang vor `.env`; `--model` überschreibt `NEGOTIATION_MODEL`.
Das Modell ist standardmäßig `claude-opus-5-5` mit Effort `medium`. Die Modellvariable heißt
bewusst nicht `ANTHROPIC_MODEL`, weil Claude Code diesen Namen bereits verwendet.

Für `claude-opus-5-5` (sowie Opus 5, Fable 5.1 und Sonnet 5.5) ist der serverseitige
Refusal-Fallback aktiv (`fallbacks="default"`, Beta `server-side-fallback-2026-07-01`):
Lehnt das Modell aus Sicherheitsgründen ab, beantwortet die API die Anfrage im selben
Aufruf mit einem von Anthropic gewählten Ersatzmodell. Welches Modell geantwortet hat,
steht im Protokoll unter `metadata.model`. Lehnt auch das Ersatzmodell ab, endet der
Lauf mit `strategy_error:model_refusal`.

**API-Nutzung kann Kosten verursachen.** Jeder Versuch ist ein API-Aufruf, maximal
`max_turns × max_attempts_per_turn` pro Lauf (standardmäßig 24). Das SDK wiederholt
Verbindungsfehler, Timeouts, 429 und 5xx bis zu zweimal automatisch. Pro Aufruf sind
höchstens 16.000 Ausgabe-Tokens erlaubt; Claude Opus 5.5 denkt immer mit
(adaptives Denken), was Ausgabe-Tokens verbraucht. Der Text des Denkens wird nicht
zurückgegeben. Bleibende Fehler führen zum protokollierten Abbruch; es gibt keinen
stillen Wechsel zur Offline-Demo. Die Eingabedaten einschließlich des eigenen
Agentenprofils werden dabei an Anthropic übermittelt. Aufbewahrung und Verarbeitung
richten sich nach den Bedingungen des API-Kontos; das ersetzt keine umfassende
Prüfung der Datenverarbeitungsbedingungen für einen späteren produktiven Einsatz.

Die Anbindung ist mit einem simulierten SDK-Client getestet. Ein echter API-Lauf
benötigt einen lokal eingerichteten Schlüssel und wurde bei Erstellung nicht ausgeführt.

## Browser-Oberfläche

```powershell
python gui.py
```

Oder in VS Code `4 - Browser-Oberfläche` wählen und F5 drücken. Der Browser öffnet
`http://127.0.0.1:8765/`; Beenden mit `Strg+C` im Terminal. Optionen: `--port`, `--model`,
`--no-browser`. Der Server nutzt nur die Python-Standardbibliothek; für Claude gilt dieselbe
Einrichtung wie oben (`ANTHROPIC_API_KEY`, Paket `anthropic`). Ohne Claude bleibt die
Demo-Strategie verfügbar.

1. **Beschreiben:** Verhandlung in eigenen Worten eingeben, etwa *„Ich verkaufe mein
   Trekkingrad, hätte gern 480 €, unter 380 € nicht. Der Käufer zahlt höchstens 420 €.“*
   Claude erzeugt daraus einen Entwurf und nennt geschätzte Werte als **Annahmen**.
2. **Prüfen und anpassen:** Der Entwurf landet in einem Formular. Produkt, Wunschpreise,
   Grenzen, Zugzahl und Strategie je Seite (Claude oder Demo) lassen sich ändern. Eine
   Vorschau zeigt beide Preisbereiche und den Einigungsbereich. Gestartet wird erst
   nach deiner Bestätigung; der Server prüft das Szenario mit denselben Regeln wie
   `config/*.json`. „JSON speichern“ lädt das Szenario als Datei für `config/` herunter.
3. **Verhandlung verfolgen:** Das Preisdiagramm zeigt Angebote beider Seiten, Ziele,
   Grenzen, den Einigungsbereich und abgelehnte Vorschläge (×). Der Verlauf zeigt die
   Nachrichten; private Begründungen lassen sich einblenden. „Abbrechen“ beendet den
   Lauf vor dem nächsten Zug.

Die Oberfläche ist eine **Beobachteransicht**: Sie zeigt beide Grenzen und Begründungen.
Die Agenten selbst erhalten weiterhin nur ihr eigenes Profil. Jeder Lauf wird wie im CLI
unter `runs/` protokolliert. Die Spracherkennung kostet einen zusätzlichen API-Aufruf
(Effort `low`).

Der Server ist nur über `127.0.0.1` erreichbar, akzeptiert nur Anfragen mit eigenem
Host-Namen und JSON-Inhalt (Schutz gegen Zugriffe fremder Webseiten) und gibt den
API-Schlüssel nie an den Browser weiter. Er ist für die lokale Einzelnutzung gedacht,
nicht für den Betrieb im Netzwerk.

## Szenario ändern

Die Datei `config/default.json` enthält die Versuchsparameter:

| Parameter | Bedeutung | Standard |
| --- | --- | --- |
| `product` | Beschreibung des Produkts einschließlich vereinbarter Nebenbedingungen | Gebrauchte Tastatur inkl. Versand |
| `buyer.target_cents` | Wunschpreis des Käufers | 8000 = 80,00 EUR |
| `buyer.limit_cents` | Absoluter Höchstpreis des Käufers | 12000 = 120,00 EUR |
| `seller.target_cents` | Wunschpreis des Verkäufers | 14000 = 140,00 EUR |
| `seller.limit_cents` | Absoluter Mindestpreis des Verkäufers | 10000 = 100,00 EUR |
| `max_turns` | Maximale Zahl einzelner Agentenzüge insgesamt | 12 |
| `max_attempts_per_turn` | Einschließlich Erstversuch; bei Regelverstoß erneuter Versuch mit Feedback | 2 |

Der Käufer beginnt. **Ein Zug ist eine Agentenentscheidung, keine komplette Runde.**
Alle Preise sind ganzzahlige Cent-Beträge; es gibt keine Gleitkomma-Geldrechnung.
Im ersten Prototyp sind ausschließlich positive EUR-Preise zulässig.
Ziel und Grenze werden zu Laufbeginn als unveränderliche Datenobjekte geladen.
Grenzen dürfen sich überschneiden oder unvereinbar sein. Bei unvereinbaren Grenzen
wird weiterverhandelt, ohne den Agenten die versteckten Gegnergrenzen zu verraten.

## Ablauf und Geschäftsregeln

1. Der aktive Agent bekommt sein Profil und alle bisher gültigen öffentlichen Züge.
2. Seine Strategie liefert `action`, `price_cents`, `message` und eine kurze `rationale`.
3. Das Regelmodul prüft Datentypen, Preisgrenze und Annahmeregeln.
4. Ein ungültiger Vorschlag bleibt intern im Audit-Protokoll. Derselbe Agent erhält
   Regel-Feedback und höchstens die konfigurierte Zahl weiterer Versuche.
5. Ein gültiges Angebot ersetzt das vorherige offene Angebot. Dann ist der andere Agent dran.
6. Die Annahme des letzten Gegenangebots beendet die Verhandlung mit Einigung.
   Ein letzter zentraler Check prüft vor dem Abschluss erneut beide Preisgrenzen.

| Aktion | Wirkung |
| --- | --- |
| `offer` | Neues Angebot oder Gegenangebot innerhalb der eigenen Preisgrenze |
| `accept` | Exakte Annahme des letzten offenen Angebots der Gegenseite |
| `withdraw` | Sofortiger Abbruch; `price_cents` muss `null` sein |

**Verbindlich sind die strukturierten Felder `action` und `price_cents`.**
Der LLM-Freitext wird protokolliert, aber nicht semantisch auf Widersprüche geprüft.
Ein „Ja“ allein im Nachrichtentext gilt deshalb nicht als Annahme. Zwei gleiche
Angebote allein erzeugen ebenfalls noch keine Einigung. Der Prompt fordert passende
Nachrichten und Geheimhaltung privater Ziele; eine inhaltliche Offenlegung dieser
Werte durch das LLM im Freitext wird bislang nicht technisch ausgeschlossen.

Abbruchgründe sind Rückzug, Zuglimit, wiederholte ungültige Vorschläge, API-/Strategiefehler
oder `Strg+C`. Falsch konfigurierte Szenarien werden vor Beginn zurückgewiesen.
Bei einem erzwungenen Prozessende, Stromausfall oder nicht beschreibbarem Datenträger
kann kein finales Ergebnis garantiert werden; bereits geschriebene Ereignisse bleiben
soweit möglich erhalten. Dies ist ein Forschungsprototyp für simulierte Verhandlungen.

## Protokolle

Jeder Lauf erzeugt einen eigenen Ordner `runs/<UTC-Zeitstempel>_<ID>/`:

| Datei | Inhalt |
| --- | --- |
| `events.jsonl` | Zeitstempel, Konfiguration, Beobachtungen, alle Vorschläge, Regelprüfungen, kurze Begründungen und Ergebnis |
| `result.json` | Status, Abbruch-/Abschlussgrund, Preis, Zug- und Versuchszahl |
| `transcript.txt` | Lesbarer Verlauf der gültigen öffentlichen Entscheidungen |

Im LLM-Modus werden zusätzlich Systemprompt, Schema, verwendetes Modell,
API-Antwort-ID, rohe Ausgabetexte und verfügbare Token-Nutzungswerte gespeichert.
Private Begründungen und beide Grenzen sind **im lokalen Forschungsprotokoll sichtbar**.
Die Strategie des Gegners erhält sie nicht automatisch. API-Schlüssel und HTTP-Fehlertexte
werden nicht protokolliert. `.env` und `runs/` sind in `.gitignore` ausgeschlossen.
Die Logs sind nicht manipulationssicher. LLM-Läufe sind nicht deterministisch; der
Demomodus eignet sich zur reproduzierbaren Prüfung des Ablaufs.

Exitcodes: `0` = reguläre Einigung oder regulärer Abbruch; `1` = technischer oder
Regelabbruch; `2` = Start-/Dateifehler; `130` = Benutzerabbruch mit `Strg+C`.
`result.json.status` unterscheidet Einigung und regulären Abbruch.

## Architektur und spätere Erweiterungen

| Datei | Verantwortung |
| --- | --- |
| `negotiation/models.py` | Agentenprofil, Beobachtung, Aktion, öffentliches Protokoll und Ergebnis |
| `negotiation/config.py` | Laden und Prüfen der Szenarien |
| `negotiation/json_support.py` | Eindeutiges JSON ohne doppelte Schlüssel, NaN oder Infinity |
| `negotiation/strategies.py` | `Strategy`-Schnittstelle, lineare Baseline und LLM-Adapter |
| `negotiation/rules.py` | Unabhängige verbindliche Geschäftsregeln |
| `negotiation/engine.py` | Zugfolge, Zustandsänderung, Wiederholungen und Abschluss |
| `negotiation/audit.py` | Dauerhafte Ereignisdateien pro Lauf |
| `negotiation/cli.py` | CLI, Konfiguration und Ausgabe |
| `negotiation/scenario_text.py` | Natürliche Sprache → Szenario-Entwurf über Claude |
| `negotiation/web.py` | Lokaler HTTP-Server, Live-Ereignisse per Server-Sent Events |
| `negotiation/web/index.html` | Browser-Oberfläche (ohne externe Bibliotheken) |

Eine neue Strategie implementiert `name` und `propose(observation) -> StrategyOutput`.
`Observation` enthält nur die für diesen Agenten erlaubten Informationen.
Die Engine übernimmt Validierung, Nachrichtenzustellung, Abbruch und Logging.
`StrategyOutput.raw` und `metadata` müssen JSON-kompatibel sein. Fehlerhafte
Rückgaben eigener Strategien führen zu einem protokollierten technischen Abbruch.
Käufer und Verkäufer können unterschiedliche Strategien verwenden, beispielsweise:

```powershell
python main.py --buyer-strategy claude --seller-strategy demo
```

Für Reinforcement Learning kann später eine `RLStrategy` ergänzt werden, die aus
der Beobachtung eine Aktion erzeugt. Das Regelmodul bleibt unverändert vorgeschaltet.
Vor dem Training wären zusätzlich eine schrittweise `reset`/`step`-Umgebung, eine
Reward-Definition, eine Diskretisierung oder Parametrisierung der Preise sowie
getrennte Trainings- und Evaluationsszenarien nötig. **RL ist noch nicht implementiert.**
Die vorhandenen strukturierten Ereignisse erleichtern die spätere Auswertung von
Einigungsquote, Preis, Zugzahl und Regelverstößen.

## Tests

```powershell
python -m unittest discover -s tests -v
```

Die Tests prüfen Preisgrenzen, exakte Annahme, begrenzte Wiederholungen, Abbruch,
Informationsweitergabe, Protokolle und den API-Adapter mit simuliertem SDK-Client.
Zusätzlich prüfen 100 fest gesäte zufällige Szenarien, dass die Baseline stets endet
und keine gültige Aktion die jeweilige Preisgrenze verletzt. Die Tests benötigen
keinen Schlüssel und rufen keinen externen Dienst auf. Ist das Paket `anthropic`
nicht installiert, werden die SDK-abhängigen Adaptertests übersprungen.

LLM-Text mit ungültigen Zahlen oder doppelten JSON-Schlüsseln wird im Protokoll
bewahrt und als ungültiger Vorschlag zurückgewiesen. Der Prompt und die
Regelprüfung sind getrennt: Eine Aufforderung im Gespräch kann die lokal
festgelegten Preisgrenzen nicht ändern.

## Technische Referenz

Die API-Anbindung nutzt das offizielle [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python)
(getestet mit Version 2.6.0) und
[Structured Outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
der Claude API (umgesetzt am 7. November 2026). Für andere Anbieter kann ein eigener
`Strategy`-Adapter ergänzt werden.
