"""ResumeFit: Flask server for the AI Resume Analyzer and Job Matcher."""
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

import analyzer  # noqa: E402  (loaded after .env so settings are available)
from analyzer import AnalysisError  # noqa: E402

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB upload limit

MIN_CV_CHARS = 100
MIN_JD_CHARS = 50


@app.after_request
def allow_local_pages(resp):
    """Lets the page work when opened from VS Code Live Server (port 5500) while Flask runs on 5000."""
    origin = request.headers.get("Origin", "")
    if origin.startswith(("http://127.0.0.1", "http://localhost")):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def status():
    """Lets the page show whether AI analysis is switched on."""
    return jsonify({"ai": analyzer.ai_configured()})


@app.post("/analyze")
def analyze_route():
    jd = (request.form.get("job_description") or "").strip()
    cv_text = (request.form.get("cv_text") or "").strip()
    cv_file = request.files.get("cv_file")

    try:
        if cv_file and cv_file.filename:
            if not cv_file.filename.lower().endswith(".pdf"):
                raise AnalysisError("Upload a PDF file, or paste the CV text.")
            cv_text = analyzer.extract_pdf_text(cv_file.read())

        if len(cv_text) < MIN_CV_CHARS:
            raise AnalysisError("Add your CV: upload a PDF or paste at least a few lines of text.")
        if len(jd) < MIN_JD_CHARS:
            raise AnalysisError("Paste the full job description (at least a few lines).")

        # Nothing is saved: the text only lives in memory for this request.
        notice = ""
        result = None
        if analyzer.ai_configured():
            try:
                result = analyzer.ai_analyze(cv_text, jd)
            except AnalysisError as exc:
                notice = f"{exc} Showing the basic analysis instead."
        if result is None:
            result = analyzer.basic_analyze(cv_text, jd)
        result["notice"] = notice
        return jsonify(result)

    except AnalysisError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        app.logger.exception("Unexpected error")
        return jsonify({"error": "Something went wrong on the server. Try again."}), 500


@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": "File is too large. The limit is 5 MB."}), 413


if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG") == "1",
    )
