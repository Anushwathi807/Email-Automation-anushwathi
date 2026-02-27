import os
from dotenv import load_dotenv
import requests

# Load environment variables
load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

def test_openrouter_connection():
    """Test OpenRouter API connection directly"""
    
    if not OPENROUTER_API_KEY:
        print("❌ OPENROUTER_API_KEY not found in .env file")
        return False
    
    print(f"✅ API Key loaded: {OPENROUTER_API_KEY[:20]}...")
    
    url = "https://openrouter.ai/api/v1/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "google/gemini-2.0-flash-001",
        "messages": [
            {
                "role": "user",
                "content": "Hello! Please confirm you are working. Reply with 'Connection successful'."
            }
        ],
        "max_tokens": 100
    }
    
    try:
        print("\n🔄 Sending test request to OpenRouter...")
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        
        print(f"📊 Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            message = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            print("\n--- Response from Gemini 2.0 Flash ---")
            print(message)
            print("--------------------------------------")
            print("\n✅ Test Passed! OpenRouter connection is working.")
            return True
        else:
            print(f"\n❌ Request failed with status {response.status_code}")
            print(f"Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"\n❌ Test Failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    test_openrouter_connection()
