# app.py
# Application web SEMANTCHIK - Version TEMPS RÉEL avec cooldown et salons persistants

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
COOLDOWN_DURATION = 10      # 10 secondes de cooldown après chaque soumission
BASE_PENALTY = 1            # Pénalité de base pendant cooldown (-1, puis -2, -4, -8...)

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
# Stockage des salons en mémoire (persistants)
# ======================
rooms = {}  # code -> room_state

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
    """Génère un code de salon à 4 lettres."""
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
    elif rank <= 1000:
        return 1
    elif rank <= 10000:
        return 0
    else:
        # Rang > 10000 = gelé = pénalité
        return -1

def get_top_20_words(secret_word):
    """Retourne les 20 mots les plus proches du mot secret."""
    if secret_word not in RANKS:
        return []
    
    ranks_for_secret = RANKS[secret_word]
    sorted_words = sorted(ranks_for_secret.items(), key=lambda x: x[1])
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
    elif rank <= 10000:
        return "🥶"
    else:
        return "💀"  # Gelé - pénalité

def get_remaining_round_time(room):
    """Retourne le temps restant pour ce mot (en secondes)."""
    elapsed = time.time() - room['round_start_time']
    remaining = max(0, ROUND_TIME_LIMIT - elapsed)
    return int(remaining)

def check_round_timeout(room):
    """Vérifie si le temps du mot est écoulé."""
    return get_remaining_round_time(room) <= 0 and not room['found']

def get_player_cooldown_remaining(room, player_id):
    """Retourne le temps de cooldown restant pour un joueur (en secondes)."""
    player_data = room['player_data'].get(player_id, {})
    cooldown_end = player_data.get('current_cooldown_end', 0)
    remaining = max(0, cooldown_end - time.time())
    return remaining

def get_cooldown_penalty(room, player_id):
    """Calcule la pénalité actuelle si le joueur est en cooldown."""
    player_data = room['player_data'].get(player_id, {})
    consecutive_cooldown_guesses = player_data.get('consecutive_cooldown_guesses', 0)
    # Pénalité exponentielle: 1, 2, 4, 8, 16...
    return BASE_PENALTY * (2 ** consecutive_cooldown_guesses)

def start_new_round(room):
    """Démarre une nouvelle manche dans le salon."""
    now = time.time()
    room['secret_word'] = random.choice(TARGETS)
    room['guesses'] = []
    room['found'] = False
    room['winner'] = None
    room['round_start_time'] = now
    room['round_timeout'] = False
    room['round_number'] = room.get('round_number', 0) + 1
    # Reset les compteurs de cooldown pour tous les joueurs
    for pid in room['player_data']:
        room['player_data'][pid]['consecutive_cooldown_guesses'] = 0
        room['player_data'][pid]['current_cooldown_end'] = 0

# ======================
# Routes
# ======================
@app.route('/')
def index():
    return render_template('index.html', room_code=None)

@app.route('/<code>')
def room_page(code):
    """Page d'un salon spécifique - URL partageable."""
    code = code.upper()
    # Vérifier que c'est un code valide (4 lettres)
    if len(code) == 4 and code.isalpha():
        return render_template('index.html', room_code=code)
    # Sinon, rediriger vers l'accueil
    return render_template('index.html', room_code=None)

@app.route('/api/create', methods=['POST'])
def create_room():
    """Crée un nouveau salon."""
    data = request.json
    player_name = data.get('name', 'Joueur')
    
    # Générer un code unique
    code = generate_code()
    while code in rooms:
        code = generate_code()
    
    now = time.time()
    player_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    
    # Créer le salon
    rooms[code] = {
        'players': {player_id: player_name},
        'player_data': {
            player_id: {
                'score': 0,
                'current_cooldown_end': 0,
                'consecutive_cooldown_guesses': 0
            }
        },
        'secret_word': random.choice(TARGETS),
        'guesses': [],
        'found': False,
        'winner': None,
        'created_at': datetime.now().isoformat(),
        'round_start_time': now,
        'round_timeout': False,
        'round_number': 1,
        'started': False
    }
    
    return jsonify({
        'success': True,
        'code': code,
        'player_id': player_id,
        'round_time_limit': ROUND_TIME_LIMIT,
        'cooldown_duration': COOLDOWN_DURATION
    })

