from graph.build import build_graph
from storage import init_db


def run_demo():
    init_db()
    app = build_graph()

    print("\n=== Scenario 1: patient asks a routine question ===")
    result = app.invoke({
        "patient_id": "patient_001",
        "discharge_text": open("data/sample_discharge.txt").read(),
        "patient_message": "Can I eat spicy food after this surgery?",
        "language": "en",
    })
    print("Final state:", result)
    result = app.invoke({
        "patient_id": "patient_001",
        "phone_number": "+92XXXXXXXXXX",   # your real number, international format
        "discharge_text": open("data/sample_discharge.txt").read(),
        "patient_message": "Can I eat spicy food after this surgery?",
        "language": "en",
    })

    print("\n=== Scenario 2: patient reports a concerning symptom ===")
    result = app.invoke({
        "patient_id": "patient_001",
        "discharge_text": open("data/sample_discharge.txt").read(),
        "patient_message": "I have a fever and my pain is really bad.",
        "language": "en",
    })
    print("Final state:", result)
    result = app.invoke({
        "patient_id": "patient_001",
        "phone_number": "+92XXXXXXXXXX",   # your real number, international format
        "discharge_text": open("data/sample_discharge.txt").read(),
        "patient_message": "Can I eat spicy food after this surgery?",
        "language": "en",
    })

    print("\n=== Scenario 3: Urdu patient message ===")
    result = app.invoke({
        "patient_id": "patient_001",
        "discharge_text": open("data/sample_discharge.txt").read(),
        "patient_message": "مجھے بخار ہے اور میرا درد بہت زیادہ ہے۔",  # "I have a fever and my pain is very bad"
        "language": "ur",
    })
    print("Final state:", result)
    result = app.invoke({
        "patient_id": "patient_001",
        "phone_number": "+92XXXXXXXXXX",   # your real number, international format
        "discharge_text": open("data/sample_discharge.txt").read(),
        "patient_message": "Can I eat spicy food after this surgery?",
        "language": "en",
    })


if __name__ == "__main__":
    run_demo()