# magicpin AI Challenge - Bot Submission

## Approach
Our solution leverages a stateless-friendly architecture using FastAPI, but maintains in-memory state mapping for context tracking as required by the simulator. The core logic relies on **Google's Gemini 3.5 Flash** model (the primary high-speed reasoning model) to compose proactive messages and intelligently manage conversational turns.

### Key Components:
1. **Context Management**: We store all pushed contexts (`category`, `merchant`, `trigger`, `customer`) in a dictionary mapping `(scope, id)` to the latest payload payload version, ensuring atomic updates.
2. **Tick Composer (`/v1/tick`)**: For each tick, the bot iterates through active triggers, fetching the relevant `merchant` and `category` contexts. We inject these details into a strict prompt for the LLM. We enforce the model to output using `application/json` mime-type to guarantee a structurally sound parsing loop without markdown artifacts. 
3. **Reply Handler (`/v1/reply`)**: 
   - **Auto-reply mitigation**: Before hitting the LLM, the system runs a heuristic check (detecting if the last 3 merchant messages are strictly identical strings). If detected, it bypasses the LLM and hard-stops the conversation, preserving token budget and simulated time.
   - **Intent/Hostility tracking**: The conversation history is passed to the LLM with explicit rules to detect transition words ("let's do it") or hostility ("stop spamming"), gracefully switching to action steps or exiting.

## Tradeoffs Made
1. **In-Memory Storage vs. Persistent DB**: We opted for an in-memory `contexts` dictionary for speed and simplicity. In a real-world scenario (or if the testing harness restarted instances), this would be migrated to Redis or PostgreSQL.
2. **Prompt Complexity vs. Pipeline Chaining**: We utilized a single comprehensive prompt combining all contexts rather than a multi-agent retrieval chain. Given the strict 30-second timeout constraints of the challenge and the strong context-window performance of Gemini 3.5 Flash, this ensures faster generation times (`< 5s`) while still accurately reflecting the tone and specificity guidelines.

## Additional Context Requests
It would have been helpful to have access to historical A/B tested copy variants for the different verticals to better align the LLM's "Engagement Compulsion" strategies with empirical merchant click-through rates.
