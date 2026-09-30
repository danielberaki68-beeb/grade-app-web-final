from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify
import sqlite3
import json
import io
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

APP_NAME = "Grade App"
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "grade_app.db"

app = Flask(__name__)
app.secret_key = "change-this-secret-key"

DEFAULT_GRADES = [
    (90, "A+", "Excellent"),
    (80, "A", "Very Good"),
    (70, "B", "Good"),
    (60, "C", "Satisfactory"),
    (50, "D", "Pass"),
    (0, "F", "Fail"),
]

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY CHECK (id=1),
        school_name TEXT DEFAULT 'My School',
        address TEXT DEFAULT '',
        phone TEXT DEFAULT '',
        email TEXT DEFAULT '',
        teacher_name TEXT DEFAULT '',
        principal_name TEXT DEFAULT '',
        pass_mark REAL DEFAULT 50
    );

    CREATE TABLE IF NOT EXISTS grades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        min_mark REAL NOT NULL,
        label TEXT NOT NULL,
        description TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        class_name TEXT DEFAULT '',
        academic_year TEXT DEFAULT '',
        term TEXT DEFAULT 'Term 1',
        subjects_json TEXT DEFAULT '[]',
        average REAL DEFAULT 0,
        overall_grade TEXT DEFAULT ''
    );
    """)
    if con.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0:
        con.execute("""INSERT INTO settings
            (id, school_name, pass_mark) VALUES (1, 'My School', 50)""")
    if con.execute("SELECT COUNT(*) FROM grades").fetchone()[0] == 0:
        con.executemany(
            "INSERT INTO grades (min_mark,label,description) VALUES (?,?,?)",
            DEFAULT_GRADES
        )
    con.commit()
    con.close()

def get_settings():
    return dict(db().execute("SELECT * FROM settings WHERE id=1").fetchone())

def get_grades():
    return [dict(r) for r in db().execute(
        "SELECT * FROM grades ORDER BY min_mark DESC"
    ).fetchall()]

def grade_for(mark):
    for g in get_grades():
        if mark >= g["min_mark"]:
            return g["label"]
    return get_grades()[-1]["label"]

def parse_subjects(raw):
    subjects = []
    for row in raw:
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        try:
            mark = float(row.get("mark", 0))
        except (TypeError, ValueError):
            mark = 0
        mark = max(0, min(100, mark))
        subjects.append({"name": name, "mark": mark, "grade": grade_for(mark)})
    return subjects

@app.context_processor
def inject_globals():
    return {"APP_NAME": APP_NAME, "settings": get_settings()}

@app.route("/")
def home():
    con = db()
    students = con.execute("SELECT * FROM students ORDER BY id DESC").fetchall()
    avg = con.execute("SELECT AVG(average) FROM students").fetchone()[0] or 0
    con.close()
    return render_template("index.html", students=students, average=round(avg, 2))

@app.route("/students")
def students():
    q = request.args.get("q", "").strip()
    con = db()
    if q:
        rows = con.execute(
            "SELECT * FROM students WHERE name LIKE ? OR student_id LIKE ? ORDER BY id DESC",
            (f"%{q}%", f"%{q}%")
        ).fetchall()
    else:
        rows = con.execute("SELECT * FROM students ORDER BY id DESC").fetchall()
    con.close()
    return render_template("students.html", students=rows, q=q)

@app.route("/student/new", methods=["GET", "POST"])
def new_student():
    if request.method == "POST":
        student_id = request.form.get("student_id", "").strip()
        name = request.form.get("name", "").strip()
        class_name = request.form.get("class_name", "").strip()
        academic_year = request.form.get("academic_year", "").strip()
        term = request.form.get("term", "Term 1")
        if not student_id or not name:
            flash("Student ID and name are required.", "error")
            return redirect(url_for("new_student"))

        names = request.form.getlist("subject_name")
        marks = request.form.getlist("subject_mark")
        subjects = parse_subjects(
            [{"name": n, "mark": m} for n, m in zip(names, marks)]
        )
        average = round(sum(s["mark"] for s in subjects) / len(subjects), 2) if subjects else 0
        overall = grade_for(average)

        con = db()
        try:
            con.execute("""INSERT INTO students
                (student_id,name,class_name,academic_year,term,subjects_json,average,overall_grade)
                VALUES (?,?,?,?,?,?,?,?)""",
                (student_id, name, class_name, academic_year, term,
                 json.dumps(subjects), average, overall))
            con.commit()
            flash("Student saved successfully.", "success")
        except sqlite3.IntegrityError:
            flash("That Student ID already exists.", "error")
        finally:
            con.close()
        return redirect(url_for("students"))
    return render_template("student_form.html", student=None)

@app.route("/student/<int:student_pk>/edit", methods=["GET", "POST"])
def edit_student(student_pk):
    con = db()
    row = con.execute("SELECT * FROM students WHERE id=?", (student_pk,)).fetchone()
    if not row:
        con.close()
        return "Student not found", 404

    if request.method == "POST":
        names = request.form.getlist("subject_name")
        marks = request.form.getlist("subject_mark")
        subjects = parse_subjects([{"name": n, "mark": m} for n, m in zip(names, marks)])
        average = round(sum(s["mark"] for s in subjects) / len(subjects), 2) if subjects else 0
        overall = grade_for(average)
        con.execute("""UPDATE students SET student_id=?,name=?,class_name=?,
            academic_year=?,term=?,subjects_json=?,average=?,overall_grade=? WHERE id=?""",
            (request.form.get("student_id","").strip(),
             request.form.get("name","").strip(),
             request.form.get("class_name","").strip(),
             request.form.get("academic_year","").strip(),
             request.form.get("term","Term 1"),
             json.dumps(subjects), average, overall, student_pk))
        con.commit()
        con.close()
        flash("Student updated successfully.", "success")
        return redirect(url_for("students"))

    student = dict(row)
    student["subjects"] = json.loads(student["subjects_json"] or "[]")
    con.close()
    return render_template("student_form.html", student=student)

@app.post("/student/<int:student_pk>/delete")
def delete_student(student_pk):
    con = db()
    con.execute("DELETE FROM students WHERE id=?", (student_pk,))
    con.commit()
    con.close()
    flash("Student deleted.", "success")
    return redirect(url_for("students"))

@app.route("/grades", methods=["GET", "POST"])
def grades():
    if request.method == "POST":
        mins = request.form.getlist("min_mark")
        labels = request.form.getlist("label")
        descriptions = request.form.getlist("description")
        parsed = []
        seen = set()
        try:
            for mn, label, desc in zip(mins, labels, descriptions):
                label = label.strip()
                value = float(mn)
                if not label or not 0 <= value <= 100:
                    raise ValueError
                if label.lower() in seen or value in [x[0] for x in parsed]:
                    raise ValueError
                seen.add(label.lower())
                parsed.append((value, label, desc.strip()))
            if not parsed:
                raise ValueError
            parsed.sort(reverse=True)
            con = db()
            con.execute("DELETE FROM grades")
            con.executemany(
                "INSERT INTO grades (min_mark,label,description) VALUES (?,?,?)",
                parsed
            )
            con.commit()
            con.close()
            flash("Custom grading system saved.", "success")
        except ValueError:
            flash("Check grade labels and limits. Limits must be unique and between 0 and 100.", "error")
        return redirect(url_for("grades"))
    return render_template("grades.html", grades=get_grades())

@app.post("/grades/reset")
def reset_grades():
    con = db()
    con.execute("DELETE FROM grades")
    con.executemany(
        "INSERT INTO grades (min_mark,label,description) VALUES (?,?,?)",
        DEFAULT_GRADES
    )
    con.commit()
    con.close()
    flash("Default grading system restored.", "success")
    return redirect(url_for("grades"))

@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    if request.method == "POST":
        con = db()
        con.execute("""UPDATE settings SET school_name=?,address=?,phone=?,email=?,
            teacher_name=?,principal_name=?,pass_mark=? WHERE id=1""",
            (request.form.get("school_name","").strip(),
             request.form.get("address","").strip(),
             request.form.get("phone","").strip(),
             request.form.get("email","").strip(),
             request.form.get("teacher_name","").strip(),
             request.form.get("principal_name","").strip(),
             float(request.form.get("pass_mark", 50) or 50)))
        con.commit()
        con.close()
        flash("School settings saved.", "success")
        return redirect(url_for("settings_page"))
    return render_template("settings.html", school=get_settings())

@app.route("/report/<int:student_pk>")
def report(student_pk):
    con = db()
    row = con.execute("SELECT * FROM students WHERE id=?", (student_pk,)).fetchone()
    con.close()
    if not row:
        return "Student not found", 404
    student = dict(row)
    student["subjects"] = json.loads(student["subjects_json"] or "[]")
    return render_template("report.html", student=student)

@app.route("/report/<int:student_pk>/pdf")
def report_pdf(student_pk):
    con = db()
    row = con.execute("SELECT * FROM students WHERE id=?", (student_pk,)).fetchone()
    con.close()
    if not row:
        return "Student not found", 404
    student = dict(row)
    student["subjects"] = json.loads(student["subjects_json"] or "[]")
    school = get_settings()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(school["school_name"], styles["Title"]),
        Paragraph("Student Grade Report", styles["Heading2"]),
        Spacer(1, 12),
        Paragraph(f"Student: {student['name']}", styles["Normal"]),
        Paragraph(f"Student ID: {student['student_id']}", styles["Normal"]),
        Paragraph(f"Class: {student['class_name']}", styles["Normal"]),
        Paragraph(f"Academic Year: {student['academic_year']}", styles["Normal"]),
        Paragraph(f"Term: {student['term']}", styles["Normal"]),
        Spacer(1, 12),
    ]
    data = [["Subject", "Mark", "Grade"]]
    for s in student["subjects"]:
        data.append([s["name"], f"{s['mark']:.2f}", s["grade"]])
    data.append(["Average", f"{student['average']:.2f}", student["overall_grade"]])
    table = Table(data, colWidths=[260, 100, 100])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1d4ed8")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("PADDING", (0,0), (-1,-1), 7),
    ]))
    story.append(table)
    doc.build(story)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f"{student['student_id']}_report.pdf",
                     mimetype="application/pdf")

@app.route("/api/ai", methods=["POST"])
def ai_placeholder():
    return jsonify({
        "success": False,
        "message": "AI is not connected yet. We will connect the remote AI service after the web app is working."
    }), 503

if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="127.0.0.1", port=5000)
else:
    init_db()
