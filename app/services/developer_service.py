"""Snippets, dev projects, work log and built-in (offline) help. Nothing here executes user code."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import DevActivity, DevProject, Snippet
from app.utils import validation as v
from app.utils.security import audit

PROJECT_STATUSES = {"idea", "active", "paused", "done", "archived"}


# ---- snippets -------------------------------------------------------------------------------
def save_snippet(session: Session, user_id: int, name: str, language: str, description: str, code: str) -> Snippet:
    name = v.clean(name, max_len=60, field="Name").lower().replace(" ", "-")
    code = v.clean(code, max_len=20000, field="Code")
    language = v.clean(language or "text", max_len=30, field="Language").lower()
    if get_snippet(session, user_id, name):
        raise ValueError(f"A snippet named '{name}' already exists. Delete it first or pick another name.")
    s = Snippet(user_id=user_id, name=name, language=language,
                description=v.clean(description, max_len=300, min_len=0), code=code)
    session.add(s)
    session.flush()
    audit(session, user_id, "snippet.save", name)
    return s


def get_snippet(session: Session, user_id: int, name: str) -> Snippet | None:
    return session.scalar(select(Snippet).where(Snippet.user_id == user_id, Snippet.name == name.strip().lower().replace(" ", "-")))


def list_snippets(session: Session, user_id: int) -> list[Snippet]:
    return list(session.scalars(select(Snippet).where(Snippet.user_id == user_id).order_by(Snippet.name)))


def delete_snippet(session: Session, user_id: int, snippet_id: int) -> None:
    s = session.scalar(select(Snippet).where(Snippet.id == snippet_id, Snippet.user_id == user_id))
    if s is None:
        raise LookupError("Snippet not found.")
    session.delete(s)
    audit(session, user_id, "snippet.delete", s.name)


# ---- projects -------------------------------------------------------------------------------
def create_project(session: Session, user_id: int, name: str, technologies: str = "", repo_url: str = "",
                   notes: str = "") -> DevProject:
    name = v.clean(name, max_len=80, field="Project name")
    if get_project(session, user_id, name):
        raise ValueError(f"Project '{name}' already exists.")
    p = DevProject(user_id=user_id, name=name, technologies=v.clean(technologies, max_len=200, min_len=0),
                   repo_url=v.url(repo_url) if repo_url else "", notes=v.clean(notes, max_len=2000, min_len=0))
    session.add(p)
    session.flush()
    audit(session, user_id, "devproject.create", name)
    return p


def get_project(session: Session, user_id: int, name: str) -> DevProject | None:
    return session.scalar(select(DevProject).where(DevProject.user_id == user_id,
                                                   DevProject.name.ilike(name.strip())))


def list_projects(session: Session, user_id: int) -> list[DevProject]:
    return list(session.scalars(select(DevProject).where(DevProject.user_id == user_id).order_by(DevProject.name)))


def set_status(session: Session, user_id: int, name: str, status: str) -> DevProject:
    status = status.strip().lower()
    if status not in PROJECT_STATUSES:
        raise ValueError("Status must be one of: " + ", ".join(sorted(PROJECT_STATUSES)))
    p = get_project(session, user_id, name)
    if p is None:
        raise LookupError(f"Project '{name}' not found.")
    p.status = status
    session.add(DevActivity(project_id=p.id, description=f"Status changed to {status}"))
    return p


def log_work(session: Session, user_id: int, project: str, description: str) -> DevActivity:
    p = get_project(session, user_id, project)
    if p is None:
        raise LookupError(f"Project '{project}' not found. Create it with /project {project}")
    a = DevActivity(project_id=p.id, description=v.clean(description, max_len=500, field="Description"))
    session.add(a)
    session.flush()
    return a


# ---- built-in help (works offline) ------------------------------------------------------------
HELP_TOPICS: dict[str, tuple[list[str], str]] = {
    "git": (["git", "commit", "branch", "merge", "rebase", "stash", "push", "pull", "clone"],
            "Git quick reference\n• git status / git diff — see changes\n• git add -p && git commit -m \"msg\"\n"
            "• git switch -c feature/x — new branch\n• git pull --rebase — update without merge commits\n"
            "• git stash / git stash pop — park work\n• git log --oneline --graph --all\n"
            "• Undo last commit (keep changes): git reset --soft HEAD~1\n• Never commit .env or tokens; use .gitignore."),
    "java": (["java", "jvm", "maven", "gradle", "spring", "nullpointer", "classpath"],
             "Java tips\n• Build: mvn clean package / gradle build\n• NullPointerException: find the null variable in the first stack-trace line "
             "of your own code; use Optional or null checks.\n• Prefer List<String> over raw types; use try-with-resources for streams.\n"
             "• ClassNotFoundException: check dependencies and classpath."),
    "python": (["python", "pip", "venv", "pytest", "traceback", "importerror", "modulenotfound", "indentation"],
               "Python tips\n• python -m venv .venv && source .venv/bin/activate (Windows: .venv\\Scripts\\Activate.ps1)\n"
               "• ModuleNotFoundError: activate the venv, then pip install -r requirements.txt\n"
               "• Read tracebacks bottom-up: the last line is the error, above it is where it happened.\n"
               "• IndentationError: don't mix tabs and spaces.\n• pytest -x -q runs tests, stops at first failure."),
    "javascript": (["javascript", "js", "node", "npm", "async", "promise", "undefined", "react"],
                   "JavaScript tips\n• Use const/let, never var.\n• 'undefined is not a function' usually means a wrong "
                   "import or a missing return.\n• async/await: always wrap awaits in try/catch.\n• npm ci installs exactly what the lockfile says.\n"
                   "• === not ==. Debug with console.log or the browser debugger."),
    "sql": (["sql", "select", "join", "query", "database", "sqlite", "index", "group by"],
            "SQL tips\n• SELECT cols FROM t WHERE cond ORDER BY col LIMIT n;\n• JOIN: INNER keeps matches only, LEFT keeps all left rows.\n"
            "• Always use parameterised queries (never string-concatenate user input).\n• Add indexes on columns used in WHERE/JOIN.\n"
            "• EXPLAIN QUERY PLAN (SQLite) shows how a query runs."),
    "workflow": (["workflow", "deploy", "release", "ci", "review", "pull request", "pr", "testing", "docker"],
                 "Dev workflow checklist\n1. Branch from main\n2. Small commits with clear messages\n3. Write/run tests\n"
                 "4. Open a pull request, review your own diff first\n5. Merge, tag a release, keep a changelog\n"
                 "• Docker: docker compose up -d --build; check logs with docker compose logs -f"),
}


def builtin_help(question: str) -> str:
    q = question.lower()
    scores = {k: sum(1 for kw in kws if kw in q) for k, (kws, _) in HELP_TOPICS.items()}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return ("I don't have a built-in topic for that. Built-in help covers: " + ", ".join(HELP_TOPICS) +
                ".\nTry e.g. /devhelp git rebase")
    return HELP_TOPICS[best][1]
