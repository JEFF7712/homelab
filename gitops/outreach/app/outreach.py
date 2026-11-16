#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["openpyxl>=3.1"]
# ///
"""Outreach pipeline: dedupe, render, queue as Gmail drafts, and send on schedule.

Gmail has no API for native scheduled send, so queued emails live as ordinary
Gmail drafts and `send-due` (run by a systemd user timer) sends each one over
SMTP once its time arrives. Edits made to the draft in Gmail are honored.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import datetime as dt
import email
import email.policy
import fcntl
import functools
import hashlib
import html
import imaplib
import json
import os
import re
import smtplib
import sys
import time
import urllib.request
from email.message import EmailMessage
from email.utils import (
    formataddr,
    formatdate,
    getaddresses,
    make_msgid,
    parsedate_to_datetime,
)
from pathlib import Path
from zoneinfo import ZoneInfo

import openpyxl
from openpyxl.formatting.formatting import ConditionalFormattingList
from openpyxl.worksheet.cell_range import CellRange, MultiCellRange

TZ = ZoneInfo("America/Chicago")
OUTREACH = Path(os.environ.get("OUTREACH_ROOT", Path(__file__).resolve().parent.parent))
REPO = OUTREACH.parent
MASTER = OUTREACH / "outputs/2026-11-13-master/outreach_master.xlsx"
QUEUE = OUTREACH / "queue/queue.json"
CREDS = Path(
    os.environ.get("OUTREACH_CREDS", Path.home() / ".config/outreach/gmail.env")
)
NOTIFIER_ENV = Path(
    os.environ.get("OUTREACH_NOTIFIER_ENV", REPO / "notifier/config.env")
)
ACCOUNT = "rupanpandyan@gmail.com"
FROM_NAME = "Rupan Sunderapandyan"
ATTACHMENT_NAME = "Ramrupan_Sunderapandyan_resume.pdf"
RESUMES = {
    "biotechML": "biotechMLAI_resume.pdf",
    "scientificML": "scientificML_resume.pdf",
    "general": "general_resume.pdf",
}
SEND_GRACE = dt.timedelta(hours=6)
# Tue-Thu mid-morning Pacific lands at the top of SF inboxes; 10:30-12:00 Central is 8:30-10:00 Pacific.
SEND_WEEKDAYS = (1, 2, 3)
WINDOW_START = dt.time(10, 30)
WINDOW = dt.timedelta(minutes=90)
MIN_LEAD = dt.timedelta(hours=1)
DAILY_CAP = 50
GUESS_DAILY_CAP = 10
BOUNCE_LIMIT_7D = 4
MAX_ATTEMPTS = 2
FIRST_ROW = 7

SIGNATURE_TEXT = (
    "--\nRupan Sunderapandyan\nChemistry + Computer Sciences, UW-Madison\n"
    "rupan.dev | LinkedIn <https://www.linkedin.com/in/rupan-sunderapandyan/> |\n"
    "GitHub <https://github.com/JEFF7712>"
)
SIGNATURE_HTML = (
    '<br><span class="gmail_signature_prefix">-- </span><br>'
    '<div dir="ltr" class="gmail_signature" data-smartmail="gmail_signature"><div dir="ltr">'
    "<div>Rupan Sunderapandyan</div><div>Chemistry + Computer Sciences, UW-Madison</div>"
    '<div><a href="http://rupan.dev" target="_blank">rupan.dev</a> | '
    '<a href="https://www.linkedin.com/in/rupan-sunderapandyan/" target="_blank">LinkedIn</a> | '
    '<a href="https://github.com/JEFF7712" target="_blank">GitHub</a></div></div></div>'
)


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def now() -> dt.datetime:
    return dt.datetime.now(TZ)


def parse_time(value: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(value)
    return t.replace(tzinfo=TZ) if t.tzinfo is None else t.astimezone(TZ)


def domain_of(addr: str) -> str:
    return addr.rsplit("@", 1)[-1].lower().strip(">")


def norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            m = re.match(r'\s*([A-Z_]+)\s*=\s*"?([^"#]*)"?', line)
            if m:
                env[m[1]] = m[2].strip()
    return env


def notify(title: str, body: str) -> None:
    env = load_env(NOTIFIER_ENV)
    topic = env.get("NTFY_TOPIC")
    if not topic:
        return
    req = urllib.request.Request(
        f"{env.get('NTFY_SERVER', 'https://ntfy.sh')}/{topic}",
        data=body.encode(),
        headers={"Title": title, "Tags": "email"},
    )
    with contextlib.suppress(Exception):
        urllib.request.urlopen(req, timeout=10)


# ---------- queue state ----------


@contextlib.contextmanager
def queue_lock():
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    with open(QUEUE.with_suffix(".lock"), "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        data = json.loads(QUEUE.read_text()) if QUEUE.exists() else {"items": []}
        yield data
        tmp = QUEUE.with_suffix(".tmp")
        with tmp.open("w") as out:
            out.write(json.dumps(data, indent=2) + "\n")
            out.flush()
            os.fsync(out.fileno())
        tmp.replace(QUEUE)
        directory = os.open(QUEUE.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def sender_locked(fn):
    @functools.wraps(fn)
    def run(args):
        QUEUE.parent.mkdir(parents=True, exist_ok=True)
        with open(QUEUE.with_suffix(".sender.lock"), "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            return fn(args)

    return run


def read_queue() -> dict:
    return json.loads(QUEUE.read_text()) if QUEUE.exists() else {"items": []}


# ---------- master workbook ----------

COLS = [
    "Company",
    "Status",
    "Contact",
    "First sent",
    "Last outreach",
    "Follow-ups sent",
    "Next follow-up",
    "Scheduled for (CT)",
    "Resume",
    "Attachment",
    "Last human reply",
    "Next action",
    "Location",
    "Notes",
    "Company key",
    "Aliases / prior contact",
    "Gmail",
    "Source",
    "Verified on",
    "Blocked addresses",
    "Batch history",
]
COL = {name: i + 1 for i, name in enumerate(COLS)}


def master_locked() -> bool:
    return (MASTER.parent / f".~lock.{MASTER.name}#").exists()


def master_rows() -> list[dict]:
    ws = openpyxl.load_workbook(MASTER, read_only=True).active
    rows = []
    for r in ws.iter_rows(min_row=FIRST_ROW, values_only=True):
        if r[0] is None:
            continue
        rows.append({name: r[i] for i, name in enumerate(COLS)})
    return rows


def _shift_ranges(ws, old_last: int, new_last: int) -> None:
    def bump(sqref: str) -> str:
        parts = []
        for rng in str(sqref).split():
            cr = CellRange(rng)
            if cr.max_row == old_last:
                cr.expand(down=new_last - old_last)
            parts.append(cr.coord)
        return " ".join(parts)

    for dv in ws.data_validations.dataValidation:
        dv.sqref = MultiCellRange(bump(dv.sqref))
    old_cf = ws.conditional_formatting
    ws.conditional_formatting = ConditionalFormattingList()
    for cf in old_cf:
        for rule in cf.rules:
            ws.conditional_formatting.add(bump(cf.sqref), rule)
    for table in ws.tables.values():
        cr = CellRange(table.ref)
        cr.expand(down=new_last - cr.max_row)
        table.ref = cr.coord
        if table.autoFilter:
            table.autoFilter.ref = cr.coord
    for cell in ws[5]:
        if isinstance(cell.value, str) and cell.value.startswith("="):
            cell.value = re.sub(
                rf"(?P<c>[A-Z]){FIRST_ROW}:(?P=c){old_last}\b",
                rf"\g<c>{FIRST_ROW}:\g<c>{new_last}",
                cell.value,
            )


def master_update(ops: list[dict]) -> None:
    """ops: {"op": "add", "row": {...}} or {"op": "set", "key": ..., "fields": {...}}"""
    if master_locked():
        raise RuntimeError("master workbook is open in an editor (lock file present)")
    wb = openpyxl.load_workbook(MASTER)
    ws = wb.active
    last = ws.max_row
    while last >= FIRST_ROW and ws.cell(last, 1).value is None:
        last -= 1
    keys = {
        norm(ws.cell(r, COL["Company key"]).value): r
        for r in range(FIRST_ROW, last + 1)
    }
    for op in ops:
        if op["op"] == "add":
            if norm(op["row"]["Company key"]) in keys:
                raise RuntimeError(
                    f"master already has company key {op['row']['Company key']}"
                )
            last += 1
            for c in range(1, len(COLS) + 1):
                src, dst = ws.cell(last - 1, c), ws.cell(last, c)
                dst._style = copy.copy(src._style)
            ws.cell(last, COL["Next follow-up"]).value = (
                f'=IF(B{last}="Waiting",IF(E{last}="","",IF(F{last}=0,WORKDAY(E{last},5),'
                f'IF(F{last}=1,WORKDAY(E{last},7),""))),"")'
            )
            ws.cell(last, COL["Follow-ups sent"]).value = 0
            _write(ws, last, op["row"])
            keys[norm(op["row"]["Company key"])] = last
            _shift_ranges(ws, last - 1, last)
        else:
            r = keys.get(norm(op["key"]))
            if r is None:
                raise RuntimeError(f"master has no company key {op['key']}")
            _write(ws, r, op["fields"])
    wb.save(MASTER)


def _write(ws, r: int, fields: dict) -> None:
    for name, value in fields.items():
        if name.startswith("+"):
            name = name[1:]
            prev = [
                x.strip()
                for x in str(ws.cell(r, COL[name]).value or "").split(";")
                if x.strip()
            ]
            value = "; ".join(dict.fromkeys([*prev, value]))
        cell = ws.cell(r, COL[name])
        if isinstance(value, dict) and "link" in value:
            cell.value = value["text"]
            cell.hyperlink = value["link"]
        else:
            if isinstance(value, dt.datetime) and value.tzinfo:
                value = value.astimezone(TZ).replace(tzinfo=None)
            cell.value = value
            if value is None and cell.hyperlink:
                cell.hyperlink = None


def gmail_search_link(query: str) -> str:
    from urllib.parse import quote

    return f"https://mail.google.com/mail/u/?authuser={quote(ACCOUNT)}#search/{quote(query)}"


# ---------- IMAP / SMTP ----------


def creds() -> tuple[str, str]:
    env = load_env(CREDS)
    user, pw = (
        env.get("GMAIL_USER", ACCOUNT),
        env.get("GMAIL_APP_PASSWORD", "").replace(" ", ""),
    )
    if not pw:
        die(f"missing GMAIL_APP_PASSWORD in {CREDS} (see outreach/AUTOMATION.md)")
    return user, pw


class Mailbox:
    def __init__(self) -> None:
        user, pw = creds()
        self.imap = imaplib.IMAP4_SSL("imap.gmail.com")
        self.imap.login(user, pw)
        self.special = {}
        _, lines = self.imap.list()
        for line in lines or []:
            text = line.decode()
            flag = re.search(r"\\(Drafts|Sent|All)\b", text)
            name = re.search(r'"([^"]+)"\s*$', text) or re.search(r"(\S+)$", text)
            if flag and name:
                self.special[flag[1]] = name[1]
        for k in ("Drafts", "Sent", "All"):
            if k not in self.special:
                die(f"could not find Gmail special-use mailbox {k}")

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.imap.logout()

    def select(self, kind: str) -> None:
        typ, _ = self.imap.select(f'"{self.special[kind]}"')
        if typ != "OK":
            raise RuntimeError(f"cannot select {kind}")

    def search_raw(self, kind: str, query: str) -> list[bytes]:
        self.select(kind)
        typ, data = self.imap.uid("SEARCH", "X-GM-RAW", f'"{query}"')
        return data[0].split() if typ == "OK" and data and data[0] else []

    def headers(self, uids: list[bytes]) -> list[dict]:
        out = []
        for uid in uids[-25:]:
            _, data = self.imap.uid(
                "FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (TO SUBJECT DATE MESSAGE-ID)])"
            )
            if data and isinstance(data[0], tuple):
                m = email.message_from_bytes(data[0][1])
                out.append(
                    {
                        "to": m["To"],
                        "subject": m["Subject"],
                        "date": m["Date"],
                        "message_id": m["Message-ID"],
                    }
                )
        return out

    def fetch(self, uid: bytes) -> bytes:
        _, data = self.imap.uid("FETCH", uid, "(BODY.PEEK[])")
        return data[0][1]

    def append_draft(self, msg: EmailMessage) -> None:
        typ, _ = self.imap.append(
            f'"{self.special["Drafts"]}"',
            r"(\Draft \Seen)",
            imaplib.Time2Internaldate(time.time()),
            msg.as_bytes(policy=email.policy.SMTP),
        )
        if typ != "OK":
            raise RuntimeError("IMAP APPEND to Drafts failed")

    def find_draft(self, message_id: str, to: str) -> bytes | None:
        self.select("Drafts")
        typ, data = self.imap.uid("SEARCH", "HEADER", "Message-ID", f'"{message_id}"')
        if typ == "OK" and data[0]:
            return data[0].split()[-1]
        # Editing a draft in Gmail replaces its Message-ID; fall back to the recipient.
        uids = self.search_raw("Drafts", f"to:{to}")
        return uids[-1] if len(uids) == 1 else None

    def delete_draft(self, uid: bytes) -> None:
        self.select("Drafts")
        self.imap.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
        self.imap.expunge()

    def sent_time(self, message_id: str) -> dt.datetime | None:
        self.select("Sent")
        typ, data = self.imap.uid("SEARCH", "HEADER", "Message-ID", f'"{message_id}"')
        if typ != "OK":
            raise RuntimeError("cannot search Sent for delivery reconciliation")
        if not data or not data[0]:
            return None
        msg = email.message_from_bytes(self.fetch(data[0].split()[-1]))
        if str(msg["Message-ID"]) != message_id:
            raise RuntimeError("Sent search returned a different Message-ID")
        return parsedate_to_datetime(str(msg["Date"])).astimezone(TZ)


def smtp_send(msg: EmailMessage) -> None:
    user, pw = creds()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(user, pw)
        s.send_message(msg)


# ---------- rendering and lint ----------


def resume_path(variant: str) -> Path:
    if variant not in RESUMES:
        die(f"unknown resume variant {variant!r}; choose from {sorted(RESUMES)}")
    return (
        Path(os.environ.get("OUTREACH_RESUMES", Path.home() / "documents/resumes"))
        / variant
        / ATTACHMENT_NAME
    )


def resume_drift(variant: str) -> str | None:
    built = REPO / "resumes/build" / RESUMES[variant]
    sent = resume_path(variant)
    if not sent.exists():
        return f"{sent} missing"
    if (
        built.exists()
        and hashlib.sha256(built.read_bytes()).digest()
        != hashlib.sha256(sent.read_bytes()).digest()
    ):
        return f"{sent} differs from {built.relative_to(REPO)} (copy the validated build over before queueing)"
    return None


def body_html(body: str) -> str:
    paras = [p for p in body.strip().split("\n\n")]
    parts = []
    for i, p in enumerate(paras):
        if i:
            parts.append("<div><br></div>")
        parts.append(
            "<div>"
            + "<br>".join(html.escape(line, quote=False) for line in p.split("\n"))
            + "</div>"
        )
    return (
        '<div dir="ltr">'
        + "".join(parts)
        + '<div><br clear="all"></div>'
        + SIGNATURE_HTML
        + "</div>"
    )


def build_message(e: dict, message_id: str | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((FROM_NAME, ACCOUNT))
    msg["To"] = formataddr((e.get("to_name") or "", e["to"]))
    msg["Subject"] = e["subject"]
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = message_id or make_msgid(domain="mail.gmail.com")
    body = e["body"].strip()
    msg.set_content(body + "\n\n" + SIGNATURE_TEXT + "\n")
    msg.add_alternative(body_html(body), subtype="html")
    if e.get("resume"):
        msg.add_attachment(
            resume_path(e["resume"]).read_bytes(),
            maintype="application",
            subtype="pdf",
            filename=ATTACHMENT_NAME,
        )
    return msg


PLACEHOLDER = re.compile(
    r"TODO|TBD|XXX|\[[A-Za-z ]+\]|\{[A-Za-z_ ]+\}|<[A-Za-z ]+>|lorem", re.I
)


def lint(
    e: dict, known: dict[str, str], blocked: set[str], seen: set[str]
) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    for field in ("company", "key", "to", "subject", "body", "resume"):
        if not e.get(field):
            errs.append(f"missing {field}")
    if errs:
        return errs, warns
    body, subj = e["body"], e["subject"]
    text = body + "\n" + subj
    if re.search("[\u2014\u2013]", text):
        errs.append("em/en dash present")
    if re.search("[\U0001f300-\U0001faff\u2600-\u27bf]", text):
        errs.append("emoji present")
    if PLACEHOLDER.search(text):
        errs.append(f"placeholder text: {PLACEHOLDER.search(text)[0]!r}")
    if not re.match(r"Hi [^,\n]+,\n", body):
        errs.append("body must open with 'Hi <Name>,' on its own line")
    if "Resume attached." not in body:
        errs.append("body must say 'Resume attached.'")
    if not body.rstrip().endswith("Thanks,\nRupan"):
        errs.append("body must end with 'Thanks,\\nRupan'")
    if "manuscript" in body.lower() and "in preparation" not in body.lower():
        errs.append("manuscript must be described as 'in preparation'")
    words = len(re.findall(r"\b\w[\w'.-]*\b", body))
    if not 70 <= words <= 190:
        errs.append(f"body is {words} words (target 100-150)")
    elif not 95 <= words <= 165:
        warns.append(f"body is {words} words (target 100-150)")
    if not re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[a-z]{2,}", e["to"], re.I):
        errs.append(f"bad address {e['to']!r}")
    if e["to"].lower() in blocked:
        errs.append(f"{e['to']} is a blocked address")
    k = norm(e["key"])
    for cand in {k, norm(domain_of(e["to"])), norm(e["company"])}:
        if cand in known:
            errs.append(f"already in master/queue as {known[cand]!r}")
            break
    if k in seen:
        errs.append("duplicate company key within this batch")
    seen.add(k)
    if norm(domain_of(e["to"])) != k and not domain_of(e["to"]).endswith(
        e["key"].lower()
    ):
        warns.append(
            f"recipient domain {domain_of(e['to'])} differs from company key {e['key']}"
        )
    if e.get("contact_status") in ("accept_all", "unknown"):
        warns.append(f"contact status {e['contact_status']}: may bounce")
    if e.get("contact_status") == "guessed":
        cands = [c.lower() for c in e.get("guess_candidates") or []]
        if not cands or cands[0] != e["to"].lower():
            errs.append(
                "guessed contact needs guess_candidates (from `outreach guess`) with `to` first"
            )
        if not e.get("to_name"):
            errs.append("guessed contact needs to_name")
    elif not e.get("contact_status"):
        warns.append("no contact_status (found, published, guessed, shared_inbox)")
    if drift := resume_drift(e["resume"]):
        errs.append(drift)
    return errs, warns


def known_companies() -> tuple[dict[str, str], set[str]]:
    known, blocked = {}, set()
    for r in master_rows():
        label = f"{r['Company']} ({r['Status']})"
        for v in (r["Company"], r["Company key"]):
            if v:
                known[norm(v)] = label
        for alias in re.split(r"[;\n]", r["Aliases / prior contact"] or ""):
            alias = alias.strip()
            if alias:
                known[norm(domain_of(alias) if "@" in alias else alias)] = label
        for c in re.findall(r"[\w.+-]+@[\w.-]+", r["Contact"] or ""):
            known[norm(domain_of(c))] = label
        for b in re.findall(r"[\w.+-]+@[\w.-]+", r["Blocked addresses"] or ""):
            blocked.add(b.lower())
    for it in read_queue()["items"]:
        if it["state"] in ("queued", "sending", "uncertain", "sent"):
            for v in (it["key"], it["company"], domain_of(it["to"])):
                known[norm(v)] = f"{it['company']} (queue: {it['state']})"
    return known, blocked


def day_load() -> dict[dt.date, list[int]]:
    """[total, guessed] sends already booked per day: Gmail-scheduled master rows plus the queue."""
    load: dict[dt.date, list[int]] = {}
    for r in master_rows():
        t = r["Scheduled for (CT)"]
        if r["Status"] == "Scheduled" and isinstance(t, dt.datetime):
            load.setdefault(t.date(), [0, 0])[0] += 1
    for it in read_queue()["items"]:
        if it["state"] != "queued":
            continue
        d = load.setdefault(parse_time(it["send_at"]).date(), [0, 0])
        if it.get("master") != "synced":
            d[0] += 1
        d[1] += it.get("contact_status") == "guessed"
    return load


def allocate_slots(
    keys: list[str],
    guessed: list[bool],
    days: list[dt.date] | None = None,
    cap: int = DAILY_CAP,
) -> list[dt.datetime]:
    """Assign each email to the next Tue-Thu (or given day) with room, then spread it over the send window."""
    load = day_load()
    earliest = now() + MIN_LEAD
    if days:
        candidates = sorted(days)
    else:
        start = earliest.date()
        candidates = [
            d
            for d in (start + dt.timedelta(days=i) for i in range(70))
            if d.weekday() in SEND_WEEKDAYS
        ]
    candidates = [
        d for d in candidates if dt.datetime.combine(d, WINDOW_START, TZ) >= earliest
    ]
    per_day: dict[dt.date, list[int]] = {}
    for i, g in enumerate(guessed):
        for d in candidates:
            total, gcount = load.setdefault(d, [0, 0])
            if total < cap and (not g or gcount < GUESS_DAILY_CAP):
                load[d][0] += 1
                load[d][1] += g
                per_day.setdefault(d, []).append(i)
                break
        else:
            die(
                f"no send day with room for {keys[i]} (daily cap {cap}, guessed cap {GUESS_DAILY_CAP})"
            )
    times: list[dt.datetime] = [now()] * len(keys)
    for d, idxs in per_day.items():
        step = WINDOW / len(idxs)
        for j, i in enumerate(idxs):
            jitter = dt.timedelta(
                minutes=int(hashlib.sha256(keys[i].encode()).hexdigest(), 16) % 4
            )
            times[i] = (
                dt.datetime.combine(d, WINDOW_START, TZ) + step * j + jitter
            ).replace(second=0, microsecond=0)
    return times


def schedule_times(spec: dict) -> list[dt.datetime]:
    emails = spec["emails"]
    if spec.get("send_at"):
        base = parse_time(spec["send_at"])
        step = dt.timedelta(minutes=spec.get("spread_minutes", 0)) / max(
            len(emails) - 1, 1
        )
        times = [
            (base + step * i).replace(second=0, microsecond=0)
            for i in range(len(emails))
        ]
    else:
        days = spec.get("send_on")
        days = (
            [
                dt.date.fromisoformat(d)
                for d in ([days] if isinstance(days, str) else days)
            ]
            if days
            else None
        )
        times = allocate_slots(
            [e.get("key", str(i)) for i, e in enumerate(emails)],
            [e.get("contact_status") == "guessed" for e in emails],
            days,
            spec.get("daily_cap", DAILY_CAP),
        )
    return [
        parse_time(e["send_at"]) if e.get("send_at") else t
        for e, t in zip(emails, times)
    ]


# ---------- address guessing ----------

PATTERNS = {
    "first": lambda f, s: f,
    "first.last": lambda f, s: f"{f}.{s}",
    "flast": lambda f, s: f[0] + s,
    "firstlast": lambda f, s: f + s,
    "f.last": lambda f, s: f"{f[0]}.{s}",
    "firstl": lambda f, s: f + s[0],
    "first_last": lambda f, s: f"{f}_{s}",
    "last": lambda f, s: s,
}
SMALL_ORDER = ["first", "first.last", "flast", "firstlast", "f.last", "firstl"]
LARGE_ORDER = ["first.last", "flast", "first", "firstlast", "f.last", "firstl"]


def name_parts(name: str) -> tuple[str, str]:
    import unicodedata

    ascii_name = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    )
    words = [
        re.sub(r"[^a-z]", "", w)
        for w in re.sub(r"\(.*?\)|,.*$", "", ascii_name).split()
    ]
    words = [
        w for w in words if w and w not in ("dr", "phd", "md", "jr", "sr", "ii", "iii")
    ]
    if len(words) < 2:
        die(f"need a first and last name, got {name!r}")
    return words[0], words[-1]


def mx_hosts(domain: str) -> list[str]:
    req = urllib.request.Request(
        f"https://cloudflare-dns.com/dns-query?name={domain}&type=MX",
        headers={"accept": "application/dns-json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        answers = json.load(r).get("Answer") or []
    return sorted(
        a["data"].split()[-1].rstrip(".").lower()
        for a in answers
        if a.get("type") == 15
    )


def infer_patterns(known: list[str]) -> list[str]:
    found = []
    for k in known:
        name, addr = email.utils.parseaddr(k)
        if not name or "@" not in addr:
            continue
        f, s = name_parts(name)
        local = addr.split("@")[0].lower()
        found += [
            p for p, fn in PATTERNS.items() if fn(f, s) == local and p not in found
        ]
    return found


def cmd_guess(args) -> None:
    domain = args.domain.lower().removeprefix("www.")
    mx = mx_hosts(domain)
    if not mx:
        die(f"{domain} has no MX records; mail to it cannot be delivered")
    f, s = name_parts(args.name)
    evidence = infer_patterns(args.known or [])
    if args.pattern:
        evidence = [args.pattern, *[p for p in evidence if p != args.pattern]]
    order = LARGE_ORDER if args.team_size and args.team_size > 50 else SMALL_ORDER
    ranked = evidence + [p for p in order if p not in evidence]
    candidates = list(dict.fromkeys(f"{PATTERNS[p](f, s)}@{domain}" for p in ranked))[
        : args.max
    ]
    provider = (
        "google"
        if any("google" in h for h in mx)
        else "microsoft"
        if any("outlook" in h for h in mx)
        else mx[0]
    )
    print(
        json.dumps(
            {
                "to": candidates[0],
                "to_name": args.name,
                "contact_status": "guessed",
                "guess_candidates": candidates,
                "guess_basis": f"pattern {ranked[0]} from {'known addresses' if evidence else 'default order'}"
                + f"; mail host {provider}",
            },
            indent=2,
        )
    )


# ---------- commands ----------


def cmd_check(args) -> None:
    known, blocked = known_companies()
    mb = None if args.offline else Mailbox()
    results = []
    for term in args.terms:
        res = {
            "term": term,
            "master_or_queue": known.get(norm(term))
            or known.get(norm(domain_of(term))),
        }
        if mb:
            q = f"{term} OR to:{term} OR from:{term}" if "." in term else term
            res["gmail_all"] = mb.headers(mb.search_raw("All", q))
            res["gmail_drafts"] = mb.headers(mb.search_raw("Drafts", q))
        res["clear"] = (
            not res["master_or_queue"]
            and not res.get("gmail_all")
            and not res.get("gmail_drafts")
        )
        results.append(res)
    if mb:
        mb.close()
    print(json.dumps(results, indent=2, default=str))


def load_spec(path: str) -> dict:
    spec = json.loads(Path(path).read_text())
    if not spec.get("batch") or not spec.get("emails"):
        die("spec needs 'batch' and 'emails'")
    return spec


def cmd_lint(args) -> int:
    spec = load_spec(args.spec)
    known, blocked = known_companies()
    times = schedule_times(spec)
    seen: set[str] = set()
    bad = 0
    for e, t in zip(spec["emails"], times):
        errs, warns = lint(e, known, blocked, seen)
        if t < now() + dt.timedelta(minutes=5):
            errs.append(f"send time {t:%a %b %d %H:%M} is in the past or too soon")
        if t.weekday() not in SEND_WEEKDAYS:
            warns.append(f"send day {t:%A} is outside the Tue-Thu target")
        if (
            not WINDOW_START
            <= t.time()
            <= (dt.datetime.combine(t.date(), WINDOW_START) + WINDOW).time()
        ):
            warns.append("send time is outside the 10:30-12:00 CT window")
        status = "FAIL" if errs else "ok"
        bad += bool(errs)
        print(
            f"[{status}] {e.get('company')} -> {e.get('to')} @ {t:%a %b %d %H:%M} CT ({e.get('resume')})"
        )
        for m in errs:
            print(f"    error: {m}")
        for m in warns:
            print(f"    warn:  {m}")
    print(f"\n{len(spec['emails']) - bad}/{len(spec['emails'])} pass")
    return bad


def cmd_render(args) -> None:
    spec = load_spec(args.spec)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for e in spec["emails"]:
        path = out / f"{norm(e['key'])}.eml"
        path.write_bytes(build_message(e).as_bytes(policy=email.policy.SMTP))
        print(path)


def cmd_queue(args) -> None:
    if cmd_lint(args):
        die("lint failed; nothing queued")
    spec = load_spec(args.spec)
    times = schedule_times(spec)
    if master_locked():
        die("close the master workbook first (lock file present)")
    mb = Mailbox()
    for e in spec["emails"]:
        dom = domain_of(e["to"])
        hits = mb.search_raw("All", f"to:{dom} OR from:{dom}") + mb.search_raw(
            "Drafts", f"to:{dom}"
        )
        if hits:
            mb.close()
            die(
                f"{e['company']}: Gmail already has mail with {dom}; run `outreach check {dom}`"
            )
    queued = []
    try:
        for e, t in zip(spec["emails"], times):
            msg = build_message(e)
            mb.append_draft(msg)
            item = {
                "id": msg["Message-ID"],
                "batch": spec["batch"],
                "company": e["company"],
                "key": e["key"],
                "to": e["to"],
                "to_name": e.get("to_name"),
                "subject": e["subject"],
                "resume": e["resume"],
                "send_at": t.isoformat(),
                "state": "queued",
                "queued_at": now().isoformat(),
                "master": "pending",
                "contact_status": e.get("contact_status"),
                "attempt": 1,
                "email": e,
            }
            queued.append((item, e))
            print(f"drafted {e['company']} -> {e['to']} for {t:%a %b %d %H:%M} CT")
    finally:
        mb.close()
        with queue_lock() as q:
            q["items"].extend(item for item, _ in queued)
    ops = []
    for item, e in queued:
        ops.append(
            {
                "op": "add",
                "row": {
                    "Company": e["company"],
                    "Status": "Scheduled",
                    "Contact": e["to"],
                    "Scheduled for (CT)": parse_time(item["send_at"]),
                    "Resume": e["resume"],
                    "Attachment": "Attached by outreach CLI",
                    "Next action": "Auto-sends from the outreach queue; edit or delete the Gmail draft to change or cancel",
                    "Location": e.get("location"),
                    "Notes": " ".join(
                        x
                        for x in (
                            e.get("notes"),
                            f"Contact {e.get('to_name') or ''} ({e.get('title') or 'role n/a'}),"
                            f" {e.get('contact_status') or 'unrecorded'}:"
                            f" {e.get('guess_basis') or e.get('contact_source') or 'no source recorded'}.",
                        )
                        if x
                    ),
                    "Company key": e["key"],
                    "Aliases / prior contact": e.get("aliases"),
                    "Gmail": {
                        "text": "Open draft",
                        "link": gmail_search_link(f"in:drafts to:{e['to']}"),
                    },
                    "Source": {"text": "Company source", "link": e["source"]}
                    if e.get("source")
                    else None,
                    "Verified on": now().replace(
                        hour=12, minute=0, second=0, microsecond=0
                    ),
                    "Batch history": spec["batch"],
                },
            }
        )
    sync_master(ops, [item["id"] for item, _ in queued])
    print(f"\nqueued {len(queued)} emails; homelab checks the queue every minute")


def sync_master(ops: list[dict], ids: list[str]) -> None:
    try:
        master_update(ops)
        state = "synced"
    except RuntimeError as exc:
        print(
            f"warn: master not updated ({exc}); will retry on next send-due run",
            file=sys.stderr,
        )
        state = "pending"
    serial = json.loads(json.dumps(ops, default=lambda v: v.isoformat()))
    with queue_lock() as q:
        for it in q["items"]:
            if it["id"] in ids:
                mine = [o for o in serial if _op_key(o) == norm(it["key"])]
                it["master"] = "synced" if state == "synced" else {"pending_ops": mine}


def _op_key(op: dict) -> str:
    return norm(op["row"]["Company key"] if op["op"] == "add" else op["key"])


def retry_master() -> None:
    q = read_queue()
    pending = [it for it in q["items"] if isinstance(it.get("master"), dict)]
    if not pending or master_locked():
        return
    ops = [op for it in pending for op in it["master"]["pending_ops"]]
    sync_master(_jsonable_ops(ops), [it["id"] for it in pending])


def _jsonable_ops(ops: list[dict]) -> list[dict]:
    for op in ops:
        fields = op.get("row") or op.get("fields")
        for k, v in fields.items():
            if isinstance(v, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:.+-]+", v):
                fields[k] = parse_time(v)
    return ops


def bounces_7d() -> int:
    cutoff = now() - dt.timedelta(days=7)
    return sum(
        1
        for it in read_queue()["items"]
        if it.get("bounced_at") and parse_time(it["bounced_at"]) >= cutoff
    )


def master_key_for(addr: str) -> str | None:
    for r in master_rows():
        if addr in (r["Contact"] or "").lower():
            return r["Company key"]
    return None


def scan_bounces(mb: Mailbox) -> list[str]:
    q = read_queue()
    seen = set(q.get("bounces_seen", []))
    lines = []
    for h in mb.headers(mb.search_raw("All", "from:mailer-daemon newer_than:7d")):
        mid = h["message_id"]
        if not mid or mid in seen:
            continue
        mb.select("All")
        _, data = mb.imap.uid("SEARCH", "HEADER", "Message-ID", f'"{mid}"')
        uids = data[0].split() if data and data[0] else []
        if not uids:
            continue
        m = email.message_from_bytes(mb.fetch(uids[-1]), policy=email.policy.default)
        failed = [
            a.strip().lower()
            for a in str(m["X-Failed-Recipients"] or "").split(",")
            if a.strip()
        ]
        status = re.search(r"Status:\s*([245]\.\d+\.\d+)", m.as_string())
        code = status[1] if status else "5.0.0"
        for addr in failed if code.startswith("5") else []:
            lines.append(handle_bounce(mb, addr, code))
        with queue_lock() as qq:
            qq.setdefault("bounces_seen", []).append(mid)
    return lines


def handle_bounce(mb: Mailbox, addr: str, code: str) -> str:
    items = [
        it
        for it in read_queue()["items"]
        if it["state"] == "sent" and it["to"].lower() == addr
    ]
    if not items:
        key = master_key_for(addr)
        if key:
            with contextlib.suppress(RuntimeError):
                master_update(
                    [{"op": "set", "key": key, "fields": {"+Blocked addresses": addr}}]
                )
        return f"bounce {addr} ({code}){'' if key else ', no master row'}"
    it = items[-1]
    _set(it["id"], state="bounced", bounced_at=now().isoformat(), bounce_status=code)
    retry = maybe_retry(mb, it)
    if retry:
        _master_set(
            it,
            {
                "Status": "Scheduled",
                "Contact": retry["to"],
                "Scheduled for (CT)": parse_time(retry["send_at"]),
                "+Blocked addresses": addr,
                "Next action": f"Guess {addr} bounced ({code}); retrying {retry['to']} from the queue",
            },
        )
        return f"bounce {it['company']} {addr}; retry {retry['to']} {parse_time(retry['send_at']):%a %H:%M}"
    _master_set(
        it,
        {
            "Status": "Address bounced",
            "Scheduled for (CT)": None,
            "+Blocked addresses": addr,
            "Next action": "Address bounced; find a sourced contact before trying again",
        },
    )
    return f"bounce {it['company']} {addr} ({code}); no retry"


def maybe_retry(mb: Mailbox, it: dict) -> dict | None:
    if it.get("contact_status") != "guessed" or it.get("attempt", 1) >= MAX_ATTEMPTS:
        return None
    if bounces_7d() >= BOUNCE_LIMIT_7D:
        return None
    tried = {
        x["to"].lower()
        for x in read_queue()["items"]
        if norm(x["key"]) == norm(it["key"])
    }
    nxt = next(
        (c for c in it["email"].get("guess_candidates", []) if c.lower() not in tried),
        None,
    )
    if not nxt:
        return None
    e = dict(it["email"], to=nxt)
    t = allocate_slots([e["key"]], [True])[0]
    msg = build_message(e)
    mb.append_draft(msg)
    new = dict(
        it,
        id=msg["Message-ID"],
        to=nxt,
        send_at=t.isoformat(),
        state="queued",
        queued_at=now().isoformat(),
        attempt=it.get("attempt", 1) + 1,
        retry_of=it["id"],
        email=e,
        master="synced",
    )
    for k in ("sent_at", "sent_message_id", "bounced_at", "bounce_status", "note"):
        new.pop(k, None)
    with queue_lock() as q:
        q["items"].append(new)
    return new


def record_sent(
    it: dict, sent_at: dt.datetime, to_addrs: str, note: str | None = None
) -> None:
    noon = sent_at.replace(hour=12, minute=0, second=0, microsecond=0)
    fields = {
        "Status": "Waiting",
        "Contact": to_addrs,
        "First sent": noon.isoformat(),
        "Last outreach": noon.isoformat(),
        "Scheduled for (CT)": None,
        "Attachment": "Verified PDF",
        "Next action": "Review live thread before considering follow-up 1",
        "Gmail": {
            "text": "Open Gmail",
            "link": gmail_search_link(
                f"rfc822msgid:{it['sent_message_id'].strip('<>')}"
            ),
        },
    }
    with queue_lock() as q:
        for item in q["items"]:
            if item["id"] == it["id"]:
                pending = (
                    item["master"]["pending_ops"]
                    if isinstance(item.get("master"), dict)
                    else []
                )
                item.update(
                    state="sent",
                    sent_at=sent_at.isoformat(),
                    to=to_addrs,
                    note=note,
                    master={
                        "pending_ops": pending
                        + [{"op": "set", "key": it["key"], "fields": fields}]
                    },
                )


@sender_locked
def cmd_send_due(args) -> None:
    retry_master()
    q = read_queue()
    due = [
        it
        for it in q["items"]
        if it["state"] == "queued" and parse_time(it["send_at"]) <= now()
    ]
    unresolved = [it for it in q["items"] if it["state"] in ("sending", "uncertain")]
    last_scan = q.get("last_bounce_scan")
    scan = (
        args.scan_bounces
        or not last_scan
        or now() - parse_time(last_scan) > dt.timedelta(minutes=30)
    )
    if not due and not unresolved and not scan:
        return
    mb = Mailbox()
    lines: list[str] = []
    sent, missed, failed, held = [], [], [], []
    try:
        for it in unresolved:
            if args.dry_run:
                print(
                    f"would reconcile {it['company']} against Gmail Sent; never auto-resend"
                )
                continue
            sent_at = mb.sent_time(it["sent_message_id"])
            if sent_at is not None:
                record_sent(
                    it,
                    sent_at,
                    it["attempt_to"],
                    "Recovered from Gmail Sent; review any leftover draft",
                )
                lines.append(f"recovered sent {it['company']}")
            else:
                note = "Delivery uncertain; not found in Gmail Sent. Held for review, never auto-resend"
                _set(it["id"], state="uncertain", note=note)
                if it["state"] != "uncertain":
                    lines.append(f"UNCERTAIN {it['company']}: {note}")
        if scan and not args.dry_run:
            lines += scan_bounces(mb)
            with queue_lock() as qq:
                qq["last_bounce_scan"] = now().isoformat()
        for it in due:
            late = now() - parse_time(it["send_at"])
            if late > SEND_GRACE and not args.force:
                if args.dry_run:
                    print(f"would mark missed {it['company']}")
                    continue
                _set(it["id"], state="missed")
                missed.append(it)
                _master_set(
                    it,
                    {
                        "Next action": "Missed send window (laptop asleep?); run outreach reschedule"
                    },
                )
                continue
            if (
                it.get("contact_status") == "guessed"
                and bounces_7d() >= BOUNCE_LIMIT_7D
            ):
                if not args.dry_run:
                    _set(
                        it["id"],
                        state="held",
                        note=f"bounce limit {BOUNCE_LIMIT_7D}/7d reached",
                    )
                    _master_set(
                        it,
                        {
                            "Next action": "Held: guessed-address bounce limit reached; reschedule later"
                        },
                    )
                held.append(it)
                continue
            uid = mb.find_draft(it["id"], it["to"])
            if uid is None:
                if args.dry_run:
                    print(f"draft missing for {it['company']}")
                    continue
                _set(
                    it["id"],
                    state="cancelled",
                    note="draft not found in Gmail at send time",
                )
                _master_set(
                    it,
                    {
                        "Status": "Dropped",
                        "Scheduled for (CT)": None,
                        "Next action": "Queued draft was deleted in Gmail before sending",
                    },
                )
                continue
            msg = email.message_from_bytes(mb.fetch(uid), policy=email.policy.SMTP)
            if it.get("resume") and not any(
                p.get_filename() for p in msg.iter_attachments()
            ):
                _set(it["id"], state="failed", note="draft has no attachment")
                failed.append(it)
                continue
            for h in ("Date", "Message-ID", "Bcc"):
                del msg[h]
            msg["Date"] = formatdate(localtime=True)
            sent_id = make_msgid(domain="mail.gmail.com")
            msg["Message-ID"] = sent_id
            if args.dry_run:
                print(f"would send {it['company']} -> {msg['To']}")
                continue
            to_addrs = ", ".join(a for _, a in getaddresses([msg["To"]]))
            _set(
                it["id"],
                state="sending",
                sent_message_id=sent_id,
                attempt_started_at=now().isoformat(),
                attempt_to=to_addrs,
            )
            it["sent_message_id"] = sent_id
            try:
                smtp_send(msg)
            except Exception as exc:  # noqa: BLE001
                _set(
                    it["id"],
                    state="uncertain",
                    note=f"SMTP outcome uncertain: {exc}; held for review",
                )
                lines.append(
                    f"UNCERTAIN {it['company']}: SMTP outcome requires reconciliation"
                )
                continue
            sent_at = now()
            record_sent(it, sent_at, to_addrs)
            try:
                mb.delete_draft(uid)
            except Exception as exc:  # noqa: BLE001
                _set(it["id"], note=f"Sent successfully; draft cleanup failed: {exc}")
                lines.append(
                    f"CLEANUP {it['company']}: sent, but leftover draft needs review"
                )
            sent.append(it)
            print(f"sent {it['company']} -> {to_addrs}")
    finally:
        mb.close()
    retry_master()
    if sent:
        lines.insert(0, f"sent {len(sent)}: " + ", ".join(i["company"] for i in sent))
    if missed:
        lines.append(
            f"MISSED {len(missed)} (>{SEND_GRACE} late): "
            + ", ".join(i["company"] for i in missed)
        )
    if held:
        lines.append(
            f"HELD {len(held)} guessed (bounce limit): "
            + ", ".join(i["company"] for i in held)
        )
    if failed:
        lines.append(
            f"FAILED {len(failed)}: " + ", ".join(i["company"] for i in failed)
        )
    for line in lines:
        print(line)
    if lines and not args.dry_run:
        notify("Outreach sender", "\n".join(lines))


def _set(item_id: str, **fields) -> None:
    with queue_lock() as q:
        for it in q["items"]:
            if it["id"] == item_id:
                it.update(fields)


def _master_set(it: dict, fields: dict) -> None:
    op = {"op": "set", "key": it["key"], "fields": fields}
    try:
        master_update([op])
    except RuntimeError:
        serial = json.loads(json.dumps(op, default=lambda v: v.isoformat()))
        with queue_lock() as q:
            for x in q["items"]:
                if x["id"] == it["id"]:
                    prev = (
                        x["master"]["pending_ops"]
                        if isinstance(x.get("master"), dict)
                        else []
                    )
                    x["master"] = {"pending_ops": prev + [serial]}


def select_items(q: dict, target: str, states: tuple[str, ...]) -> list[dict]:
    return [
        it
        for it in q["items"]
        if it["state"] in states
        and target in (it["batch"], it["key"], it["company"], it["to"])
    ]


def cmd_status(args) -> None:
    q = read_queue()
    items = [
        it
        for it in q["items"]
        if args.all
        or it["state"] in ("queued", "sending", "uncertain", "missed", "failed", "held")
    ]
    for it in sorted(items, key=lambda i: i["send_at"]):
        flag = "" if it.get("master") == "synced" else "  [master pending]"
        print(
            f"{parse_time(it['send_at']):%a %b %d %H:%M}  {it['state']:<9} b{it['batch']:<4} "
            f"{it['company']} <{it['to']}> {it['resume']}{flag}{'  ' + it['note'] if it.get('note') else ''}"
        )
    if not items:
        print("queue empty")


@sender_locked
def cmd_reschedule(args) -> None:
    q = read_queue()
    items = select_items(q, args.target, ("queued", "missed", "failed", "held"))
    if not items:
        die(f"nothing queued/missed matching {args.target!r}")
    base = parse_time(args.at)
    step = dt.timedelta(minutes=args.spread_minutes) / max(len(items) - 1, 1)
    for i, it in enumerate(sorted(items, key=lambda x: x["send_at"])):
        t = (base + step * i).replace(second=0, microsecond=0)
        _set(it["id"], state="queued", send_at=t.isoformat(), note=None)
        _master_set(
            it,
            {
                "Status": "Scheduled",
                "Scheduled for (CT)": t,
                "Next action": "Auto-sends from the outreach queue; edit or delete the Gmail draft to change or cancel",
            },
        )
        print(f"{it['company']} -> {t:%a %b %d %H:%M} CT")


@sender_locked
def cmd_cancel(args) -> None:
    q = read_queue()
    items = select_items(q, args.target, ("queued", "missed", "failed", "held"))
    if not items:
        die(f"nothing queued matching {args.target!r}")
    for it in items:
        _set(it["id"], state="cancelled", note="cancelled via CLI; Gmail draft kept")
        _master_set(
            it,
            {
                "Status": "Draft ready",
                "Scheduled for (CT)": None,
                "Next action": "Unscheduled; Gmail draft kept for manual review",
            },
        )
        print(f"cancelled {it['company']} (draft left in Gmail)")


BAY_AREA = re.compile(
    r"San Francisco|South San Francisco|Oakland|Berkeley|Emeryville|Alameda|San Mateo|Redwood City|Palo Alto|"
    r"Menlo Park|Mountain View|Sunnyvale|Santa Clara|San Jose|San Carlos|Burlingame|Foster City|Brisbane|"
    r"Hayward|Fremont|Newark, CA|Richmond, CA|Bay Area",
    re.I,
)


def cmd_discover(args) -> None:
    known, _ = known_companies()
    seen, out = set(), []
    for tag in args.tags.split(","):
        url = f"https://yc-oss.github.io/api/tags/{tag.strip()}.json"
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                companies = json.load(r)
        except Exception as exc:  # noqa: BLE001
            print(f"warn: {url}: {exc}", file=sys.stderr)
            continue
        for c in companies:
            site = (c.get("website") or "").lower()
            dom = re.sub(r"^https?://(www\.)?", "", site).split("/")[0]
            if not dom or dom in seen or c.get("status") not in ("Active", None):
                continue
            seen.add(dom)
            if not args.anywhere and not BAY_AREA.search(c.get("all_locations") or ""):
                continue
            if (c.get("team_size") or 0) > args.max_team:
                continue
            prior = known.get(norm(dom)) or known.get(norm(c.get("name")))
            if prior and not args.include_known:
                continue
            out.append(
                {
                    "company": c.get("name"),
                    "domain": dom,
                    "one_liner": c.get("one_liner"),
                    "locations": c.get("all_locations"),
                    "team_size": c.get("team_size"),
                    "batch": c.get("batch"),
                    "hiring": c.get("isHiring"),
                    "tags": c.get("tags"),
                    "yc_url": c.get("url"),
                    "prior": prior,
                }
            )
    out.sort(key=lambda c: (not c["hiring"], c["batch"] or ""), reverse=False)
    print(json.dumps(out, indent=2))
    print(f"{len(out)} candidates", file=sys.stderr)


def cmd_doctor(args) -> None:
    for v in RESUMES:
        print(f"resume {v}: {resume_drift(v) or 'ok'}")
    mb = Mailbox()
    print(f"imap: ok, mailboxes {mb.special}")
    mb.close()
    user, pw = creds()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(user, pw)
    print("smtp: ok")
    print(f"master: {len(master_rows())} rows{', LOCKED' if master_locked() else ''}")
    print(
        f"queue: {sum(it['state'] == 'queued' for it in read_queue()['items'])} queued"
    )


def main() -> None:
    p = argparse.ArgumentParser(prog="outreach")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser(
        "check", help="dedupe companies/domains against master, queue and Gmail"
    )
    s.add_argument("terms", nargs="+")
    s.add_argument("--offline", action="store_true", help="skip the Gmail search")
    s.set_defaults(fn=cmd_check)
    s = sub.add_parser("lint", help="validate a batch spec without touching Gmail")
    s.add_argument("spec")
    s.set_defaults(fn=lambda a: sys.exit(1 if cmd_lint(a) else 0))
    s = sub.add_parser("render", help="write .eml previews of a batch spec")
    s.add_argument("spec")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_render)
    s = sub.add_parser(
        "queue", help="lint, create Gmail drafts, schedule, and record in the master"
    )
    s.add_argument("spec")
    s.set_defaults(fn=cmd_queue)
    s = sub.add_parser(
        "send-due", help="send queued drafts whose time has come (systemd timer)"
    )
    s.add_argument("--dry-run", action="store_true")
    s.add_argument(
        "--force", action="store_true", help="send even if past the grace window"
    )
    s.add_argument(
        "--scan-bounces",
        action="store_true",
        help="scan for bounces now instead of every 30 min",
    )
    s.set_defaults(fn=cmd_send_due)
    s = sub.add_parser(
        "guess", help="rank likely addresses for a named person at a domain"
    )
    s.add_argument("name")
    s.add_argument("domain")
    s.add_argument(
        "--known", nargs="*", help='known addresses at the domain, "Full Name <addr>"'
    )
    s.add_argument(
        "--pattern", choices=sorted(PATTERNS), help="pattern known from another source"
    )
    s.add_argument("--team-size", type=int)
    s.add_argument("--max", type=int, default=3)
    s.set_defaults(fn=cmd_guess)
    s = sub.add_parser("status")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("reschedule", help="move a batch/company/address to a new time")
    s.add_argument("target")
    s.add_argument("--at", required=True)
    s.add_argument("--spread-minutes", type=int, default=0)
    s.set_defaults(fn=cmd_reschedule)
    s = sub.add_parser(
        "cancel", help="unschedule a batch/company/address; keeps the Gmail draft"
    )
    s.add_argument("target")
    s.set_defaults(fn=cmd_cancel)
    s = sub.add_parser(
        "discover",
        help="list YC companies by tag, Bay Area by default, minus known companies",
    )
    s.add_argument(
        "--tags", default="drug-discovery,biotech,machine-learning,generative-ai"
    )
    s.add_argument("--max-team", type=int, default=150)
    s.add_argument("--anywhere", action="store_true")
    s.add_argument("--include-known", action="store_true")
    s.set_defaults(fn=cmd_discover)
    s = sub.add_parser("doctor", help="check credentials, mailboxes, resumes, master")
    s.set_defaults(fn=cmd_doctor)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
