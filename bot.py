import os
import time
import json
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from typing import Any, Optional, Dict, List
from fastapi.middleware.cors import CORSMiddleware
import google.generativeai as genai

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

START = time.time()

# Configure your Gemini API key (Ensure this is set in your environment before running, or replace locally)
# os.environ["GEMINI_API_KEY"] = "your-api-key"
genai.configure(api_key=os.environ.get("GEMINI_API_KEY", ""))

# Try to use gemini-3.5-flash as it is fast and suitable for composing messages
try:
    model = genai.GenerativeModel('gemini-3.5-flash')
except Exception:
    model = None

# In-memory stores
contexts: Dict[tuple[str, str], dict] = {}    # (scope, context_id) -> {version, payload}
conversations: Dict[str, list] = {}           # conversation_id -> [turns]

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Magicpin AI Bot is running!"}

@app.get("/v1/healthz")
async def healthz():
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for (scope, _), _ in contexts.items():
        counts[scope] = counts.get(scope, 0) + 1
    return {"status": "ok", "uptime_seconds": int(time.time() - START), "contexts_loaded": counts}

@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": "Antigravity Bot", 
        "team_members": ["Agent"], 
        "model": "gemini-3.5-flash",
        "approach": "prompt composer with context injection", 
        "contact_email": "bot@example.com",
        "version": "1.0.0", 
        "submitted_at": datetime.utcnow().isoformat() + "Z"
    }

class CtxBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str

@app.post("/v1/context")
def push_context(body: CtxBody):
    key = (body.scope, body.context_id)
    cur = contexts.get(key)
    if cur and cur["version"] >= body.version:
        return {"accepted": False, "reason": "stale_version", "current_version": cur["version"]}
    contexts[key] = {"version": body.version, "payload": body.payload}
    return {"accepted": True, "ack_id": f"ack_{body.context_id}_v{body.version}",
            "stored_at": datetime.utcnow().isoformat() + "Z"}

class TickBody(BaseModel):
    now: str
    available_triggers: List[str] = []

@app.post("/v1/tick")
def tick(body: TickBody):
    actions = []
    for trg_id in body.available_triggers:
        trg = contexts.get(("trigger", trg_id), {}).get("payload")
        if not trg: continue
        merchant_id = trg.get("merchant_id")
        merchant = contexts.get(("merchant", merchant_id), {}).get("payload")
        category = contexts.get(("category", merchant.get("category_slug")), {}).get("payload") if merchant else None
        
        if not (merchant and category): continue
        
        prompt = f"""
You are Vera, an AI assistant for magicpin merchants. Compose a WhatsApp message for a merchant based on the contexts below.

Category Voice: {category.get('voice', {}).get('tone', '')}
Allowed Vocab: {category.get('voice', {}).get('vocab_allowed', [])}
Taboos: {category.get('voice', {}).get('taboos', [])}
Digest: {json.dumps(category.get('digest', []))}
Peer Stats: {json.dumps(category.get('peer_stats', {}))}

Merchant Name: {merchant.get('identity', {}).get('name', '')}
Merchant Locality: {merchant.get('identity', {}).get('locality', '')}
Merchant Languages: {merchant.get('identity', {}).get('languages', [])}
Active Offers: {json.dumps([o for o in merchant.get('offers', []) if o.get('status') == 'active'])}
Performance: {json.dumps(merchant.get('performance', {}))}

Trigger: {json.dumps(trg)}

Rules:
1. Include specific verifiable facts from the contexts (numbers, dates, headlines).
2. Match the tone of the category (e.g. clinical for dentists, warm for salons).
3. Honor language preference (use Hindi-English mix if 'hi' is present).
4. Do NOT hallucinate data or competitors.
5. End with a single clear Call To Action (CTA), like "Reply YES to do X".

Return ONLY a valid JSON object (without markdown code blocks) with the following structure:
{{
    "body": "The actual WhatsApp message text",
    "cta": "open_ended" or "none" or "YES/STOP",
    "rationale": "Why this message is compelling, leveraging which psychological lever"
}}
"""
        try:
            if not model:
                raise Exception("LLM not initialized")
            response = model.generate_content(prompt, generation_config={"temperature": 0.2, "response_mime_type": "application/json"})
            text = response.text.replace('```json\n', '').replace('```', '').strip()
            data = json.loads(text)
            
            actions.append({
                "conversation_id": f"conv_{merchant_id}_{trg_id}_{int(time.time())}",
                "merchant_id": merchant_id, 
                "customer_id": None,
                "send_as": "vera", 
                "trigger_id": trg_id,
                "template_name": "vera_composer_v1",
                "template_params": [],
                "body": data.get("body", "Hi, I noticed some activity on your profile. Reply YES to know more!"), 
                "cta": data.get("cta", "open_ended"),
                "suppression_key": trg.get("suppression_key", ""),
                "rationale": data.get("rationale", "Generated by LLM")
            })
        except Exception as e:
            print(f"Error calling LLM for tick: {e}")
            pass
            
    return {"actions": actions}

