"""Memory verse tracker for a weekly kids ministry program."""

import os
import csv
import base64
import html
import json
import re
from pathlib import Path
from datetime import date
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from fpdf import FPDF
from google.cloud import firestore

APP_TITLE = os.environ.get("APP_TITLE", "Adventure Club Memory Verse Tracker")
TEACHER_PIN = os.environ.get("TEACHER_PIN", "ac2627")
ESV_API_TOKEN = os.environ.get("ESV_API_TOKEN")
LESSONS_PATH = Path(__file__).with_name("lessons_2026_2027.csv")
KIDS_PATH = Path(__file__).with_name("kids.json")
WATERMARK_PATH = Path(__file__).with_name("AC-Logo-4c-RGB.png")
CALENDAR_ID = os.environ.get(
    "GOOGLE_CALENDAR_ID",
    "c_d659594b907cdc6b6b3e82b1c5f82f74249ecfd4abacbbfc3119fb6e34cfa6ee@group.calendar.google.com",
)
CALENDAR_TIMEZONE = os.environ.get("CALENDAR_TIMEZONE", "America/Chicago")

st.set_page_config(page_title=APP_TITLE, page_icon="📖", layout="wide")


@st.cache_data
def load_watermark_data():
    return base64.b64encode(WATERMARK_PATH.read_bytes()).decode("ascii")


