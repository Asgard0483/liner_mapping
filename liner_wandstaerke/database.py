"""Liner-Datenbank auf Basis von SQLite (ohne bpy).

Ein Datensatz ("record") ist ein dict:
    {
      "id": int | None,
      "hersteller": str, "artikel": str, "groesse": str, "form": str,
      "material": str, "notiz": str,
      "nenn_wandstaerke_mm": float,          # Herstellerangabe, 0 = unbekannt
      "seite": "RIGHT" | "LEFT",
      "umfang_bezug": "INNER" | "OUTER",
      "distal_mm": float, "laenge_cm": float,
      "messungen": [(hoehe_cm, (A, M, P, L), umfang_cm), ...],
    }
"""

import csv
import datetime
import os
import sqlite3

META_FIELDS = ("hersteller", "artikel", "groesse", "form", "material", "notiz")
FILTER_FIELDS = ("hersteller", "artikel", "groesse", "form")
NUM_FIELDS = ("nenn_wandstaerke_mm", "distal_mm", "laenge_cm")

SCHEMA = """
CREATE TABLE IF NOT EXISTS liner (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hersteller TEXT NOT NULL DEFAULT '',
    artikel TEXT NOT NULL DEFAULT '',
    groesse TEXT NOT NULL DEFAULT '',
    form TEXT NOT NULL DEFAULT '',
    material TEXT NOT NULL DEFAULT '',
    notiz TEXT NOT NULL DEFAULT '',
    nenn_wandstaerke_mm REAL NOT NULL DEFAULT 0,
    seite TEXT NOT NULL DEFAULT 'RIGHT',
    umfang_bezug TEXT NOT NULL DEFAULT 'INNER',
    distal_mm REAL NOT NULL DEFAULT 8,
    laenge_cm REAL NOT NULL DEFAULT 0,
    erstellt TEXT NOT NULL,
    geaendert TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messung (
    liner_id INTEGER NOT NULL REFERENCES liner(id) ON DELETE CASCADE,
    hoehe_cm REAL NOT NULL,
    a_mm REAL NOT NULL, m_mm REAL NOT NULL, p_mm REAL NOT NULL, l_mm REAL NOT NULL,
    umfang_cm REAL NOT NULL,
    PRIMARY KEY (liner_id, hoehe_cm)
);
"""

CSV_COLUMNS = ("liner_nr",) + META_FIELDS + NUM_FIELDS + (
    "seite", "umfang_bezug", "hoehe_cm", "anterior_mm", "medial_mm",
    "posterior_mm", "lateral_mm", "umfang_cm")


def empty_record():
    rec = {f: "" for f in META_FIELDS}
    rec.update(id=None, nenn_wandstaerke_mm=0.0, seite="RIGHT", umfang_bezug="INNER",
               distal_mm=8.0, laenge_cm=0.0, messungen=[])
    return rec


def record_label(rec):
    parts = [p for p in (rec.get("hersteller"), rec.get("artikel")) if p]
    label = " ".join(parts) or "Liner #%s" % rec.get("id")
    if rec.get("groesse"):
        label += " Gr. %s" % rec["groesse"]
    return label


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _num(v):
    return float(str(v).strip().replace(",", ".")) if str(v).strip() else 0.0


