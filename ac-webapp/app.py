"""Memory verse tracker for a weekly kids ministry program."""

import os
import csv
import html
import json
from pathlib import Path
from datetime import date
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from fpdf import FPDF
from google.cloud import firestore

APP_TITLE = os.environ.get("APP_TITLE", "Memory Verse Tracker")
TEACHER_PIN = os.environ.get("TEACHER_PIN", "ac2627")
ESV_API_TOKEN = os.environ.get("ESV_API_TOKEN")
LESSONS_PATH = Path(__file__).with_name("lessons_2026_2027.csv")
KIDS_PATH = Path(__file__).with_name("kids.json")
CALENDAR_ID = os.environ.get(
    "GOOGLE_CALENDAR_ID",
    "c_d659594b907cdc6b6b3e82b1c5f82f74249ecfd4abacbbfc3119fb6e34cfa6ee@group.calendar.google.com",
)
CALENDAR_TIMEZONE = os.environ.get("CALENDAR_TIMEZONE", "America/Los_Angeles")

st.set_page_config(page_title=APP_TITLE, page_icon="📖", layout="wide")


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


@st.cache_data
def load_lessons():
    with LESSONS_PATH.open(newline="", encoding="utf-8") as lessons_file:
        return list(csv.DictReader(lessons_file))


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
            "include-verse-numbers": "false",
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


def esv_reference(reference):
    text = html.escape(get_esv_text(reference))
    label = html.escape(reference)
    return f'<span class="esv-reference" tabindex="0">{label}<span class="esv-tooltip">{text}</span></span>'


