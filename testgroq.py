from groq import Groq

client = Groq()

response = client.chat.completions.create(
    model="openai/gpt-oss-120b",
    messages=[
        {
            "role": "user",
            "content": "Hey what's up?"
        }
    ]
)

print(response.choices[0].message.content)

