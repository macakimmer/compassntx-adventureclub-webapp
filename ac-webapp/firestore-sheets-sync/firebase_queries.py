"""
Firebase queries for:
  1. Total verses completed (all kids, right now — memory + bonus combined)
  2. Per-kid verse tracker, ALL program_groups in one combined report,
     sorted by group (Trackers1-4, then Scouts1-2, then Trailblazers1-2)
     and alphabetically by name within each group.

"Verses completed" — rules differ by program_group:
  - Any "Trackers" group: memory_verse_completed == 1 only (bonus ignored).
  - Everyone else (Scouts, Trailblazers): memory_verse_completed AND
    bonus_verse_completed both count, as separate tokens in the same list,
    e.g. "3, 3-Bonus, 4".
  Completion status is read as-is, with NO date filtering -- kids can cite
  a verse any time after the lesson week, so a completed flag counts
  regardless of what date is on that lesson_progress record.

"Missing" = lesson_numbers that (a) belong to that kid's program_group's
            curriculum (i.e. appear anywhere in that group's lesson_progress
            records), AND (b) have an occurred-by-today date in the
            `lesson_dates` collection (doc_id = lesson_number, field
            `date` = "YYYY-MM-DD" string), AND (c) are NOT a Review week
            (doc_id contains "Review") -- minus whatever that kid completed.

All "today" cutoffs use America/Chicago local time, not UTC.
"""

import re
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
from zoneinfo import ZoneInfo
import gspread
from google.oauth2.service_account import Credentials

cred = credentials.Certificate("firebase-key.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

CENTRAL = ZoneInfo("America/Chicago")
today = datetime.now(CENTRAL)
today_str = today.date().isoformat()

# --- Google Sheets setup ---
scopes = ["https://www.googleapis.com/auth/spreadsheets"]
gs_creds = Credentials.from_service_account_file("sheets-key.json", scopes=scopes)
gc = gspread.authorize(gs_creds)
spreadsheet = gc.open_by_key("1SGL2j82xX7r4hBTdi-MHvFTeluyOKw9BIO4hI0MlpAI")
SHEET_NAME = "AdvClub26-27 Verse-Summary"

# --- Program group sort order ---
GROUP_ORDER = [
    "Trackers1", "Trackers2", "Trackers3", "Trackers4",
    "Scouts1", "Scouts2",
    "Trailblazers1", "Trailblazers2",
]
GROUP_RANK = {name: i for i, name in enumerate(GROUP_ORDER)}


def get_or_create_worksheet(title, rows=200, cols=10):
    """Fetch a worksheet tab by name, creating it if it doesn't exist yet."""
    try:
        return spreadsheet.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)


def date_key(d):
    """Normalize a Firestore Timestamp (returned as datetime) or a date string
    to a plain 'YYYY-MM-DD' key in America/Chicago local time."""
    if hasattr(d, "date"):
        return d.astimezone(CENTRAL).date().isoformat()
    return str(d)[:10]


def _natural_sort_key(s):
    """Break a string into alternating digit/non-digit chunks so mixed
    identifiers sort sensibly (e.g. 'Y2L2' before 'Y2L10'), while staying
    directly comparable to plain numeric identifiers like '3' or '12'.
    Each chunk is tagged (0, int) or (1, str) so Python never has to compare
    an int to a str directly (which would raise a TypeError)."""
    chunks = re.findall(r"\d+|\D+", str(s))
    return tuple((0, int(c)) if c.isdigit() else (1, c) for c in chunks)


def verse_sort_key(token):
    """Sort verse tokens like '3' / '3-Bonus' / 'Y2L2' / 'Y2L2-Bonus' so they
    read in lesson order, memory verse before its bonus counterpart."""
    is_bonus = token.endswith("-Bonus")
    base = token[: -len("-Bonus")] if is_bonus else token
    return (_natural_sort_key(base), is_bonus)