st.markdown(
    f"""
    <style>
    [data-testid="stAppViewContainer"]::before {{
        background-image: url("data:image/png;base64,{load_watermark_data()}");
        background-position: center center;
        background-repeat: no-repeat;
        background-size: min(616px, 68.2vw);
        content: "";
        inset: 0;
        opacity: 0.075;
        pointer-events: none;
        position: fixed;
        z-index: 0;
    }}
    [data-testid="stAppViewContainer"] > .main {{
        position: relative;
        z-index: 1;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def get_db():
    return firestore.Client()


try:
    db = get_db()
except Exception:
    st.error("Firestore is not connected. Configure Google credentials or a Firestore emulator.")
    st.stop()


def collection(name):
    return db.collection(name)


def records(name, order_by=None, descending=False):
    query = collection(name)
    if order_by:
        direction = firestore.Query.DESCENDING if descending else firestore.Query.ASCENDING
        query = query.order_by(order_by, direction=direction)
    rows = []
    for snapshot in query.stream():
        row = snapshot.to_dict()
        row["id"] = snapshot.id
        rows.append(row)
    return rows


def add_record(name, values):
    return collection(name).add(values)[1].id


def update_record(name, record_id, values):
    collection(name).document(record_id).set(values, merge=True)


def delete_record(name, record_id):
    collection(name).document(record_id).delete()

def sync_kids_from_json():
    """Syncs the local kids.json data into Firestore, preserving existing records."""
    if not KIDS_PATH.exists():
        return
    with KIDS_PATH.open(encoding="utf-8") as f:
        roster = json.load(f)
    
    existing_kids = {k["name"]: k for k in records("kids")}
    for group_name, kids_list in roster.items():
        for kid_data in kids_list:
            existing = existing_kids.get(kid_data["name"])
            values = {
                "name": kid_data["name"],
                "class_group": kid_data.get("game_group") or None,
                "program_group": group_name,
                "emergency_contact": kid_data.get("emergency_contact", ""),
                "medical_notes": kid_data.get("medical_notes", ""),
                "active": 1,
            }
            if existing:
                update_record("kids", existing["id"], values)
            else:
                add_record("kids", {
                    **values,
                })

@st.cache_resource(show_spinner="Syncing roster from local file...")
def run_startup_sync():
    """Runs once when the app server starts to ensure data is synced."""
    sync_kids_from_json()

#run_startup_sync()


@st.cache_data
def load_lessons(file_path, modified_time):
    with Path(file_path).open(newline="", encoding="utf-8") as lessons_file:
        return list(csv.DictReader(lessons_file))


def current_lessons():
    return load_lessons(str(LESSONS_PATH), LESSONS_PATH.stat().st_mtime_ns)


@st.cache_data
def load_roster_groups():
    with KIDS_PATH.open(encoding="utf-8") as kids_file:
        roster = json.load(kids_file)
    return {
        kid["name"]: group.removesuffix("1").removesuffix("2").removesuffix("3").removesuffix("4")
        for group, kids in roster.items()
        for kid in kids
    }


@st.cache_data(ttl=86400, show_spinner=False)
def get_esv_text(reference):
    if not ESV_API_TOKEN:
        return "Set ESV_API_TOKEN to load ESV text."
    response = requests.get(
        "https://api.esv.org/v3/passage/text/",
        headers={"Authorization": f"Token {ESV_API_TOKEN}"},
        params={
            "q": reference,
            "include-headings": "false",
            "include-footnotes": "false",
            "include-verse-numbers": "true",
            "include-short-copyright": "true",
            "indent-poetry": "false",
            "indent-paragraphs": "false",
            "line-length": "0",
        },
        timeout=10,
    )
    if response.status_code != 200:
        return "ESV text is unavailable. Check the ESV_API_TOKEN configuration."
    passages = response.json().get("passages", [])
    return passages[0].strip() if passages else "ESV text was not found."


ESV_BOOK_NAMES = {
    "1 Jn": "1 John",
    "1 Pet": "1 Peter",
    "1 Th": "1 Thessalonians",
    "1 Cor": "1 Corinthians",
    "2 Cor": "2 Corinthians",
    "2 Pet": "2 Peter",
    "2 Th": "2 Thessalonians",
    "2 Jn": "2 John",
    "2 Tim": "2 Timothy",
    "3 Jn": "3 John",
    "Acts": "Acts",
    "Col": "Colossians",
    "Eph": "Ephesians",
    "Gen": "Genesis",
    "Heb": "Hebrews",
    "Isa": "Isaiah",
    "Jer": "Jeremiah",
    "Jn": "John",
    "John": "John",
    "Job": "Job",
    "Lev": "Leviticus",
    "Luke": "Luke",
    "Mark": "Mark",
    "Matt": "Matthew",
    "Php": "Philippians",
    "Phil": "Philippians",
    "Ps": "Psalms",
    "Rom": "Romans",
    "Rev": "Revelation",
}


def expanded_verse_reference(reference):
    for abbreviation in sorted(ESV_BOOK_NAMES, key=len, reverse=True):
        if reference == abbreviation or reference.startswith(f"{abbreviation} "):
            return ESV_BOOK_NAMES[abbreviation] + reference[len(abbreviation):]
    return reference


def esv_reference(reference, class_name="esv-reference"):
    if not reference:
        return ""
    display_reference = expanded_verse_reference(reference)
    text = html.escape(get_esv_text(display_reference))
    label = html.escape(display_reference)
    tooltip_class = f"{class_name}-tooltip"
    return f'<span class="{class_name}" tabindex="0" aria-label="{label}">{label}<span class="{tooltip_class}" role="tooltip">{text}</span></span>'


def render_lesson_reference():
    st.subheader("2026-2027 Lesson Reference")
    st.caption("Hover over a verse reference to preview the ESV text. On touch devices, tap the reference.")
    st.markdown(
        """
        <style>
        .lesson-table { border-collapse: collapse; color: #162b33 !important; width: 100%; font-size: 0.92rem; }
        .lesson-table th, .lesson-table td { border: 1px solid #9eafb8; color: #162b33 !important; padding: 0.65rem; text-align: left; vertical-align: top; }
        .lesson-table td * { color: #162b33 !important; }
        .lesson-table thead { display: table-header-group; }
        .lesson-table th { background: #dce8ec !important; color: #162b33 !important; font-size: 0.85rem; font-weight: 700; white-space: nowrap; }
        .esv-reference { color: #075985 !important; cursor: help; position: relative; text-decoration: underline dotted; }
        .esv-reference .esv-reference-tooltip { background: #1f2937; border: 0; border-radius: 0.35rem; bottom: 1.6rem; color: white !important; display: none !important; font-size: 0.85rem; font-weight: normal; left: 0; line-height: 1.45; padding: 0.7rem; position: absolute; white-space: normal; width: min(28rem, 70vw); z-index: 10; }
        .esv-reference:hover .esv-reference-tooltip, .esv-reference:focus .esv-reference-tooltip { display: block !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    rows = []
    for lesson in current_lessons():
        rows.append(
            "<tr>"
            f"<td>{html.escape(lesson['Date'])}</td>"
            f"<td>{html.escape(lesson['Lesson Number'])}</td>"
            f"<td>{html.escape(lesson['Lesson Title'])}</td>"
            f"<td>{html.escape(lesson['Catechism Q'])}</td>"
            f"<td>{html.escape(lesson['Central Truth'])}</td>"
            f"<td>{esv_reference(lesson['Memory Verse'])}</td>"
            f"<td>{esv_reference(lesson['Bonus Verse'])}</td>"
            "</tr>"
        )
    st.markdown(
        "<table class=\"lesson-table\"><thead><tr>"
        "<th>Date</th><th>Lesson</th><th>Title</th><th>Catechism Q</th>"
        "<th>Central Truth</th><th>Memory Verse</th><th>Bonus Verse</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>",
        unsafe_allow_html=True,
    )


def render_calendar():
    st.subheader("Kids Ministry Calendar")
    st.caption("Select an event to view its details or add it to your calendar.")
    calendar_url = (
        "https://calendar.google.com/calendar/embed?"
        f"src={quote(CALENDAR_ID, safe='')}&ctz={quote(CALENDAR_TIMEZONE, safe='')}"
    )
    components.html(
        f"""
        <iframe
            src="{calendar_url}"
            style="border: 0; width: 100%; height: 800px;"
            frameborder="0"
            scrolling="no"
            title="Kids Ministry Calendar">
        </iframe>
        """,
        height=820,
        scrolling=True,
    )


def progress_rows():
    kids = {row["id"]: row for row in records("kids")}
    verses = {row["id"]: row for row in records("verses")}
    rows = []
    for progress in records("progress"):
        kid = kids.get(progress.get("kid_id"))
        verse = verses.get(progress.get("verse_id"))
        if kid and verse:
            program_group = kid.get("program_group") or load_roster_groups().get(kid.get("name"), "Unknown")
            rows.append({
                "kid_id": progress.get("kid_id"),
                "name": kid.get("name", ""),
                "class_group": kid.get("class_group"),
                "program_group": program_group,
                "reference": verse.get("reference", ""),
                "week_date": verse.get("week_date", ""),
                "memorized": int(progress.get("memorized", 0)),
                "date_recorded": progress.get("date_recorded"),
            })
    return pd.DataFrame(rows, columns=["kid_id", "name", "class_group", "program_group", "reference", "week_date", "memorized", "date_recorded"])


def lesson_progress_rows(kid_id):
    lesson_records = {
        row["id"]: row for row in records("lesson_progress")
        if row.get("kid_id") == kid_id
    }
    attendance_records = {
        row.get("date_recorded"): row for row in records("attendance")
        if row.get("kid_id") == kid_id
    }
    rows = []
    for lesson in current_lessons():
        progress = lesson_records.get(lesson_progress_id(kid_id, lesson["Lesson Number"]), {})
        attendance = attendance_records.get(lesson["Date"], {})
        rows.append({
            "Week": f"{lesson['Date']} · {lesson['Lesson Number']}",
            "Attendance": "Completed" if attendance.get("present", 0) else "Incomplete",
            "Central Truth": "Completed" if progress.get("central_truth_completed", 0) else "Incomplete",
            "Memory Verse": "Completed" if progress.get("memory_verse_completed", 0) else "Incomplete",
            "Bonus Verse": "Completed" if progress.get("bonus_verse_completed", 0) else "Incomplete",
        })
    return pd.DataFrame(rows)


def build_progress_snapshot():
    kids = records("kids", "name")
    verses = {row["id"]: row for row in records("verses")}
    progress_by_kid = {kid["id"]: {} for kid in kids}
    for progress in records("progress"):
        kid_id = progress.get("kid_id")
        verse = verses.get(progress.get("verse_id"))
        if kid_id in progress_by_kid and verse:
            progress_by_kid[kid_id][progress["verse_id"]] = {
                "reference": verse.get("reference", ""),
                "week_date": verse.get("week_date", ""),
                "memorized": bool(progress.get("memorized", 0)),
                "date_recorded": progress.get("date_recorded"),
            }
    roster_groups = load_roster_groups()
    return {
        "schema_version": 1,
        "school_year": "2026-2027",
        "source_of_truth": "Firestore progress collection",
        "kids": [
            {
                "kid_id": kid["id"],
                "name": kid.get("name", ""),
                "program_group": kid.get("program_group") or roster_groups.get(kid.get("name"), "Unknown"),
                "progress": progress_by_kid[kid["id"]],
            }
            for kid in kids
        ],
    }


def pin_tracker_rows(kids):
    regular_counts = {}
    for progress in records("progress"):
        if progress.get("memorized", 0):
            kid_id = progress.get("kid_id")
            regular_counts[kid_id] = regular_counts.get(kid_id, 0) + 1

    bonus_counts = {}
    for progress in records("lesson_progress"):
        if progress.get("bonus_verse_completed", 0):
            kid_id = progress.get("kid_id")
            bonus_counts[kid_id] = bonus_counts.get(kid_id, 0) + 1

    rows = []
    for kid in kids:
        completed_verses = regular_counts.get(kid["id"], 0) + bonus_counts.get(kid["id"], 0)
        rows.append({
            "Name": kid.get("name", ""),
            "Completed Verses": completed_verses,
            "Pins Received": completed_verses // 5,
            "Verses Until Next Pin": 5 - (completed_verses % 5),
        })
    return rows


def progress_id(kid_id, verse_id):
    return f"{kid_id}_{verse_id}"


def lesson_progress_id(kid_id, lesson_number):
    return f"{kid_id}_{lesson_number}"


def render_reference_label(label, reference):
    return f'<span title="{html.escape(reference, quote=True)}">{html.escape(label)}</span>'


def emergency_contact_html(contact):
    escaped_contact = html.escape(contact or "")

    def phone_link(match):
        phone = match.group(0)
        digits = re.sub(r"\D", "", phone)
        return f'<a href="tel:{digits}">{phone}</a>'

    return re.sub(r"(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]\d{3}[\s.-]\d{4}", phone_link, escaped_contact)



st.sidebar.header("🔑 Teacher Portal")
teacher_mode = st.sidebar.checkbox("Enable Teacher Mode")
if teacher_mode:
    pin = st.sidebar.text_input("Enter Teacher PIN", type="password")
    is_verified = pin == TEACHER_PIN
    if not is_verified and pin:
        st.sidebar.error("Incorrect PIN")
else:
    is_verified = False

st.title(f"📖 {APP_TITLE}")
page = st.sidebar.radio("Navigate", ["Dashboard", "Record Progress", "Game Groups", "Roster", "Lesson Reference", "Calendar", "Manage Kids"])

if page == "Calendar":
    render_calendar()

elif page == "Lesson Reference":
    render_lesson_reference()

elif page == "Dashboard":
    kids = records("kids")
    verses = records("verses")
    prog_df = progress_rows()
    if is_verified:
        st.download_button(
            "Download current progress JSON",
            data=json.dumps(build_progress_snapshot(), indent=2),
            file_name="progress.json",
            mime="application/json",
        )
    roster_groups = load_roster_groups()
    for kid in kids:
        kid["program_group"] = kid.get("program_group") or roster_groups.get(kid.get("name"), "Unknown")
    class_order = {"Trackers": 0, "Scouts": 1, "Trailblazers": 2}
    groups = sorted(
        {kid.get("program_group") for kid in kids if kid.get("program_group")},
        key=lambda class_name: (
            class_order.get(class_name.rstrip("1234567890"), len(class_order)),
            int(class_name[len(class_name.rstrip("1234567890")): ] or 0),
            class_name,
        ),
    )
    groups.insert(0, "All")
    group_filter = st.selectbox("Group", groups)
    filtered_kids = [kid for kid in kids if group_filter == "All" or kid.get("program_group") == group_filter]
    filtered_kid_ids = {kid["id"] for kid in filtered_kids}
    filtered_prog_df = prog_df[prog_df["kid_id"].isin(filtered_kid_ids)].copy() if not prog_df.empty else prog_df
    st.subheader("Kids")
    active_filtered_kids = sorted((kid for kid in filtered_kids if kid.get("active", 1)), key=lambda kid: kid["name"])
    for start in range(0, len(active_filtered_kids), 4):
        buttons = st.columns(4)
        for button, kid in zip(buttons, active_filtered_kids[start:start + 4]):
            if button.button(kid["name"], key=f"dashboard_kid_{kid['id']}"):
                st.session_state["dashboard_kid_id"] = kid["id"]
    st.subheader("Pin Tracker")
    st.caption("Kids receive one pin for every five memorized regular or bonus verses.")
    st.dataframe(
        pd.DataFrame(pin_tracker_rows(active_filtered_kids)),
        use_container_width=True,
        hide_index=True,
    )
    col1, col2, col3 = st.columns(3)
    col1.metric("Active Kids", sum(1 for kid in filtered_kids if kid.get("active", 1)))
    col2.metric("Verses Assigned", len(verses))
    if not filtered_prog_df.empty:
        col3.metric("Overall Memorized %", f"{round(100 * filtered_prog_df['memorized'].mean())}%")
        st.subheader("Progress by Kid")
        summary = filtered_prog_df.groupby(["kid_id", "name", "class_group"])["memorized"].agg(memorized="sum", total="count").reset_index()
        summary["% memorized"] = (100 * summary["memorized"] / summary["total"]).round().astype(int)
        summary = summary.sort_values("name")
        st.dataframe(summary.drop(columns=["kid_id"]), use_container_width=True, hide_index=True)
        selected_kid_id = st.session_state.get("dashboard_kid_id")
        if selected_kid_id in filtered_kid_ids:
            selected_kid = next(kid for kid in filtered_kids if kid["id"] == selected_kid_id)
            st.subheader(f"{selected_kid['name']}'s Verse Progress")
            kid_progress = filtered_prog_df[filtered_prog_df["kid_id"] == selected_kid_id].copy()
            if kid_progress.empty:
                st.info("No progress recorded yet for this kid.")
            else:
                kid_progress["memorized"] = kid_progress["memorized"].map({1: "✅ Memorized", 0: "⏳ In progress"})
                st.dataframe(kid_progress[["reference", "week_date", "memorized", "date_recorded"]], use_container_width=True, hide_index=True)
            st.subheader("Lesson Progress")
            st.dataframe(lesson_progress_rows(selected_kid_id), use_container_width=True, hide_index=True)
        st.subheader("All Records")
        display_df = filtered_prog_df.copy()
        display_df["memorized"] = display_df["memorized"].map({1: "✅ Memorized", 0: "⏳ In progress"})
        st.dataframe(display_df, use_container_width=True, hide_index=True)
    else:
        col3.metric("Overall Memorized %", "0%")
        selected_kid_id = st.session_state.get("dashboard_kid_id")
        if selected_kid_id in filtered_kid_ids:
            selected_kid = next(kid for kid in filtered_kids if kid["id"] == selected_kid_id)
            st.subheader(f"{selected_kid['name']}'s Verse Progress")
            st.info("No progress recorded yet for this kid.")
            st.subheader("Lesson Progress")
            st.dataframe(lesson_progress_rows(selected_kid_id), use_container_width=True, hide_index=True)
        else:
            st.info("No progress recorded yet. Add kids and verses, then use **Record Progress**.")

elif page == "Manage Kids":
    if not is_verified:
        st.warning("Enable Teacher Mode and enter the teacher PIN to manage kids.")
    else:
        st.subheader("Data Sync")
        if st.button("📥 Manual Sync from kids.json"):
            sync_kids_from_json()
            st.success("Sync complete! New kids from the JSON file have been added.")
            st.rerun()

        st.subheader("Add a Kid")
        with st.form("add_kid", clear_on_submit=True):
            name = st.text_input("Name")
            class_group = st.text_input("Class / Group (optional)")
            program_group = st.selectbox("Program", ["Trackers", "Scouts", "Trailblazers"])
            if st.form_submit_button("Add Kid"):
                if name.strip():
                    add_record("kids", {"name": name.strip(), "class_group": class_group.strip() or None, "program_group": program_group, "active": 1})
                    st.success(f"Added {name}.")
                    st.rerun()
                else:
                    st.warning("Please enter a name.")
        st.subheader("Current Kids")
        for kid in records("kids", "name"):
            c1, c2, c3, c4 = st.columns([3, 2, 1.5, 1.5])
            c1.write(kid["name"])
            c2.write(kid.get("class_group") or "-")
            c3.write("Active" if kid.get("active", 1) else "Inactive")
            if c4.button("Deactivate" if kid.get("active", 1) else "Reactivate", key=f"toggle_{kid['id']}"):
                update_record("kids", kid["id"], {"active": 0 if kid.get("active", 1) else 1})
                st.rerun()

elif page == "Record Progress":
    all_kids = [kid for kid in records("kids", "name") if kid.get("active", 1)]
    class_order = {"Trackers": 0, "Scouts": 1, "Trailblazers": 2}
    classes = sorted(
        {kid.get("program_group") for kid in all_kids if kid.get("program_group")},
        key=lambda class_name: (
            class_order.get(class_name.rstrip("1234567890"), len(class_order)),
            int(class_name[len(class_name.rstrip("1234567890")): ] or 0),
            class_name,
        ),
    )
    classes.append("All")
    if len(classes) == 1:
        st.info("Sync the roster or add a kid with a program group first.")
    else:
        selected_class = st.selectbox("Group", classes)
        class_kids = all_kids if selected_class == "All" else [kid for kid in all_kids if kid.get("program_group") == selected_class]

        selected_kid_id = st.selectbox(
            "Kid progress",
            [kid["id"] for kid in class_kids],
            format_func=lambda kid_id: next(kid["name"] for kid in class_kids if kid["id"] == kid_id),
        )
        existing_lessons = {
            row["id"]: row for row in records("lesson_progress")
            if row.get("kid_id") == selected_kid_id
        }
        existing_attendance = {
            row.get("date_recorded"): row for row in records("attendance")
            if row.get("kid_id") == selected_kid_id
        }
        bonus_classes = {"Scouts1", "Scouts2", "Trailblazers1", "Trailblazers2"}
        selected_kid = next(kid for kid in class_kids if kid["id"] == selected_kid_id)
        bonus_enabled = selected_kid.get("program_group") in bonus_classes
        bonus_column_visible = selected_class not in {"Trackers1", "Trackers2", "Trackers3", "Trackers4"}

        st.markdown('<div id="record-progress-top"></div>', unsafe_allow_html=True)
        st.subheader("Progress Chart")
        st.caption("Hover over a week, central truth, or verse reference to see its full reference.")
        st.markdown(
            """
            <style>
            .progress-grid { overflow-x: auto; }
            .progress-grid-header, .progress-grid-row { align-items: center; display: grid; gap: .5rem; min-width: 760px; }
            .progress-grid-header.has-bonus, .progress-grid-row.has-bonus { grid-template-columns: 2.3fr .55fr .55fr 1.8fr .55fr 1.2fr .55fr 1.2fr; min-width: 900px; }
            .progress-grid-header.no-bonus, .progress-grid-row.no-bonus { grid-template-columns: 2.3fr .55fr .55fr 1.8fr .55fr 1.2fr; }
            .progress-grid-header { background: #dce8ec; border: 1px solid #81959e; color: #162b33; font-weight: 700; padding: .55rem; }
            .progress-grid-row { border-bottom: 1px solid #9eafb8; padding: .35rem .55rem; }
            .progress-grid-row span[title], .progress-esv-reference { cursor: help; text-decoration: underline dotted; }
            .progress-esv-reference { color: #075985 !important; display: inline-block; position: relative; }
            .progress-esv-reference .progress-esv-reference-tooltip { background: #1f2937; border-radius: .35rem; bottom: 1.6rem; color: white; display: none !important; font-size: .85rem; font-weight: normal; left: 0; line-height: 1.45; padding: .7rem; position: absolute; white-space: normal; width: min(28rem, 70vw); z-index: 10; }
            .progress-esv-reference:hover .progress-esv-reference-tooltip, .progress-esv-reference:focus .progress-esv-reference-tooltip { display: block !important; }
            [data-testid="stForm"] [data-testid="stHorizontalBlock"] { border-bottom: 1px solid #81959e; border-left: 1px solid #81959e; border-right: 1px solid #81959e; padding: .35rem .25rem; }
            [data-testid="stForm"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { border-right: 1px solid #9eafb8; min-height: 2.5rem; padding: 0 .35rem; }
            [data-testid="stForm"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child { border-right: 0; }
            [data-testid="stFormSubmitButton"] { bottom: 1rem; position: fixed; right: 2rem; z-index: 1000; }
            [data-testid="stFormSubmitButton"] button { box-shadow: 0 3px 12px rgba(22, 43, 51, .28); }
            .record-progress-top-link { background: #356b70; border-radius: .35rem; color: white !important; display: inline-block; font-weight: 700; margin: 1rem 0 2rem; padding: .55rem 1rem; text-decoration: none !important; }
            .record-progress-top-link:hover { background: #285257; }
            </style>
            """,
            unsafe_allow_html=True,
        )
        with st.form("record_lesson_progress"):
            st.markdown(
                f"<div class='progress-grid'><div class='progress-grid-header {'has-bonus' if bonus_column_visible else 'no-bonus'}'><span>Week</span><span>Attendance</span><span></span><span>Central Truth</span><span></span><span>Memory Verse</span>{'<span></span><span>Bonus Verse</span>' if bonus_column_visible else ''}</div>",
                unsafe_allow_html=True,
            )
            lesson_results = {}
            for lesson in current_lessons():
                lesson_id = lesson["Lesson Number"]
                is_review_week = lesson_id.startswith("Review")
                existing = existing_lessons.get(lesson_progress_id(selected_kid_id, lesson_id), {})
                lesson_date = lesson["Date"]
                central_truth_key = f"central_{selected_kid_id}_{lesson_id}"
                memory_key = f"memory_{selected_kid_id}_{lesson_id}"
                bonus_key = f"bonus_{selected_kid_id}_{lesson_id}"
                attendance_key = f"lesson_attendance_{selected_kid_id}_{lesson_id}"
                column_widths = [2.3, .55, .55, 1.8, .55, 1.2, .55, 1.2] if bonus_column_visible else [2.3, .55, .55, 1.8, .55, 1.2]
                row_cols = st.columns(column_widths)
                row_cols[0].markdown(render_reference_label(f"{lesson['Date']} · {lesson_id} · {lesson['Lesson Title']}", lesson["Lesson Title"]), unsafe_allow_html=True)
                attendance_checked = row_cols[1].checkbox("Present", value=bool(existing_attendance.get(lesson_date, {}).get("present", 0)), key=attendance_key, label_visibility="collapsed")
                if is_review_week:
                    central_checked = False
                    memory_checked = False
                    bonus_checked = False
                    for placeholder in row_cols[2:]:
                        placeholder.markdown("&nbsp;", unsafe_allow_html=True)
                else:
                    central_checked = row_cols[2].checkbox("Done", value=bool(existing.get("central_truth_completed", 0)), key=central_truth_key, label_visibility="collapsed")
                    row_cols[3].markdown(render_reference_label(lesson["Central Truth"], lesson["Central Truth"]), unsafe_allow_html=True)
                    memory_checked = row_cols[4].checkbox("Done", value=bool(existing.get("memory_verse_completed", 0)), key=memory_key, label_visibility="collapsed")
                    row_cols[5].markdown(esv_reference(lesson["Memory Verse"], "progress-esv-reference"), unsafe_allow_html=True)
                    if bonus_column_visible:
                        bonus_checked = row_cols[6].checkbox("Done", value=bool(existing.get("bonus_verse_completed", 0)), key=bonus_key, label_visibility="collapsed", disabled=not bonus_enabled)
                        row_cols[7].markdown(esv_reference(lesson["Bonus Verse"], "progress-esv-reference"), unsafe_allow_html=True)
                    else:
                        bonus_checked = False
                lesson_results[lesson_id] = {"attendance": attendance_checked, "central": central_checked, "memory": memory_checked, "bonus": bonus_checked, "date": lesson_date}
            st.markdown("</div>", unsafe_allow_html=True)
            if st.form_submit_button("Save Lesson Progress"):
                recorded_on = date.today().isoformat()
                for lesson_id, result in lesson_results.items():
                    update_record(
                        "attendance",
                        f"att_{selected_kid_id}_{result['date']}",
                        {"kid_id": selected_kid_id, "date_recorded": result["date"], "present": int(result["attendance"])},
                    )
                    update_record(
                        "lesson_progress",
                        lesson_progress_id(selected_kid_id, lesson_id),
                        {
                            "kid_id": selected_kid_id,
                            "lesson_number": lesson_id,
                            "central_truth_completed": int(result["central"]),
                            "memory_verse_completed": int(result["memory"]),
                            "bonus_verse_completed": int(result["bonus"]) if bonus_enabled else 0,
                            "date_recorded": recorded_on,
                        },
                    )
                st.success("30-week progress saved.")
                st.rerun()
            st.markdown('<a class="record-progress-top-link" href="#record-progress-top">Top</a>', unsafe_allow_html=True)

elif page == "Game Groups":
    kids = [kid for kid in records("kids", "name") if kid.get("active", 1)]
    rotation = st.selectbox("Rotation", ["Trackers", "Scouts/Trailblazers"])
    game_group = st.selectbox("Game Group", ["All", "Blue", "Green", "Red", "Yellow"])

    if rotation == "Trackers":
        rotation_kids = [kid for kid in kids if kid.get("program_group", "").startswith("Trackers")]
    else:
        rotation_kids = [kid for kid in kids if not kid.get("program_group", "").startswith("Trackers")]

    if game_group == "All":
        visible_kids = rotation_kids
    else:
        visible_kids = [kid for kid in rotation_kids if (kid.get("class_group") or "").lower() == game_group.lower()]
    visible_kids.sort(key=lambda kid: kid.get("name", "").casefold())

    st.subheader(f"{rotation} Game Groups")
    st.caption(f"{len(visible_kids)} kids")
    if not visible_kids:
        st.info("No kids match the selected filters.")
    else:
        group_rows = [
            {
                "Name": kid.get("name", ""),
                "Game Group": (kid.get("class_group") or "").title() or "Unassigned",
                "Class": kid.get("program_group", "") or "Unassigned",
            }
            for kid in visible_kids
        ]
        st.dataframe(pd.DataFrame(group_rows), use_container_width=True, hide_index=True)

elif page == "Roster":
    if not is_verified:
        st.warning("Enable Teacher Mode and enter the teacher PIN to view the roster.")
    else:
        kids = [kid for kid in records("kids", "name") if kid.get("active", 1)]
        class_order = {"Trackers": 0, "Scouts": 1, "Trailblazers": 2}
        groups = sorted(
            {kid.get("program_group") for kid in kids if kid.get("program_group")},
            key=lambda group: (
                class_order.get(group.rstrip("1234567890"), len(class_order)),
                int(group[len(group.rstrip("1234567890")): ] or 0),
                group,
            ),
        )
        groups.insert(0, "All")
        selected_group = st.selectbox("Group", groups, index=0)
        visible_kids = [
            kid for kid in kids
            if selected_group == "All" or kid.get("program_group") == selected_group
        ]
        visible_kids.sort(key=lambda kid: kid.get("name", "").casefold())

        st.subheader("Kids Roster")
        st.caption(f"{len(visible_kids)} kids")
        roster_rows = [
            "<tr>"
            f"<td>{html.escape(kid.get('name', ''))}</td>"
            f"<td>{emergency_contact_html(kid.get('emergency_contact', ''))}</td>"
            f"<td>{html.escape(kid.get('medical_notes', '') or 'None')}</td>"
            f"<td>{html.escape((kid.get('class_group') or 'Unassigned').title())}</td>"
            "</tr>"
            for kid in visible_kids
        ]
        st.markdown(
            """
            <style>
            .roster-table { border-collapse: collapse; color: #162b33 !important; width: 100%; }
            .roster-table th, .roster-table td { border: 1px solid #9eafb8; color: #162b33 !important; padding: .65rem; text-align: left; vertical-align: top; }
            .roster-table td * { color: #162b33 !important; }
            .roster-table th { background: #dce8ec !important; color: #162b33 !important; font-weight: 700; }
            .roster-table a { color: #162b33 !important; font-weight: 600; }
            </style>
            """
            "<table class='roster-table'><thead><tr>"
            "<th>Name</th><th>Emergency Contact</th><th>Medical Notes</th><th>Game Group</th>"
            f"</tr></thead><tbody>{''.join(roster_rows)}</tbody></table>",
            unsafe_allow_html=True,
        )
