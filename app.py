"""SpeedLearning — transforma um PDF em resumos, quizzes e um teste final."""

from __future__ import annotations

import os
import secrets

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

from speedlearning.generator import claude_available, generate_course
from speedlearning.pdf_reader import PdfError, extract_text, split_sections
from speedlearning.store import CourseStore

MAX_UPLOAD_MB = 50


def create_app(data_dir: str | None = None, prefer_claude: bool = True) -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
    store = CourseStore(data_dir or os.path.join(app.instance_path, "courses"))

    def load(course_id: str) -> dict:
        course = store.get(course_id)
        if course is None:
            abort(404)
        return course

    def progress(course_id: str) -> dict:
        return session.get("progress", {}).get(course_id, {})

    def save_progress(course_id: str, key: str, correct: int, total: int) -> None:
        all_progress = session.get("progress", {})
        all_progress.setdefault(course_id, {})[key] = [correct, total]
        session["progress"] = all_progress

    @app.get("/")
    def index():
        return render_template("index.html", claude=claude_available())

    @app.post("/upload")
    def upload():
        file = request.files.get("pdf")
        if not file or not file.filename:
            flash("Escolhe um ficheiro PDF.")
            return redirect(url_for("index"))
        if not file.filename.lower().endswith(".pdf"):
            flash("O ficheiro tem de ser um PDF.")
            return redirect(url_for("index"))

        try:
            title, pages = extract_text(file.read())
        except PdfError as exc:
            flash(str(exc))
            return redirect(url_for("index"))

        sections = split_sections(pages)
        use_claude = prefer_claude and request.form.get("mode", "auto") != "local"
        course = generate_course(title, sections, prefer_claude=use_claude)
        course["filename"] = file.filename
        course_id = store.save(course)
        return redirect(url_for("overview", course_id=course_id))

    @app.get("/curso/<course_id>")
    def overview(course_id):
        course = load(course_id)
        return render_template(
            "overview.html", course=course, course_id=course_id, progress=progress(course_id)
        )

    @app.route("/curso/<course_id>/secao/<int:index>", methods=["GET", "POST"])
    def section(course_id, index):
        course = load(course_id)
        if not 0 <= index < len(course["sections"]):
            abort(404)
        sec = course["sections"][index]
        result = None
        if request.method == "POST":
            result = grade(sec["questions"], request.form)
            save_progress(course_id, str(index), result["correct"], result["total"])
        return render_template(
            "section.html",
            course=course,
            course_id=course_id,
            index=index,
            section=sec,
            result=result,
        )

    @app.route("/curso/<course_id>/teste-final", methods=["GET", "POST"])
    def final_test(course_id):
        course = load(course_id)
        result = None
        if request.method == "POST":
            result = grade(course["final_test"], request.form)
            save_progress(course_id, "final", result["correct"], result["total"])
        return render_template(
            "final.html", course=course, course_id=course_id, result=result
        )

    @app.errorhandler(413)
    def too_large(_):
        flash(f"O ficheiro excede o limite de {MAX_UPLOAD_MB} MB.")
        return redirect(url_for("index"))

    return app


def grade(questions: list[dict], form) -> dict:
    """Corrige as respostas submetidas (campos q0, q1, ...)."""
    answers = []
    correct = 0
    for i, q in enumerate(questions):
        raw = form.get(f"q{i}")
        chosen = int(raw) if raw is not None and raw.isdigit() else None
        ok = chosen == q["answer"]
        correct += ok
        answers.append({"chosen": chosen, "ok": ok})
    total = len(questions)
    return {
        "answers": answers,
        "correct": correct,
        "total": total,
        "percent": round(100 * correct / total) if total else 0,
    }


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
