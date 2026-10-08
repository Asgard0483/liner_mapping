"""Farbskalen für die Darstellung in Blender und im Bericht (ohne bpy)."""

# Sequentiell (Wandstärke): ein Farbton, hell = dünn -> dunkel = dick
SEQUENTIAL = ("#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b")

# Divergierend (Differenz): blau = dünner, grau = gleich, rot = dicker
DIVERGING_NEG = "#184f95"
DIVERGING_MID = "#f0efec"
DIVERGING_POS = "#b3261e"

# Kategoriale Reihenfolge für Liner im Vergleich (fest, nie zyklisch)
CATEGORICAL_LIGHT = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100",
                     "#e87ba4", "#008300", "#4a3aa7", "#e34948")
CATEGORICAL_DARK = ("#3987e5", "#d95926", "#199e70", "#c98500",
                    "#d55181", "#008300", "#9085e9", "#e66767")
MAX_COMPARE = len(CATEGORICAL_LIGHT)

NO_DATA = "#a9a8a2"


def hex_to_srgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def srgb_to_linear(c):
    return tuple(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c)


def _lerp(a, b, u):
    return tuple(a[i] + (b[i] - a[i]) * u for i in range(3))


def _ramp(stops_hex, f):
    stops = [hex_to_srgb(h) for h in stops_hex]
    f = min(max(f, 0.0), 1.0) * (len(stops) - 1)
    i = min(int(f), len(stops) - 2)
    return _lerp(stops[i], stops[i + 1], f - i)


def thickness_rgba(t, tmin, tmax):
    """Lineare RGBA-Farbe (für Blender-Farbattribute) einer Wandstärke."""
    f = 0.5 if tmax - tmin < 1e-9 else (t - tmin) / (tmax - tmin)
    return srgb_to_linear(_ramp(SEQUENTIAL, f)) + (1.0,)


def difference_rgba(d, limit):
    """Lineare RGBA-Farbe einer Differenz (symmetrische Skala +-limit). None = keine Daten."""
    if d is None:
        return srgb_to_linear(hex_to_srgb(NO_DATA)) + (1.0,)
    f = 0.0 if limit < 1e-9 else max(-1.0, min(1.0, d / limit))
    mid = hex_to_srgb(DIVERGING_MID)
    pole = hex_to_srgb(DIVERGING_POS if f > 0 else DIVERGING_NEG)
    return srgb_to_linear(_lerp(mid, pole, abs(f))) + (1.0,)


def categorical_rgba(i):
    return srgb_to_linear(hex_to_srgb(CATEGORICAL_LIGHT[i % MAX_COMPARE])) + (1.0,)
