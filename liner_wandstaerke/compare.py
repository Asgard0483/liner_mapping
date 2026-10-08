"""Vergleich von Linern (ohne bpy).

Alle Vergleiche laufen im anatomischen Bezugssystem (A/M/P/L), unabhängig davon,
ob der Liner für ein rechtes oder linkes Bein erfasst wurde. Verglichen wird nur
im gemeinsamen Höhenbereich beider Liner (zwischen der jeweils untersten und
höchsten Messhöhe).

Ähnlichkeit (0-100 %):
  Wand  = 100 * (1 - mittlere |Δ Wandstärke| / mittlere Wandstärke beider Liner)
  Form  = 100 * (1 - mittlere |Δ Innenumfang| / mittlerer Innenumfang beider Liner)
  Gesamt = gewichteter Mittelwert (Standard: 70 % Wand, 30 % Form)
Werte unter 0 werden auf 0 begrenzt.
"""

import math

from .database import record_label
from .geometry import SIDES, SIDE_ANGLES_DEG, LinerModel, point_label

DEFAULT_WALL_WEIGHT = 0.7
GRID_DZ_MM = 10.0
GRID_THETAS = 16
_OCTANTS = ("A", "AM", "M", "PM", "P", "PL", "L", "AL")


def angle_label(j, n=GRID_THETAS):
    """Bezeichnung des j-ten Rasterwinkels, z. B. 'A', 'AM' oder '22,5°'."""
    deg = 360.0 * j / n
    if abs(deg / 45.0 - round(deg / 45.0)) < 1e-9:
        return _OCTANTS[int(round(deg / 45.0)) % 8]
    return ("%g°" % deg).replace(".", ",")


ANGLE_NAMES = tuple(angle_label(j) for j in range(GRID_THETAS))


def model_from_record(rec):
    return LinerModel(rec["messungen"], distal_mm=rec.get("distal_mm") or 8.0,
                      circ_reference=rec.get("umfang_bezug") or "INNER")


def overlap(m1, m2):
    lo, hi = max(m1.z_min, m2.z_min), min(m1.z_max, m2.z_max)
    return (lo, hi) if hi >= lo else None


def grid_heights(lo, hi, dz=GRID_DZ_MM):
    zs = []
    z = lo
    while z < hi - 1e-6:
        zs.append(z)
        z += dz
    zs.append(hi)
    return zs


def grid_thetas(n=GRID_THETAS):
    return [2.0 * math.pi * j / n for j in range(n)]


