"""
Interactive terminal tester for the post-discharge assistant.

Loads a discharge image (or text), extracts the care plan ONCE, then
lets you type patient messages one at a time. For each message it shows:
  - classified intent
  - which path the graph took
  - the exact reply the patient gets
  - whether it escalated (and why)

Type 'dash' at any time to see what has been sent to the nurse dashboard.
Type 'quit' to exit.

Uses the same graph.build.build_graph() the production webhook uses --
so whatever you see here is what actually happens in production.
"""

import base64
import sys
from pathlib import Path

from graph.build import build_graph
from storage import init_db, get_escalations


def _load_discharge(image_path: str) -> str:
    """Load a discharge summary either from an image (via vision) or a text file."""
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {image_path}")

    if path.suffix.lower() in {".txt", ".md"}:
        return path.read_text(encoding="utf-8")

    # Image path -- transcribe via the vision helper we built earlier
    from graph.extraction import extract_text_from_image
    mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    image_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return extract_text_from_image(image_b64, mime)


def _print_dashboard():
    """Show everything currently in the escalation store."""
    rows = get_escalations()
    if not rows:
        print("\n[dashboard] empty -- nothing escalated yet.\n")
        return

    print("\n================ NURSE DASHBOARD ================")
    urgent = [r for r in rows if str(r.get("priority", "")).lower() == "urgent"]
    low = [r for r in rows if str(r.get("priority", "")).lower() != "urgent"]

    print(f"\n--- URGENT ({len(urgent)}) ---")
    for r in urgent:
        print(f"  * [{r.get('patient_id', '?')}] {r.get('reasoning', r.get('reason', ''))}")
        print(f"    message: {r.get('message', '')[:120]}")

    print(f"\n--- LOW PRIORITY ({len(low)}) ---")
    for r in low:
        print(f"  * [{r.get('patient_id', '?')}] {r.get('reasoning', r.get('reason', ''))}")
        print(f"    message: {r.get('message', '')[:120]}")

    print(f"\nTotal: {len(rows)} escalation(s)")
    print("=================================================\n")


def main():
    if len(sys.argv) < 2:
        print("Usage: python interactive_test.py data\\your_discharge.jpg")
        sys.exit(1)

    print(f"Loading {sys.argv[1]} ...")
    discharge_text = _load_discharge(sys.argv[1])
    print(f"Loaded {len(discharge_text)} chars of discharge text.\n")

    init_db()
    graph = build_graph()

    patient_id = "patient_001"
    print("Extracting care plan (once)...")
    state = {
        "patient_id": patient_id,
        "discharge_text": discharge_text,
        "language": "en",
    }
    # Run a first pass to extract the plan; we'll reuse the same plan for each message.
    seed = graph.invoke({**state, "patient_message": "hello"})
    care_plan = seed.get("care_plan")
    print(f"Care plan: diagnosis={getattr(care_plan, 'diagnosis', '?')}")
    print(f"Medications: {len(getattr(care_plan, 'medications', []))}")
    print(f"Red flags: {getattr(care_plan, 'red_flags', [])}\n")

    print("Type a patient message (or 'dash', or 'quit'):")
    while True:
        try:
            msg = input("\npatient> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            break

        if not msg:
            continue
        if msg.lower() in {"quit", "exit", "q"}:
            print("bye")
            break
        if msg.lower() == "dash":
            _print_dashboard()
            continue

        result = graph.invoke({
            **state,
            "patient_message": msg,
            "care_plan": care_plan,  # reuse the plan; don't re-extract
        })

        intent = result.get("intent")
        escalated = result.get("escalated")
        risk = result.get("risk_level")
        answer = result.get("answer")
        grounded = result.get("grounded")

        print(f"  intent      : {intent}")
        print(f"  risk_level  : {risk}")
        print(f"  grounded    : {grounded}")
        print(f"  escalated   : {escalated}")

        if escalated:
            print(f"  -> SENT TO NURSE DASHBOARD")
        elif answer:
            print(f"  reply to patient: {answer}")


if __name__ == "__main__":
    main()