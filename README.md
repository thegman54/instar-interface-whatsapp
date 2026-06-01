# Instar Interface: WhatsApp

> **Status: Work in Progress.** The code is complete and functional, but the Meta Business Account verification process required to use the WhatsApp Business Cloud API has not been fully validated. If you have an approved Meta Business Account, this interface should work as-is.

A WhatsApp interface for [Project Instar](https://github.com/thegman54/project-instar) that connects your bot to WhatsApp via the Meta Business Cloud API. Receives messages via webhook, sends responses via REST API.

## How It Works

```
WhatsApp user sends message
  └─► Meta Cloud → Webhook POST (inbound via tunnel/public URL)
        └─► This interface (whatsapp-api container)
              ├─► Gatekeeper /message  (sanitize passphrases, get tools)
              ├─► Bot /chat            (LLM processes message with tools)
              └─► Gatekeeper /record   (store conversation history)
                    └─► WhatsApp Cloud API (send response back to user)
```

Messages arrive via Meta's webhook system. The interface verifies the signature, extracts the text, routes through the Gatekeeper for passphrase extraction and tool resolution, sends the cleaned message to the bot service, and returns the response to the WhatsApp conversation. Long messages are automatically chunked to fit WhatsApp's 4096-character limit.

## Prerequisites

- A running Project Instar deployment (Gatekeeper + Bot + MCP Server)
- Docker and Docker Compose
- A **Meta Business Account** (requires verification by Meta)
- A public URL for webhook delivery (Cloudflare Tunnel, ngrok, or similar)

## Meta / WhatsApp Setup

### 1. Create a Meta Business Account

1. Go to [business.facebook.com](https://business.facebook.com/) and create or log into a Business Account
2. Complete Meta's business verification process (this can take days — requires legal business documents)

### 2. Create a WhatsApp App

1. Go to [developers.facebook.com](https://developers.facebook.com/) and click **My Apps** > **Create App**
2. Select **Business** as the app type
3. Fill in app details, select your Business Account
4. Click **Create App**
5. On the product setup page, find **WhatsApp** and click **Set Up**

### 3. Get Your Credentials

1. In the left sidebar, go to **WhatsApp** > **API Setup**
2. You'll see a **Temporary access token** — copy this as your initial `WHATSAPP_ACCESS_TOKEN`
   - For production, generate a permanent **System User Token** (see below)
3. Copy your **Phone number ID** → this is your `WHATSAPP_PHONE_NUMBER_ID`

### 4. Set Up a Permanent Token (Production)

The temporary token expires in 24 hours. For production:

1. Go to [business.facebook.com](https://business.facebook.com/) > **Settings** > **System Users**
2. Create a System User with **Admin** role
3. Click **Generate New Token**
4. Select your WhatsApp app
5. Add permissions: **`whatsapp_business_messaging`**, **`whatsapp_business_management`**
6. Copy the token → this is your permanent `WHATSAPP_ACCESS_TOKEN`

### 5. Configure Webhooks

1. In the Facebook Developer portal, go to **WhatsApp** > **Configuration**
2. Under **Webhook**, click **Edit**
3. Set the **Callback URL** to your public endpoint:
   ```
   https://your-public-domain.com/webhook
   ```
4. Set the **Verify Token** to a random string you choose → this becomes your `WHATSAPP_VERIFY_TOKEN`
5. Click **Verify and Save** — Meta will send a verification GET request to your endpoint
6. Under **Webhook Fields**, subscribe to: **`messages`**

### 6. Get App Secret (Optional but Recommended)

1. Go to **Settings** > **Basic** in the Facebook Developer portal
2. Copy the **App Secret** → this is your `WHATSAPP_APP_SECRET`
3. When configured, the interface will verify the `X-Hub-Signature-256` header on every webhook to ensure messages are genuinely from Meta

### 7. Set Up Webhook Ingress

The WhatsApp interface needs a public URL for Meta to deliver webhooks. Options:

- **Cloudflare Tunnel** — add a route in your existing tunnel pointing to `http://whatsapp-api:8089`
- **ngrok** — `ngrok http 8089`
- **Any reverse proxy** with a valid SSL certificate

The public URL you configure here is what goes in the Meta webhook configuration (step 5).

## Required Secrets

Store these in Infisical (or provide as environment variables):

| Secret Name | Description | Where to Find |
|---|---|---|
| `WHATSAPP_ACCESS_TOKEN` | System User Token or temporary token | Meta Business > System Users > Generate Token |
| `WHATSAPP_PHONE_NUMBER_ID` | Phone Number ID for your WhatsApp Business number | Facebook Developer > WhatsApp > API Setup |
| `WHATSAPP_VERIFY_TOKEN` | Webhook verification token (you choose this) | Any random string — must match webhook config |
| `WHATSAPP_APP_SECRET` | App Secret for webhook signature verification (optional) | Facebook Developer > Settings > Basic |

## Installation

### Via Admin UI (Recommended)

1. Download this repo as a zip file
2. In the Instar Admin UI, go to **Interfaces**
3. Click **Upload Interface** and select the zip
4. The interface will be auto-discovered via `manifest.yaml`
5. Configure the required secrets in Infisical
6. Start the interface from the Admin UI

### Manual

```bash
git clone https://github.com/thegman54/instar-interface-whatsapp.git tools/whatsapp
```

The Instar stack manager will detect the `manifest.yaml` and make the interface available.

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `WHATSAPP_ACCESS_TOKEN` | Yes | — | Meta API access token |
| `WHATSAPP_PHONE_NUMBER_ID` | Yes | — | WhatsApp Business phone number ID |
| `WHATSAPP_VERIFY_TOKEN` | Yes | — | Webhook verification token (you set this) |
| `WHATSAPP_APP_SECRET` | No | — | App secret for signature verification |
| `GATEKEEPER_URL` | No | `http://gatekeeper:8080` | Instar Gatekeeper API URL |
| `BOT_SERVICE_URL` | No | `http://bot:8080` | Instar Bot Service API URL |

## Endpoints

| Endpoint | Description |
|---|---|
| `GET /health` | Health check + configuration status |
| `GET /webhook` | Meta webhook verification (hub.challenge handshake) |
| `POST /webhook` | Incoming message webhook from Meta |

## Message Handling

- Only **text messages** are processed (images, audio, etc. are ignored with a 200 OK)
- Long responses are automatically split at newlines or spaces to fit WhatsApp's 4096-character limit
- Conversation ID is derived from the sender's phone number (`whatsapp_<phone>`)
- Passphrases unlocked in a conversation persist for that phone number's session

## License

MIT
