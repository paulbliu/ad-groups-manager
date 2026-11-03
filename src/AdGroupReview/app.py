"""Streamlit app for reviewing AD group membership and recording Keep/Remove decisions."""

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

DATA_DIR = Path(os.environ.get("AD_GROUP_REVIEW_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
MEMBERS_CSV = DATA_DIR / "members.csv"
DECISIONS_DB = DATA_DIR / "decisions.db"
STALE_LOGON_DAYS = 90
DECISION_OPTIONS = ["Keep", "Remove", "Unsure"]
BLANK = "(blank)"
FILTER_COLUMNS_PER_ROW = 4
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
GROUP_SELF_SERVICE_URL = (
    "https://oxy.service-now.com/it?id=sc_cat_item_guide&sys_id=c9b0ed7b87b6b090960c0ed5cebb3545"
    "&table=sc_cat_item&searchTerm=group%20self-service%20administration"
)
COLUMN_LABELS = {
    "GroupName": "AD group",
    "DisplayName": "Name",
    "SamAccountName": "Account",
    "Title": "Title",
    "Department": "Department",
    "ManagerName": "Manager",
    "Enabled": "Enabled",
    "LastLogonDate": "Last logon",
    "Membership": "Membership",
    "RemoveFrom": "Remove from",
    "Flags": "Flags",
    "Decision": "Decision",
    "Comment": "Comment",
    "Reviewer": "Reviewer",
    "DecidedAtUtc": "Decided (UTC)",
}

CREATE_DECISION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ReviewDecision (
    ReviewDecisionId INTEGER PRIMARY KEY AUTOINCREMENT,
    GroupName TEXT NOT NULL,
    MemberObjectGuid TEXT NOT NULL,
    Decision TEXT NOT NULL,
    Comment TEXT NOT NULL,
    Reviewer TEXT NOT NULL,
    DecidedAtUtc TEXT NOT NULL
)
"""

LATEST_DECISIONS_SQL = """
SELECT d.GroupName, d.MemberObjectGuid, d.Decision, d.Comment, d.Reviewer, d.DecidedAtUtc
FROM ReviewDecision AS d
WHERE d.ReviewDecisionId = (
    SELECT MAX(latest.ReviewDecisionId)
    FROM ReviewDecision AS latest
    WHERE latest.GroupName = d.GroupName
      AND latest.MemberObjectGuid = d.MemberObjectGuid
)
"""

INSERT_DECISION_SQL = """
INSERT INTO ReviewDecision (GroupName, MemberObjectGuid, Decision, Comment, Reviewer, DecidedAtUtc)
VALUES (?, ?, ?, ?, ?, ?)
"""


def open_decisions_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DECISIONS_DB)
    connection.execute(CREATE_DECISION_TABLE_SQL)
    return connection


def load_latest_decisions() -> pd.DataFrame:
    with closing(open_decisions_db()) as connection:
        return pd.read_sql_query(LATEST_DECISIONS_SQL, connection)


def save_decisions(changes: list[tuple[str, str, str, str]], reviewer: str) -> None:
    """Append (GroupName, MemberObjectGuid, Decision, Comment) records to the decision history."""
    decided_at_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records = [
        (group_name, member_guid, decision, comment, reviewer, decided_at_utc)
        for group_name, member_guid, decision, comment in changes
    ]
    with closing(open_decisions_db()) as connection:
        with connection:
            connection.executemany(INSERT_DECISION_SQL, records)


def save_editor_changes(editor_key: str, rows: list[tuple[str, str, str, str]], reviewer: str) -> None:
    """Persist data editor edits as soon as they are made, then reset the editor to the saved state."""
    changes = []
    for position, edits in st.session_state[editor_key]["edited_rows"].items():
        group_name, member_guid, decision, comment = rows[int(position)]
        new_decision = edits.get("Decision", decision) or ""
        new_comment = edits.get("Comment", comment) or ""
        if (new_decision, new_comment) != (decision, comment):
            changes.append((group_name, member_guid, new_decision, new_comment))

    if changes:
        save_decisions(changes, reviewer)
        st.session_state["save_message"] = f"Saved {len(changes)} decision(s)."

    # Row positions in edited_rows go stale once the data changes, so always start a fresh editor.
    st.session_state["editor_version"] = st.session_state.get("editor_version", 0) + 1


def get_removal_target(row: pd.Series) -> str:
    if row["Membership"] == "Direct":
        return row["GroupName"]
    if row["Membership"] == "Nested":
        return row["ViaGroups"]
    return f"{row['GroupName']}; {row['ViaGroups']}"


@st.cache_data
def load_members(csv_path: str, modified_time: float) -> pd.DataFrame:
    members = pd.read_csv(csv_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    last_logon = pd.to_datetime(members["LastLogonDate"], errors="coerce")
    days_since_logon = (pd.Timestamp.now().normalize() - last_logon).dt.days
    is_user = members["ObjectClass"] == "user"
    flags = pd.DataFrame(
        {
            "Disabled": is_user & (members["Enabled"] == "False"),
            "Never logged on": is_user & last_logon.isna(),
            f"No logon {STALE_LOGON_DAYS}+ days": days_since_logon >= STALE_LOGON_DAYS,
            "Nested": members["Membership"] != "Direct",
            "Not a user": ~is_user,
        }
    )
    members["Flags"] = flags.apply(lambda row: ", ".join(name for name, is_set in row.items() if is_set), axis=1)
    members["RemoveFrom"] = members.apply(get_removal_target, axis=1)
    return members


def get_filter_matches(values: pd.Series, column: str, selected: list[str]) -> pd.Series:
    if column == "Flags":
        return values.str.split(", ").apply(lambda flags: any((flag or BLANK) in selected for flag in flags))
    return values.replace("", BLANK).isin(selected)


def get_filter_options(values: pd.Series, column: str) -> list[str]:
    if column == "Flags":
        return sorted({flag or BLANK for flags in values for flag in flags.split(", ")})
    return sorted(values.replace("", BLANK).unique())


def render_column_filters(review: pd.DataFrame) -> tuple[pd.Series, tuple]:
    """Show one include/exclude filter per column; return the row mask and a signature of the active filters."""
    mask = pd.Series(True, index=review.index)
    active_filters = []

    with st.expander("Column filters (click to show/hide)", expanded=False):
        grid = st.columns(FILTER_COLUMNS_PER_ROW)
        for position, (column, label) in enumerate(COLUMN_LABELS.items()):
            with grid[position % FILTER_COLUMNS_PER_ROW]:
                options = get_filter_options(review[column], column)
                selected = st.multiselect(label, options, key=f"filter-{column}", placeholder="All")
                exclude = st.checkbox("Exclude selected", key=f"exclude-{column}", disabled=not selected)

            if selected:
                matches = get_filter_matches(review[column], column, selected)
                mask &= ~matches if exclude else matches
                active_filters.append((column, tuple(selected), exclude))

    return mask, tuple(active_filters)


def to_safe_csv(frame: pd.DataFrame) -> bytes:
    """Neutralise spreadsheet formulas so the export can be opened safely in Excel."""
    safe_frame = frame.map(lambda value: f"'{value}" if isinstance(value, str) and value.startswith(FORMULA_PREFIXES) else value)
    return safe_frame.to_csv(index=False).encode("utf-8")


def render_removal_instructions() -> None:
    st.subheader("How to remove members")
    st.caption(
        "Saving a Remove decision does not change AD. Remove members through Group Self-Service "
        "Administration, then re-run the export to confirm the removal."
    )
    st.markdown(
        f"""
1. Open [**Group Self-Service Administration**]({GROUP_SELF_SERVICE_URL}) in ServiceNow.
2. Open the group shown in the **Remove from** column.
3. Go to the group's members, find the person by **Account** (SamAccountName), and remove them.
4. You must be an owner/manager of the group. If you are not, ask the group owner to remove them
   or submit the request through the same portal.

This follows the standard process and leaves an audit trail.
Changes sync to Azure within about 30 minutes; active sessions may keep access for up to about an hour.
"""
    )


def main() -> None:
    st.set_page_config(page_title="AD Group Membership Review", layout="wide")
    st.title("AD Group Membership Review")

    if not MEMBERS_CSV.exists():
        st.error(f"No membership snapshot found at {MEMBERS_CSV}. Run Export-AdGroupMembers.ps1 first.")
        st.stop()

    members = load_members(str(MEMBERS_CSV), MEMBERS_CSV.stat().st_mtime)
    review = members.merge(load_latest_decisions(), on=["GroupName", "MemberObjectGuid"], how="left")
    for column in ("Decision", "Comment", "Reviewer", "DecidedAtUtc"):
        review[column] = review[column].fillna("")

    if not members.empty:
        st.caption(f"AD snapshot taken {members['SnapshotUtc'].iloc[0]} (UTC). Re-run the export to refresh.")

    if "save_message" in st.session_state:
        st.toast(st.session_state.pop("save_message"))

    search_column, flagged_column = st.columns([3, 1], vertical_alignment="bottom")
    search_text = search_column.text_input("Search name, account, title, department").strip()
    flagged_only = flagged_column.checkbox("Only flagged members")

    mask, active_filters = render_column_filters(review)
    if flagged_only:
        mask &= review["Flags"] != ""
    if search_text:
        searchable = review[["DisplayName", "SamAccountName", "Title", "Department"]].agg(" ".join, axis=1)
        mask &= searchable.str.contains(search_text, case=False, regex=False)
    view = review.loc[mask]

    metric_columns = st.columns(4)
    metric_columns[0].metric("Memberships shown", len(view))
    metric_columns[1].metric("Undecided", int((view["Decision"] == "").sum()))
    metric_columns[2].metric("Marked Remove", int((view["Decision"] == "Remove").sum()))
    metric_columns[3].metric("Flagged", int((view["Flags"] != "").sum()))

    display_columns = [
        "GroupName", "DisplayName", "SamAccountName", "Title", "Department", "ManagerName", "Enabled",
        "LastLogonDate", "Membership", "RemoveFrom", "Flags", "Decision", "Comment", "Reviewer",
        "DecidedAtUtc", "MemberObjectGuid",
    ]
    editable_columns = ("Decision", "Comment")
    original = view[display_columns]
    editor_version = st.session_state.setdefault("editor_version", 0)
    filter_signature = hash((active_filters, flagged_only, search_text))
    editor_key = f"editor-{editor_version}-{filter_signature}"
    rows = list(view[["GroupName", "MemberObjectGuid", "Decision", "Comment"]].itertuples(index=False, name=None))

    name_column, _ = st.columns([2, 4])
    reviewer = name_column.text_input(
        "Your name", key="reviewer", help="Recorded with every decision. Required before you can edit."
    ).strip()
    if reviewer:
        st.caption("Decision and Comment changes are saved automatically as soon as you make them.")
    else:
        st.info("Enter your name (and press Enter) to start editing Decision and Comment.")

    st.data_editor(
        original,
        key=editor_key,
        hide_index=True,
        width="stretch",
        disabled=[column for column in display_columns if column not in editable_columns] if reviewer else True,
        column_config={
            **COLUMN_LABELS,
            "Decision": st.column_config.SelectboxColumn("Decision", options=DECISION_OPTIONS),
            "Comment": st.column_config.TextColumn("Comment", max_chars=500),
            "MemberObjectGuid": None,
        },
        on_change=save_editor_changes,
        args=(editor_key, rows, reviewer),
    )

    st.subheader("Approved removals")
    removal_columns = [
        "GroupName", "DisplayName", "SamAccountName", "Mail", "Membership", "RemoveFrom", "Comment", "Reviewer",
        "DecidedAtUtc",
    ]
    removals = review.loc[review["Decision"] == "Remove", removal_columns]
    st.dataframe(removals, hide_index=True, width="stretch")
    st.download_button(
        "Download removal list (CSV)",
        data=to_safe_csv(removals),
        file_name="ad-group-removals.csv",
        mime="text/csv",
        disabled=removals.empty,
    )

    render_removal_instructions()


main()
