from fastapi.middleware.cors import CORSMiddleware
from ai_adapter.ollama_adapter.ollama_bot import ask_ollama
from pydantic import BaseModel
from fastapi import FastAPI

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str

@app.post("/chat")
async def chat(request: ChatRequest):
    print(type(request))
    print(repr(request))
    print(request.message)

    response = await ask_ollama(
        sender_message={
            "id": 0,
            "name": "minyinpop",
            "role": "user",
            "message": request.message
        },
        short_memory_messages=[],
        long_memory=[]
    )

    return {
        "message": response
    }