class LinerDB:
    def __init__(self, path):
        self.path = os.path.abspath(os.path.expanduser(path))
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        self.con = sqlite3.connect(self.path)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.executescript(SCHEMA)

    def close(self):
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- Schreiben ---------------------------------------------------------

    def save(self, rec):
        """Speichert einen Datensatz (neu oder Update, falls rec['id'] existiert)."""
        if not rec.get("messungen"):
            raise ValueError("Datensatz hat keine Messwerte")
        heights = [m[0] for m in rec["messungen"]]
        if len(set(heights)) != len(heights):
            raise ValueError("Messhöhen müssen eindeutig sein")
        vals = {f: str(rec.get(f) or "").strip() for f in META_FIELDS}
        vals.update({f: float(rec.get(f) or 0.0) for f in NUM_FIELDS})
        vals["seite"] = rec.get("seite") or "RIGHT"
        vals["umfang_bezug"] = rec.get("umfang_bezug") or "INNER"
        now = _now()
        with self.con:
            rid = rec.get("id")
            exists = rid and self.con.execute(
                "SELECT 1 FROM liner WHERE id = ?", (rid,)).fetchone()
            cols = list(vals)
            if exists:
                self.con.execute(
                    "UPDATE liner SET %s, geaendert = ? WHERE id = ?"
                    % ", ".join("%s = ?" % c for c in cols),
                    [vals[c] for c in cols] + [now, rid])
                self.con.execute("DELETE FROM messung WHERE liner_id = ?", (rid,))
            else:
                cur = self.con.execute(
                    "INSERT INTO liner (%s, erstellt, geaendert) VALUES (%s)"
                    % (", ".join(cols), ", ".join("?" * (len(cols) + 2))),
                    [vals[c] for c in cols] + [now, now])
                rid = cur.lastrowid
            self.con.executemany(
                "INSERT INTO messung VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(rid, float(h), *[float(v) for v in t], float(c))
                 for h, t, c in rec["messungen"]])
        return rid

    def delete(self, rid):
        with self.con:
            self.con.execute("DELETE FROM liner WHERE id = ?", (rid,))

    # -- Lesen -------------------------------------------------------------

    def get(self, rid):
        row = self.con.execute("SELECT * FROM liner WHERE id = ?", (rid,)).fetchone()
        if row is None:
            return None
        rec = dict(row)
        rec["messungen"] = [
            (m["hoehe_cm"], (m["a_mm"], m["m_mm"], m["p_mm"], m["l_mm"]), m["umfang_cm"])
            for m in self.con.execute(
                "SELECT * FROM messung WHERE liner_id = ? ORDER BY hoehe_cm", (rid,))]
        return rec

    def get_many(self, ids):
        out = []
        for rid in ids:
            rec = self.get(rid)
            if rec is not None:
                out.append(rec)
        return out

    def distinct(self, field, where=None):
        """Vorhandene Werte eines Textfeldes (für Filter-Vorschläge)."""
        if field not in META_FIELDS:
            raise ValueError(field)
        sql = "SELECT DISTINCT %s FROM liner WHERE %s != ''" % (field, field)
        args = []
        for k, v in (where or {}).items():
            if k in FILTER_FIELDS and v:
                sql += " AND %s = ? COLLATE NOCASE" % k
                args.append(v)
        vals = [r[0] for r in self.con.execute(sql, args)]
        return sorted(vals, key=_natural_key)

    def search(self, *, hersteller="", artikel="", groesse="", form="",
               wand_min=0.0, wand_max=0.0, text=""):
        """Liefert Kurzinfos aller passenden Liner.

        Der Wandstärken-Filter nutzt die Nenn-Wandstärke; ist keine angegeben,
        die mittlere gemessene Wandstärke.
        """
        sql = """SELECT l.*, AVG((m.a_mm + m.m_mm + m.p_mm + m.l_mm) / 4.0) AS mittel_mm,
                        MIN(MIN(m.a_mm, m.m_mm, m.p_mm, m.l_mm)) AS min_mm,
                        MAX(MAX(m.a_mm, m.m_mm, m.p_mm, m.l_mm)) AS max_mm,
                        COUNT(m.hoehe_cm) AS n_hoehen
                 FROM liner l LEFT JOIN messung m ON m.liner_id = l.id WHERE 1 = 1"""
        args = []
        for field, val in (("hersteller", hersteller), ("artikel", artikel),
                           ("groesse", groesse), ("form", form)):
            if val:
                sql += " AND l.%s = ? COLLATE NOCASE" % field
                args.append(val)
        if text:
            sql += (" AND (l.hersteller LIKE ? OR l.artikel LIKE ? OR l.material LIKE ?"
                    " OR l.notiz LIKE ? OR l.form LIKE ?)")
            args += ["%" + text + "%"] * 5
        sql += " GROUP BY l.id"
        out = []
        for r in self.con.execute(sql, args):
            d = dict(r)
            wand = d["nenn_wandstaerke_mm"] or d["mittel_mm"] or 0.0
            if wand_min and wand < wand_min:
                continue
            if wand_max and wand > wand_max:
                continue
            d["wand_mm"] = wand
            d["label"] = record_label(d)
            out.append(d)
        out.sort(key=lambda d: (d["hersteller"].lower(), d["artikel"].lower(),
                                _natural_key(d["groesse"]), d["id"]))
        return out

    def count(self):
        return self.con.execute("SELECT COUNT(*) FROM liner").fetchone()[0]

    # -- CSV ---------------------------------------------------------------

    def export_csv(self, path, ids=None):
        """Exportiert Liner im Langformat (eine Zeile pro Messhöhe), Trennzeichen ';'."""
        if ids is None:
            ids = [r[0] for r in self.con.execute("SELECT id FROM liner ORDER BY id")]
        n = 0
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(CSV_COLUMNS)
            for rec in self.get_many(ids):
                n += 1
                for h, t, c in rec["messungen"]:
                    w.writerow([rec["id"]] + [rec[k] for k in META_FIELDS]
                               + ["%g" % rec[k] for k in NUM_FIELDS]
                               + [rec["seite"], rec["umfang_bezug"], "%g" % h]
                               + ["%g" % v for v in t] + ["%g" % c])
        return n

    def import_csv(self, path):
        """Importiert eine mit export_csv erzeugte Datei. Gibt die neuen IDs zurück."""
        with open(path, newline="", encoding="utf-8-sig") as f:
            text = f.read()
        delim = ";" if text.count(";") >= text.count(",") else ","
        reader = csv.DictReader(text.splitlines(), delimiter=delim)
        missing = [c for c in CSV_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError("Spalten fehlen: " + ", ".join(missing))
        groups = {}
        for row in reader:
            key = row["liner_nr"]
            rec = groups.get(key)
            if rec is None:
                rec = empty_record()
                for k in META_FIELDS:
                    rec[k] = row[k]
                for k in NUM_FIELDS:
                    rec[k] = _num(row[k])
                rec["seite"] = row["seite"] if row["seite"] in ("RIGHT", "LEFT") else "RIGHT"
                rec["umfang_bezug"] = (row["umfang_bezug"]
                                       if row["umfang_bezug"] in ("INNER", "OUTER") else "INNER")
                groups[key] = rec
            rec["messungen"].append((
                _num(row["hoehe_cm"]),
                tuple(_num(row[k]) for k in ("anterior_mm", "medial_mm",
                                             "posterior_mm", "lateral_mm")),
                _num(row["umfang_cm"])))
        return [self.save(rec) for rec in groups.values()]


def _natural_key(s):
    """Sortiert '8' vor '10' und Text sinnvoll ('S' < 'M' bleibt alphabetisch)."""
    s = str(s).strip()
    try:
        return (0, float(s.replace(",", ".")), "")
    except ValueError:
        return (1, 0.0, s.lower())
