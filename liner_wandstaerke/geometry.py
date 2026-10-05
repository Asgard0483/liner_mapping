"""Reine Geometrie des Liner-Modells (ohne bpy, auch außerhalb von Blender nutzbar).

Konventionen:
  * Höhen werden intern in mm gerechnet, Z = 0 ist das distale Ende (außen).
  * Anatomischer Winkel theta: 0° = anterior, 90° = medial, 180° = posterior,
    270° = lateral. Die Seite (rechts/links) bestimmt nur die Lage im Raum.
  * Raum: -Y = anterior, medial = +X (rechtes Bein) bzw. -X (linkes Bein).
"""

import math

SIDES = ("A", "M", "P", "L")
SIDE_NAMES = ("Anterior", "Medial", "Posterior", "Lateral")
SIDE_ANGLES_DEG = (0.0, 90.0, 180.0, 270.0)


class Pchip:
    """Monotone kubische Hermite-Interpolation (Fritsch-Carlson).

    Kein Überschwingen zwischen den Stützstellen; außerhalb konstant.
    """

    def __init__(self, xs, ys):
        if len(xs) != len(ys) or not xs:
            raise ValueError("Pchip braucht gleich viele x- und y-Werte")
        self.xs = list(xs)
        self.ys = list(ys)
        self.m = self._slopes(self.xs, self.ys)

    @staticmethod
    def _slopes(xs, ys):
        n = len(xs)
        if n == 1:
            return [0.0]
        h = [xs[i + 1] - xs[i] for i in range(n - 1)]
        d = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
        if n == 2:
            return [d[0], d[0]]
        m = [0.0] * n
        for i in range(1, n - 1):
            if d[i - 1] * d[i] > 0.0:
                w1 = 2.0 * h[i] + h[i - 1]
                w2 = h[i] + 2.0 * h[i - 1]
                m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])

        def end_slope(h0, h1, d0, d1):
            s = ((2.0 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
            if s * d0 <= 0.0:
                return 0.0
            if d0 * d1 <= 0.0 and abs(s) > abs(3.0 * d0):
                return 3.0 * d0
            return s

        m[0] = end_slope(h[0], h[1], d[0], d[1])
        m[-1] = end_slope(h[-1], h[-2], d[-1], d[-2])
        return m

    def __call__(self, x):
        xs, ys, m = self.xs, self.ys, self.m
        if len(xs) == 1 or x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        lo, hi = 0, len(xs) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if xs[mid] <= x:
                lo = mid
            else:
                hi = mid
        i = lo
        h = xs[i + 1] - xs[i]
        t = (x - xs[i]) / h
        t2, t3 = t * t, t * t * t
        return ((2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h * m[i]
                + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h * m[i + 1])


def pchip(xs, ys, x):
    return Pchip(xs, ys)(x)


def ring_thickness(values, theta):
    """Glatte, periodische Interpolation der 4 Messwerte (0°, 90°, 180°, 270°).

    Trigonometrisches Polynom, das exakt durch alle vier Werte läuft; auf den
    Bereich [min, max] der Messwerte begrenzt, damit kein Überschwingen entsteht.
    theta in Radiant (anatomischer Winkel).
    """
    v0, v1, v2, v3 = values
    a0 = (v0 + v1 + v2 + v3) / 4.0
    a1 = (v0 - v2) / 2.0
    b1 = (v1 - v3) / 2.0
    a2 = (v0 - v1 + v2 - v3) / 4.0
    t = a0 + a1 * math.cos(theta) + b1 * math.sin(theta) + a2 * math.cos(2.0 * theta)
    return min(max(t, min(values)), max(values))


def inner_radius_mm(thickness4, circumference_cm, circ_reference):
    """Innenradius aus dem Umfang (innen am Stumpf oder außen über dem Liner)."""
    r = circumference_cm * 10.0 / (2.0 * math.pi)
    if circ_reference == "OUTER":
        r -= sum(thickness4) / 4.0
    return r


class LinerModel:
    """Kontinuierliches Modell eines Liners aus den Messwerten.

    rows: Liste von (hoehe_cm, (A, M, P, L) in mm, umfang_cm).
    """

    def __init__(self, rows, *, distal_mm=8.0, circ_reference="INNER"):
        rows = sorted(rows, key=lambda r: r[0])
        if not rows:
            raise ValueError("Keine Messhöhen vorhanden")
        self.rows = [(float(h), tuple(float(v) for v in t), float(c)) for h, t, c in rows]
        self.hs = [r[0] * 10.0 for r in self.rows]
        if len(set(self.hs)) != len(self.hs):
            raise ValueError("Messhöhen müssen eindeutig sein")
        if any(len(r[1]) != 4 for r in self.rows):
            raise ValueError("Pro Höhe werden genau 4 Wandstärken erwartet")
        if min(min(r[1]) for r in self.rows) <= 0.0:
            raise ValueError("Wandstärken müssen größer als 0 sein")
        if distal_mm <= 0.0:
            raise ValueError("Distale Wandstärke muss größer als 0 sein")
        if self.hs[0] <= distal_mm:
            raise ValueError("Unterste Messhöhe muss über der distalen Wandstärke liegen")
        self.distal_mm = float(distal_mm)
        self.circ_reference = circ_reference
        self.r_in = [inner_radius_mm(t, c, circ_reference) for (_, t, c) in self.rows]
        if min(self.r_in) <= 0.0:
            raise ValueError("Umfang zu klein für die angegebene Wandstärke")
        self._cols = [Pchip(self.hs, [r[1][k] for r in self.rows]) for k in range(4)]
        self._rin = Pchip(self.hs, self.r_in)

    @property
    def z_min(self):
        return self.hs[0]

    @property
    def z_max(self):
        return self.hs[-1]

    def side_values(self, z_mm):
        return [c(z_mm) for c in self._cols]

    def thickness(self, z_mm, theta):
        return ring_thickness(self.side_values(z_mm), theta)

    def radius_in(self, z_mm):
        return self._rin(z_mm)

    def circumference_in_cm(self, z_mm):
        return 2.0 * math.pi * self.radius_in(z_mm) / 10.0

    def sample(self, z_mm, theta, weight):
        """Wandstärke an einem Geometrie-Sample (siehe build_liner_geometry)."""
        if weight <= 0.0:
            return self.distal_mm
        return weight * self.thickness(z_mm, theta) + (1.0 - weight) * self.distal_mm


def build_liner_geometry(rows, *, distal_mm=8.0, total_length_cm=None,
                         circ_reference="INNER", right_side=True,
                         segments=64, step_mm=5.0, cap_rings=12):
    """Erzeugt die Liner-Geometrie.

    Rückgabe: (verts in Metern, faces, thickness_mm pro Vertex, info-dict).
    info["samples"] enthält pro Vertex (z_mm, theta, gewicht); damit lässt sich
    jede Größe, die von der Wandstärke abhängt (z. B. die Differenz zu einem
    anderen Liner), exakt für jeden Vertex auswerten.
    """
    model = LinerModel(rows, distal_mm=distal_mm, circ_reference=circ_reference)
    hs = model.hs
    top = max(hs[-1], (total_length_cm or 0.0) * 10.0)
    side = 1.0 if right_side else -1.0
    thetas = [2.0 * math.pi * j / segments for j in range(segments)]
    dirs = [(side * math.sin(th), -math.cos(th)) for th in thetas]

    zs = set(hs)
    z = hs[0]
    while z < top:
        zs.add(round(z, 6))
        z += step_mm
    zs.add(top)
    zs = sorted(zs)

    z1 = hs[0]
    r1 = model.radius_in(z1)
    t1 = [model.thickness(z1, th) for th in thetas]

    outer_rings, inner_rings, ring_t, ring_s = [], [], [], []

    # Distale Kappe: Ellipsoid-Viertel unterhalb der untersten Messhöhe.
    # Außen von z1 bis z=0, innen von z1 bis z=distal_mm.
    for i in range(cap_rings, 0, -1):
        phi = 0.5 * math.pi * i / cap_rings
        c, s = math.cos(phi), math.sin(phi)
        zo = z1 - z1 * s
        zi = z1 - (z1 - distal_mm) * s
        orow, irow, trow, srow = [], [], [], []
        for j, (dx, dy) in enumerate(dirs):
            ro = (r1 + t1[j]) * c
            ri = r1 * c
            orow.append((dx * ro, dy * ro, zo))
            irow.append((dx * ri, dy * ri, zi))
            trow.append(t1[j] * c + distal_mm * (1.0 - c))
            srow.append((z1, thetas[j], c))
        outer_rings.append(orow)
        inner_rings.append(irow)
        ring_t.append(trow)
        ring_s.append(srow)

    for z in zs:
        ri = model.radius_in(z)
        orow, irow, trow, srow = [], [], [], []
        for (dx, dy), th in zip(dirs, thetas):
            t = model.thickness(z, th)
            ro = ri + t
            orow.append((dx * ro, dy * ro, z))
            irow.append((dx * ri, dy * ri, z))
            trow.append(t)
            srow.append((z, th, 1.0))
        outer_rings.append(orow)
        inner_rings.append(irow)
        ring_t.append(trow)
        ring_s.append(srow)

    verts, thick, samples, faces = [], [], [], []
    n_rings = len(outer_rings)

    def add_surface(rings, tip):
        tip_idx = len(verts)
        verts.append(tip)
        thick.append(distal_mm)
        samples.append((z1, 0.0, 0.0))
        start = len(verts)
        for ring, trow, srow in zip(rings, ring_t, ring_s):
            verts.extend(ring)
            thick.extend(trow)
            samples.extend(srow)
        return tip_idx, start

    o_tip, o0 = add_surface(outer_rings, (0.0, 0.0, 0.0))
    i_tip, i0 = add_surface(inner_rings, (0.0, 0.0, distal_mm))

    def idx(base, r, j):
        return base + r * segments + (j % segments)

    # Außenfläche (Normalen nach außen)
    for j in range(segments):
        faces.append((o_tip, idx(o0, 0, j + 1), idx(o0, 0, j)))
    for r in range(n_rings - 1):
        for j in range(segments):
            faces.append((idx(o0, r, j), idx(o0, r, j + 1),
                          idx(o0, r + 1, j + 1), idx(o0, r + 1, j)))
    # Innenfläche (Normalen in den Hohlraum)
    for j in range(segments):
        faces.append((i_tip, idx(i0, 0, j), idx(i0, 0, j + 1)))
    for r in range(n_rings - 1):
        for j in range(segments):
            faces.append((idx(i0, r, j), idx(i0, r + 1, j),
                          idx(i0, r + 1, j + 1), idx(i0, r, j + 1)))
    # Proximaler Rand
    last = n_rings - 1
    for j in range(segments):
        faces.append((idx(o0, last, j), idx(o0, last, j + 1),
                      idx(i0, last, j + 1), idx(i0, last, j)))

    # Beim linken Bein ist die Umlaufrichtung gespiegelt
    if not right_side:
        faces = [tuple(reversed(f)) for f in faces]

    verts_m = [(x / 1000.0, y / 1000.0, zz / 1000.0) for (x, y, zz) in verts]
    info = {
        "min_mm": min(thick),
        "max_mm": max(thick),
        "length_cm": top / 10.0,
        "max_radius_m": max(math.hypot(v[0], v[1]) for v in verts_m),
        "samples": samples,
        "model": model,
    }
    return verts_m, faces, thick, info


def marker_positions(rows, *, circ_reference="INNER", right_side=True):
    """Außenpunkte der Messstellen in Metern: Liste von (name, (x, y, z), mm)."""
    side = 1.0 if right_side else -1.0
    out = []
    for (h, t, circ) in sorted(rows, key=lambda r: r[0]):
        r = inner_radius_mm(t, circ, circ_reference)
        for k, ang in enumerate(SIDE_ANGLES_DEG):
            th = math.radians(ang)
            ro = r + t[k]
            x, y = side * math.sin(th) * ro, -math.cos(th) * ro
            name = "%gcm_%s" % (h, SIDES[k])
            out.append((name, (x / 1000.0, y / 1000.0, h / 100.0), t[k]))
    return out
