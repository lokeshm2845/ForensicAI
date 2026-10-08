"""Entry point for a Hugging Face *Gradio-SDK* Space (free CPU): the SDK just runs this file, so we serve our FastAPI app on port 7860."""
import os
import sys

import uvicorn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from server.main import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "7860")))
