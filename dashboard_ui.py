"""
The two pages people look at: the nurse dashboard and the public home page.

They are plain functions that turn data into HTML, so they can be tested without
a database or a web server. Everything that comes from a patient or from the AI
is HTML-escaped before it goes into a page.

No external scripts or fonts are loaded: one self-contained dark theme.
"""
import html
import re
from datetime import datetime, timezone
from typing import Optional

PAGE_CSS = """
:root{color-scheme:dark;--bg:#0b1020;--panel:#121a33;--panel2:#18224a;--text:#e8ecf8;--muted:#9aa6c7;
--red:#ff6b6b;--amber:#f5b942;--teal:#3dd6c6;--line:#243056}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--teal)}
.wrap{max-width:1100px;margin:0 auto;padding:1.5rem 1rem 3rem}
h1{font-size:1.7rem;margin:.2rem 0}
h2{font-size:1.15rem;margin:0 0 .8rem}
.muted{color:var(--muted)}
.top{display:flex;flex-wrap:wrap;gap:1rem;align-items:center;justify-content:space-between;margin-bottom:1.2rem}
.btn{display:inline-block;border:1px solid var(--line);background:var(--panel2);color:var(--text);padding:.45rem .9rem;
border-radius:8px;text-decoration:none;font:inherit;cursor:pointer}
.btn:hover,.btn:focus-visible{border-color:var(--teal);outline:none}
.btn.primary{background:var(--teal);color:#06231f;border-color:var(--teal);font-weight:600}
.btn.disabled{opacity:.45;cursor:not-allowed}
.grid{display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
.col{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:1rem}
.col.urgent{border-top:3px solid var(--red)}.col.queue{border-top:3px solid var(--amber)}
.card{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:.8rem .9rem;margin:.7rem 0}
.card.urgent{border-left:4px solid var(--red)}.card.queue{border-left:4px solid var(--amber)}
.row{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;justify-content:space-between}
.badge{display:inline-block;font-size:.72rem;font-weight:700;letter-spacing:.04em;padding:.1rem .5rem;border-radius:999px}
.badge.red{background:rgba(255,107,107,.18);color:var(--red)}.badge.amber{background:rgba(245,185,66,.18);color:var(--amber)}
.badge.grey{background:rgba(154,166,199,.18);color:var(--muted)}
.msg{margin:.5rem 0;white-space:pre-wrap;word-break:break-word}
.reason{font-size:.88rem;color:var(--muted)}
details{margin:.4rem 0;font-size:.9rem}summary{cursor:pointer;color:var(--muted)}
.actions{display:flex;gap:.5rem;margin-top:.6rem}
.actions form{margin:0}
.empty{color:var(--muted);font-style:italic}
table{width:100%;border-collapse:collapse;font-size:.9rem}
td,th{padding:.45rem .5rem;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600}
footer{margin-top:2rem;color:var(--muted);font-size:.85rem}
.hero{padding:2.5rem 0 1rem}.hero h1{font-size:2.2rem}.lead{font-size:1.15rem;color:var(--muted);max-width:44rem}
.cards3{display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));margin:1.5rem 0}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:1rem}
.tile h3{margin:.1rem 0 .4rem;font-size:1.02rem}
ul.tidy{padding-left:1.2rem}ul.tidy li{margin:.3rem 0}
"""


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _page(title: str, body: str, refresh_seconds: Optional[int] = None) -> str:
    refresh = f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">' if refresh_seconds else ""
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="dark">'
        f"{refresh}<title>{_esc(title)}</title><style>{PAGE_CSS}</style></head>"
        f'<body><div class="wrap">{body}</div></body></html>'
    )


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _shown_patient(patient_id, mask: bool) -> str:
    text = str(patient_id or "unknown")
    digits = _digits(text)
    if mask and len(digits) >= 7:
        return f"***{digits[-4:]}"
    return text


def _tel_link(patient_id) -> Optional[str]:
    """A phone link needs the + sign, or the number may not dial correctly."""
    digits = _digits(patient_id)
    return f"tel:+{digits}" if len(digits) >= 7 else None


