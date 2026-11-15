# Google sign-in (Gmail and Google Calendar)

Kelo connects Gmail and Google Calendar with a "Sign in with Google" button. The operator (the person who runs the server that
holds the shared sign-in relay, `https://kelo.chat/oauth/relay`) registers **one** Google OAuth client. Every other copy of Kelo
uses that client through the operator's server:

- The client secret is stored only on the operator's server, encrypted. Other copies never see it.
- Google access tokens expire after an hour. To get a new one, a copy asks the operator's server (`POST /broker/google/refresh`).
  The operator's server adds the secret, asks Google, and passes the answer back without keeping it.
- Each connector asks Google only for its own scopes. Gmail asks for `https://mail.google.com/`. Calendar asks for
  `calendar.readonly` and `calendar.events`. Both requests send `include_granted_scopes=true`, so one Google account can grant
  Gmail and Calendar side by side. Connecting one never asks for the other's scopes (incremental authorization).
- Kelo stores, for each connected account, the address, the scopes Google actually granted, and the tokens. They are kept in the
  encrypted vault and never shown to the agent.

Until the operator finishes the steps below, the app explains why the button can't be used yet ("The operator hasn't set up Google
sign-in yet."). Gmail can still be connected with an app password, and Calendar with a Google service account.

## 1. Create a Google Cloud project

1. Open <https://console.cloud.google.com/> and sign in with the Google account that will own the app.
2. Project picker (top bar) → **New project** → give it a name (for example `kelo`) → **Create**. Make sure the new project is
   selected.
