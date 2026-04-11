# iPhone Shortcut: NXT 名片スキャナー

Step-by-step instructions to build the iPhone Shortcut that works with the backend API.

## Prerequisites

- Backend API deployed and accessible via HTTPS (e.g., `https://meishi.your-domain.com`)
- API key configured in both the backend `.env` and the Shortcut

---

## Shortcut Build Steps

Open the **Shortcuts** app on your iPhone and create a new Shortcut named **「名片スキャン」**.

### Step 1: Set Variables

1. **Text** action → set to your API URL (e.g., `https://meishi.your-domain.com`)
   - Set Variable Name: `API_URL`

2. **Text** action → set to your API key (e.g., `your-secret-key-here`)
   - Set Variable Name: `API_KEY`

### Step 2: Capture Card Front (Required)

3. **Take Photo** action
   - Camera: Back
   - Show Camera Preview: ON
   - Set Variable Name: `CardFront`

### Step 3: Capture Card Back (Optional)

4. **Choose from Menu** action
   - Prompt: "Scan back side?"
   - Options: "Yes", "No"

5. Under **"Yes"**:
   - **Take Photo** action → Set Variable Name: `CardBack`

6. Under **"No"**:
   - **Nothing** (leave empty)

### Step 4: Capture Person Photo (Optional)

7. **Choose from Menu** action
   - Prompt: "Take person's photo?"
   - Options: "Yes", "No"

8. Under **"Yes"**:
   - **Take Photo** action (Front camera) → Set Variable Name: `PersonPhoto`

9. Under **"No"**:
   - **Nothing**

### Step 5: Send to API for Parsing

10. **Show Notification** action
    - Title: "Scanning..."
    - Body: "Analyzing business card with AI"

11. **Get Contents of URL** action
    - URL: `API_URL` + `/api/v1/scan`  (use Text action to concatenate)
    - Method: **POST**
    - Headers:
      - `Authorization`: `Bearer ` + `API_KEY`
    - Request Body: **Form**
    - Form fields:
      - `card_front`: `CardFront` (File)
      - `card_back`: `CardBack` (File) — only if captured
      - `person_photo`: `PersonPhoto` (File) — only if captured
    - Set Variable Name: `ScanResult`

### Step 6: Parse and Display Results

12. **Get Dictionary Value** from `ScanResult`
    - Key: `card`
    - Set Variable Name: `Card`

13. **Get Dictionary Value** from `Card` → key `person`
    - Set Variable Name: `Person`

14. Build display text using **Text** action:
    ```
    === Scan Results ===

    Name: [Get Dictionary Value: Person.names.0.value]
    Company: [Get Dictionary Value: Person.positions.0.company]
    Title: [Get Dictionary Value: Person.positions.0.title]
    Phone: [Get Dictionary Value: Person.phones.0.value]
    Mobile: [Get Dictionary Value: Person.phones.1.value]
    Email: [Get Dictionary Value: Person.emails.0.value]
    Address: [Get Dictionary Value: Person.addresses.0.full]
    Website: [Get Dictionary Value: Person.website]
    ```
    - Set Variable Name: `DisplayText`

15. **Show Alert** action
    - Title: "Scan Result"
    - Message: `DisplayText`
    - Show Cancel Button: ON

### Step 7: Check for Existing Contact Match

16. **Get Dictionary Value** from `Card` → key `match`
    - Set Variable Name: `Match`

17. **Get Dictionary Value** from `Match` → key `is_existing`

18. **If** `is_existing` equals `true`:
    - **Get Dictionary Value** from `Match` → key `matched_name`
    - **Choose from Menu**:
      - Prompt: "Similar contact found: [matched_name]. Link to existing?"
      - "Yes — Link to existing"
      - "No — Create new"
    - Under "Yes": (keep the match data as-is in Card)
    - Under "No": Clear the match by setting `match.is_existing` to false

19. **End If**

### Step 8: Edit Option

20. **Choose from Menu** action
    - Prompt: "Save contact?"
    - Options: "Save as-is", "Edit first", "Cancel"

21. Under **"Edit first"**:
    - **Ask for Input** (Text) — Prompt: "Name", Default: [parsed name]
      → Set Variable Name: `EditedName`
    - **Ask for Input** (Text) — Prompt: "Company", Default: [parsed company]
      → Set Variable Name: `EditedCompany`
    - **Ask for Input** (Text) — Prompt: "Title", Default: [parsed title]
      → Set Variable Name: `EditedTitle`
    - **Ask for Input** (Text) — Prompt: "Email", Default: [parsed email]
      → Set Variable Name: `EditedEmail`
    - **Ask for Input** (Text) — Prompt: "Phone", Default: [parsed phone]
      → Set Variable Name: `EditedPhone`
    - **Ask for Input** (Text) — Prompt: "Notes", Default: ""
      → Set Variable Name: `EditedNotes`
    - Update the Card JSON dictionary with edited values

22. Under **"Cancel"**:
    - **Stop Shortcut**

### Step 9: Set Received Date

23. **Ask for Input** (Date) — Prompt: "Date card was received"
    - Default: Current Date
    - Set Variable Name: `ReceivedDate`

24. Update `Card.received_date` with `ReceivedDate` formatted as "yyyy-MM-dd"

### Step 10: Confirm and Save

25. **Get Contents of URL** action
    - URL: `API_URL` + `/api/v1/confirm`
    - Method: **POST**
    - Headers:
      - `Authorization`: `Bearer ` + `API_KEY`
      - `Content-Type`: `application/json`
    - Request Body: **JSON** → the full `Card` dictionary
    - Set Variable Name: `ConfirmResult`

26. **Get Dictionary Value** from `ConfirmResult` → key `status`

27. **If** status equals "ok":
    - **Show Notification**: "✅ Contact saved to Odoo, Google, and OneDrive!"
28. **Otherwise**:
    - **Get Dictionary Value** from `ConfirmResult` → key `errors`
    - **Show Alert**: "⚠️ Partially saved. Errors: [errors]"
29. **End If**

---

## Shortcut Tips

### Adding to Home Screen
- In Shortcuts app, tap the "..." on your shortcut
- Tap the dropdown arrow next to the name at top
- Tap "Add to Home Screen"
- Choose a nice icon (📇 or 🗂️)

### Quick Access
- You can also trigger this Shortcut from:
  - Siri: "Hey Siri, 名片スキャン"
  - Widget on home screen
  - Back Tap (Settings → Accessibility → Touch → Back Tap)

### Handling Large Images
- iPhone photos can be 3-12MB. The backend automatically resizes them.
- If you experience timeouts, add a **Resize Image** action (1200px width)
  before the API call to reduce upload size.

---

## Simplified Alternative: Two-Shortcut Approach

If the single Shortcut becomes too complex, split it into two:

### Shortcut 1: 「名片スキャン」(Scan)
- Steps 1-15: Capture photos, send to API, show results
- Save the full `Card` JSON to a file in iCloud Drive or clipboard

### Shortcut 2: 「名片保存」(Save)
- Read the saved JSON
- Steps 16-29: Match check, edit, confirm, save
- This can be run later when you have time to review

---

## Testing

1. First test the backend health check:
   - Create a simple Shortcut with just: Get Contents of URL → `API_URL/api/v1/health`
   - Should return `{"status": "ok"}`

2. Then test with a real business card photo.

3. Check the backend logs for any parsing or sync errors.