def _ago(timestamp) -> tuple[str, str]:
    """('5 min ago', '2026-11-05 11:36 UTC'). Database times are stored in UTC."""
    if not isinstance(timestamp, datetime):
        return "", ""
    ts = timestamp.astimezone(timezone.utc).replace(tzinfo=None) if timestamp.tzinfo else timestamp
    seconds = (datetime.now(timezone.utc).replace(tzinfo=None) - ts).total_seconds()
    if seconds < 60:
        label = "just now"
    elif seconds < 3600:
        label = f"{int(seconds // 60)} min ago"
    elif seconds < 86400:
        label = f"{int(seconds // 3600)} h ago"
    else:
        label = f"{int(seconds // 86400)} d ago"
    return label, ts.strftime("%Y-%m-%d %H:%M UTC")


def is_urgent(row: dict) -> bool:
    """Urgent = marked urgent, OR judged high risk (older rows were saved without a priority)."""
    return str(row.get("priority") or "").lower() == "urgent" or str(row.get("risk_level") or "").lower() == "high"


# ---------------------------------------------------------------------------
# nurse dashboard
# ---------------------------------------------------------------------------

def _case_card(row: dict, kind: str, mask: bool) -> str:
    ago, exact = _ago(row.get("timestamp"))
    badge = (
        '<span class="badge red">URGENT</span>' if kind == "urgent"
        else '<span class="badge amber">NEEDS A LOOK</span>'
    )
    risk = str(row.get("risk_level") or "").strip()
    risk_html = f' <span class="badge grey">risk: {_esc(risk)}</span>' if risk else ""
    tel = None if mask else _tel_link(row.get("patient_id"))
    call = (
        f'<a class="btn primary" href="{_esc(tel)}">Call patient</a>' if tel
        else '<span class="btn disabled" title="Hidden in privacy mode">Call patient</span>'
    )
    resolve = ""
    if row.get("id") is not None:
        confirm = (
            ' onsubmit="return confirm(\'Mark this urgent case as resolved?\')"' if kind == "urgent" else ""
        )
        resolve = (
            f'<form method="post" action="/dashboard/resolve/{int(row["id"])}"{confirm}>'
            '<button class="btn" type="submit">Resolve</button></form>'
        )
    reply = (
        f"<details><summary>What the patient was told</summary><div class=\"msg\">{_esc(row.get('answer'))}</div></details>"
        if row.get("answer") else ""
    )
    reason = f'<div class="reason">Why: {_esc(row.get("reasoning"))}</div>' if row.get("reasoning") else ""
    return (
        f'<div class="card {kind}">'
        f'<div class="row"><div>{badge}{risk_html}</div>'
        f'<div class="muted" title="{_esc(exact)}">{_esc(ago)}</div></div>'
        f'<div class="muted">Patient: {_esc(_shown_patient(row.get("patient_id"), mask))}</div>'
        f'<div class="msg">{_esc(row.get("content"))}</div>'
        f"{reason}{reply}"
        f'<div class="actions">{call}{resolve}</div></div>'
    )


