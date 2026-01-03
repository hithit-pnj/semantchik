# app.py
# Application web Semantix - Version TEST avec timers et points dynamiques

import os
import json
import random
import string
import unicodedata
import time
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

# ======================
# Configuration
# ======================
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'semantix-secret-key-2024')
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data", "game_data.json")

# === PARAMÈTRES DE JEU (MODIFIABLES) ===
ROUND_TIME_LIMIT = 300      # 5 minutes max par mot
TURN_TIME_LIMIT = 15        # 15 secondes par tour
PENALTY_THRESHOLD = 1000    # Rang au-delà duquel on perd des points
TIMEOUT_PENALTY = -1        # Pénalité si on ne répond pas à temps

# ======================
# Chargement des données pré-calculées
# ======================
print("Chargement des données de jeu...")
with open(DATA_FILE, "r", encoding="utf-8") as f:
    GAME_DATA = json.load(f)

TARGETS = GAME_DATA["targets"]
RANKS = GAME_DATA["ranks"]
print(f"[OK] {len(TARGETS)} mots cibles disponibles")

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
    """Calcule les points selon le rang - avec pénalités !"""
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
    elif rank <= 1000:
        return 0
    else:
        # PÉNALITÉ : -1 point si trop loin !
        return -1

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
    elif rank <= 1000:
        return "❄️"
    else:
        return "💀"  # Pénalité !

def get_remaining_round_time(game):
    """Retourne le temps restant pour ce mot (en secondes)."""
    elapsed = time.time() - game['round_start_time']
    remaining = max(0, ROUND_TIME_LIMIT - elapsed)
    return int(remaining)

def get_remaining_turn_time(game):
    """Retourne le temps restant pour ce tour (en secondes)."""
    elapsed = time.time() - game['turn_start_time']
    remaining = max(0, TURN_TIME_LIMIT - elapsed)
    return int(remaining)

def check_and_advance_turn(game):
    """Vérifie si le temps du tour est écoulé et passe au suivant si besoin."""
    if get_remaining_turn_time(game) <= 0 and not game['found'] and not game.get('advancing_turn', False):
        # Marquer qu'on avance le tour pour éviter les appels multiples
        game['advancing_turn'] = True
        
        # Temps écoulé, pénalité pour le joueur actuel
        current = game['current_player']
        game['scores'][current] = game['scores'].get(current, 0) + TIMEOUT_PENALTY
        game['last_timeout_player'] = current
        
        # Passer au joueur suivant
        num_players = len(game['players'])
        game['current_player'] = (game['current_player'] % num_players) + 1
        game['turn_start_time'] = time.time()
        game['turn_skipped'] = True
        
        # Déverrouiller après un petit délai
        game['advancing_turn'] = False
        return True
    return False

def check_round_timeout(game):
    """Vérifie si le temps du mot est écoulé."""
    return get_remaining_round_time(game) <= 0 and not game['found']

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
    now = time.time()
    
    # Créer la partie
    games[code] = {
        'secret_word': secret_word,
        'players': {1: player_name},
        'scores': {1: 0},
        'guesses': [],
        'current_player': 1,
        'found': False,
        'winner': None,
        'created_at': datetime.now().isoformat(),
        'round_start_time': now,
        'turn_start_time': now,
        'turn_skipped': False,
        'round_timeout': False,
        'started': False  # La partie n'est pas encore lancée
    }
    
    return jsonify({
        'success': True,
        'code': code,
        'player_id': 1,
        'round_time_limit': ROUND_TIME_LIMIT,
        'turn_time_limit': TURN_TIME_LIMIT
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
        'players': game['players'],
        'round_time_limit': ROUND_TIME_LIMIT,
        'turn_time_limit': TURN_TIME_LIMIT
    })

@app.route('/api/start', methods=['POST'])
def start_game():
    """Démarre officiellement la partie (lance les timers)."""
    data = request.json
    code = data.get('code', '').upper()
    
    if code not in games:
        return jsonify({'success': False, 'error': 'Partie non trouvée'})
    
    game = games[code]
    now = time.time()
    game['round_start_time'] = now
    game['turn_start_time'] = now
    game['started'] = True  # Marquer la partie comme démarrée
    
    return jsonify({'success': True})

