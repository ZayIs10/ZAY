Yes say# Gen Z Capital — Coworker Setup Guide
**Last updated: April 2026**

This guide walks you through setting up the Gen Z Capital Instagram automation from scratch.
Follow every step in order. Do not skip anything.

---

## What This System Does

1. **Research automation** — Finds trending topics → writes them to Google Sheets
2. **Publisher automation** — Reads Google Sheets → generates images + captions → posts to Instagram

---

## STEP 1 — Get Access to Everything (Marc Must Do This)

Ask Marc to give you:
- [ ] The `.env` file (contains all API keys — never share this publicly)
- [ ] The `google_service_account.json` file
- [ ] Editor access to the Google Sheet:
  `https://docs.google.com/spreadsheets/d/13AEU80ULx2Lxnq9SWDeSSFN7unfhr-x_mPyi37oz7O4`
- [ ] The `logo.png` file (Gen Z Capital brand logo)
- [ ] Access to the Facebook Developer App (Marc adds you as a tester)

---

## STEP 2 — Install Python

1. Download Python 3.11 or newer: https://www.python.org/downloads/
2. During install, **check the box: "Add Python to PATH"** — very important
3. Open Command Prompt and verify:
   ```
   python --version
   ```
   You should see: `Python 3.11.x` or newer

---

## STEP 3 — Download the Project

