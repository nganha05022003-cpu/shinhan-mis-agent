"""
test_api_connection.py
Minimal sanity check: does OPENAI_API_KEY actually work?
No tools, no database — just one plain text call, like example in our chat earlier.

Run: python3 agent/test_api_connection.py
"""

import os
from openai import OpenAI

api_key = os.environ.get("OPENAI_API_KEY")
if not api_key:
    raise SystemExit("OPENAI_API_KEY not found in environment. Did 'setx' actually take effect? "
                      "Try closing and reopening PowerShell, then run 'echo $env:OPENAI_API_KEY' to check.")

client = OpenAI(api_key=api_key)

response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[
        {"role": "user", "content": "Say 'API connection working' and nothing else."}
    ],
)

print("Response from model:")
print(response.choices[0].message.content)
