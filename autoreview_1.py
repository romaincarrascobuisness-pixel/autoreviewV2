#!/usr/bin/env python3
"""
AutoReview - Réponse automatique aux avis Google avec Claude AI
Version Railway - variables d'environnement
"""

import os
import json
import time
import pickle
import random
import requests
import tempfile
from datetime import datetime, timezone

import anthropic
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# ============================================================
# CONFIGURATION CLIENTS
# ============================================================

CLIENTS = [
    {
        "business_name": "Café Test Carrasco",
        "business_type": "café / restaurant",
        "business_tone": "chaleureux, convivial, comme un patron de café de quartier",
        "business_description": "Café de quartier avec une ambiance conviviale.",
        "reply_to_old_reviews": False,
    },
]

# Lecture des variables d'environnement Railway
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_TOKEN = os.environ.get("GOOGLE_TOKEN", "")

CHECK_INTERVAL = 300
DELAY_OLD_REVIEWS_MIN = 60
DELAY_OLD_REVIEWS_MAX = 120
DELAY_NEW_REVIEWS = 15
SCOPES = ["https://www.googleapis.com/auth/business.manage"]

# ============================================================
# AUTHENTIFICATION GOOGLE
# ============================================================

def get_credentials():
    creds = None

    # Si on a un token sauvegardé dans les variables d'environnement
    if GOOGLE_TOKEN:
        try:
            token_data = json.loads(GOOGLE_TOKEN)
            creds = Credentials.from_authorized_user_info(token_data, SCOPES)
        except:
            pass

    # Si on a un token local (pour le développement)
    if not creds and os.path.exists("token.pickle"):
        with open("token.pickle", "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Crée un fichier temporaire avec les credentials Google
            if GOOGLE_CLIENT_SECRET:
                with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                    f.write(GOOGLE_CLIENT_SECRET)
                    temp_path = f.name
            else:
                temp_path = "clients_secret.json"

            flow = InstalledAppFlow.from_client_secrets_file(temp_path, SCOPES)
            creds = flow.run_local_server(port=8080)

            if GOOGLE_CLIENT_SECRET:
                os.unlink(temp_path)

        # Sauvegarde locale
        with open("token.pickle", "wb") as f:
            pickle.dump(creds, f)

        print("⚠️  TOKEN GOOGLE — copie ce JSON dans la variable GOOGLE_TOKEN sur Railway :")
        print(creds.to_json())

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
        print(f"❌ Erreur : {r.status_code} - {r.text}")
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
        tone_instruction = "remercie, reconnais qu'il y a des points à améliorer, reste positif"
    else:
        tone_instruction = "excuse-toi sincèrement, propose de recontacter directement"

    prompt = f"""Tu es le gérant de {client_config['business_name']}, un {client_config['business_type']}.
Informations : {client_config.get('business_description', '')}
Style : {client_config['business_tone']}.

Réponds à cet avis :
- Note : {star_rating}/5
- Client : {reviewer_name}
- Avis : "{review_text}"

Règles : français uniquement, max 3 phrases, {tone_instruction}, signe avec "L'équipe de {client_config['business_name']}", écris UNIQUEMENT la réponse."""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text

# ============================================================
# MÉMOIRE
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

def main():
    print("🚀 AutoReview démarré")
    print(f"👥 {len(CLIENTS)} client(s) configuré(s)")
    print(f"⏱️  Vérification toutes les {CHECK_INTERVAL // 60} minutes\n")

    if not ANTHROPIC_API_KEY:
        print("❌ Variable ANTHROPIC_API_KEY manquante dans Railway")
        return

    creds = get_credentials()
    processed_reviews = load_processed()
    start_time = load_start_time()

    locations = get_all_locations(creds)
    if not locations:
        print("❌ Aucun établissement trouvé")
        return

    print(f"✅ {len(locations)} établissement(s) :")
    for loc in locations:
        title = loc.get("title", loc["name"])
        client = find_client_config(title)
        status = f"✓ {client['business_type']}" if client else "⚠️ NON CONFIGURÉ"
        print(f"   - {title} → {status}")
    print()

    while True:
        try:
            creds = get_credentials()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Vérification...")

            for location in locations:
                location_name = location["name"]
                location_title = location.get("title", location_name)
                client_config = find_client_config(location_title)
                if not client_config:
                    continue

                reviews = get_reviews(creds, location_name)

                for review in reviews:
                    review_name = review["name"]
                    if review_name in processed_reviews:
                        continue
                    if "reviewReply" in review:
                        processed_reviews.add(review_name)
                        continue

                    is_old = False
                    review_time_str = review.get("createTime", "")
                    try:
                        review_time = datetime.fromisoformat(review_time_str.replace("Z", "+00:00"))
                        if review_time < start_time:
                            is_old = True
                    except:
                        pass

                    if is_old and not client_config.get("reply_to_old_reviews", False):
                        processed_reviews.add(review_name)
                        continue

                    star_rating = {"ONE":1,"TWO":2,"THREE":3,"FOUR":4,"FIVE":5}.get(review.get("starRating","THREE"),3)
                    reviewer_name = review.get("reviewer", {}).get("displayName", "client")
                    review_text = review.get("comment", "Pas de commentaire")

                    print(f"\n⭐ [{location_title}] {reviewer_name} ({star_rating}/5)")
                    reply = generate_reply(client_config, review_text, star_rating, reviewer_name)
                    print(f"💬 {reply[:100]}...")

                    success = post_reply(creds, review_name, reply)
                    if success:
                        processed_reviews.add(review_name)
                        save_processed(processed_reviews)
                        delay = random.randint(DELAY_OLD_REVIEWS_MIN, DELAY_OLD_REVIEWS_MAX) if is_old else DELAY_NEW_REVIEWS
                        print(f"⏳ Pause {delay}s...")
                        time.sleep(delay)

            print(f"✓ Terminé — prochain cycle dans {CHECK_INTERVAL//60} min\n")

        except Exception as e:
            print(f"❌ Erreur : {e}")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
