# app.py
# Application web Semantix - Version avec timers et points dynamiques

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
TURN_TIME_LIMIT = 15        # 15 secondes par tour (défaut)
REDUCED_TURN_TIME = 10      # Timer réduit après 2 timeouts consécutifs
TIMEOUT_THRESHOLD = 2       # Nombre de timeouts avant réduction
PENALTY_THRESHOLD = 1000    # Seuil par défaut si aucun mot trouvé
TIMEOUT_PENALTY = -1        # Pénalité si on ne répond pas à temps
REGRESSION_PENALTY = -1     # Pénalité si le mot est pire que le meilleur actuel
BEST_WORD_BONUS = 1         # Bonus si on trouve le meilleur mot de la manche

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

def calculate_points(rank, avg_top_10=None):
    """Calcule les points selon le rang - avec pénalités dynamiques !
    
    Si le rang est pire (plus élevé) que la moyenne des 10 meilleurs → pénalité !
    """
    # Déterminer le seuil de pénalité
    threshold = avg_top_10 if avg_top_10 else PENALTY_THRESHOLD
    
    # Si le rang est pire que la moyenne top 10 → pénalité
    if rank > threshold:
        return REGRESSION_PENALTY
    
    # Sinon, points normaux selon le rang
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
    else:
        return 0

def get_avg_top_10(game):
    """Calcule la moyenne des 10 meilleurs rangs de la manche."""
    if not game['guesses']:
        return None
    
    # Récupérer tous les rangs et les trier
    ranks = sorted([g['rank'] for g in game['guesses']])
    
    # Prendre les 10 meilleurs (ou moins s'il y en a moins)
    top_ranks = ranks[:10]
    
    # Calculer la moyenne
    return sum(top_ranks) / len(top_ranks)

def get_top_20_words(secret_word):
    """Retourne les 20 mots les plus proches du mot secret."""
    if secret_word not in RANKS:
        return []
    
    ranks_for_secret = RANKS[secret_word]
    # Trier par rang et prendre les 20 premiers (excluant le mot lui-même qui est rang 1)
    sorted_words = sorted(ranks_for_secret.items(), key=lambda x: x[1])
    # Prendre rangs 2 à 21 (le rang 1 c'est le mot secret)
    top_20 = [{'word': word, 'rank': rank} for word, rank in sorted_words[1:21]]
    return top_20

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

def get_current_turn_limit(game):
    """Retourne la limite de temps actuelle pour le joueur courant."""
    current = game['current_player']
    consecutive = game.get('consecutive_timeouts', {}).get(current, 0)
    if consecutive >= TIMEOUT_THRESHOLD:
        return REDUCED_TURN_TIME
    return TURN_TIME_LIMIT

def get_remaining_turn_time(game):
    """Retourne le temps restant pour ce tour (en secondes)."""
    elapsed = time.time() - game['turn_start_time']
    turn_limit = get_current_turn_limit(game)
    remaining = max(0, turn_limit - elapsed)
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
        
        # Incrémenter le compteur de timeouts consécutifs
        if 'consecutive_timeouts' not in game:
            game['consecutive_timeouts'] = {}
        game['consecutive_timeouts'][current] = game['consecutive_timeouts'].get(current, 0) + 1
        
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
        'started': False,  # La partie n'est pas encore lancée
        'consecutive_timeouts': {1: 0}  # Compteur de timeouts consécutifs par joueur
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
    game['consecutive_timeouts'][player_id] = 0
    
    # Si la partie est déjà commencée, on la redémarre pour l'équité !
    game_restarted = False
    if game.get('started', False):
        now = time.time()
        # Reset complet de la manche
        game['secret_word'] = random.choice(TARGETS)
        game['guesses'] = []
        game['current_player'] = 1
        game['found'] = False
        game['winner'] = None
        game['round_start_time'] = now
        game['turn_start_time'] = now
        game['round_timeout'] = False
        game['turn_skipped'] = False
        # Reset tous les scores à 0 pour l'équité
        for pid in game['players']:
            game['scores'][pid] = 0
            game['consecutive_timeouts'][pid] = 0
        game_restarted = True
    
    return jsonify({
        'success': True,
        'code': code,
        'player_id': player_id,
        'players': game['players'],
        'round_time_limit': ROUND_TIME_LIMIT,
        'turn_time_limit': TURN_TIME_LIMIT,
        'game_restarted': game_restarted
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
    
    # Calculer la moyenne des 10 meilleurs rangs pour la pénalité de régression
    avg_top_10 = get_avg_top_10(game)
    points = calculate_points(rank, avg_top_10)
    indicator = get_indicator(rank)
    
    # Vérifier si c'est le nouveau meilleur rang → bonus !
    is_new_best = False
    if game['guesses']:
        current_best = min(g['rank'] for g in game['guesses'])
        if rank < current_best:
            is_new_best = True
            points += BEST_WORD_BONUS
    
    # Enregistrer la tentative
    guess_data = {
        'word': word,
        'rank': rank,
        'points': points,
        'indicator': indicator,
        'player_id': player_id,
        'player_name': game['players'][player_id],
        'is_new_best': is_new_best
    }
    game['guesses'].append(guess_data)
    game['scores'][player_id] = game['scores'].get(player_id, 0) + points
    
    # Reset le compteur de timeouts consécutifs (le joueur a répondu !)
    if 'consecutive_timeouts' not in game:
        game['consecutive_timeouts'] = {}
    game['consecutive_timeouts'][player_id] = 0
    
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
    
    # Calculer le timer actuel pour chaque joueur
    current_turn_limit = get_current_turn_limit(game)
    is_reduced_timer = current_turn_limit < TURN_TIME_LIMIT
    
    # Récupérer les 20 mots les plus proches si la manche est terminée
    game_ended = game['found'] or round_timeout
    top_20 = get_top_20_words(game['secret_word']) if game_ended else None
    
    return jsonify({
        'success': True,
        'players': game['players'],
        'scores': game['scores'],
        'guesses': sorted_guesses,
        'current_player': game['current_player'],
        'found': game['found'],
        'winner': game['winner'],
        'secret_word': game['secret_word'] if game_ended else None,
        'top_20_words': top_20,
        'round_remaining': get_remaining_round_time(game),
        'turn_remaining': get_remaining_turn_time(game),
        'turn_limit': current_turn_limit,
        'is_reduced_timer': is_reduced_timer,
        'round_timeout': round_timeout,
        'turn_skipped': turn_skipped,
        'avg_top_10': get_avg_top_10(game)
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
    # Reset les compteurs de timeouts consécutifs (nouvelle manche)
    for pid in game['players']:
        game['consecutive_timeouts'][pid] = 0
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
    port = int(os.environ.get('PORT', 5000))
    print(f"\n[SEMANTIX] Serveur démarré - Port {port}")
    print(f"   Timer mot: {ROUND_TIME_LIMIT}s | Timer tour: {TURN_TIME_LIMIT}s (réduit à {REDUCED_TURN_TIME}s après {TIMEOUT_THRESHOLD} timeouts)")
    print(f"   Penalite rang > moyenne top 10: -1pt | Timeout: {TIMEOUT_PENALTY}pt | Bonus meilleur mot: +{BEST_WORD_BONUS}pt\n")
    app.run(host='0.0.0.0', port=port, debug=True)
