from agent.llm import get_llm

print("🚀 Waking up the Groq API...\n")

try:
    # 1. Test the Light Model (Cleaning)
    print("Testing Cleaning Model (Llama 3 8B)...")
    llm_clean = get_llm(task_type="cleaning")
    response_clean = llm_clean.invoke("Reply with exactly the word: CLEAN")
    print(f"✅ Success! AI says: {response_clean.content}\n")

    # 2. Test the Heavy Model (Extraction)
    print("Testing Extraction Model (Llama 3 70B)...")
    llm_extract = get_llm(task_type="extraction")
    response_extract = llm_extract.invoke("Reply with exactly the word: EXTRACT")
    print(f"✅ Success! AI says: {response_extract.content}\n")

    print("🎉 All systems go! Phase 1 is officially complete.")

except Exception as e:
    print(f"❌ ERROR: Something went wrong. Details:\n{e}")