Option A — If Marc shared a zip file:
- Unzip it to your Desktop
- Rename the folder to: `Gen Z autamation`
- Full path should be: `C:\Users\YourName\Desktop\Gen Z autamation\`

Option B — If using Git:
```
git clone [repo-url] "Gen Z autamation"
```

---

## STEP 4 — Add the Secret Files

Place these files in the ROOT of the project folder (`Gen Z autamation\`):

1. **`.env`** — paste the contents Marc gave you. It should look like this:
   ```
   OPENAI_API_KEY=sk-...
   INSTAGRAM_ACCESS_TOKEN=EAAb...
   INSTAGRAM_IG_USER_ID=17841478285470926
   GOOGLE_SHEET_ID=13AEU80ULx2Lxnq9SWDeSSFN7unfhr-x_mPyi37oz7O4
   IMGBB_API_KEY=...
   ```

2. **`google_service_account.json`** — the Google credentials file Marc gave you

3. **`logo.png`** — Gen Z Capital logo

---

## STEP 5 — Install Python Dependencies

Open Command Prompt. Navigate to the project folder:
```
cd "C:\Users\YourName\Desktop\Gen Z autamation"
```

Install all required packages:
```
pip install -r requirements.txt
```

Wait for it to finish. If you see errors about `pip` not found, reinstall Python and check "Add to PATH".

---

## STEP 6 — Verify Font Files

The image generator needs the **Anton** and **Inter** fonts.

1. Download Anton: https://fonts.google.com/specimen/Anton
2. Download Inter: https://fonts.google.com/specimen/Inter
3. Install both on your Windows (right-click the .ttf → Install)
4. The script will find them automatically

---

## STEP 7 — Test the Publisher (No Instagram, Local Only)

This generates post images to your desktop WITHOUT posting to Instagram.

Open Command Prompt in the project folder:
```
cd "C:\Users\YourName\Desktop\Gen Z autamation\publisher"
python post_generator.py --local
```

If it works, you will see image files appear on your Desktop:
- `post_YYYYMMDD_HHMMSS.jpg` — single image posts
- `genz_carousel_XX_topic_name\` — carousel folders with 8 slides each

---

## STEP 8 — Instagram Token (THE MOST IMPORTANT PART)

> **The Instagram access token expires every few days. This is the #1 reason publishing fails.**

### What is the token?
The token is a password that lets the script post to Instagram on behalf of Marc's account.
It looks like: `EAAbzxVfZAZBMEBR...` (a long string starting with EAAb)

### How to get a fresh token:

1. Go to: https://developers.facebook.com/tools/explorer/
2. Log in with Marc's Facebook account
3. In the top-right dropdown, select the **Gen Z Capital app**
4. In the second dropdown, select the **Gen Z Facebook Page** (not personal profile)
5. Under **Permissions**, make sure these are checked:
   - `instagram_basic`
   - `instagram_content_publish`
   - `pages_read_engagement`
   - `pages_show_list`
6. Click **Generate Access Token**
7. Copy the token that appears
8. Open the `.env` file and replace the old `INSTAGRAM_ACCESS_TOKEN=` value with the new one
9. Save the `.env` file

> **How often:** You need to do this every 2-3 days. Long-lived tokens (60 days) can be generated
> from the Graph API — ask Marc if you need this set up.

### How to check if your token works:
Open a browser and paste this URL (replace TOKEN with your actual token):
```
https://graph.facebook.com/me?access_token=TOKEN
```
If you see `{"name": "Gen Z", "id": "..."}` — the token works.
If you see `"error"` — the token is expired. Get a new one (see above).

---

## STEP 9 — Run the Full Publisher (Posts to Instagram)

Once the token is fresh and working:

```
cd "C:\Users\YourName\Desktop\Gen Z autamation\publisher"
python post_generator.py
```

The script will:
1. Read the next unpublished row from Google Sheets
2. Generate a DALL-E image
3. Overlay text + logo
4. Upload image to hosting service
5. Post to Instagram
6. Update Google Sheets with "Published" status + post URL

---

## STEP 10 — Run the Research Script (Finds New Topics)

This runs weekly to find new topics and add them to Google Sheets:

```
cd "C:\Users\YourName\Desktop\Gen Z autamation\research"
python research.py
```

It will:
1. Search Reddit, HackerNews, and finance RSS feeds for trending topics
2. Use GPT-4o to generate 7 topic ideas
3. Write them to Google Sheets with Status = "Ready"

---

## Google Sheets Column Structure

The script reads these columns from the sheet:

| Column | What It Is |
|--------|-----------|
| Topic | Main post topic |
| Key Points / Slide Content | Bullet points for the post |
| Post Type | `single` or `carousel` |
| Headline Line 1 (White) | Top line of image text |
| Headline Line 2 (Neon Green) | Key number or power word |
| Headline Line 3 (White) | Bottom line of image text |
| Subheadline (Gray) | Small supporting text |
| Status | `Ready` / `Published` / `needs_review` |
| Image File | URL after image is generated (auto-filled) |
| Post URL | Instagram post URL (auto-filled after publish) |

---

---

## FACEBOOK DEVELOPER DEBUGGING — Full History of What Marc Faced

> Read this carefully. These are real problems that happened during setup. Every mistake here
> cost hours. You now know exactly what to do and what NOT to do.

---

### Problem 1 — Wrong Instagram User ID

**What happened:**
Marc was using the wrong Instagram User ID in the `.env` file. The ID `1415642273049110` was
the Facebook App ID — not the Instagram Business Account ID. This caused every API call to fail
with a "not found" error.

**The correct Instagram Business Account ID is: `17841478285470926`**

**How to find the correct Instagram User ID (if you ever need to check):**

Step 1 — Get your Facebook Page ID:
Go to this URL in your browser (use a valid token):
```
https://graph.facebook.com/me/accounts?access_token=YOUR_TOKEN
```
Look for the page named **"Gen Z"** in the response. The `id` field is the **Facebook Page ID**.
Example response:
```json
{
  "data": [{
    "name": "Gen Z",
    "id": "1081687478355463",
    "access_token": "EAAb..."
  }]
}
```
The Facebook Page ID here is `1081687478355463`.

Step 2 — Get the Instagram Business Account linked to that page:
```
https://graph.facebook.com/1081687478355463?fields=instagram_business_account&access_token=YOUR_TOKEN
```
Response:
```json
{
  "instagram_business_account": {
    "id": "17841478285470926"
  }
}
```
This `17841478285470926` is the correct Instagram User ID. This is what goes in `.env`.

---

### Problem 2 — Wrong Type of Token (User Token vs Page Token)

**What happened:**
Marc was using a **User Access Token** — the default token the Graph API Explorer generates.
This token is tied to Marc's personal Facebook account, NOT the Gen Z Capital Facebook Page.
Instagram publishing requires a **Page Access Token** — a token tied to the Facebook Page.

**How to get the correct Page Access Token:**

1. Go to: https://developers.facebook.com/tools/explorer/
2. Log in with Marc's Facebook account
3. Top-right dropdown — select the **Gen Z Capital app**
4. Second dropdown (below) — **MUST select the Gen Z Facebook Page, NOT "Me"**
   - If you select "Me" you get a User Token (WRONG)
   - If you select the Gen Z Page you get a Page Token (CORRECT)
5. Under Permissions, add all of these:
   - `instagram_basic`
   - `instagram_content_publish`
   - `pages_read_engagement`
   - `pages_show_list`
6. Click **Generate Access Token**
7. Facebook will ask you to approve the permissions — click Allow
8. Copy the token and paste into `.env` as `INSTAGRAM_ACCESS_TOKEN=`

**How to verify the token is a Page Token (not User Token):**
```
https://graph.facebook.com/me?access_token=YOUR_TOKEN
```
- If `"name": "Gen Z"` → correct, it's a Page Token
- If `"name": "Tan Zay"` (or Marc's real name) → WRONG, it's a User Token. Redo the steps above.

---

### Problem 3 — Token Expiry (Happens Every 2-3 Days)

**What happened:**
The short-lived token Marc generated expired after a few days. When expired, every API call
returns: `"Session has expired on Sunday, 05-Apr-26 at 1:00am PDT"`

**The fix:**
You must regenerate the token. Follow Problem 2 steps above to get a fresh token.
Then update `.env`: `INSTAGRAM_ACCESS_TOKEN=NEW_TOKEN_HERE`

**How to know if your token is expired:**
Run this in your browser:
```
https://graph.facebook.com/me?access_token=YOUR_TOKEN
```
- Works → not expired
- Shows error → expired, regenerate it

**Optional — Get a 60-day Long-Lived Token:**
Short-lived tokens last 1-2 hours. You can exchange them for a 60-day token:
```
https://graph.facebook.com/oauth/access_token
  ?grant_type=fb_exchange_token
  &client_id=YOUR_APP_ID
  &client_secret=YOUR_APP_SECRET
  &fb_exchange_token=YOUR_SHORT_LIVED_TOKEN