@app.route('/api/join', methods=['POST'])
def join_room():
    """Rejoint un salon existant ou crée un nouveau joueur dedans."""
    data = request.json
    code = data.get('code', '').upper()
    player_name = data.get('name', 'Joueur')
    existing_player_id = data.get('player_id')  # Pour reconnecter après refresh
    
    if code not in rooms:
        return jsonify({'success': False, 'error': 'Code de salon invalide'})
    
    room = rooms[code]
    game_restarted = False
    
    # Vérifier si c'est une reconnexion
    if existing_player_id and existing_player_id in room['players']:
        # Reconnexion - on garde le même player_id
        player_id = existing_player_id
        # Mettre à jour le nom si différent
        room['players'][player_id] = player_name
    else:
        # Nouveau joueur
        if len(room['players']) >= 8:
            return jsonify({'success': False, 'error': 'Salon complet (8 joueurs max)'})
        
        player_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        room['players'][player_id] = player_name
        room['player_data'][player_id] = {
            'score': 0,
            'last_guess_time': 0,
            'consecutive_cooldown_guesses': 0,
            'current_cooldown_end': 0
        }
        
        # Si la partie est déjà commencée, on la redémarre pour l'équité
        if room.get('started', False):
            start_new_round(room)
            # Reset tous les scores à 0
            for pid in room['player_data']:
                room['player_data'][pid]['score'] = 0
            game_restarted = True
    
    return jsonify({
        'success': True,
        'code': code,
        'player_id': player_id,
        'players': room['players'],
        'round_time_limit': ROUND_TIME_LIMIT,
        'cooldown_duration': COOLDOWN_DURATION,
        'started': room.get('started', False),
        'round_number': room.get('round_number', 1),
        'game_restarted': game_restarted
    })

@app.route('/api/start', methods=['POST'])
def start_game():
    """Démarre officiellement la partie (lance les timers)."""
    data = request.json
    code = data.get('code', '').upper()
    
    if code not in rooms:
        return jsonify({'success': False, 'error': 'Salon non trouvé'})
    
    room = rooms[code]
    now = time.time()
    room['round_start_time'] = now
    room['started'] = True
    
    return jsonify({'success': True})

@app.route('/api/lobby/<code>')
def get_lobby_state(code):
    """Récupère l'état du lobby (pour synchroniser le démarrage)."""
    code = code.upper()
    
    if code not in rooms:
        return jsonify({'success': False, 'error': 'Salon non trouvé'})
    
    room = rooms[code]
    
    return jsonify({
        'success': True,
        'players': room['players'],
        'started': room.get('started', False),
        'round_number': room.get('round_number', 1)
    })

@app.route('/api/guess', methods=['POST'])
def make_guess():
    """Fait une tentative - TEMPS RÉEL avec cooldown."""
    data = request.json
    code = data.get('code', '').upper()
    player_id = data.get('player_id')
    word = data.get('word', '')
    
    if code not in rooms:
        return jsonify({'success': False, 'error': 'Salon non trouvé'})
    
    room = rooms[code]
    
    if player_id not in room['players']:
        return jsonify({'success': False, 'error': 'Joueur non trouvé dans le salon'})
    
    # Vérifier timeout du mot
    if check_round_timeout(room):
        room['round_timeout'] = True
        return jsonify({
            'success': False, 
            'error': 'Temps écoulé pour ce mot !',
            'timeout': True,
            'secret_word': room['secret_word']
        })
    
    if room['found']:
        return jsonify({'success': False, 'error': 'Mot déjà trouvé ! Attendez la prochaine manche.'})
    
    # Normaliser le mot
    word = normalize_text(word)
    
    if not word:
        return jsonify({'success': False, 'error': 'Mot vide'})
    
    # Vérifier si déjà essayé
    guessed_words = [g['word'] for g in room['guesses']]
    if word in guessed_words:
        return jsonify({'success': False, 'error': 'Mot déjà essayé'})
    
    # Chercher le rang
    secret = room['secret_word']
    if secret not in RANKS:
        return jsonify({'success': False, 'error': 'Erreur de configuration'})
    
    ranks_for_secret = RANKS[secret]
    
    if word not in ranks_for_secret:
        return jsonify({'success': False, 'error': 'Mot non reconnu (pas dans le dictionnaire)'})
    
    rank = ranks_for_secret[word]
    
    # Vérifier le cooldown
    cooldown_remaining = get_player_cooldown_remaining(room, player_id)
    is_in_cooldown = cooldown_remaining > 0
    cooldown_penalty = 0
    
    if is_in_cooldown:
        # Appliquer la pénalité exponentielle
        cooldown_penalty = get_cooldown_penalty(room, player_id)
        room['player_data'][player_id]['consecutive_cooldown_guesses'] += 1
        # ADDITIONNER le cooldown : ajouter 10s au cooldown restant
        room['player_data'][player_id]['current_cooldown_end'] = time.time() + cooldown_remaining + COOLDOWN_DURATION
    else:
        # Reset le compteur si on a attendu le cooldown
        room['player_data'][player_id]['consecutive_cooldown_guesses'] = 0
        # Nouveau cooldown de 10s
        room['player_data'][player_id]['current_cooldown_end'] = time.time() + COOLDOWN_DURATION
    
    # Calculer les points
    base_points = calculate_points(rank)
    total_points = base_points - cooldown_penalty
    indicator = get_indicator(rank)
    
    # Enregistrer la tentative
    guess_data = {
        'word': word,
        'rank': rank,
        'base_points': base_points,
        'cooldown_penalty': cooldown_penalty,
        'total_points': total_points,
        'indicator': indicator,
        'player_id': player_id,
        'player_name': room['players'][player_id],
        'was_in_cooldown': is_in_cooldown,
        'timestamp': time.time()
    }
    room['guesses'].append(guess_data)
    room['player_data'][player_id]['score'] += total_points
    
    # Mot trouvé ?
    found = (rank == 1)
    if found:
        room['found'] = True
        room['winner'] = player_id
    
    # Calculer le nouveau cooldown total
    new_cooldown = get_player_cooldown_remaining(room, player_id)
    
    return jsonify({
        'success': True,
        'word': word,
        'rank': rank,
        'base_points': base_points,
        'cooldown_penalty': cooldown_penalty,
        'total_points': total_points,
        'indicator': indicator,
        'found': found,
        'was_in_cooldown': is_in_cooldown,
        'secret_word': room['secret_word'] if found else None,
        'new_cooldown': new_cooldown
    })

