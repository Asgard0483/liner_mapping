# SPDX-License-Identifier: GPL-3.0-or-later
"""
Liner-Wandstärken – Blender-Add-on

Wandstärken prothetischer Liner erfassen (je Höhe 4 Messpunkte: anterior,
medial, posterior, lateral), als 3D-Modell darstellen, in einer Datenbank
sammeln, nach Hersteller/Artikel/Größe/Wandstärke/Form filtern und mehrere
Liner grafisch und numerisch vergleichen.

Installation: Bearbeiten > Einstellungen > Add-ons > "Von Datenträger
installieren" und die ZIP-Datei wählen. Danach im 3D-Viewport die
Seitenleiste (Taste N) öffnen, Reiter "Liner".
"""

bl_info = {
    "name": "Liner-Wandstärken",
    "author": "liner_mapping",
    "version": (2, 0, 0),
    "blender": (3, 6, 0),
    "location": "3D-Viewport > Seitenleiste (N) > Liner",
    "description": "Wandstärken von Prothesen-Linern erfassen, in einer Datenbank sammeln und vergleichen",
    "category": "Object",
}


def register():
    from . import ui
    ui.register()


def unregister():
    from . import ui
    ui.unregister()
