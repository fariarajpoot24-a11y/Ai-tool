"""PDF extraction, OpenRouter AI analysis, and a basic on-server fallback analysis."""
import io
import json
import os
import re
from collections import Counter
from datetime import date

import requests
from pypdf import PdfReader

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_CV_CHARS = 9000
MAX_JD_CHARS = 6000

SYSTEM_PROMPT = """You are an experienced recruiter and CV coach.
Compare the CV with the job description and respond with ONE JSON object and nothing else.
No markdown, no code fences, no commentary.

Schema:
{
  "match_score": integer 0-100,
  "summary": "2-3 sentences on overall fit",
  "matched_skills": ["skills or requirements clearly present in the CV"],
  "missing_skills": ["skills or requirements from the job description not evidenced in the CV"],
  "experience_gap": "1-3 sentences comparing the CV's experience level with what the job asks for",
  "improvement_tips": ["concrete, specific edits to the CV, most useful first"],
  "suggested_keywords": ["terms from the job description worth adding if truthful"]
}

Rules:
- Base everything ONLY on the CV and job description provided. Never invent experience.
- Tips must be concrete, e.g. "Add a bullet quantifying the impact of your Flask project",
  not generic advice like "improve your CV".
- Keep each list to at most 8 items."""

STOPWORDS = set("""a about above after all also an and any are as at be because been before being by can could
do does for from had has have he her his how i if in into is it its may more most must no not of on or our
other out over she should so some such than that the their them then there these they this those to under
up us use used using very was we were what when where which who will with would you your experience work
working ability skills skill strong team role job candidate required requirements responsibilities
including etc years year plus knowledge understanding good great excellent looking join company""".split())


class AnalysisError(Exception):
    """Carries a message that is safe to show to the user."""


# --------------------------------------------------------------------------- PDF
def extract_pdf_text(file_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        if reader.is_encrypted:
            raise AnalysisError("This PDF is password-protected. Paste the CV text instead.")
        pages = [(page.extract_text() or "") for page in reader.pages[:12]]
    except AnalysisError:
        raise
    except Exception:
        raise AnalysisError("Could not read this PDF. Paste the CV text instead.")
    text = "\n".join(pages).strip()
    if not text:
        raise AnalysisError(
            "No text found in this PDF. It may be a scanned image. Paste the CV text instead."
        )
    return text


# --------------------------------------------------------------------------- scoring helpers
def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z][a-z+#.\-]+", text.lower())
    words = [w.rstrip(".-") for w in words]
    return [w for w in words if len(w) >= 2 and w not in STOPWORDS]


def keyword_score(cv: str, jd: str) -> int:
    """Share (0-100) of the job description's 30 most frequent keywords found in the CV."""
    counts = Counter(_tokens(jd))
    top = [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:30]]
    if not top:
        return 0
    cv_words = set(_tokens(cv))
    return round(100 * sum(1 for w in top if w in cv_words) / len(top))


def _str_list(value, limit=8) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:limit]


def _normalize(data: dict) -> dict:
    try:
        score = int(round(float(data.get("match_score", 0))))
    except (TypeError, ValueError):
        score = 0
    return {
        "match_score": max(0, min(100, score)),
        "summary": str(data.get("summary", "")).strip(),
        "matched_skills": _str_list(data.get("matched_skills")),
        "missing_skills": _str_list(data.get("missing_skills")),
        "experience_gap": str(data.get("experience_gap", "")).strip(),
        "improvement_tips": _str_list(data.get("improvement_tips")),
        "suggested_keywords": _str_list(data.get("suggested_keywords")),
    }


def _parse_json(raw: str) -> dict:
    text = re.sub(r"```(?:json)?", "", raw).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


# --------------------------------------------------------------------------- AI analysis
def ai_configured() -> bool:
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    return bool(key) and key != "your_key_here"


def _models() -> list[str]:
    primary = os.getenv("MODEL_NAME", "").strip()
    fallbacks = [m.strip() for m in os.getenv("FALLBACK_MODELS", "").split(",") if m.strip()]
    return list(dict.fromkeys(m for m in [primary, *fallbacks] if m))


def _call_model(model: str, cv: str, jd: str, api_key: str) -> str:
    resp = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f'CV:\n"""\n{cv}\n"""\n\nJOB DESCRIPTION:\n"""\n{jd}\n"""'},
            ],
        },
        timeout=90,
    )
    if resp.status_code in (401, 403):
        raise AnalysisError("OpenRouter rejected the API key. Check OPENROUTER_API_KEY in .env.")
    resp.raise_for_status()
    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError("Unexpected response shape")
    if not content:
        raise ValueError("Empty response")
    return content


