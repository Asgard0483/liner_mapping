# Liner-Wandstärken (Blender-Add-on)

Erfasst die Wandstärke eines prothetischen Liners und erzeugt daraus ein 3D-Modell
mit farbiger Wandstärken-Karte (blau = dünn → rot = dick).

## Installation

* **Als Add-on:** Bearbeiten → Einstellungen → Add-ons → „Von Datenträger installieren…“
  → `liner_wandstaerke.py` wählen und aktivieren.
* **Ohne Installation:** Datei im Text-Editor öffnen → „Skript ausführen“.

Danach im 3D-Viewport die Seitenleiste öffnen (Taste **N**), Reiter **Liner**.

## Messschema

| Spalte | Lage      | Winkel |
|--------|-----------|--------|
| A      | anterior  | 0°     |
| M      | medial    | 90°    |
| P      | posterior | 180°   |
| L      | lateral   | 270°   |

Voreingestellte Höhen (von distal): 4, 8, 12, 16, 20, 25, 30 cm. Zeilen lassen sich
hinzufügen/entfernen, die Höhen sind frei editierbar.

Pro Höhe wird außerdem der **Umfang** eingetragen (wahlweise innen = Stumpf oder
außen über dem Liner gemessen) – daraus ergibt sich der Innenradius des Modells.
Die Wandstärken werden in mm eingegeben.

## Modell

* Koordinaten: Z nach oben, Z = 0 ist das distale Ende (außen), −Y = anterior,
  medial = +X (rechtes Bein) bzw. −X (linkes Bein). Maßstab in Metern (1:1).
* Zwischen den 4 Messpunkten wird glatt (trigonometrisch) interpoliert, zwischen den
  Höhen monoton kubisch (kein Überschwingen).
* Unterhalb der untersten Messhöhe wird eine ellipsoide distale Kappe mit der
  eingestellten distalen Wandstärke erzeugt; oberhalb der höchsten Messhöhe bleibt
  die Wandstärke bis zur Gesamtlänge konstant.
* Das Mesh ist geschlossen (wasserdicht) und kann direkt als STL/OBJ exportiert werden.
* Die Wandstärke liegt als Attribut `Wandstaerke_mm` an jedem Vertex vor, die Farbe als
  Farbattribut `Wandstaerke_Farbe`. Messpunkte werden als kleine Kugeln (Sammlung
  „Liner-Messpunkte“) markiert, optional mit Beschriftung.
* Messwerte können als CSV (Trennzeichen `;`) gespeichert und geladen werden.
