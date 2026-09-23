from graph.build import build_graph


def _load_discharge_text() -> str:
    """Read the sample discharge summary from disk, once per run."""
    with open("data/sample_discharge.txt", "r", encoding="utf-8") as f:
        return f.read()


def run_demo():
    app = build_graph()
    discharge_text = _load_discharge_text()

    print("\n=== Scenario 1: patient asks a routine question ===")
    result = app.invoke({
        "discharge_text": discharge_text,
        "patient_message": "Can I eat spicy food after this surgery?",
        "language": "en",
    })
    print("Final state:", result)

    print("\n=== Scenario 2: patient reports a concerning symptom ===")
    result = app.invoke({
        "discharge_text": discharge_text,
        "patient_message": "I have a fever and my pain is really bad.",
        "language": "en",
    })
    print("Final state:", result)


if __name__ == "__main__":
    run_demo()