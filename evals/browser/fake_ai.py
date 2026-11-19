"""A fake AI server that speaks the same language as Groq (OpenAI chat format) and acts like a sensible model.
For end-to-end tests only. It never calls the internet."""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEYS_SEEN = "keys_seen.txt"


def tool_call(name, args):
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": f"call_{name}_{abs(hash(json.dumps(args))) % 10**6}", "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)}}]}


def say(text):
    return {"role": "assistant", "content": text}


def brain(messages):
    last = messages[-1]
    if last["role"] == "tool":
        res = json.loads(last["content"])
        # which tool answered? find the assistant call just before
        call = next(m for m in reversed(messages) if m["role"] == "assistant" and m.get("tool_calls"))
        name = call["tool_calls"][0]["function"]["name"]
        if name == "check_free_times":
            slots = res.get("slots") or res.get("nearest") or []
            if not slots:
                return say("Sorry, I have no free times that day.")
            user = next(m for m in reversed(messages) if m["role"] == "user")["content"]
            phone = re.search(r"(\d{3}[-\s]?\d{3}[-\s]?\d{4})", user)
            name_m = re.search(r"I'm (\w+)", user)
            return tool_call("book_appointment", {"start": slots[0]["start"], "customer_name": name_m.group(1) if name_m else "Guest",
                                                  "service": "Drain cleaning", "contact_phone": phone.group(1) if phone else None,
                                                  "phone_confirmed": True})
        if name == "book_appointment":
            if res.get("ok"):
                return say(f"You're booked: {res.get('confirmation', '')}")
            return say("Sorry, that time is not available. " + ", ".join(s["label"] for s in res.get("nearest", [])))
        if name == "save_lead":
            return say("Thanks, a person will call you back soon.")
        return say("Done.")
    text = (last.get("content") or "").lower()
    if "book" in text:
        return tool_call("check_free_times", {"date": "2026-10-13", "service": "Drain cleaning"})
    if "price" in text or "cost" in text:
        return say("Drain cleaning is $149.")
    if "hours" in text or "open" in text:
        return say("We're open Monday to Friday, 8 AM to 5 PM.")
    if "water heater" in text:
        return tool_call("save_lead", {"name": "Sam", "need": "water heater install quote", "reason": "price_followup"})
    return say("Hi! How can I help you today?")


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers["content-length"])
        body = json.loads(self.rfile.read(n))
        with open(KEYS_SEEN, "a") as f:
            f.write(self.headers.get("authorization", "")[-4:] + "\n")
        msg = brain(body["messages"])
        out = {"choices": [{"message": msg, "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}],
               "usage": {"prompt_tokens": 1400, "completion_tokens": 30}, "model": body.get("model")}
        b = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


ThreadingHTTPServer(("127.0.0.1", 8765), H).serve_forever()
