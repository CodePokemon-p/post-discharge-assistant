from graph.build import build_graph


def run_demo():
    app = build_graph()

    print("\n=== Scenario 1: patient asks a routine question ===")
    result = app.invoke({
        "discharge_text": "(mock discharge summary text)",
        "patient_message": "Can I eat spicy food after this surgery?",
        "language": "en",
    })
    print("Final state:", result)

    print("\n=== Scenario 2: patient reports a concerning symptom ===")
    result = app.invoke({
        "discharge_text": "(mock discharge summary text)",
        "patient_message": "I have a fever and my pain is really bad.",
        "language": "en",
    })
    print("Final state:", result)


if __name__ == "__main__":
    run_demo()