def _try_model(model: str, cv: str, jd: str, api_key: str):
    """Return (result, error). Retries once if the model returns malformed JSON."""
    error = "unknown error"
    for _ in range(2):
        try:
            return _normalize(_parse_json(_call_model(model, cv, jd, api_key))), None
        except requests.HTTPError as exc:
            return None, f"{model}: HTTP {exc.response.status_code}"
        except requests.RequestException:
            return None, f"{model}: network error"
        except (ValueError, AttributeError):  # includes json.JSONDecodeError
            error = f"{model}: invalid JSON"
    return None, error


def ai_analyze(cv: str, jd: str) -> dict:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    models = _models()
    if not models:
        raise AnalysisError("MODEL_NAME is not set in .env.")
    cv, jd = cv[:MAX_CV_CHARS], jd[:MAX_JD_CHARS]
    last_error = "unknown error"
    for model in models:
        result, error = _try_model(model, cv, jd, api_key)
        if result:
            result["keyword_score"] = keyword_score(cv, jd)
            result["analysis_type"] = "AI analysis"
            result["model"] = model
            return result
        last_error = error
    raise AnalysisError(
        f"All AI models failed ({last_error}). Free models are often rate-limited."
    )


# --------------------------------------------------------------------------- basic analysis
SKILLS = [
    ("Python", ["python"]), ("Java", ["java"]), ("JavaScript", ["javascript"]), ("TypeScript", ["typescript"]),
    ("C++", ["c++"]), ("C#", ["c#"]), ("Go", ["golang"]), ("PHP", ["php"]), ("Ruby", ["ruby"]),
    ("Swift", ["swift"]), ("Kotlin", ["kotlin"]), ("SQL", ["sql"]), ("HTML", ["html", "html5"]), ("CSS", ["css", "css3"]),
    ("React", ["react", "react.js", "reactjs"]), ("Angular", ["angular"]), ("Vue", ["vue", "vue.js"]),
    ("Node.js", ["node.js", "nodejs"]), ("Django", ["django"]), ("Flask", ["flask"]),
    ("Spring Boot", ["spring boot", "spring"]), (".NET", [".net", "dotnet", "asp.net"]),
    ("REST APIs", ["rest api", "rest apis", "restful", "rest"]), ("GraphQL", ["graphql"]),
    ("Git", ["git", "github", "gitlab"]), ("Docker", ["docker"]), ("Kubernetes", ["kubernetes", "k8s"]),
    ("AWS", ["aws", "amazon web services"]), ("Azure", ["azure"]), ("Google Cloud", ["gcp", "google cloud"]),
    ("Linux", ["linux"]), ("CI/CD", ["ci/cd", "jenkins", "github actions"]),
    ("PostgreSQL", ["postgresql", "postgres"]), ("MySQL", ["mysql"]), ("MongoDB", ["mongodb"]), ("Redis", ["redis"]),
    ("Microservices", ["microservices", "microservice"]),
    ("Machine Learning", ["machine learning"]), ("Deep Learning", ["deep learning"]),
    ("NLP", ["nlp", "natural language processing"]), ("LLMs", ["llm", "llms", "large language model"]),
    ("Data Analysis", ["data analysis", "data analytics"]), ("Pandas", ["pandas"]), ("NumPy", ["numpy"]),
    ("TensorFlow", ["tensorflow"]), ("PyTorch", ["pytorch"]), ("Power BI", ["power bi", "powerbi"]),
    ("Tableau", ["tableau"]), ("Excel", ["microsoft excel", "ms excel", "advanced excel", "excel spreadsheets"]),
    ("Agile", ["agile", "scrum", "kanban"]), ("Jira", ["jira"]),
    ("Software Testing", ["unit testing", "unit tests", "junit", "pytest", "test automation", "selenium"]),
    ("Cybersecurity", ["cybersecurity", "cyber security", "information security"]),
    ("Figma", ["figma"]), ("UI/UX Design", ["ui/ux", "ux design", "user experience"]),
    ("Project Management", ["project management", "pmp"]), ("Leadership", ["leadership", "led a team", "team lead"]),
    ("Teamwork", ["teamwork", "collaboration", "collaborative"]), ("Communication", ["communication", "communicator"]),
    ("Problem Solving", ["problem solving", "problem-solving"]),
    ("Stakeholder Management", ["stakeholder", "stakeholders"]),
    ("Presentation Skills", ["presentation", "presentations", "public speaking"]),
    ("Negotiation", ["negotiation", "negotiating"]), ("Research", ["research"]),
    ("Customer Service", ["customer service", "customer support"]), ("Sales", ["sales"]),
    ("Marketing", ["marketing"]), ("SEO", ["seo"]), ("Content Writing", ["content writing", "copywriting"]),
    ("Social Media", ["social media"]), ("Accounting", ["accounting", "bookkeeping"]),
    ("Financial Analysis", ["financial analysis", "financial modelling", "financial modeling"]),
    ("Budgeting", ["budgeting", "budget management"]),
    ("Recruitment", ["recruitment", "recruiting", "talent acquisition"]),
    ("Salesforce", ["salesforce"]), ("SAP", ["sap"]),
]

