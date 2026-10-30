# Evaluation cases

Labeled patient messages used to measure how well the assistant behaves
(intent, risk triage, answer grounding), in English, Urdu and Roman Urdu.

Each message is run against ONE of two reference patients, chosen in the
`patient` column:

- `pneumonia`: pneumonia / COPD flare / mild heart failure (breathing, fever, chest)
- `surgery`: post-operative patient with an incision (wound, dressings, activity)

Questions are checked against what that patient's discharge document really says.
The reference documents must be synthetic or fully de-identified before they go
in this repository.

## Columns

| Column | Meaning |
|---|---|
| `id` | Unique, e.g. T001 |
| `message` | Exactly as a patient would type it (typos and all) |
| `language` | `en`, `ur` (Urdu script), `roman_ur` (Urdu in English letters), or `mixed` |
| `patient` | `pneumonia` or `surgery` (see above) |
| `expected_intent` | `question`, `symptom_report` or `off_topic` |
| `expected_risk` | Only for `symptom_report`: `high` (a nurse should be alerted) or `low` |
| `borderline` | `yes` if reasonable people could disagree about the risk, else `no` |
| `in_document` | Only for `question`: `yes` if the document answers it, `no` if not |
| `notes` | One line on why these labels were chosen. `[CHECK: ...]` marks a label to review first |
| `source` | Where the message came from: `seed`, `self_written`, `ai_drafted`, `friend_family`, `real_test` |
| `clinician_reviewed` | `no` until a nurse or doctor has confirmed the labels |

## Rules

- Never use real patient messages or real names.
- Be honest in `source`. Messages drafted with an AI are fine, as long as they are labeled `ai_drafted`.
- When unsure whether something is urgent, label it `high` and set `borderline` to `yes`.
- A missed urgent message is much worse than an extra alert, so urgent cases are reported separately.
- The labels are a first draft until `clinician_reviewed` is `yes`.