```
The App ID and App Secret are in the Facebook Developer App dashboard under Settings → Basic.
This gives you a token that lasts 60 days instead of hours. Still expires — just less often.

---

### Problem 4 — `instagram_content_publish` Permission Error

**What happened:**
Even with a Page Token, Marc got an error saying the app doesn't have
`instagram_content_publish` permission. This permission is required to post images.

**Why this happens:**
In Facebook's App Review system, `instagram_content_publish` is a restricted permission.
However, while the app is in **Development Mode**, you can use it on test users and page admins
without full review.

**The fix:**
1. Go to https://developers.facebook.com → your app
2. Go to **App Review → Permissions and Features**
3. Find `instagram_content_publish`
4. Click **Request** (this is for production/live mode)
   — OR —
   If still in Development Mode, make sure your account is listed as an **Administrator** or
   **Tester** in **Roles** section of the app dashboard

**Check your role:**
- Facebook Developer App → Roles → Roles
- Your Facebook account must appear under **Administrators** or **Testers**
- If not listed, add yourself

---

### Problem 5 — Image URLs Blocked by Instagram

**What happened:**
The script was uploading images to **ImgBB** (`i.ibb.co`). Instagram's servers tried to fetch
those images but blocked them — ImgBB URLs don't work with the Instagram Graph API.

**The fix:**
The script now uses a fallback chain:
1. **0x0.st** — tried first (sometimes returns 503 error, skip if fails)
2. **litterbox.catbox.moe** — tried second (works well, URLs last 72 hours)
3. **ImgBB** — final fallback

This is already coded into `publisher/post_generator.py`. No action needed unless all 3 fail.

If all 3 fail, the error will be: `"All image upload services failed."`
Fix: check your internet connection, wait 5 minutes, try again.

---

### Problem 6 — App is in Development Mode (Posts Not Public)

**What happened:**
The Facebook App was in **Development Mode**. This means posts published through the API are
only visible to app admins and testers — NOT to the general public. Real followers cannot see
the posts.

**How to check which mode you're in:**
1. Go to https://developers.facebook.com → your app
2. Look at the top of the page for a toggle: **Development | Live**

**How to go Live:**
1. Toggle from **Development** to **Live**
2. Facebook requires:
   - A valid Privacy Policy URL (create a simple one-page site or use a free generator)
   - An app icon (1024×1024px)
   - Business verification (may be required for instagram_content_publish)
3. For now, while testing, Development Mode is fine — just know posts won't be public

**Workaround while in Development Mode:**
Post manually from Instagram instead of through the API. Generate the images with the script,
then upload them manually on Instagram. Fully public, no API needed.

---

### Summary — Checklist Before Every Publishing Run

Before running `python post_generator.py`, always verify:

- [ ] Token is NOT expired (test it in browser — see Problem 3)
- [ ] Token is a PAGE token, not a User token (name should show "Gen Z" — see Problem 2)
- [ ] `.env` has the correct Instagram User ID: `17841478285470926`
- [ ] `.env` token starts with `EAAb` and is the latest one you generated
- [ ] Google Sheet has at least one row with Status = "Ready"
- [ ] You have internet connection (image upload needs it)

---

## Common Errors and Fixes

| Error | Fix |
|-------|-----|
| `Session has expired` | Token is expired — get a new one (Step 8) |
| `instagram_content_publish` permission | Token missing permission — regenerate with correct permissions |
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` again |
| `FileNotFoundError: google_service_account.json` | Put the file in the project root folder |
| `No unpublished rows found` | Add a new row to Google Sheets with Status = "Ready" |
| `DALL-E failed` | OpenAI API key is wrong or out of credits |
| Image upload fails | Try running again — upload services sometimes have downtime |

