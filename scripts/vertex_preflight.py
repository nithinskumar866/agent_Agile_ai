"""Check that AgentCorp can call Gemini on Vertex AI, one link at a time.

Run after the Google Cloud setup in the README:

    .venv\\Scripts\\python scripts\\vertex_preflight.py

Each check prints OK or the exact thing to fix. It makes one tiny model call (a few
tokens) and changes nothing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "pm_agent" / ".env")


def check(label: str, ok: bool, fix: str = "") -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + ("" if ok else f"\n       -> {fix}"))
    return ok


def main() -> int:
    vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").upper() in ("TRUE", "1")
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "")
    model = os.getenv("PM_MODEL", "gemini-3.6-flash")

    if not check("GOOGLE_GENAI_USE_VERTEXAI=TRUE in pm_agent/.env", vertex,
                 "Set GOOGLE_GENAI_USE_VERTEXAI=TRUE (Option B in .env.example)."):
        return 1
    if not check(f"GOOGLE_CLOUD_PROJECT set ({project or 'missing'})", bool(project),
                 "Set GOOGLE_CLOUD_PROJECT to your project ID."):
        return 1
    if not check(f"GOOGLE_CLOUD_LOCATION set ({location or 'missing'})", bool(location),
                 "Set GOOGLE_CLOUD_LOCATION, e.g. us-central1 or global."):
        return 1
    if not check(f"PM_MODEL is a Gemini model ({model})", "/" not in model,
                 "Comment out the NVIDIA PM_MODEL; a model name with '/' bypasses Vertex AI."):
        return 1

    try:
        import google.auth

        credentials, adc_project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    except Exception as exc:
        check("Application Default Credentials", False,
              f"Run: gcloud auth application-default login   ({type(exc).__name__}: {exc})")
        return 1
    check(f"Application Default Credentials ({type(credentials).__name__})", True)
    if adc_project and adc_project != project:
        print(f"[NOTE] ADC quota project is {adc_project!r}; requests are billed to GOOGLE_CLOUD_PROJECT={project!r}.")

    try:
        from google import genai

        client = genai.Client(vertexai=True, project=project, location=location)
        reply = client.models.generate_content(model=model, contents="Reply with the single word: ready")
        text = (reply.text or "").strip()
    except Exception as exc:
        message = str(exc)
        hint = ("Enable the API: gcloud services enable aiplatform.googleapis.com" if "SERVICE_DISABLED" in message
                or "has not been used" in message else
                "Check billing is linked and your account has roles/aiplatform.user" if "PERMISSION_DENIED" in message
                else f"Check the model name is available in {location}" if "NOT_FOUND" in message
                else "See the error above")
        check(f"Gemini call on Vertex AI ({model} in {location})", False, f"{hint}\n       {message[:400]}")
        return 1
    check(f"Gemini call on Vertex AI ({model} in {location}) replied: {text[:40]!r}", True)
    print("\nVertex AI is ready. Restart `adk web` and rerun the evals:\n"
          "  adk eval pm_agent evals/agentcorp.evalset.json --config_file_path evals/test_config.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