3. **No billing account is needed.** Standard use of the Gmail API and the Google Calendar API costs nothing. If the console offers a
   free trial, asks for prepayment, or asks for a payment method, you can close or skip that prompt. Enabling these two APIs works
   without a billing account. (Google's OAuth guide says to enable billing only "if prompted":
   <https://developers.google.com/identity/protocols/oauth2/web-server>.)
   - Calendar: "All standard use of the Google Calendar API is available at no additional cost."
     (<https://developers.google.com/workspace/calendar/api/guides/quota>)
   - Gmail: "All standard use of the Gmail API is available at no additional cost."
     (<https://developers.google.com/workspace/gmail/api/reference/quota>)
   - Both pages also say that charges for going over the quota are planned "later in 2026". The free daily amounts are large: about
     1,000,000 Calendar requests and 80,000,000 Gmail quota units per project per day. A personal Kelo uses a tiny fraction of that.

## 2. Turn on the APIs

**APIs & Services → Library**. Search for each API, open it, and click **Enable**:

- **Gmail API**. Kelo reads and sends mail over IMAP/SMTP with the Google token, using the `https://mail.google.com/` scope below.
  With the API turned on, the console lists the Gmail scopes in step 4.
- **Google Calendar API**

If the Calendar API is off, connecting Calendar fails with "The Google Calendar API is not turned on in the operator's Google project
yet." and nothing is saved.

## 3. Set up the consent screen (Google Auth Platform)

Menu → **Google Auth Platform** → **Branding**. If the page says "Google Auth platform not configured yet", click **Get started**.
(Reference: <https://developers.google.com/workspace/guides/configure-oauth-consent>)

1. **App information**: App name `Kelo`, and pick a **User support email** → **Next**.
2. **Audience**: choose **External** → **Next**.
3. **Contact information**: enter your email → **Next**.
4. **Finish**: tick "I agree to the Google API Services: User Data Policy" → **Continue** → **Create**.
5. Leave the publishing status as **Testing** (see the limits in section 6).
6. **Audience** → **Test users** → **Add users**. Add the Google address of every person who will sign in (you as well) →
   **Save**. In Testing mode, only these addresses can sign in.

## 4. Add the scopes

**Google Auth Platform → Data Access → Add or remove scopes**. If a scope isn't listed, paste it into **Manually add scopes**,
then click **Update** and **Save**. (Reference: <https://support.google.com/cloud/answer/15549135>)

| Scope | Used for | Google's category |
|--- |--- |--- |
| `openid`, `.../auth/userinfo.email` | knowing which account signed in | non-sensitive |
| `https://mail.google.com/` | Gmail (read, search, send through IMAP/SMTP) | **restricted** |
| `https://www.googleapis.com/auth/calendar.readonly` | reading calendars and events | sensitive (see note) |
| `https://www.googleapis.com/auth/calendar.events` | adding, changing and deleting events | sensitive (see note) |

- `https://mail.google.com/` is listed as **restricted** on Google's Gmail scope page
  (<https://developers.google.com/workspace/gmail/api/auth/scopes>). Google's restricted-scope list says it "includes any usage of
  IMAP, SMTP, and POP3 protocols" (<https://support.google.com/cloud/answer/13464325>). Kelo uses IMAP/SMTP, so it needs this scope.
- Note on the Calendar scopes: Google's public scope tables don't print a category. Google says that "sensitive scopes ... have a
  *sensitive* indicator on the Google Cloud Console's OAuth consent screen configuration page"
  (<https://developers.google.com/identity/protocols/oauth2/scopes>). The **Data Access** page shows the category when you add
  these two scopes. They are not restricted.

## 5. Create the Web client

**Google Auth Platform → Clients → Create client**. (Reference: <https://support.google.com/cloud/answer/15549257>)

1. Application type: **Web application**. Name: `Kelo`.
2. **Authorized redirect URIs** → **Add URI** → `https://kelo.chat/oauth/relay` (exactly, https, no trailing slash).
   No JavaScript origins are needed.
3. **Create**. Copy the **Client ID** (ends with `.apps.googleusercontent.com`) and the **Client secret** (starts with `GOCSPX-`).
   Google notes that a change to a client can take from 5 minutes to a few hours to apply.
4. In the Kelo app on the operator's server: **My tab → Operator settings → Sign-in apps → Google OAuth client**. Paste both values
   and save. (This calls `PUT /api/oauth-apps/google`. The secret is encrypted on this server and never shown again.) The page then
   says this server relays sign-ins.
5. Check: in **Connectors**, open **Gmail** or **Google Calendar** and tap the Google sign-in button. Google's page should list only
   that connector's permissions.

## 6. Limits of Testing mode, and what publishing involves

Facts below are quoted from Google's documentation (checked November 2026).

- **At most 100 test users.** "Projects configured with a publishing status of Testing are limited to up to 100 test users listed in
  the OAuth consent screen." (<https://support.google.com/cloud/answer/15549945>)
- **Sign-ins expire after 7 days in Testing.** "A Google Cloud Platform project with an OAuth consent screen configured for an
  external user type and a publishing status of 'Testing' is issued a refresh token expiring in 7 days, unless the only OAuth scopes
  requested are a subset of name, email address, and user profile."
  (<https://developers.google.com/identity/protocols/oauth2#expiration>). For Kelo, this means each person has to connect Gmail and
  Calendar again every week. When the token stops working, the Connectors tab shows the connection needs attention, and the agent
  asks the user to reconnect.
- **Other ways a refresh token stops working**, in any mode: it wasn't used for six months; the user changed their password and the
  token has Gmail scopes; or the account already has 100 refresh tokens for this client, in which case the oldest is dropped
  without warning (same page).
- **Publishing (In production) requires Google's verification for these scopes.** "Apps that request access to scopes categorized
  as sensitive or restricted must complete Google's OAuth app verification before being granted access."
  (<https://support.google.com/cloud/answer/13463073>). Until an app is verified, Google shows an "unverified app" screen, and that
  screen is allowed for only 100 new users in total (same page).
  - **Calendar (sensitive)**: standard app verification (brand and domain checks, a privacy policy, and a video that shows how the
    scopes are used).
  - **Gmail with `https://mail.google.com/` (restricted)**: verification **plus a yearly security assessment**. "Applications
    requesting access to restricted scopes must undergo an annual security assessment" under the "Cloud App Security Assessment
    framework (CASA)", and "All applications must be revalidated every year." (<https://support.google.com/cloud/answer/13465431>).
    Google's verification page also says: "Apps requesting restricted scopes data need to complete 're-verification' annually."
    (<https://support.google.com/cloud/answer/13463073>)
- What this means in practice: for a small group (up to 100 people, reconnecting every week), Testing mode is enough. Publishing
  Calendar alone is a one-time review. Publishing Gmail with full mailbox access also needs a paid third-party security assessment
  every year.

## How it works in Kelo

- `src/assistant/google_web.py` builds the sign-in link (`PRODUCTS`: the scopes for each connector), exchanges the code, and
  refreshes tokens through the broker.
- `src/assistant/google_calendar.py` handles the Calendar account, the API calls, and the agent's tools: `gcal_calendars` and
  `gcal_events` read, while `gcal_create_event`, `gcal_update_event` and `gcal_delete_event` each show an approval card that says
  what will change.
- The app's endpoints are `GET /api/google/calendar` (can the sign-in be used here, and if not, why; the connected accounts),
  `POST /api/google/web/start {"origin", "product": "gmail"|"calendar"}`, and `DELETE /api/credentials/{name}` to disconnect.
