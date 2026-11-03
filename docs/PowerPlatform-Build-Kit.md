# OPCR AD Groups Manager – Power Platform Build Kit

Fully automatic AD group access review: a scheduled Power Automate flow keeps a SharePoint list of
group members up to date, and a Power App lets OPC Reserves reviewers record Keep / Remove decisions.
No local scripts, no extra licences (standard connectors only).

```
Scheduled flow (daily) ──reads──► Microsoft Entra ID (synced from AD)
        │
        └──writes──► SharePoint list "AD Group Members"
                                   ▲
Power App (reviewers) ──reads──────┘
        └──writes──► SharePoint list "AD Group Review Decisions" (version history = audit trail)
```

**Verified on 2026-10-23** with `az-ACE-Reserves-ORCM-prod` using the *Office 365 Groups → Send an HTTP
request V2* action: group lookup by name and member read (`transitiveMembers`) both work.

**Known limitation:** accounts that exist only in on-prem AD (not synced to Azure) are not returned —
e.g. `srv-monroyr` and `monroyrx` in the ORCM group. They cannot use Azure access, but should be removed
anyway; run `src/AdGroupReview/Export-AdGroupMembers.ps1` occasionally (e.g. quarterly) to catch them.

> The flow and app formulas below follow standard patterns but were not built in your tenant by the
> author. If a step shows an error, compare the action names with the ones used here first.

---

## Part 1 – SharePoint lists