def render_lesson_reference():
    st.subheader("2026-2027 Lesson Reference")
    st.caption("Hover over a verse reference to preview the ESV text. On touch devices, tap the reference.")
    st.markdown(
        """
        <style>
        .lesson-table { border-collapse: collapse; width: 100%; font-size: 0.92rem; }
        .lesson-table th, .lesson-table td { border-bottom: 1px solid #d9dee7; padding: 0.65rem; text-align: left; vertical-align: top; }
        .lesson-table th { background: #f3f6fa; }
        .esv-reference { color: #1261a0; cursor: help; position: relative; text-decoration: underline dotted; }
        .esv-tooltip { background: #1f2937; border-radius: 0.35rem; bottom: 1.6rem; color: white; display: none; font-size: 0.85rem; font-weight: normal; left: 0; line-height: 1.45; padding: 0.7rem; position: absolute; width: min(28rem, 70vw); z-index: 10; }
        .esv-reference:hover .esv-tooltip, .esv-reference:focus .esv-tooltip { display: block; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    rows = []
    for lesson in load_lessons():
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


def progress_id(kid_id, verse_id):
    return f"{kid_id}_{verse_id}"


def build_kid_report_pdf(kid_name, class_group, rows):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "Memory Verse Progress Report", ln=True)
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 8, f"Name: {kid_name}", ln=True)
    if class_group:
        pdf.cell(0, 8, f"Class: {class_group}", ln=True)
    pdf.cell(0, 8, f"Generated: {date.today().isoformat()}", ln=True)
    pdf.ln(4)
    memorized_count = sum(1 for row in rows if row[2])
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, f"Verses memorized: {memorized_count} / {len(rows)}", ln=True)
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(230, 230, 230)
    for width, label in [(90, "Verse"), (35, "Week"), (30, "Status"), (35, "Date")]:
        pdf.cell(width, 8, label, border=1, fill=True)
    pdf.ln()
    pdf.set_font("Helvetica", "", 11)
    for reference, week_date, memorized, date_recorded in rows:
        pdf.cell(90, 8, str(reference)[:45], border=1)
        pdf.cell(35, 8, str(week_date or ""), border=1)
        pdf.cell(30, 8, "Memorized" if memorized else "In progress", border=1)
        pdf.cell(35, 8, str(date_recorded or ""), border=1)
        pdf.ln()
    return bytes(pdf.output())


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
page = st.sidebar.radio("Navigate", ["Dashboard", "Lesson Reference", "Calendar", "Manage Kids", "Manage Verses", "Record Progress", "Reports"])

if page == "Calendar":
    render_calendar()

elif page == "Lesson Reference":
    render_lesson_reference()

elif page == "Dashboard":
    kids = records("kids")
    verses = records("verses")
    prog_df = progress_rows()
    roster_groups = load_roster_groups()
    for kid in kids:
        kid["program_group"] = kid.get("program_group") or roster_groups.get(kid.get("name"), "Unknown")
    group_filter = st.selectbox("Program", ["All", "Trackers", "Scouts", "Trailblazers"])
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
        else:
            st.info("No progress recorded yet. Add kids and verses, then use **Record Progress**.")

elif page == "Manage Kids":
    if not is_verified:
        st.warning("Enable Teacher Mode and enter the teacher PIN to manage kids.")
    else:
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

elif page == "Manage Verses":
    if not is_verified:
        st.warning("Enable Teacher Mode and enter the teacher PIN to manage verses.")
    else:
        st.subheader("Add a Verse")
        with st.form("add_verse", clear_on_submit=True):
            reference = st.text_input("Reference (e.g. John 3:16)")
            text = st.text_area("Verse text (optional)")
            week_date = st.date_input("Week assigned", value=date.today())
            if st.form_submit_button("Add Verse"):
                if reference.strip():
                    add_record("verses", {"reference": reference.strip(), "text": text.strip() or None, "week_date": week_date.isoformat()})
                    st.success(f"Added {reference}.")
                    st.rerun()
                else:
                    st.warning("Please enter a verse reference.")
        verses = records("verses", "week_date", descending=True)
        st.subheader("Current Verses")
        if not verses:
            st.info("No verses added yet.")
        else:
            st.dataframe(pd.DataFrame(verses), use_container_width=True, hide_index=True)
            verse_id = st.selectbox("Delete a verse", [None] + [verse["id"] for verse in verses], format_func=lambda value: "-" if value is None else next(v["reference"] for v in verses if v["id"] == value))
            if verse_id and st.button("Delete selected verse", type="secondary"):
                delete_record("verses", verse_id)
                st.rerun()

elif page == "Record Progress":
    kids = [kid for kid in records("kids", "name") if kid.get("active", 1)]
    verses = records("verses", "week_date", descending=True)
    if not kids or not verses:
        st.info("Add at least one kid and one verse first.")
    else:
        verse_id = st.selectbox("Verse", [verse["id"] for verse in verses], format_func=lambda value: next(f"{v['reference']} ({v['week_date']})" for v in verses if v["id"] == value))
        existing = {row["kid_id"]: row for row in records("progress") if row.get("verse_id") == verse_id}
        with st.form("record_progress"):
            checks = {kid["id"]: st.checkbox(kid["name"], value=bool(existing.get(kid["id"], {}).get("memorized", 0))) for kid in kids}
            if st.form_submit_button("Save Progress"):
                today = date.today().isoformat()
                for kid_id, memorized in checks.items():
                    update_record("progress", progress_id(kid_id, verse_id), {"kid_id": kid_id, "verse_id": verse_id, "memorized": int(memorized), "date_recorded": today if memorized else None})
                st.success("Progress saved.")

elif page == "Reports":
    kids = records("kids", "name")
    if not kids:
        st.info("Add kids first.")
    else:
        kid_id = st.selectbox("Choose a kid", [kid["id"] for kid in kids], format_func=lambda value: next(k["name"] for k in kids if k["id"] == value))
        kid = next(k for k in kids if k["id"] == kid_id)
        verses = {verse["id"]: verse for verse in records("verses")}
        rows_df = pd.DataFrame([
            (verses[row["verse_id"]]["reference"], verses[row["verse_id"]]["week_date"], row.get("memorized", 0), row.get("date_recorded"))
            for row in records("progress") if row.get("kid_id") == kid_id and row.get("verse_id") in verses
        ], columns=["reference", "week_date", "memorized", "date_recorded"])
        if rows_df.empty:
            st.info(f"No progress recorded yet for {kid['name']}.")
        else:
            display_df = rows_df.copy()
            display_df["memorized"] = display_df["memorized"].map({1: "✅ Memorized", 0: "⏳ In progress"})
            st.dataframe(display_df, use_container_width=True, hide_index=True)
            pdf_bytes = build_kid_report_pdf(kid["name"], kid.get("class_group"), list(rows_df.itertuples(index=False, name=None)))
            st.download_button("📄 Download printable PDF report", data=pdf_bytes, file_name=f"{kid['name'].replace(' ', '_')}_verse_report.pdf", mime="application/pdf")
