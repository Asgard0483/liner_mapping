# Liner-Wandstärken (Blender-Add-on)

Wandstärken prothetischer Liner erfassen, als 3D-Modell darstellen, in einer
Datenbank sammeln, filtern und mehrere Liner grafisch und numerisch vergleichen –
inklusive Ähnlichkeitswert.

## Installation

1. ZIP erzeugen: `python3 tools/build_addon.py` → `dist/liner_wandstaerke.zip`
2. Blender: Bearbeiten → Einstellungen → Add-ons → „Von Datenträger installieren…“
   → ZIP wählen und aktivieren.
3. 3D-Viewport → Seitenleiste (**N**) → Reiter **Liner**.

In den Add-on-Einstellungen (oder oben im Panel „Datenbank“) lässt sich der Speicherort
der Datenbank festlegen (Standard: `~/liner_datenbank.sqlite`). Eine Datei auf einem
Netzlaufwerk kann von mehreren Arbeitsplätzen genutzt werden (nicht gleichzeitig schreiben).

## 1. Liner erfassen (Panel „Liner erfassen“)

* **Stammdaten:** Hersteller, Artikel, Größe, Form, Nenn-Wandstärke (laut Hersteller),
  Material, Notiz. Bereits vorhandene Werte werden beim Tippen vorgeschlagen.
* **Messtabelle:** Höhen von distal (Standard 4, 8, 12, 16, 20, 25, 30 cm), je Höhe vier
  Wandstärken in mm und der Umfang in cm.

  | Spalte | Lage      | Winkel |
  |--------|-----------|--------|
  | A      | anterior  | 0°     |
  | M      | medial    | 90°    |
  | P      | posterior | 180°   |
  | L      | lateral   | 270°   |

* Umfang wahlweise innen (Stumpf) oder außen über dem Liner gemessen, distale Wandstärke,
  Gesamtlänge, Seite (rechts/links).
* **Liner-Modell erzeugen** zeigt das Modell mit Wandstärken-Farbkarte
  (hell = dünn, dunkel = dick).
* **In Datenbank speichern** legt einen neuen Datensatz an bzw. aktualisiert den
  geladenen; **Als neu** speichert eine Kopie.

## 2. Datenbank (Panel „Datenbank“)

* **Filter:** Hersteller, Artikel, Größe, Form, Wandstärke von–bis (Nenn-Wandstärke;
  falls nicht angegeben, der gemessene Mittelwert – in der Liste mit „Ø“ markiert) und
  Freitextsuche. Die Liste aktualisiert sich sofort.
* Häkchen = für den Vergleich auswählen (die Auswahl bleibt beim Filtern erhalten).
* Knöpfe rechts: aktualisieren · bearbeiten (in die Erfassung laden) · **als Referenz** ·
  alle/keine auswählen · löschen.
* **Ähnliche suchen:** vergleicht die Referenz mit allen Linern der gefilterten Liste
  und sortiert nach Ähnlichkeit (Spalte „%“).
* Import/Export der ganzen Datenbank als CSV (Trennzeichen `;`, eine Zeile pro Messhöhe –
  lässt sich in Excel öffnen).

## 3. Vergleich (Panel „Vergleich“)

2–8 Liner auswählen, optional eine Referenz setzen (sonst der erste ausgewählte).

* **Vergleichen** – in Blender (Sammlung „Liner-Vergleich“):
  * alle Liner nebeneinander mit **gemeinsamer Farbskala**, beschriftet mit Ähnlichkeit;
  * darunter je Liner ein **Differenz-Modell**: blau = dünner als die Referenz,
    grau = gleich, rot = dicker (Skala automatisch oder fest einstellbar);
  * im Panel je Liner: Ähnlichkeit gesamt / Wand / Form, mittlere und größte Abweichung
    mit Ort.
* **Bericht (HTML)** – eigenständige Datei, öffnet sich im Browser:
  * Ähnlichkeits-Kacheln und **Ähnlichkeitsmatrix** (jeder gegen jeden),
  * **Wandstärkenverlauf** über die Höhe je Richtung (A/M/P/L),
  * **Querschnitte** an allen Messhöhen übereinandergelegt (Wand wahlweise überhöht),
  * **Abweichungskarten** (abgewickelte Liner-Wand, Höhe × Umfangsrichtung),
  * Tabelle aller Messwerte mit Differenzen in mm und %, Stammdaten, Berechnungsweg.

### Wie wird die Ähnlichkeit berechnet?

Verglichen wird im gemeinsamen Höhenbereich auf einem Raster von 1 cm × 22,5°,
immer anatomisch (A/M/P/L), unabhängig von der Seite.

* **Wand** = 100 % × (1 − mittlere |Δ Wandstärke| ÷ mittlere Wandstärke beider Liner)
* **Form** = 100 % × (1 − mittlere |Δ Innenumfang| ÷ mittlerer Innenumfang beider Liner)
* **Gesamt** = gewichteter Mittelwert (Standard 70 % Wand, 30 % Form; im Panel einstellbar)

Zusätzlich: mittlere Abweichung mit Vorzeichen (systematisch dicker/dünner), größte
Abweichung mit Ort, und die **Muster-Korrelation r** – sie zeigt, ob die Wandstärken
gleich *verteilt* sind (z. B. beide posterior dicker), unabhängig vom absoluten Niveau.

## Modell

* Z nach oben, Z = 0 = distales Ende außen, −Y = anterior, medial = +X (rechts) bzw. −X (links),
  Maßstab 1:1 in Metern; das Mesh ist geschlossen und kann direkt als STL exportiert werden.
* Interpolation: zwischen den vier Messrichtungen glatt (trigonometrisch), zwischen den
  Höhen monoton kubisch – ohne Überschwingen.
* Unter der untersten Messhöhe: ellipsoide distale Kappe mit der distalen Wandstärke.
* Vertex-Attribute: `Wandstaerke_mm`, bei Differenz-Modellen zusätzlich `Differenz_mm`.

## Entwicklung

```
python3 -m unittest discover -s tests      # ohne Blender
pip install bpy && python3 tests/blender_integration.py   # mit Blender-Python
```

Aufbau: `geometry.py`, `database.py` (SQLite), `compare.py`, `report.py` und `colors.py`
sind reines Python ohne Blender; `ui.py` enthält Panels und Operatoren.
