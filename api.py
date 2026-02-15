import requests
import json

def call_llm(provider, config, messages):
    if provider == "openrouter":
        yield from call_openrouter(config, messages)
    elif provider == "ollama":
        yield from call_ollama(config, messages)
    elif provider == "openai":
        yield from call_openai(config, messages)
    else:
        raise ValueError(f"Unknown provider: {provider}")

def call_openrouter(config, messages):
    url = "https://openrouter.ai/api/v1/chat/completions"
    api_key = config.get("api_key")
    if not api_key:
        raise ValueError("api_key is missing in openrouter config")
    headers = {
        "Authorization": f"Bearer {api_key}",
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
    base_url = config.get("base_url")
    if not base_url:
        raise ValueError("base_url is missing in ollama config")
    url = f"{base_url}/api/chat"
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

def call_openai(config, messages):
    api_url = config.get("api_url")
    if not api_url:
        raise ValueError("api_url is missing in openai config")
    url = f"{api_url.rstrip('/')}/chat/completions"
    api_key = config.get("api_key", "sk-no-key-required")
    headers = {
        "Authorization": f"Bearer {api_key}",
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
                    if data.get("choices") and len(data["choices"]) > 0:
                        content = data["choices"][0]["delta"].get("content", "")
                        if content:
                            yield content
                except json.JSONDecodeError:
                    continue

def get_models(provider, config):
    if provider == "openrouter":
        url = "https://openrouter.ai/api/v1/models"
        api_key = config.get("api_key")
        if not api_key: return []
        headers = {"Authorization": f"Bearer {api_key}"}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        return [f"openrouter:{m['id']}" for m in data.get("data", [])]
    elif provider == "ollama":
        base_url = config.get("base_url")
        if not base_url: return []
        url = f"{base_url}/api/tags"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        return [f"ollama:{m['name']}" for m in data.get("models", [])]
    elif provider == "openai":
        api_url = config.get("api_url")
        if not api_url: return []
        url = f"{api_url.rstrip('/')}/models"
        api_key = config.get("api_key", "sk-no-key-required")
        headers = {"Authorization": f"Bearer {api_key}"}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        return [f"openai:{m['id']}" for m in data.get("data", [])]
    return []
