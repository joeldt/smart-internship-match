import json, os, time
import requests

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = "qwen3:8b"

PROMPT_TEMPLATE = """Extract ONLY these things from this job/internship posting text
(it may be in French or English). The text starts with the job title, followed by a
short and possibly truncated description.
Return ONLY valid JSON with keys:
- required_skills: array of concrete named tools/technologies/hard skills mentioned (e.g. "Python", "Power BI") -- do not include generic soft skills or the job title itself
- responsibilities: array of the actual tasks/missions described
- experience_required: a short string quoting the explicit experience requirement stated in the text (e.g. "Bac+5", "enrolled in a Master's program"), or null if the text does not explicitly state one. Do NOT infer this from the job title or from seniority words -- only from an actual requirement sentence in the text.
- compensation_mentioned: a short verbatim quote of any salary/compensation/gratification FIGURE explicitly stated in the text (e.g. "600 euros/mois"), or null if no concrete number is stated. Read the ENTIRE text before concluding there is none.

Do not extract company or location. No explanation, just the JSON object.

TEXT:
{text}
"""

def call_ollama(prompt, retries=3, backoff=5):
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(
                OLLAMA_URL,
                proxies={"http": None, "https": None},
                json={"model": MODEL, "prompt": prompt, "format": "json",
                      "stream": False, "think": False},
                timeout=(10, 120),
            )
            resp.raise_for_status()
            return json.loads(resp.json()["response"])
        except Exception as e:
            print(f"    tentative {attempt}/{retries} echouee : {e}")
            if attempt < retries:
                time.sleep(backoff)
    raise RuntimeError("Ollama injoignable ou reponse invalide")

def as_string_list(value):
    # Le modele renvoie parfois autre chose qu'une liste de textes : on nettoie.
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]

def main():
    with open("data/offers/merged_offers.json", encoding="utf-8") as f:
        offers = json.load(f)["offers"]

    structured = []
    n_errors = 0
    start = time.time()
    for i, offer in enumerate(offers, 1):
        text = f"{offer.get('title') or ''}\n{offer.get('description') or ''}"
        try:
            extracted = call_ollama(PROMPT_TEMPLATE.format(text=text))
            error = None
        except RuntimeError as e:
            extracted, error = {}, str(e)
            n_errors += 1

        skills = as_string_list(extracted.get("required_skills"))
        print(f"  [{i}/{len(offers)}] {(offer.get('title') or '')[:55]!r} : {skills}")

        record = {k: v for k, v in offer.items() if k != "description"}
        record.update({
            "required_skills": skills,
            "responsibilities": as_string_list(extracted.get("responsibilities")),
            "experience_required": extracted.get("experience_required"),
            "compensation_mentioned": extracted.get("compensation_mentioned"),
            "extraction_error": error,
        })
        structured.append(record)

    total = time.time() - start
    with open("data/offers/structured_offers.json", "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "model": MODEL,
            "n_offers": len(structured),
            "n_extraction_errors": n_errors,
            "avg_seconds_per_offer": round(total / len(structured), 1) if structured else 0,
            "offers": structured,
        }, f, indent=2, ensure_ascii=False)

    n_no_skill = sum(1 for o in structured if not o["required_skills"])
    print(f"\nEcrit : data/offers/structured_offers.json ({len(structured)} offres, "
          f"{total:.0f}s, {n_errors} erreur(s), {n_no_skill} offre(s) sans aucune competence trouvee)")

if __name__ == "__main__":
    main()