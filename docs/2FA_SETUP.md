# Dashboard TOTP 2FA Setup

Optional time-based one-time password (TOTP) two-factor authentication for all
`/api/*` endpoints.  When `DASHBOARD_OTP_SECRET` is set, every login requires a
valid six-digit code from an authenticator app in addition to the normal
username/password.

---

## 1. Generate a secret

Run once on the server where the agent lives:

```powershell
.\.venv\Scripts\python.exe -c "import pyotp; print(pyotp.random_base32())"
```

Copy the printed base32 string (e.g. `JBSWY3DPEHPK3PXP`).  Keep it safe — it
is the seed for every future code.

---

## 2. Add the secret to .env

```dotenv
DASHBOARD_OTP_SECRET=JBSWY3DPEHPK3PXP
```

Restart the API server after saving.

---

## 3. Enroll your authenticator app

### Google Authenticator / Authy / any TOTP app

Either scan a QR code or enter the secret manually.

**Generate a QR code** (run once, then discard the output):

```python
import pyotp, qrcode          # pip install qrcode[pil]
secret = "JBSWY3DPEHPK3PXP"   # replace with your secret
uri = pyotp.totp.TOTP(secret).provisioning_uri(
    name="admin", issuer_name="pypoc-dashboard"
)
img = qrcode.make(uri)
img.save("2fa_qr.png")
print("Scan 2fa_qr.png with your app, then delete it.")
```

**Or enter manually in Google Authenticator:**

1. Open the app → tap **+** → *Enter a setup key*
2. Account: `pypoc-dashboard (admin)`
3. Key: paste your base32 secret
4. Type: **Time-based**
5. Tap *Add*

---

## 4. How to authenticate

All `/api/*` endpoints use HTTP Basic Auth.  When 2FA is enabled, encode the
OTP into the **username** field separated by a colon:

| Field    | Value                        |
|----------|------------------------------|
| username | `admin:<6-digit-otp>`        |
| password | `<DASHBOARD_PASSWORD>`       |

### curl example

```bash
curl -u "admin:123456" \
     -H "Content-Type: application/json" \
     https://your-server:8502/api/snapshot
```

### Python / requests example

```python
import pyotp, requests

secret = "JBSWY3DPEHPK3PXP"   # same secret stored in .env
otp    = pyotp.TOTP(secret).now()

resp = requests.get(
    "http://localhost:8502/api/snapshot",
    auth=(f"admin:{otp}", "pypoc2024"),
)
print(resp.json())
```

### React / fetch example (frontend)

```js
const otp      = "123456";          // obtained from the user's authenticator app
const password = "pypoc2024";

const resp = await fetch("/api/snapshot", {
  headers: {
    Authorization: "Basic " + btoa(`admin:${otp}:${password}`),
    //  ^^ username = "admin:<otp>", password field embedded in encoded string
  },
});
```

> Note: the browser's built-in Basic Auth dialog does not support the colon
> convention.  Use the React frontend or a REST client (curl, Postman, httpx)
> instead.

---

## 5. Disable 2FA

Remove or blank out the variable in `.env`:

```dotenv
DASHBOARD_OTP_SECRET=
```

Restart the API server.  Normal single-factor auth resumes immediately.

---

## 6. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `401 2FA required` | You set `DASHBOARD_OTP_SECRET` but did not include an OTP in the username |
| `401 Invalid or expired OTP code` | Code is wrong or clock drift >30 s — sync device clock (NTP) |
| `503 Server 2FA misconfigured` | `pyotp` is not installed — run `pip install pyotp` |
| Codes never match | Confirm the base32 secret in `.env` matches what was scanned/entered in the app |
