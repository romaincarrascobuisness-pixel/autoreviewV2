#!/usr/bin/env python3
"""
AutoReview - Réponse automatique aux avis Google avec Claude AI
Version finale sécurisée - anti-bannissement + personnalisation avancée
"""

import os
import json
import time
import pickle
import random
import requests
from datetime import datetime, timezone

import anthropic
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# ============================================================
# CONFIGURATION CLIENTS
# ============================================================

CLIENTS = [
    {
        "business_name": "Café Test Carrasco",
        "business_type": "café / restaurant",
        "business_tone": "chaleureux, convivial, comme un patron de café de quartier",
        "business_description": "Café de quartier avec une ambiance conviviale, connu pour ses croissants maison et son café de spécialité. Ouvert depuis 2018.",
        "reply_to_old_reviews": False,
    },
    # {
    #     "business_name": "Garage Martin",
    #     "business_type": "garage automobile",
    #     "business_tone": "professionnel, rassurant, expert",
    #     "business_description": "Garage multimarques, spécialisé en révision et réparation rapide. Équipe de 4 mécaniciens certifiés.",
    #     "reply_to_old_reviews": False,
    # },
]

ANTHROPIC_API_KEY = "mettre_cle_ici"
CLIENT_SECRET_FILE = "client_secret_979790378058-1d96ilmfv6q0gn0abjt6jluntu537ubs.apps.googleusercontent.com.json"
CHECK_INTERVAL = 300  # 5 minutes entre chaque cycle de vérification

# Délai anti-bannissement entre chaque réponse (en secondes)
# Pour les anciens avis : délai aléatoire entre 60 et 120 secondes
DELAY_OLD_REVIEWS_MIN = 60
DELAY_OLD_REVIEWS_MAX = 120
# Pour les nouveaux avis : délai court (ils arrivent rarement en masse)
DELAY_NEW_REVIEWS = 15

SCOPES = ["https://www.googleapis.com/auth/business.manage"]

# ============================================================
# AUTHENTIFICATION
# ============================================================

def get_credentials():
    token_file = "token.pickle"
    creds = None
    if os.path.exists(token_file):
        with open(token_file, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=8080)
        with open(token_file, "wb") as f:
            pickle.dump(creds, f)
    return creds

# ============================================================
# API GOOGLE BUSINESS
# ============================================================

def get_all_locations(creds):
    headers = {"Authorization": f"Bearer {creds.token}"}
    r = requests.get(
        "https://mybusinessaccountmanagement.googleapis.com/v1/accounts",
        headers=headers
    )
    accounts = r.json().get("accounts", [])
    all_locations = []
    for account in accounts:
        r2 = requests.get(
            f"https://mybusinessbusinessinformation.googleapis.com/v1/{account['name']}/locations",
            headers=headers,
            params={"readMask": "name,title"}
        )
        all_locations.extend(r2.json().get("locations", []))
    return all_locations

def get_reviews(creds, location_name):
    headers = {"Authorization": f"Bearer {creds.token}"}
    r = requests.get(
        f"https://mybusiness.googleapis.com/v4/{location_name}/reviews",
        headers=headers
    )
    return r.json().get("reviews", [])

def post_reply(creds, review_name, reply_text):
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json"
    }
    r = requests.put(
        f"https://mybusiness.googleapis.com/v4/{review_name}/reply",
        headers=headers,
        json={"comment": reply_text}
    )
    if r.status_code == 200:
        print("✅ Réponse postée")
        return True
    else:
        print(f"❌ Erreur post : {r.status_code} - {r.text}")
        return False

# ============================================================
# CORRESPONDANCE CLIENT
# ============================================================

def find_client_config(location_title):
    for client in CLIENTS:
        if client["business_name"].lower() in location_title.lower():
            return client
    return None

# ============================================================
# GÉNÉRATION RÉPONSE CLAUDE
# ============================================================