@app.route('/api/lobby/<code>')
def get_lobby_state(code):
    """Récupère l'état du lobby (pour synchroniser le démarrage)."""
    code = code.upper()
    
    if code not in games:
        return jsonify({'success': False, 'error': 'Partie non trouvée'})
    
    game = games[code]
    
    return jsonify({
        'success': True,
        'players': game['players'],
        'started': game.get('started', False)
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
    
    # Vérifier timeout du mot
    if check_round_timeout(game):
        game['round_timeout'] = True
        return jsonify({
            'success': False, 
            'error': 'Temps écoulé pour ce mot !',
            'timeout': True,
            'secret_word': game['secret_word']
        })
    
    if game['found']:
        return jsonify({'success': False, 'error': 'Partie terminée'})
    
    # Vérifier et avancer le tour si timeout
    check_and_advance_turn(game)
    
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
        # Passer au joueur suivant et reset le timer du tour
        num_players = len(game['players'])
        game['current_player'] = (player_id % num_players) + 1
        game['turn_start_time'] = time.time()
    
    return jsonify({
        'success': True,
        'word': word,
        'rank': rank,
        'points': points,
        'indicator': indicator,
        'found': found,
        'secret_word': game['secret_word'] if found else None
    })

@app.route('/api/skip_turn', methods=['POST'])
def skip_turn():
    """Force le passage au joueur suivant (appelé par le client si timeout)."""
    data = request.json
    code = data.get('code', '').upper()
    
    if code not in games:
        return jsonify({'success': False, 'error': 'Partie non trouvée'})
    
    game = games[code]
    
    if game['found']:
        return jsonify({'success': False, 'error': 'Partie terminée'})
    
    # Ne rien faire si le tour a déjà été avancé par le serveur
    # Le serveur gère tout via check_and_advance_turn dans get_state
    
    return jsonify({'success': True, 'current_player': game['current_player']})

@app.route('/api/state/<code>')
def get_state(code):
    """Récupère l'état actuel de la partie."""
    code = code.upper()
    
    if code not in games:
        return jsonify({'success': False, 'error': 'Partie non trouvée'})
    
    game = games[code]
    
    # Vérifier les timeouts
    round_timeout = check_round_timeout(game)
    turn_skipped = check_and_advance_turn(game)
    
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
        'secret_word': game['secret_word'] if (game['found'] or round_timeout) else None,
        'round_remaining': get_remaining_round_time(game),
        'turn_remaining': get_remaining_turn_time(game),
        'round_timeout': round_timeout,
        'turn_skipped': turn_skipped
    })

@app.route('/api/new_round', methods=['POST'])
def new_round():
    """Démarre une nouvelle manche."""
    data = request.json
    code = data.get('code', '').upper()
    
    if code not in games:
        return jsonify({'success': False, 'error': 'Partie non trouvée'})
    
    game = games[code]
    now = time.time()
    
    # Nouveau mot secret
    game['secret_word'] = random.choice(TARGETS)
    game['guesses'] = []
    game['current_player'] = 1
    game['found'] = False
    game['winner'] = None
    game['round_start_time'] = now
    game['turn_start_time'] = now
    game['round_timeout'] = False
    game['turn_skipped'] = False
    # Garder les scores !
    
    return jsonify({
        'success': True,
        'round_time_limit': ROUND_TIME_LIMIT,
        'turn_time_limit': TURN_TIME_LIMIT
    })

# ======================
# Lancement
# ======================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))  # Port 5001 pour le test
    print(f"\n[TEST] MODE TEST - Port {port}")
    print(f"   Timer mot: {ROUND_TIME_LIMIT}s | Timer tour: {TURN_TIME_LIMIT}s")
    print(f"   Penalite rang > {PENALTY_THRESHOLD}: -1pt | Timeout: {TIMEOUT_PENALTY}pt\n")
    app.run(host='0.0.0.0', port=port, debug=True)