Use a SharePoint site the OPC Reserves team owns (e.g. the team's Teams site).

**Quick way:** create each list with **New → List → From Excel** using the templates in
`docs/templates/` (column types listed below), then delete the sample rows and apply the settings.

### List 1: `AD Group Members`  (written only by the flow)

| Column | Type | Notes |
|---|---|---|
| Title | (built-in) | Holds the person's display name |
| MembershipKey | Single line of text | **Indexed**, **Enforce unique values**. Format `GroupName\|UserId` |
| GroupName | Single line of text | e.g. `az-ACE-Reserves-ORCM-prod` |
| UserId | Single line of text | Entra object ID of the person |
| Account | Single line of text | `onPremisesSamAccountName`, e.g. `cbuford` |
| Email | Single line of text | |
| JobTitle | Single line of text | |
| Department | Single line of text | |
| AccountEnabled | Yes/No | |
| Status | Single line of text | `Active` or `Left group` (text keeps Power Apps filters simple) |
| FirstSeen | Date and time | |
| LastSeen | Date and time | |
| LeftOn | Date and time | Set when the person is no longer in the group |

Settings: **List settings → Versioning settings → No versioning** (the flow updates rows daily; history
here is not useful). Permissions: reviewers **Read**, flow owner **Edit**.

> **Internal column names.** Lists created *From Excel* get internal names `field_1`, `field_2`, …
> Flow **filter queries** and **item()?[...]** lookups must use the internal name. In the OPCR lists:
> MembershipKey = `field_1`, Status = `field_9` (check via List settings → click column → `Field=` in the URL).
> Create/Update item fields and Power Apps use display names, so they are unaffected.

### List 2: `AD Group Review Decisions`  (written by the Power App)

| Column | Type | Notes |
|---|---|---|
| Title | (built-in) | Holds the MembershipKey (required by SharePoint) |
| MembershipKey | Single line of text | **Indexed**, **Enforce unique values** |
| Decision | Choice | `Keep`, `Remove`, `Unsure` (no default value) |
| Comment | Multiple lines of text | Plain text |

Settings: **Versioning on** (default for new lists). The built-in **Modified By** and **Modified**
columns are the reviewer and decision time; version history shows every change and cannot be faked.
Permissions: reviewers **Edit**.

Decisions are kept separately from members so a flow run never overwrites them.

---

## Part 2 – Scheduled flow `Monitor OPCR AD Groups`

Create at make.powerautomate.com → **Create → Scheduled cloud flow**, daily at e.g. 6:00 AM.

**Rename each action exactly as shown** (⋮ → Rename) — the expressions refer to these names.
Enter expressions with the **fx** button (or type `/` → Insert expression), never as plain text.

### 2.1 Variables (top level, after the Recurrence trigger)

1. **Initialize variable** – Name `GroupNames`, Type **Array**, Value:
   ```json
   [
     "az-ACE-Reserves-Algeria-prod",
     "az-ACE-Reserves-Bolivia-prod",
     "az-ACE-Reserves-Dolphin-prod",
     "az-ACE-Reserves-UAE-prod",
     "az-ACE-Reserves-GOM-prod",
     "az-ACE-Reserves-Oman-prod",
     "az-ACE-Reserves-PermianEOR-prod",
     "az-ACE-Reserves-PermianResources-prod",
     "az-ACE-Reserves-Rockies-prod",
     "az-ACE-Reserves-Corporate-prod",
     "az-ACE-Reserves-DolphinandUAE-prod",
     "az-ACE-Reserves-IntlGOM-prod",
     "az-ACE-Reserves-ORCM-prod"
   ]
   ```
2. **Initialize variable** – Name `SeenKeys`, Type **Array**, Value `[]`.

### 2.2 Loop over groups – **Apply to each**, renamed `For_each_group`

Input: `variables('GroupNames')`. Leave **Concurrency control off** (variables are appended inside).

Inside it:

1. **Office 365 Groups → Send an HTTP request V2**, renamed `Get_group`
   - Method `GET`
   - URI (insert the expression where shown):
     `https://graph.microsoft.com/v1.0/groups?$filter=displayName eq '` **`items('For_each_group')`** `'&$select=id,displayName`
2. **Compose**, renamed `Group_id` – expression: `first(body('Get_group')?['value'])?['id']`
3. **Send an HTTP request V2**, renamed `Get_members`
   - Method `GET`
   - URI: `https://graph.microsoft.com/v1.0/groups/` **`outputs('Group_id')`**
     `/transitiveMembers?$select=id,displayName,mail,jobTitle,department,accountEnabled,onPremisesSamAccountName&$top=999`
4. **Filter array**, renamed `Filter_users`
   - From: `body('Get_members')?['value']`
   - Condition: `item()?['@odata.type']` **is equal to** `#microsoft.graph.user`
5. **Apply to each**, renamed `For_each_member`, input `body('Filter_users')`. Concurrency off. Inside:
   1. **Compose**, renamed `Key` – `concat(items('For_each_group'), '|', items('For_each_member')?['id'])`
   2. **Append to array variable** – `SeenKeys`, value `outputs('Key')`
   3. **SharePoint → Get items**, renamed `Find_member_item` – list `AD Group Members`,
      Filter Query (fx): `concat('field_1 eq ''', outputs('Key'), '''')`, Top Count `1`
   4. **Condition** – `empty(body('Find_member_item')?['value'])` **is equal to** `true`
      - **True → SharePoint → Create item** (list `AD Group Members`):

        | Field | Value |
        |---|---|
        | Title | `items('For_each_member')?['displayName']` |
        | MembershipKey | `outputs('Key')` |
        | GroupName | `items('For_each_group')` |
        | UserId | `items('For_each_member')?['id']` |
        | Account | `items('For_each_member')?['onPremisesSamAccountName']` |
        | Email | `items('For_each_member')?['mail']` |
        | JobTitle | `items('For_each_member')?['jobTitle']` |
        | Department | `items('For_each_member')?['department']` |
        | AccountEnabled | `items('For_each_member')?['accountEnabled']` |
        | Status | `Active` |
        | FirstSeen / LastSeen | `utcNow()` |

      - **False → SharePoint → Update item** (list `AD Group Members`):
        Id = `first(body('Find_member_item')?['value'])?['ID']`; same fields as above **except FirstSeen**;
        Status `Active`; LastSeen `utcNow()`; LeftOn left empty.

### 2.3 Mark people who left (top level, **after** `For_each_group`)

1. **SharePoint → Get items**, renamed `Get_active_items` – list `AD Group Members`,
   Filter Query `field_9 eq 'Active'`, Top Count `5000`; **Settings → Pagination on, Threshold 5000**.
2. **Filter array**, renamed `Left_items`
   - From: `body('Get_active_items')?['value']`
   - Condition: `contains(variables('SeenKeys'), item()?['field_1'])` **is equal to** `false`
3. **Apply to each**, renamed `For_each_left`, input `body('Left_items')`:
   **SharePoint → Update item** – Id `items('For_each_left')?['ID']`, Title `items('For_each_left')?['Title']`,
   Status `Left group`, LeftOn `utcNow()`.

**Safety:** if any group lookup or member read fails, the flow stops before step 2.3, so a temporary
Graph error can never mark everyone as "Left group". Keep the default *run after: is successful*.

### 2.4 Test and operate

1. Temporarily set `GroupNames` to only `["az-ACE-Reserves-ORCM-prod"]`, run **Test → Manually**, and
   check that the list has **26** rows with Status `Active`.
2. Restore all 13 names and run again (expect ~575 rows).
3. **Details → Owners → add a co-owner** so the flow keeps running if you are away or change roles.
4. Failure emails go to the owners automatically; check them when they arrive.

---

## Part 3 – Power App `OPCR AD Groups Manager`

make.powerapps.com → **Create → Blank app → Blank canvas app**, format **Tablet**.

### 3.1 Setup

- **Data → Add data → SharePoint** → your site → both lists.
- **Settings → General → Data row limit = 2000** (we have ~575 rows; all filtering happens in memory).
- **App → OnStart**:
  ```powerfx
  ClearCollect(colMembers, 'AD Group Members');
  ClearCollect(colDecisions, 'AD Group Review Decisions')
  ```
- Refresh button (on each screen) **OnSelect**:
  ```powerfx
  Refresh('AD Group Members'); Refresh('AD Group Review Decisions');
  ClearCollect(colMembers, 'AD Group Members');
  ClearCollect(colDecisions, 'AD Group Review Decisions')
  ```

### 3.2 Screen `scrReview`

Controls across the top:

| Control | Name | Key properties |
|---|---|---|
| Text input | `txtSearch` | HintText `Search name, account, title, department` |
| Combo box | `cmbGroup` | Items `Sort(Distinct(colMembers, GroupName), Value)`, SelectMultiple `true` (empty = all groups) |
| Toggle | `tglDisabled` | Label `Only disabled accounts` |
| Dropdown | `ddDecision` | Items `["All", "Undecided", "Keep", "Remove", "Unsure"]` |

Summary labels (Text):
```powerfx
"Shown: " & CountRows(galMembers.AllItems)
"Marked Remove: " & CountIf(colDecisions, Decision.Value = "Remove")
"Undecided: " & CountIf(colMembers, Status = "Active" && IsBlank(LookUp(colDecisions, MembershipKey = colMembers[@MembershipKey]).Decision.Value))
```

Vertical gallery `galMembers` – **Items**:
```powerfx
Filter(
    colMembers,
    Status = "Active",
    IsEmpty(cmbGroup.SelectedItems) || GroupName in cmbGroup.SelectedItems.Value,
    IsBlank(txtSearch.Text) || txtSearch.Text in Title || txtSearch.Text in Account
        || txtSearch.Text in JobTitle || txtSearch.Text in Department,
    !tglDisabled.Value || !AccountEnabled,
    ddDecision.Selected.Value = "All" ||
        Coalesce(LookUp(colDecisions, MembershipKey = colMembers[@MembershipKey]).Decision.Value, "Undecided")
            = ddDecision.Selected.Value
)
```
(`Title` holds the person's display name.)

Inside the gallery template, labels for `ThisItem.Title`, `ThisItem.Account`, `ThisItem.GroupName`,
`ThisItem.JobTitle`, `ThisItem.Department`, and a flag label:
```powerfx
If(!ThisItem.AccountEnabled, "Disabled", "")
```

Decision dropdown `drpDecision` – Items `["Keep", "Remove", "Unsure"]`, AllowEmptySelection `true`,
**Default** `LookUp(colDecisions, MembershipKey = ThisItem.MembershipKey).Decision.Value`, **OnChange**:
```powerfx
With(
    { existing: LookUp(colDecisions, MembershipKey = ThisItem.MembershipKey) },
    With(
        {
            saved: If(
                IsBlank(existing),
                Patch('AD Group Review Decisions', Defaults('AD Group Review Decisions'),
                    { Title: ThisItem.MembershipKey, MembershipKey: ThisItem.MembershipKey,
                      Decision: { Value: drpDecision.Selected.Value } }),
                Patch('AD Group Review Decisions', LookUp('AD Group Review Decisions', ID = existing.ID),
                    { Decision: { Value: drpDecision.Selected.Value } })
            )
        },
        If(
            IsError(saved),
            Notify("Could not save the decision. Please try again.", NotificationType.Error),
            If(IsBlank(existing), Collect(colDecisions, saved), Patch(colDecisions, existing, saved));
            Notify("Saved", NotificationType.Success, 1500)
        )
    )
)
```

Comment text input `txtComment` – **Default** `LookUp(colDecisions, MembershipKey = ThisItem.MembershipKey).Comment`,
**OnChange**: same formula as above, replacing both `Decision: { Value: drpDecision.Selected.Value }`
with `Comment: txtComment.Text`.

Reviewer label:
```powerfx
With({ d: LookUp(colDecisions, MembershipKey = ThisItem.MembershipKey) },
    If(IsBlank(d), "", d.'Modified By'.DisplayName & " · " & Text(d.Modified, "yyyy-mm-dd hh:mm")))
```

### 3.3 Screen `scrRemovals`

Gallery `galRemovals` – **Items**:
```powerfx
Filter(colMembers, LookUp(colDecisions, MembershipKey = colMembers[@MembershipKey]).Decision.Value = "Remove")
```
Labels: name, account, group, comment, reviewer (as above), and status:
```powerfx
If(ThisItem.Status = "Left group", "Completed " & Text(ThisItem.LeftOn, "yyyy-mm-dd"), "Pending")
```

**How to remove (Group Self-Service Administration)** – button `Open Group Self-Service Administration`, OnSelect:
```powerfx
Launch("https://oxy.service-now.com/it?id=sc_cat_item_guide&sys_id=c9b0ed7b87b6b090960c0ed5cebb3545&table=sc_cat_item&searchTerm=group%20self-service%20administration")
```
Label with the steps: open the group shown, find the person by Account, remove them; you must be a
group owner/manager, otherwise ask the owner. Changes reach Azure in ~30 minutes, and the next flow run
marks the row **Completed** automatically.

### 3.4 Navigation and publish

- Add two buttons on each screen: `Review` → `Navigate(scrReview)`, `Removals` → `Navigate(scrRemovals)`.
- **Save → Publish**.

---

## Part 4 – Sharing

1. **App:** Power Apps → app ⋯ → **Share** → OPC Reserves reviewers (group or names). Grant **User**.
2. **SharePoint:** reviewers need **Read** on `AD Group Members` and **Edit** on
   `AD Group Review Decisions` (the share dialog offers to do this; otherwise set it in the site).
3. **Teams (optional):** in the team channel → **+** → **Power Apps** → add the app as a tab.
4. **Flow:** keep it owned by you plus a co-owner; reviewers do not need access to it.

## Later enhancements

- **Manager column:** inside `For_each_member`, add *Office 365 Users → Get manager (V2)* with the user id
  (set *Configure run after* to continue on failure for people without a manager).
- **Last sign-in:** requires IT to grant sign-in activity read permission; then add `signInActivity`.
- **Stage/dev groups:** add their names to `GroupNames` and an Environment filter in the app.
- **Power BI:** connect to the same two lists if management wants reporting outside the app.
