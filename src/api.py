"""
WhatsApp Business Cloud API Integration

Handles:
- Webhook verification (Meta requires this)
- Incoming message webhooks
- Outbound message sending via Cloud API
- Integration with gatekeeper and bot service
"""

import hashlib
import hmac
import os
from typing import Optional

import httpx
import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, Response

# =============================================================================
# CONFIGURATION
# =============================================================================

WHATSAPP_ACCESS_TOKEN = os.environ.get('WHATSAPP_ACCESS_TOKEN', '')
WHATSAPP_PHONE_NUMBER_ID = os.environ.get('WHATSAPP_PHONE_NUMBER_ID', '')
WHATSAPP_VERIFY_TOKEN = os.environ.get('WHATSAPP_VERIFY_TOKEN', '')
WHATSAPP_APP_SECRET = os.environ.get('WHATSAPP_APP_SECRET', '')
GATEKEEPER_URL = os.environ.get('GATEKEEPER_URL', 'http://gatekeeper:8080')
BOT_SERVICE_URL = os.environ.get('BOT_SERVICE_URL', 'http://bot:8080')

WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"

# =============================================================================
# LOGGING
# =============================================================================

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(colors=True)
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger()

# =============================================================================
# APP
# =============================================================================

app = FastAPI(title="WhatsApp Integration", docs_url="/docs")


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "whatsapp",
        "configured": bool(WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID),
    }


# =============================================================================
# WEBHOOK VERIFICATION
# =============================================================================

@app.get("/webhook")
async def verify_webhook(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge"),
):
    """
    Webhook verification endpoint.

    Meta sends a GET request with hub.mode, hub.verify_token, and hub.challenge.
    We must respond with the challenge if the token matches.
    """
    log.info("webhook_verification", mode=mode, token_match=(token == WHATSAPP_VERIFY_TOKEN))

    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        log.info("webhook_verified")
        return Response(content=challenge, media_type="text/plain")

    raise HTTPException(status_code=403, detail="Verification failed")


# =============================================================================
# INCOMING MESSAGES
# =============================================================================

@app.post("/webhook")
async def receive_webhook(request: Request):
    """
    Receive incoming webhook from WhatsApp.

    Processes incoming messages and routes them through gatekeeper -> bot.
    """
    body = await request.json()

    # Verify signature if app secret is configured
    if WHATSAPP_APP_SECRET:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not verify_signature(await request.body(), signature):
            log.warning("invalid_signature")
            raise HTTPException(status_code=403, detail="Invalid signature")

    log.debug("webhook_received", body=body)

    # Extract message data
    try:
        entry = body.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})

        # Check if this is a message event
        messages = value.get("messages", [])
        if not messages:
            # Could be a status update, just acknowledge
            return {"status": "ok"}

        message = messages[0]
        sender = message.get("from")  # Phone number
        message_type = message.get("type")

        # Only handle text messages for now
        if message_type != "text":
            log.info("ignoring_non_text", type=message_type)
            return {"status": "ok"}

        text = message.get("text", {}).get("body", "")
        message_id = message.get("id")

        log.info("message_received", sender=sender, text=text[:50])

        # Process through gatekeeper and bot
        await process_message(sender, text, message_id)

    except Exception as e:
        log.error("webhook_processing_error", error=str(e))

    return {"status": "ok"}


def verify_signature(payload: bytes, signature: str) -> bool:
    """Verify the X-Hub-Signature-256 header."""
    if not signature.startswith("sha256="):
        return False

    expected = hmac.new(
        WHATSAPP_APP_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(f"sha256={expected}", signature)


# =============================================================================
# MESSAGE PROCESSING
# =============================================================================

async def process_message(sender: str, text: str, message_id: str):
    """
    Process incoming message through gatekeeper -> bot -> response.
    """
    # Build conversation ID from sender phone
    conversation_id = f"whatsapp_{sender}"

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            # 1. Send to gatekeeper for sanitization
            gk_response = await client.post(
                f"{GATEKEEPER_URL}/message",
                json={
                    "conversation_id": conversation_id,
                    "source": "whatsapp",
                    "user_id": sender,
                    "message": text,
                }
            )

            if gk_response.status_code != 200:
                log.error("gatekeeper_error", status=gk_response.status_code)
                await send_message(sender, "Sorry, I encountered an error processing your message.")
                return

            gk_data = gk_response.json()

            # Check for notifications (e.g., tools unlocked)
            notifications = gk_data.get("notifications", [])
            for notification in notifications:
                await send_message(sender, notification)

            # If message was empty (just a passphrase), we're done
            cleaned_message = gk_data.get("cleaned_message", "")
            if not cleaned_message.strip():
                return

            # 2. Send to bot service
            bot_response = await client.post(
                f"{BOT_SERVICE_URL}/chat",
                json={
                    "session_id": gk_data.get("session_id"),
                    "message": cleaned_message,
                    "available_tools": gk_data.get("available_tools", []),
                }
            )

            if bot_response.status_code != 200:
                log.error("bot_error", status=bot_response.status_code)
                await send_message(sender, "Sorry, I encountered an error generating a response.")
                return

            bot_data = bot_response.json()
            response_text = bot_data.get("response", "")

            # 3. Send response back to user
            if response_text:
                await send_message(sender, response_text)

            # 4. Record in conversation history
            await client.post(
                f"{GATEKEEPER_URL}/record",
                json={
                    "session_id": gk_data.get("session_id"),
                    "role": "assistant",
                    "content": response_text,
                }
            )

        except httpx.TimeoutException:
            log.error("request_timeout")
            await send_message(sender, "Sorry, the request timed out. Please try again.")
        except Exception as e:
            log.error("processing_error", error=str(e))
            await send_message(sender, "Sorry, something went wrong.")


# =============================================================================
# SEND MESSAGES
# =============================================================================

async def send_message(to: str, text: str):
    """
    Send a text message via WhatsApp Cloud API.

    Args:
        to: Recipient phone number (with country code, no +)
        text: Message text
    """
    url = f"{WHATSAPP_API_URL}/{WHATSAPP_PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    # WhatsApp has a 4096 character limit per message
    # Split long messages if needed
    chunks = split_message(text, 4000)

    async with httpx.AsyncClient() as client:
        for chunk in chunks:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"body": chunk}
            }

            try:
                response = await client.post(url, json=payload, headers=headers)

                if response.status_code != 200:
                    log.error("send_failed", status=response.status_code, body=response.text)
                else:
                    log.info("message_sent", to=to, length=len(chunk))

            except Exception as e:
                log.error("send_error", error=str(e))


def split_message(text: str, max_length: int) -> list[str]:
    """Split a long message into chunks."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break

        # Try to split at a newline or space
        split_at = text.rfind('\n', 0, max_length)
        if split_at == -1:
            split_at = text.rfind(' ', 0, max_length)
        if split_at == -1:
            split_at = max_length

        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()

    return chunks


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    log.info("starting_whatsapp_api", port=8089)
    uvicorn.run(app, host="0.0.0.0", port=8089)
