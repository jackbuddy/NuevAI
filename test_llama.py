import os
from groq import Groq

client = Groq(api_key=os.environ["GROQ_API_KEY"])

response = client.chat.completions.create(
    model="openai/gpt-oss-20b",
    messages=[
        {"role": "system", "content": "You are NuevAI, a helpful assistant for Nueva School."},
        {"role": "user", "content": "What's a fun fact about honeybees?"},
    ],
)

print(response.choices[0].message.content)