def generate_reply(client_config, review_text, star_rating, reviewer_name):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    if star_rating >= 4:
        tone_instruction = "remercie chaleureusement et invite à revenir"
    elif star_rating == 3:
        tone_instruction = "remercie, reconnais qu'il y a des points à améliorer, reste positif et constructif"
    else:
        tone_instruction = "excuse-toi sincèrement, propose de recontacter directement pour arranger les choses, ne te justifie pas"

    prompt = f"""Tu es le gérant de {client_config['business_name']}, un {client_config['business_type']}.

Informations sur ton établissement :
{client_config.get('business_description', 'Pas de description fournie.')}

Ton style de communication : {client_config['business_tone']}.

Réponds à cet avis Google :
- Note : {star_rating}/5 étoiles
- Client : {reviewer_name}
- Avis : "{review_text}"

Règles strictes :
- Réponse uniquement en français
- Maximum 3 phrases courtes et naturelles
- {tone_instruction}
- Utilise le prénom du client si disponible, sinon "cher client"
- Tu peux mentionner un détail de l'établissement si c'est pertinent avec l'avis
- Ne jamais inventer des détails que l'avis ne mentionne pas
- Ne jamais promettre quelque chose que tu ne peux pas tenir
- Ne pas copier mot pour mot le contenu de l'avis
- Signe avec "L'équipe de {client_config['business_name']}"
- Écris UNIQUEMENT la réponse, rien d'autre"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text

# ============================================================
# GESTION MÉMOIRE
# ============================================================

def load_processed():
    if os.path.exists("processed_reviews.json"):
        with open("processed_reviews.json", "r") as f:
            return set(json.load(f))
    return set()

def save_processed(processed):
    with open("processed_reviews.json", "w") as f:
        json.dump(list(processed), f)

def load_start_time():
    if os.path.exists("start_time.json"):
        with open("start_time.json", "r") as f:
            return datetime.fromisoformat(json.load(f))
    start = datetime.now(timezone.utc)
    with open("start_time.json", "w") as f:
        json.dump(start.isoformat(), f)
    return start

# ============================================================
# BOUCLE PRINCIPALE
# ============================================================

def process_review(creds, review, location_title, client_config, processed_reviews, start_time, is_old=False):
    """Traite un seul avis et retourne True si réponse postée"""
    review_name = review["name"]

    if review_name in processed_reviews:
        return False
    if "reviewReply" in review:
        processed_reviews.add(review_name)
        return False

    # Vérifier ancienneté
    review_time_str = review.get("createTime", "")
    if review_time_str and not client_config.get("reply_to_old_reviews", False):
        try:
            review_time = datetime.fromisoformat(review_time_str.replace("Z", "+00:00"))
            if review_time < start_time:
                processed_reviews.add(review_name)
                return False
        except:
            pass

    star_rating = {
        "ONE": 1, "TWO": 2, "THREE": 3,
        "FOUR": 4, "FIVE": 5
    }.get(review.get("starRating", "THREE"), 3)

    reviewer_name = review.get("reviewer", {}).get("displayName", "client")
    review_text = review.get("comment", "Pas de commentaire écrit")

    print(f"\n⭐ [{location_title}] {reviewer_name} ({star_rating}/5)")
    print(f"   \"{review_text[:80]}\"")

    reply = generate_reply(client_config, review_text, star_rating, reviewer_name)
    print(f"💬 Réponse : {reply[:120]}...")

    success = post_reply(creds, review_name, reply)
    if success:
        processed_reviews.add(review_name)
        save_processed(processed_reviews)

        # Délai anti-bannissement
        if is_old:
            delay = random.randint(DELAY_OLD_REVIEWS_MIN, DELAY_OLD_REVIEWS_MAX)
            print(f"⏳ Pause anti-bannissement : {delay} secondes avant le prochain avis...")
            time.sleep(delay)
        else:
            time.sleep(DELAY_NEW_REVIEWS)

    return success

def main():
    print("🚀 AutoReview démarré")
    print(f"👥 {len(CLIENTS)} client(s) configuré(s)")
    print(f"⏱️  Vérification toutes les {CHECK_INTERVAL // 60} minutes")
    print(f"🛡️  Délai anti-bannissement : {DELAY_OLD_REVIEWS_MIN}-{DELAY_OLD_REVIEWS_MAX}s entre anciens avis\n")

    creds = get_credentials()
    processed_reviews = load_processed()
    start_time = load_start_time()

    locations = get_all_locations(creds)
    if not locations:
        print("❌ Aucun établissement trouvé")
        return

    print(f"✅ {len(locations)} établissement(s) détecté(s) :")
    for loc in locations:
        title = loc.get("title", loc["name"])
        client = find_client_config(title)
        status = f"✓ {client['business_type']}" if client else "⚠️  NON CONFIGURÉ — ignoré"
        print(f"   - {title} → {status}")
    print()

    while True:
        try:
            creds = get_credentials()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Vérification en cours...")

            for location in locations:
                location_name = location["name"]
                location_title = location.get("title", location_name)

                client_config = find_client_config(location_title)
                if not client_config:
                    continue

                reviews = get_reviews(creds, location_name)
                new_count = 0
                old_count = 0

                for review in reviews:
                    if review["name"] in processed_reviews:
                        continue
                    if "reviewReply" in review:
                        continue

                    review_time_str = review.get("createTime", "")
                    is_old = False
                    try:
                        review_time = datetime.fromisoformat(review_time_str.replace("Z", "+00:00"))
                        if review_time < start_time:
                            is_old = True
                    except:
                        pass

                    if is_old:
                        old_count += 1
                    else:
                        new_count += 1

                if old_count > 0:
                    print(f"📋 [{location_title}] {old_count} ancien(s) avis sans réponse à traiter")
                if new_count > 0:
                    print(f"🆕 [{location_title}] {new_count} nouvel(aux) avis à traiter")

                for review in reviews:
                    review_time_str = review.get("createTime", "")
                    is_old = False
                    try:
                        review_time = datetime.fromisoformat(review_time_str.replace("Z", "+00:00"))
                        if review_time < start_time:
                            is_old = True
                    except:
                        pass

                    process_review(creds, review, location_title, client_config, processed_reviews, start_time, is_old)

            print(f"✓ Cycle terminé — prochaine vérification dans {CHECK_INTERVAL // 60} min\n")

        except Exception as e:
            print(f"❌ Erreur : {e}")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
