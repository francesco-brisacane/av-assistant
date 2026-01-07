import google.generativeai as genai
import os

# Inserisci la tua chiave qui per il test rapido o usa os.environ
api_key = "AIzaSyB6EM_kcZvTPobZGTXFRHLlQZJHGa7065w" 
genai.configure(api_key=api_key)

print("Elenco modelli disponibili per te:")
for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        print(f"- {m.name}")