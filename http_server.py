from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Any, Dict

# Reuse existing tool and schema from MCP server
from server import beat_create, BeatInput

app = FastAPI(title="fsm-qa-mcp-server-http")

@app.post("/fsm-qa-mcp-server")
def create_beat(input: BeatInput) -> Dict[str, Any]:
    try:
        result = beat_create(input)
        if not isinstance(result, dict):
            raise HTTPException(status_code=500, detail="Unexpected tool result")
        if not result.get("ok", False):
            raise HTTPException(status_code=400, detail=result)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
