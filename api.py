import requests
import json

def call_llm(provider, config, messages):
    if provider == "openrouter":
        yield from call_openrouter(config, messages)
    elif provider == "ollama":
        yield from call_ollama(config, messages)
    else:
        raise ValueError(f"Unknown provider: {provider}")

def call_openrouter(config, messages):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": config["model"],
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        "stream": True
    }
    response = requests.post(url, headers=headers, json=payload, stream=True)
    response.raise_for_status()
    
    for line in response.iter_lines():
        if line:
            line = line.decode('utf-8')
            if line.startswith("data: "):
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    content = data["choices"][0]["delta"].get("content", "")
                    if content:
                        yield content
                except json.JSONDecodeError:
                    continue

def call_ollama(config, messages):
    url = f"{config['base_url']}/api/chat"
    payload = {
        "model": config["model"],
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        "stream": True
    }
    response = requests.post(url, json=payload, stream=True)
    response.raise_for_status()
    
    for line in response.iter_lines():
        if line:
            data = json.loads(line.decode('utf-8'))
            content = data.get("message", {}).get("content", "")
            if content:
                yield content
            if data.get("done"):
                break

def get_models(provider, config):
    if provider == "openrouter":
        url = "https://openrouter.ai/api/v1/models"
        headers = {"Authorization": f"Bearer {config['api_key']}"}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        return [m["id"] for m in data.get("data", [])]
    elif provider == "ollama":
        url = f"{config['base_url']}/api/tags"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        return [m["name"] for m in data.get("models", [])]
    return []