def _recent_table(rows: list, mask: bool, limit: int = 25) -> str:
    if not rows:
        return '<p class="empty">No messages yet.</p>'
    body = []
    for r in rows[:limit]:
        ago, exact = _ago(r.get("timestamp"))
        flag = '<span class="badge red">alerted</span>' if r.get("escalated") in (1, True) else ""
        text = str(r.get("content") or "")
        short = text if len(text) <= 140 else text[:137] + "..."
        body.append(
            f'<tr><td title="{_esc(exact)}">{_esc(ago)}</td>'
            f'<td>{_esc(_shown_patient(r.get("patient_id"), mask))}</td>'
            f'<td>{_esc(short)}</td><td>{_esc(r.get("intent"))}</td><td>{flag}</td></tr>'
        )
    return (
        "<table><thead><tr><th>When</th><th>Patient</th><th>Message</th><th>Type</th><th></th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def render_dashboard(escalations: list, all_messages: list, mask: bool = False, auto_refresh: bool = True) -> str:
    open_cases = [r for r in escalations if not r.get("resolved")]
    urgent = [r for r in open_cases if is_urgent(r)]
    queue = [r for r in open_cases if not is_urgent(r)]

    def column(rows, kind, title, empty):
        cards = "".join(_case_card(r, kind, mask) for r in rows) or f'<p class="empty">{_esc(empty)}</p>'
        return f'<section class="col {kind}"><h2>{_esc(title)} ({len(rows)})</h2>{cards}</section>'

    privacy = (
        '<a class="btn" href="/dashboard">Show phone numbers</a>' if mask
        else '<a class="btn" href="/dashboard?mask=1">Privacy mode</a>'
    )
    body = (
        '<div class="top"><div><h1>Nurse triage dashboard</h1>'
        '<div class="muted">Urgent cases first. The page refreshes by itself every 30 seconds.</div></div>'
        f'<div class="actions">{privacy}<a class="btn" href="/dashboard{"?mask=1" if mask else ""}">Refresh</a>'
        '<a class="btn primary" href="/intake">Register patient</a></div></div>'
        '<div class="grid">'
        f'{column(urgent, "urgent", "Urgent", "No urgent cases right now.")}'
        f'{column(queue, "queue", "Needs a callback or a look", "Nothing waiting.")}'
        "</div>"
        '<section class="col" style="margin-top:1rem"><h2>Recent messages</h2>'
        f"{_recent_table(all_messages, mask)}</section>"
        "<footer>Demo with made-up data. Not medical advice. "
        "Add <code>?mask=1</code> to hide phone numbers, for example in a screen recording.</footer>"
    )
    return _page("Nurse triage dashboard", body, 30 if auto_refresh else None)


# ---------------------------------------------------------------------------
# public home page
# ---------------------------------------------------------------------------

def render_home(github_url: Optional[str] = None) -> str:
    links = ['<a class="btn primary" href="/dashboard">Nurse dashboard (login)</a>']
    url = (github_url or "").strip()
    if url.startswith(("https://", "http://")):
        links.insert(0, f'<a class="btn primary" href="{_esc(url)}">Source code and demo video</a>')
    links.append('<a class="btn" href="/health">Service status</a>')
    body = (
        '<div class="hero"><span class="badge grey">VERSION 1 &middot; DEMO</span>'
        "<h1>Post-Discharge Assistant</h1>"
        '<p class="lead">A WhatsApp assistant that helps care teams stay in touch with patients '
        "after they leave hospital. It answers recovery questions from the patient's own discharge "
        "summary, and it alerts a nurse when a message looks urgent.</p>"
        f'<div class="actions">{"".join(links)}</div></div>'
        '<div class="cards3">'
        '<div class="tile"><h3>Answers from the patient\'s own document</h3>'
        '<p class="muted">Medicines, follow-up visit and warning signs, in plain language. '
        "Specific answers must be backed by an exact quote that the code checks.</p></div>"
        '<div class="tile"><h3>Spots urgent messages</h3>'
        '<p class="muted">Fast rules first, then an AI check. When it is unsure, it treats the message '
        "as high risk and alerts the care team.</p></div>"
        '<div class="tile"><h3>Fails safely</h3>'
        '<p class="muted">If something breaks, the message still reaches a nurse and the patient '
        "still gets a safe reply.</p></div></div>"
        '<section class="col"><h2>Built to be trusted</h2><ul class="tidy">'
        "<li>No patient messages or phone numbers in the logs</li>"
        "<li>Every webhook call is signature-checked, and duplicates are skipped</li>"
        "<li>The nurse dashboard is password protected</li>"
        "<li>Automated tests, including checks that deliberately try to break the protections</li>"
        "</ul></section>"
        '<footer>This is a demo that uses made-up data. It is not medical advice and has not been clinically validated. '
        "It runs on free hosting, so the first visit after a quiet period can take up to a minute.</footer>"
    )
    return _page("Post-Discharge Assistant", body)