_SKILL_RES = [
    (name, re.compile(r"(^|[^a-z0-9+#])(" + "|".join(re.escape(a) for a in aliases) + r")(?![a-z0-9+#])", re.I))
    for name, aliases in SKILLS
]
_YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years?|yrs?)\b", re.I)
_RANGE_RE = re.compile(
    r"\b((?:19|20)\d{2})\s*(?:-|\u2013|\u2014|to)\s*((?:19|20)\d{2}|present|current|now)\b", re.I
)


def _find_skills(text: str) -> list[str]:
    return [name for name, rx in _SKILL_RES if rx.search(text)]


def _required_years(jd: str) -> int:
    nums = [int(m.group(1)) for m in _YEARS_RE.finditer(jd)]
    return max([n for n in nums if n <= 30], default=0)


def _cv_years(cv: str) -> int:
    claimed = max([int(m.group(1)) for m in _YEARS_RE.finditer(cv) if int(m.group(1)) <= 40], default=0)
    this_year = date.today().year
    start, end = 10_000, 0
    for m in _RANGE_RE.finditer(cv):
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2)[0].isdigit() else this_year
        if b >= a:
            start, end = min(start, a), max(end, b)
    span = end - start if end > start else 0
    return max(claimed, span)


def basic_analyze(cv: str, jd: str) -> dict:
    jd_skills = _find_skills(jd)
    cv_skills = set(_find_skills(cv))
    matched = [s for s in jd_skills if s in cv_skills]
    missing = [s for s in jd_skills if s not in cv_skills]
    kw = keyword_score(cv, jd)

    coverage = round(100 * len(matched) / len(jd_skills)) if jd_skills else kw
    score = round(0.7 * coverage + 0.3 * kw) if jd_skills else kw
    score = max(0, min(100, score))

    need, have = _required_years(jd), _cv_years(cv)
    if not need:
        gap = "The job description does not state a number of years of experience."
    elif not have:
        gap = (f"The job asks for about {need} years of experience. Your CV does not state your years "
               "of experience clearly. Add dates for each role.")
    elif have >= need:
        gap = f"The job asks for about {need} years and your CV shows roughly {have}. That meets the stated level."
    else:
        gap = (f"The job asks for about {need} years and your CV shows roughly {have}. Highlight depth, "
               "ownership and results in your most relevant roles to offset the difference.")

    tips = []
    if missing:
        tips.append(f"For the gaps that matter most ({', '.join(missing[:4])}), add a bullet showing where you "
                    "used each one and the result, but only if it is true.")
        tips.append("For any gap you cannot honestly claim, list a related skill you do have, or a course or "
                    "project you are working on.")
    tips += [
        "Mirror the job's wording where it is accurate, so a recruiter scanning for exact terms finds them.",
        "Start each bullet with a strong verb and add a number where you can: size, time saved, users, revenue or error rate.",
        "Put a two or three line summary at the top that names the role you are applying for and your strongest matching skills.",
    ]

    if not jd_skills:
        summary = ("No common skill names were found in the job description, so this score rests on keyword "
                   "overlap only. Paste the full requirements section for a better result.")
    elif score >= 70:
        summary = "Your CV covers most of the skills this job names. Focus on proof: numbers and outcomes next to those skills."
    elif score >= 45:
        summary = "Your CV covers part of what this job asks for. Closing the gaps listed below should raise your match."
    else:
        summary = ("Your CV shows few of the skills this job names. Check whether the role fits, and add any "
                   "relevant experience you have left out.")

    return {
        "match_score": score,
        "summary": summary,
        "matched_skills": matched,
        "missing_skills": missing,
        "experience_gap": gap,
        "improvement_tips": tips,
        "suggested_keywords": missing[:8],
        "keyword_score": kw,
        "analysis_type": "Basic analysis (no AI)",
        "model": "",
    }
