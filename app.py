# app.py
# Application web Semantix - Jeu multijoueur de devinette sémantique

import os
import json
import random
import string
import unicodedata
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS

# ======================
# Configuration
# ======================
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'semantix-secret-key-2024')
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data", "game_data.json")

# ======================
# Chargement des données pré-calculées
# ======================
print("Chargement des données de jeu...")
with open(DATA_FILE, "r", encoding="utf-8") as f:
    GAME_DATA = json.load(f)

TARGETS = GAME_DATA["targets"]
RANKS = GAME_DATA["ranks"]
print(f"✓ {len(TARGETS)} mots cibles disponibles")

# ======================
# Stockage des parties en mémoire
# ======================
games = {}  # code -> game_state

# ======================
# Utilitaires
# ======================
def normalize_text(text):
    """Normalise un mot : minuscules + suppression des accents."""
    text = text.lower().strip()
    text = unicodedata.normalize('NFD', text)
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
    return text

def generate_code():
    """Génère un code de partie à 4 lettres."""
    return ''.join(random.choices(string.ascii_uppercase, k=4))

def calculate_points(rank):
    """Calcule les points selon le rang."""
    if rank == 1:
        return 100
    elif rank <= 10:
        return 15
    elif rank <= 50:
        return 10
    elif rank <= 100:
        return 5
    elif rank <= 500:
        return 2
    return 0

def get_indicator(rank):
    """Retourne l'indicateur visuel selon le rang."""
    if rank == 1:
        return "🎉"
    elif rank <= 10:
        return "🔥"
    elif rank <= 50:
        return "🌡️"
    elif rank <= 100:
        return "☀️"
    elif rank <= 500:
        return "🌤️"
    return "❄️"

# ======================
# Routes
# ======================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/create', methods=['POST'])
def create_game():
    """Crée une nouvelle partie."""
    data = request.json
    player_name = data.get('name', 'Joueur')
    
    # Générer un code unique
    code = generate_code()
    while code in games:
        code = generate_code()
    
    # Choisir un mot secret
    secret_word = random.choice(TARGETS)
    
    # Créer la partie
    games[code] = {
        'secret_word': secret_word,
        'players': {1: player_name},
        'scores': {1: 0},
        'guesses': [],
        'current_player': 1,
        'found': False,
        'winner': None,
        'created_at': datetime.now().isoformat()
    }
    
    return jsonify({
        'success': True,
        'code': code,
        'player_id': 1,
        'hint': len(secret_word)
    })

@app.route('/api/join', methods=['POST'])
def join_game():
    """Rejoint une partie existante."""
    data = request.json
    code = data.get('code', '').upper()
    player_name = data.get('name', 'Joueur')
    
    if code not in games:
        return jsonify({'success': False, 'error': 'Code de partie invalide'})
    
    game = games[code]
    
    if game['found']:
        return jsonify({'success': False, 'error': 'Cette partie est terminée'})
    
    if len(game['players']) >= 4:
        return jsonify({'success': False, 'error': 'Partie complète (4 joueurs max)'})
    
    # Ajouter le joueur
    player_id = len(game['players']) + 1
    game['players'][player_id] = player_name
    game['scores'][player_id] = 0
    
    return jsonify({
        'success': True,
        'code': code,
        'player_id': player_id,
        'hint': len(game['secret_word']),
        'players': game['players']
    })

@app.route('/api/guess', methods=['POST'])
def make_guess():
    """Fait une tentative."""
    data = request.json
    code = data.get('code', '').upper()
    player_id = data.get('player_id')
    word = data.get('word', '')
    
    if code not in games:
        return jsonify({'success': False, 'error': 'Partie non trouvée'})
    
    game = games[code]
    
    if game['found']:
        return jsonify({'success': False, 'error': 'Partie terminée'})
    
    if game['current_player'] != player_id:
        return jsonify({'success': False, 'error': "Ce n'est pas votre tour"})
    
    # Normaliser le mot
    word = normalize_text(word)
    
    if not word:
        return jsonify({'success': False, 'error': 'Mot vide'})
    
    # Vérifier si déjà essayé
    guessed_words = [g['word'] for g in game['guesses']]
    if word in guessed_words:
        return jsonify({'success': False, 'error': 'Mot déjà essayé'})
    
    # Chercher le rang
    secret = game['secret_word']
    if secret not in RANKS:
        return jsonify({'success': False, 'error': 'Erreur de configuration'})
    
    ranks_for_secret = RANKS[secret]
    
    if word not in ranks_for_secret:
        return jsonify({'success': False, 'error': 'Mot non reconnu (pas dans le dictionnaire)'})
    
    rank = ranks_for_secret[word]
    points = calculate_points(rank)
    indicator = get_indicator(rank)
    
    # Enregistrer la tentative
    guess_data = {
        'word': word,
        'rank': rank,
        'points': points,
        'indicator': indicator,
        'player_id': player_id,
        'player_name': game['players'][player_id]
    }
    game['guesses'].append(guess_data)
    game['scores'][player_id] = game['scores'].get(player_id, 0) + points
    
    # Mot trouvé ?
    found = (rank == 1)
    if found:
        game['found'] = True
        game['winner'] = player_id
    else:
        # Passer au joueur suivant
        num_players = len(game['players'])
        game['current_player'] = (player_id % num_players) + 1
    
    return jsonify({
        'success': True,
        'word': word,
        'rank': rank,
        'points': points,
        'indicator': indicator,
        'found': found,
        'secret_word': game['secret_word'] if found else None
    })

@app.route('/api/state/<code>')
def get_state(code):
    """Récupère l'état actuel de la partie."""
    code = code.upper()
    
    if code not in games:
        return jsonify({'success': False, 'error': 'Partie non trouvée'})
    
    game = games[code]
    
    # Trier les tentatives par rang
    sorted_guesses = sorted(game['guesses'], key=lambda x: x['rank'])
    
    return jsonify({
        'success': True,
        'players': game['players'],
        'scores': game['scores'],
        'guesses': sorted_guesses,
        'current_player': game['current_player'],
        'found': game['found'],
        'winner': game['winner'],
        'secret_word': game['secret_word'] if game['found'] else None,
        'hint': len(game['secret_word'])
    })

@app.route('/api/new_round', methods=['POST'])
def new_round():
    """Démarre une nouvelle manche."""
    data = request.json
    code = data.get('code', '').upper()
    
    if code not in games:
        return jsonify({'success': False, 'error': 'Partie non trouvée'})
    
    game = games[code]
    
    # Nouveau mot secret
    game['secret_word'] = random.choice(TARGETS)
    game['guesses'] = []
    game['current_player'] = 1
    game['found'] = False
    game['winner'] = None
    # Garder les scores !
    
    return jsonify({
        'success': True,
        'hint': len(game['secret_word'])
    })

# ======================
# Lancement
# ======================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