def _pearson(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx < 1e-12 or syy < 1e-12:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def compare_models(ref, other, wall_weight=DEFAULT_WALL_WEIGHT):
    """Kennzahlen 'other' gegenüber 'ref'. Gibt None zurück, wenn sich die
    Höhenbereiche nicht überlappen."""
    ov = overlap(ref, other)
    if ov is None:
        return None
    zs = grid_heights(*ov)
    ths = grid_thetas()
    ta, tb, deltas = [], [], []
    worst = (0.0, None, None)
    for z in zs:
        ra, rb = ref.ring(z), other.ring(z)
        for j, th in enumerate(ths):
            a, b = ra(th), rb(th)
            ta.append(a)
            tb.append(b)
            d = b - a
            deltas.append(d)
            if abs(d) > abs(worst[0]):
                worst = (d, z, j)
    n = len(deltas)
    mae = sum(abs(d) for d in deltas) / n
    rms = math.sqrt(sum(d * d for d in deltas) / n)
    bias = sum(deltas) / n
    t_mean = (sum(ta) + sum(tb)) / (2 * n)

    ca = [ref.circumference_in_cm(z) for z in zs]
    cb = [other.circumference_in_cm(z) for z in zs]
    dc = [b - a for a, b in zip(ca, cb)]
    circ_mae = sum(abs(d) for d in dc) / len(dc)
    circ_mean = (sum(ca) + sum(cb)) / (2 * len(zs))

    s_wall = 100.0 * max(0.0, 1.0 - mae / t_mean)
    s_form = 100.0 * max(0.0, 1.0 - circ_mae / circ_mean)
    w = min(max(wall_weight, 0.0), 1.0)
    return {
        "bereich_cm": (ov[0] / 10.0, ov[1] / 10.0),
        "mae_mm": mae,
        "rms_mm": rms,
        "bias_mm": bias,
        "max_mm": worst[0],
        "max_hoehe_cm": worst[1] / 10.0 if worst[1] is not None else None,
        "max_winkel": ANGLE_NAMES[worst[2]] if worst[2] is not None else None,
        "korrelation": _pearson(ta, tb),
        "umfang_mae_cm": circ_mae,
        "umfang_bias_cm": sum(dc) / len(dc),
        "distal_diff_mm": other.distal_mm - ref.distal_mm,
        "aehnlichkeit_wand": s_wall,
        "aehnlichkeit_form": s_form,
        "aehnlichkeit": w * s_wall + (1.0 - w) * s_form,
    }


def similarity_matrix(models, wall_weight=DEFAULT_WALL_WEIGHT):
    n = len(models)
    mat = [[100.0 if i == j else None for j in range(n)] for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            r = compare_models(models[i], models[j], wall_weight)
            s = r["aehnlichkeit"] if r else None
            mat[i][j] = mat[j][i] = s
    return mat


TABLE_MAX_ANGLES = 16


def table_angles(models, z):
    """Winkel (Grad) für die Messwert-Tabelle in Höhe z: alle dort gemessenen
    Winkel aller Liner; bei mehr als 16 das 22,5°-Raster."""
    angles = set()
    for m in models:
        for h, (_, t, _) in zip(m.hs, m.rows):
            if abs(h - z) < 1e-6:
                angles.update(round(360.0 * k / len(t), 6) for k in range(len(t)))
    if not angles or len(angles) > TABLE_MAX_ANGLES:
        angles = {round(360.0 * j / TABLE_MAX_ANGLES, 6) for j in range(TABLE_MAX_ANGLES)}
    return sorted(angles)


def angle_text(deg):
    """'A', 'M', 'P', 'L' für die Hauptrichtungen, sonst z. B. '45°'."""
    for name, a in zip(SIDES, SIDE_ANGLES_DEG):
        if abs(deg - a) < 1e-6:
            return name
    return ("%g°" % round(deg, 2)).replace(".", ",")


def point_table(models):
    """Messwerte aller Liner an allen gemessenen Höhen (Vereinigung).

    Liefert Zeilen: (hoehe_cm, winkel_grad, bezeichnung, [wert oder None, ...],
    [gemessen?, ...]). Werte außerhalb des Messbereichs eines Liners sind None;
    Werte, die der Liner an dieser Stelle nicht gemessen hat, werden interpoliert
    und als nicht gemessen markiert.
    """
    heights = sorted({h for m in models for h in m.hs})
    rows = []
    for z in heights:
        for deg in table_angles(models, z):
            th = math.radians(deg)
            vals, measured = [], []
            for m in models:
                if m.z_min - 1e-6 <= z <= m.z_max + 1e-6:
                    vals.append(m.thickness(z, th))
                    measured.append(m.measured_at(z, th))
                else:
                    vals.append(None)
                    measured.append(False)
            rows.append((z / 10.0, deg, angle_text(deg), vals, measured))
    return rows


def vertex_differences(ref, other, samples):
    """Differenz other - ref (mm) je Geometrie-Sample von 'other'; None außerhalb
    des gemeinsamen Höhenbereichs."""
    ov = overlap(ref, other)
    out = []
    for z, th, w in samples:
        if w <= 0.0:
            out.append(other.distal_mm - ref.distal_mm)
        elif ov is None or z < ov[0] - 1e-6 or z > ov[1] + 1e-6:
            out.append(None)
        else:
            out.append(other.sample(z, th, w) - ref.sample(z, th, w))
    return out


def rank_similar(ref_rec, candidates, wall_weight=DEFAULT_WALL_WEIGHT):
    """Sortiert Kandidaten nach Ähnlichkeit zu ref_rec (höchste zuerst).

    Rückgabe: Liste von (record, kennzahlen-dict oder None)."""
    ref = model_from_record(ref_rec)
    out = []
    for rec in candidates:
        if rec["id"] == ref_rec.get("id"):
            continue
        try:
            res = compare_models(ref, model_from_record(rec), wall_weight)
        except ValueError:
            res = None
        out.append((rec, res))
    out.sort(key=lambda x: -(x[1]["aehnlichkeit"] if x[1] else -1.0))
    return out


def build_comparison(records, ref_index=0, wall_weight=DEFAULT_WALL_WEIGHT):
    """Komplette Auswertung für den Bericht und die Anzeige in Blender."""
    if len(records) < 2:
        raise ValueError("Für einen Vergleich werden mindestens 2 Liner benötigt")
    records = list(records)
    ref_rec = records.pop(ref_index)
    records.insert(0, ref_rec)
    models = [model_from_record(r) for r in records]
    pairs = [compare_models(models[0], m, wall_weight) for m in models[1:]]
    return {
        "records": records,
        "labels": [record_label(r) for r in records],
        "models": models,
        "pairs": pairs,
        "matrix": similarity_matrix(models, wall_weight),
        "points": point_table(models),
        "wall_weight": wall_weight,
    }


__all__ = ["SIDES", "SIDE_ANGLES_DEG", "ANGLE_NAMES", "angle_label", "build_comparison", "compare_models",
           "model_from_record", "point_label", "point_table", "table_angles", "rank_similar", "similarity_matrix",
           "vertex_differences"]