@app.route('/api/state/<code>')
def get_state(code):
    """Récupère l'état actuel du salon."""
    code = code.upper()
    player_id = request.args.get('player_id', '')
    
    if code not in rooms:
        return jsonify({'success': False, 'error': 'Salon non trouvé'})
    
    room = rooms[code]
    
    # Vérifier le timeout
    round_timeout = check_round_timeout(room)
    
    # Trier les tentatives par rang
    sorted_guesses = sorted(room['guesses'], key=lambda x: x['rank'])
    
    # Construire les scores
    scores = {}
    for pid, data in room['player_data'].items():
        scores[pid] = data['score']
    
    # Récupérer les 20 mots les plus proches si la manche est terminée
    game_ended = room['found'] or round_timeout
    top_20 = get_top_20_words(room['secret_word']) if game_ended else None
    
    # Cooldown du joueur actuel
    player_cooldown = 0
    next_penalty = 0
    if player_id and player_id in room['player_data']:
        player_cooldown = get_player_cooldown_remaining(room, player_id)
        if player_cooldown > 0:
            next_penalty = get_cooldown_penalty(room, player_id)
    
    return jsonify({
        'success': True,
        'players': room['players'],
        'scores': scores,
        'guesses': sorted_guesses,
        'found': room['found'],
        'winner': room['winner'],
        'winner_name': room['players'].get(room['winner']) if room['winner'] else None,
        'secret_word': room['secret_word'] if game_ended else None,
        'top_20_words': top_20,
        'round_remaining': get_remaining_round_time(room),
        'round_timeout': round_timeout,
        'round_number': room.get('round_number', 1),
        'player_cooldown': int(player_cooldown),
        'next_penalty': next_penalty,
        'cooldown_duration': COOLDOWN_DURATION,
        'started': room.get('started', False)
    })

@app.route('/api/new_round', methods=['POST'])
def new_round():
    """Démarre une nouvelle manche."""
    data = request.json
    code = data.get('code', '').upper()
    
    if code not in rooms:
        return jsonify({'success': False, 'error': 'Salon non trouvé'})
    
    room = rooms[code]
    start_new_round(room)
    
    return jsonify({
        'success': True,
        'round_time_limit': ROUND_TIME_LIMIT,
        'round_number': room['round_number']
    })

@app.route('/api/leave', methods=['POST'])
def leave_room():
    """Quitte un salon (optionnel, le joueur reste visible pour les scores)."""
    data = request.json
    code = data.get('code', '').upper()
    player_id = data.get('player_id')
    
    if code not in rooms:
        return jsonify({'success': False, 'error': 'Salon non trouvé'})
    
    # On ne supprime pas vraiment le joueur, juste pour les reconnexions
    # Le joueur peut toujours revenir avec son player_id
    
    return jsonify({'success': True})

# ======================
# Lancement
# ======================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"\n[SEMANTCHIK] Mode TEMPS RÉEL - Port {port}")
    print(f"   Timer mot: {ROUND_TIME_LIMIT}s")
    print(f"   Cooldown: {COOLDOWN_DURATION}s | Pénalités: -{BASE_PENALTY}, -{BASE_PENALTY*2}, -{BASE_PENALTY*4}...\n")
    app.run(host='0.0.0.0', port=port, debug=True)
