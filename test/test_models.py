import requests

with open('.env') as f:
    key = [line.split('=')[1].strip() for line in f if line.startswith('GROQ_API_KEY=')][0]

url = "https://api.groq.com/openai/v1/models"
headers = {"Authorization": f"Bearer {key}"}

response = requests.get(url, headers=headers)
models = response.json()

for m in models.get('data', []):
    if 'vision' in m['id'].lower() or '3.2' in m['id'].lower():
        print(f"Found: {m['id']}")