class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int

@app.post("/v1/reply")
def reply(body: ReplyBody):
    conv = conversations.setdefault(body.conversation_id, [])
    conv.append({"from": body.from_role, "msg": body.message})
    
    # Auto-reply detection rule: If the last 3 messages from the merchant are identical, end conversation
    merchant_msgs = [m["msg"] for m in conv if m["from"] == "merchant"]
    if len(merchant_msgs) >= 3 and merchant_msgs[-1] == merchant_msgs[-2] == merchant_msgs[-3]:
        return {
            "action": "end",
            "rationale": "Detected auto-reply loop. Gracefully exiting."
        }
        
    merchant = contexts.get(("merchant", body.merchant_id), {}).get("payload") if body.merchant_id else {}
    category = contexts.get(("category", merchant.get("category_slug")), {}).get("payload") if merchant else {}

    prompt = f"""
You are Vera, an AI assistant for magicpin merchants. Continue the conversation with the merchant.

Merchant Details: {json.dumps(merchant.get('identity', {}))}
Category Tone: {category.get('voice', {}).get('tone', '')}

Conversation History:
{json.dumps(conv, indent=2)}

Rules for replying:
1. If the merchant expresses explicit intent (e.g. "let's do it", "ok proceed"), move to ACTION mode immediately. Do not ask more qualifying questions.
2. If the merchant is hostile, abusive, or explicitly asks to stop, END the conversation politely and apologize.
3. Keep the conversation context and stay helpful but concise.

Return ONLY a valid JSON object (without markdown code blocks) with the following structure:
{{
    "action": "send" or "wait" or "end",
    "body": "The text to send (if action is 'send')",
    "wait_seconds": 1800 (if action is 'wait'),
    "rationale": "Explanation for the decision"
}}
"""
    try:
        if not model:
            raise Exception("LLM not initialized")
        response = model.generate_content(prompt, generation_config={"temperature": 0.2, "response_mime_type": "application/json"})
        text = response.text.replace('```json\n', '').replace('```', '').strip()
        data = json.loads(text)
        
        action = data.get("action", "send")
        if action == "send":
            conv.append({"from": "vera", "msg": data.get("body", "")})
            return {
                "action": "send", 
                "body": data.get("body", "I understand. Let me look into that."), 
                "cta": "open_ended",
                "rationale": data.get("rationale", "")
            }
        elif action == "wait":
            return {
                "action": "wait",
                "wait_seconds": data.get("wait_seconds", 300),
                "rationale": data.get("rationale", "")
            }
        else:
            return {
                "action": "end",
                "rationale": data.get("rationale", "")
            }
    except Exception as e:
        print(f"Error calling LLM for reply: {e}")
        return {
            "action": "send", 
            "body": "Got it, I'll update things accordingly.", 
            "cta": "open_ended",
            "rationale": "Fallback response due to LLM error"
        }