def group_sort_key(program_group):
    """Known groups sort in GROUP_ORDER; anything unrecognized sorts after,
    alphabetically among themselves, rather than crashing."""
    return (GROUP_RANK.get(program_group, len(GROUP_ORDER)), program_group)


def occurred_lesson_numbers():
    """Lesson numbers (as strings, matching doc_id) from `lesson_dates` whose
    date has occurred on or before today, excluding any 'Review' week docs."""
    valid = set()
    skipped_no_date = []
    for doc in db.collection("lesson_dates").stream():
        lesson_number = doc.id
        if "review" in lesson_number.lower():
            continue
        raw_date = doc.to_dict().get("date")
        if raw_date is None:
            skipped_no_date.append(lesson_number)
            continue
        if date_key(raw_date) <= today_str:
            valid.add(lesson_number)

    if skipped_no_date:
        print(f"!! {len(skipped_no_date)} lesson_dates doc(s) missing a 'date' "
              f"field, skipped: {skipped_no_date}")

    return valid


# ---------------------------------------------------------------------------
# Query 1: Total verses completed, all kids, as of right now
# ---------------------------------------------------------------------------
def total_verses_completed():
    # Combined count: memory verses + bonus verses, across all kids.
    # No date filtering -- a completion flag counts whenever it was set.
    docs = db.collection("lesson_progress").stream()

    total = 0
    for doc in docs:
        d = doc.to_dict()
        if d.get("memory_verse_completed") == 1:
            total += 1
        if d.get("bonus_verse_completed") == 1:
            total += 1

    return total


# ---------------------------------------------------------------------------
# Query 2: Per-kid verse tracker, all program_groups combined
# ---------------------------------------------------------------------------
def verse_report():
    # Step 1: all kids, any program_group
    kids_ref = db.collection("kids").stream()
    kids = {}  # kid_id -> {"name": ..., "program_group": ...}
    for doc in kids_ref:
        d = doc.to_dict()
        kids[doc.id] = {"name": d.get("name", doc.id), "program_group": d.get("program_group", "")}

    # Step 2: authoritative "has this lesson happened by today, and it's not
    # a Review week" set, from lesson_dates
    occurred = occurred_lesson_numbers()

    # Step 3: pull ALL lesson_progress (no date filter -- completions count
    # whenever recorded). Build, per program_group, the set of lesson_numbers
    # that belong to that group's curriculum at all (any date), plus each
    # kid's own completed-token set.
    all_progress_docs = list(db.collection("lesson_progress").stream())

    group_lesson_numbers = {}  # program_group -> set of lesson_number strings seen for that group
    completed_by_kid = {kid_id: set() for kid_id in kids}
    skipped_docs = []

    for doc in all_progress_docs:
        d = doc.to_dict()
        lesson_number = d.get("lesson_number")
        if lesson_number is None:
            skipped_docs.append(doc.id)
            continue
        lesson_number = str(lesson_number)

        kid_id = d.get("kid_id")
        if kid_id not in kids:
            continue

        program_group = kids[kid_id]["program_group"]
        group_lesson_numbers.setdefault(program_group, set()).add(lesson_number)

        is_tracker = program_group.startswith("Trackers")

        if d.get("memory_verse_completed") == 1:
            completed_by_kid[kid_id].add(lesson_number)
        if not is_tracker and d.get("bonus_verse_completed") == 1:
            completed_by_kid[kid_id].add(f"{lesson_number}-Bonus")

    if skipped_docs:
        print(f"!! Skipped {len(skipped_docs)} lesson_progress doc(s) missing "
              f"a lesson_number field: {skipped_docs}")

    # Step 4: assemble report rows
    rows = []
    for kid_id, info in kids.items():
        program_group = info["program_group"]
        is_tracker = program_group.startswith("Trackers")
        completed = completed_by_kid.get(kid_id, set())

        # "assigned so far" = lessons belonging to this group's curriculum
        # AND that have actually occurred by today (per lesson_dates),
        # excluding Review weeks
        assigned = group_lesson_numbers.get(program_group, set()) & occurred

        possible = set()
        for lesson_number in assigned:
            possible.add(lesson_number)
            if not is_tracker:
                possible.add(f"{lesson_number}-Bonus")

        completed_sorted = sorted(completed, key=verse_sort_key)
        missing_sorted = sorted(possible - completed, key=verse_sort_key)

        rows.append({
            "name": info["name"],
            "program_group": program_group,
            "verses_completed_count": len(completed),
            "verses_completed": ", ".join(completed_sorted),
            "verses_missing": ", ".join(missing_sorted),
        })

    # Sort: program group order first (Trackers1-4, Scouts1-2, Trailblazers1-2),
    # then alphabetically by name within each group.
    rows.sort(key=lambda r: (group_sort_key(r["program_group"]), r["name"].lower()))

    return rows