---

## File Structure (What Each File Does)

```
Gen Z autamation/
├── .env                          ← ALL secrets — never share this
├── google_service_account.json   ← Google login credentials
├── requirements.txt              ← Python packages list
├── logo.png                      ← Brand logo
│
├── research/
│   └── research.py               ← Run this to find new topics
│
├── publisher/
│   ├── post_generator.py         ← Run this to generate + publish posts
│   ├── carousel_generator.py     ← Auto-called for carousel posts
│   ├── scheduler.py              ← Runs publisher automatically on schedule
│   └── usage_guard.py            ← Stops spending if budget exceeded
│
├── assets/images/generated/      ← All output images saved here
└── logs/                         ← Log files from each run
```

---

## Contacts

- **Marc** — owner of all accounts and API keys
- **Facebook Developer App** — Marc must add you as a tester to use the Graph API
- **Google Sheet** — Marc must share with your Gmail or service account email

---

## Quick Reference — Commands

```bash
# Generate posts locally (no Instagram):
cd publisher
python post_generator.py --local

# Generate + post to Instagram:
cd publisher
python post_generator.py

# Run research to find new topics:
cd research
python research.py

# Test just image generation:
cd publisher
python post_generator.py --dry-run
```

---

## Security Rules

- **Never share the `.env` file** — it contains real API keys
- **Never commit `.env` to GitHub**
- **Never share the `google_service_account.json`**
- The Instagram token is short-lived — refresh it every 2-3 days
- If keys are leaked, Marc must regenerate them immediately from:
  - OpenAI: https://platform.openai.com/api-keys
  - Facebook: https://developers.facebook.com
  - ImgBB: https://api.imgbb.com/

---

*This guide covers everything needed. If something breaks, check the logs folder first,
then refer to the Common Errors table above.*
