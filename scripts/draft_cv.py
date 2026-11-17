import csv, json, os, time
import requests

# Brouillons de paragraphes d'accroche pour les meilleures offres (Ollama, en local).
# Les brouillons restent sur ce PC (output/cv_drafts/ est ignore par Git) :
# ils doivent etre relus et corriges a la main avant toute utilisation.

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = "qwen3:8b"
TOP_N = 3
MIN_COVERAGE = 0.3

PROMPT_TEMPLATE = """Tu es un(e) assistant(e) de redaction. Redige UNIQUEMENT le paragraphe
d'accroche (3-4 phrases) d'une lettre de motivation en francais, a la premiere personne,
pour l'offre de stage suivante. Mets en avant UNIQUEMENT les competences du candidat qui
correspondent reellement a l'offre, en t'appuyant UNIQUEMENT sur les preuves fournies.

Regles strictes (version 2, ajoutees apres relecture des premiers brouillons) :
1. N'invente aucune competence, experience, chiffre, outil ou responsabilite absente des preuves.
2. Chaque preuve est independante : ne melange pas deux preuves et n'attribue jamais a un
   projet ce qui vient d'une autre preuve.
3. Les noms de projets (FirmFactor, PCFinder, CrewUp, OpenHealth...) sont des projets
   academiques ou personnels, PAS des entreprises : n'ecris jamais "chez <nom du projet>".
4. Pour un projet realise en equipe, ne decris pas de role personnel qui n'est pas precise.
5. Ne mentionne pas de competences non presente dans les preuves.
6. Le candidat est un etudiant : accorde au masculin, sans ecriture du type "passionne(e)".


Offre : {title} chez {company}
Missions connues (peuvent etre incompletes) : {responsibilities}
Competences du candidat qui correspondent a l'offre : {matched_skills}
Preuves concretes du candidat (a utiliser telles quelles) : {evidence}

Reponds uniquement avec le paragraphe.
"""


def load_profile(path="data/profile/my_skills.csv"):
    profile = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            profile[row["skill"]] = row["evidence"]
    return profile


def call_ollama(prompt, retries=3, backoff=5):
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(
                OLLAMA_URL,
                proxies={"http": None, "https": None},
                json={"model": MODEL, "prompt": prompt, "stream": False, "think": False},
                timeout=(10, 180),
            )
            resp.raise_for_status()
            return resp.json()["response"].strip()
        except Exception as e:
            print(f"    tentative {attempt}/{retries} echouee : {e}")
            if attempt < retries:
                time.sleep(backoff)
    raise RuntimeError("Ollama injoignable")


def main():
    profile = load_profile()
    # Fichier local (toutes sources) : rediger un brouillon prive est un usage personnel.
    with open("data/offers/matches_all_local.json", encoding="utf-8") as f:
        data = json.load(f)

    # Les offres sont deja triees par score. On ne garde que celles ou assez de
    # competences sont connues, pour ne pas ecrire une lettre a l'aveugle.
    candidates = [
        o for o in data["offers"]
        if not o["insufficient_data"] and o["coverage"] >= MIN_COVERAGE
    ][:TOP_N]
    print(f"Brouillons pour {len(candidates)} offres sur {data['n_offers']} "
          f"(coverage >= {MIN_COVERAGE:.0%}, top {TOP_N})")

    os.makedirs("output/cv_drafts", exist_ok=True)
    for offer in candidates:
        evidence = {s: profile[s] for s in offer["matched"] if s in profile}
        prompt = PROMPT_TEMPLATE.format(
            title=offer["title"],
            company=offer.get("company") or "cette entreprise",
            responsibilities=", ".join(offer.get("responsibilities") or []) or "non precisees",
            matched_skills=", ".join(offer["matched"]),
            evidence=json.dumps(evidence, ensure_ascii=False),
        )
        print(f"  Redaction pour : {offer['title'][:60]} @ {offer.get('company')}...")
        t0 = time.time()
        paragraph = call_ollama(prompt)
        out_path = f"output/cv_drafts/offer_{offer['id']}_v2.txt"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"Offre : {offer['title']} @ {offer.get('company')}\n")
            f.write(f"Lien : {offer.get('url')}\n")
            f.write(f"Score : {offer['score']} | competences correspondantes : {', '.join(offer['matched'])}\n")
            f.write(f"Preuves fournies au modele : {json.dumps(evidence, ensure_ascii=False)}\n")
            f.write("\n--- BROUILLON (a relire et corriger) ---\n\n")
            f.write(paragraph + "\n")
        print(f"    -> {out_path} ({time.time() - t0:.0f}s)")

    print("\nRappel : output/cv_drafts/ est ignore par Git. Relis chaque brouillon et compare-le "
          "aux preuves indiquees en haut du fichier : tout ce qui n'y figure pas est une invention du modele.")


if __name__ == "__main__":
    main()