# ---------------------------------------------------------------------------
# Write results to Google Sheets
# ---------------------------------------------------------------------------
def write_summary_tab(total):
    ws = get_or_create_worksheet("Summary")
    ws.clear()
    ws.append_row(["Metric", "Value", "As of"])
    ws.append_row(["Total verses completed (all kids)", total, today.strftime("%Y-%m-%d %H:%M %Z")])


def write_verse_report_tab(rows):
    ws = get_or_create_worksheet("Verse Report")
    ws.clear()
    ws.append_row(["Program Group", "Name", "Verses Completed Count", "Verses Completed", "Verses Missing"])
    if rows:
        ws.append_rows([
            [r["program_group"], r["name"], r["verses_completed_count"], r["verses_completed"], r["verses_missing"]]
            for r in rows
        ])
        apply_group_shading(ws, rows)


# Light highlight colors (0-1 float RGB) for even-numbered groups.
# Unlisted groups (Trackers1/3, Scouts1, Trailblazers1) stay unshaded.
GROUP_HIGHLIGHT_COLORS = {
    "Trackers2": {"red": 0.956, "green": 0.800, "blue": 0.800},      # light red
    "Trackers4": {"red": 0.956, "green": 0.800, "blue": 0.800},      # light red
    "Scouts2": {"red": 0.851, "green": 0.918, "blue": 0.827},        # light green
    "Trailblazers2": {"red": 0.812, "green": 0.886, "blue": 0.953},  # light blue
}


def apply_group_shading(ws, rows):
    """Shade contiguous blocks of rows by program_group. Rows are already
    sorted by group, so each group occupies one unbroken range of rows --
    this finds those ranges and applies one format call per shaded group
    (rather than one call per row, which would be far slower)."""
    NUM_COLS = 5  # Program Group, Name, Verses Completed Count, Verses Completed, Verses Missing
    last_col_letter = chr(ord("A") + NUM_COLS - 1)  # "E"

    start_row = 2  # row 1 is the header
    current_group = rows[0]["program_group"]

    def maybe_format(group, first_row, last_row):
        color = GROUP_HIGHLIGHT_COLORS.get(group)
        if color:
            ws.format(f"A{first_row}:{last_col_letter}{last_row}", {"backgroundColor": color})

    row_num = start_row
    for r in rows:
        if r["program_group"] != current_group:
            maybe_format(current_group, start_row, row_num - 1)
            current_group = r["program_group"]
            start_row = row_num
        row_num += 1

    # format the final block
    maybe_format(current_group, start_row, row_num - 1)


if __name__ == "__main__":
    total = total_verses_completed()
    print("Total verses completed (all kids):", total)
    write_summary_tab(total)

    rows = verse_report()
    for row in rows:
        print(row)
    write_verse_report_tab(rows)

    print(f"\nSynced to Google Sheet '{SHEET_NAME}': Summary + Verse Report tabs updated.")