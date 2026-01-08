from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_pymongo import PyMongo
from flask_socketio import SocketIO, emit, join_room
from bson.objectid import ObjectId
from datetime import datetime, timedelta # NEW
import bcrypt
import secrets
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "studysync_2026_key")
app.config["MONGO_URI"] = os.getenv("MONGO_URI")

mongo = PyMongo(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# --- HELPERS ---
def get_user():
    if 'user_id' in session and mongo.db is not None:
        return mongo.db.users.find_one({"_id": ObjectId(session['user_id'])})
    return None

def calculate_progress(group_id):
    group = mongo.db.groups.find_one({"_id": ObjectId(group_id)})
    total_concepts = 0
    for sub in group.get('syllabus', []):
        for unit in sub.get('units', []):
            total_concepts += len(unit.get('concepts', []))
    
    progress_docs = list(mongo.db.progress.find({"group_id": ObjectId(group_id)}))
    stats = []
    for p in progress_docs:
        # Done is now calculated based on the length of the 'history' list
        done = len(p.get('history', []))
        perc = (done / total_concepts * 100) if total_concepts > 0 else 0
        stats.append({"username": p['username'], "percentage": round(perc, 1), "count": done})
    
    # Sort for Leaderboard (highest count first)
    stats = sorted(stats, key=lambda x: x['count'], reverse=True)
    return stats, total_concepts

# NEW: Smart Priority Logic with Test Topics
def get_priority_map(group, user_history):
    priority_map = {}
    now = datetime.now()
    
    # Check if a test is < 7 days away and get those topics
    urgent_topics = []
    for t in group.get('tests', []):
        test_date = datetime.strptime(t['date'], '%Y-%m-%d')
        if 0 <= (test_date - now).days <= 7:
            urgent_topics.extend(t.get('covered_topics', []))
    
    # 1. Get all topics in syllabus order
    all_ordered = []
    for sub in group.get('syllabus', []):
        for unit in sub.get('units', []):
            for concept in unit.get('concepts', []):
                key = f"{sub['subject_name']}||{unit['unit_name']}||{concept}"
                all_ordered.append(key)

    # 2. Split into Finished and Unfinished
    finished_keys = [h['concept'] for h in user_history]
    unfinished = [t for t in all_ordered if t not in finished_keys]

    # 3. Apply Priority Rules
    # Urgent test topics get highest priority
    for topic in urgent_topics:
        if topic in unfinished:
            priority_map[topic] = "critical"
        else:
            priority_map[topic] = "revision-old"
    
    # Remaining unfinished: Earliest > Latter
    remaining_unfinished = [t for t in unfinished if t not in urgent_topics]
    u_mid = len(remaining_unfinished) // 2
    for t in remaining_unfinished[:u_mid]:
        if t not in priority_map:
            priority_map[t] = "critical"
    for t in remaining_unfinished[u_mid:]:
        if t not in priority_map:
            priority_map[t] = "high"

    # Finished: Earliest Finished > Latest Finished (for revision)
    finished_not_urgent = [t for t in finished_keys if t not in urgent_topics]
    f_mid = len(finished_not_urgent) // 2
    for t in finished_not_urgent[:f_mid]:
        if t not in priority_map:
            priority_map[t] = "revision-old"
    for t in finished_not_urgent[f_mid:]:
        if t not in priority_map:
            priority_map[t] = "revision-recent"

    return priority_map

# --- AUTH (Unchanged) ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        hashed_pw = bcrypt.hashpw(request.form['password'].encode('utf-8'), bcrypt.gensalt())
        user_id = mongo.db.users.insert_one({"username": request.form['username'], "password": hashed_pw}).inserted_id
        session['user_id'] = str(user_id)
        return redirect(url_for('index'))
    return render_template('login.html', type="Register")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = mongo.db.users.find_one({"username": request.form['username']})
        if user and bcrypt.checkpw(request.form['password'].encode('utf-8'), user['password']):
            session['user_id'] = str(user['_id'])
            return redirect(url_for('index'))
    return render_template('login.html', type="Login")

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- GROUP LOGIC ---
@app.route('/')
def index():
    user = get_user()
    if not user: return redirect(url_for('login'))
    groups = list(mongo.db.groups.find({"members": user['_id']}))
    return render_template('dashboard.html', user=user, groups=groups)

# NEW: Tabular Syllabus Entry Route
@app.route('/add-syllabus', methods=['POST'])
def add_syllabus():
    gid = request.form.get('group_id')
    sub_name = request.form.get('subject_name')
    unit_name = request.form.get('unit_name')
    topics_csv = request.form.get('topics_csv')
    
    topics_list = [t.strip() for t in topics_csv.split(',') if t.strip()]
    
    # Hierarchy: Subject -> Unit -> Topics
    # Finds subject, if exists adds unit. If subject doesn't exist, creates it.
    mongo.db.groups.update_one(
        {"_id": ObjectId(gid)},
        {"$push": {"syllabus": {
            "subject_name": sub_name,
            "units": [{"unit_name": unit_name, "concepts": topics_list}]
        }}}
    )
    return redirect(url_for('group_view', group_id=gid))

@app.route('/group/<group_id>')
def group_view(group_id):
    user = get_user()
    if not user: return redirect(url_for('login'))
    group = mongo.db.groups.find_one({"_id": ObjectId(group_id)})
    stats, total = calculate_progress(group_id)
    
    # Progress handling
    user_prog = mongo.db.progress.find_one({"user_id": user['_id'], "group_id": ObjectId(group_id)})
    history = user_prog.get('history', []) if user_prog else []
    completed_dict = {h['concept']: True for h in history}
    
    # Get priorities
    prio_map = get_priority_map(group, history)
    
    return render_template('group_view.html', 
                           group=group, user=user, 
                           is_owner=(group['owner_id']==user['_id']), 
                           peers_progress=stats, 
                           total_concepts=total, 
                           completed_dict=completed_dict,
                           priority_map=prio_map)

# --- Create & Join Group Routes ---
@app.route('/create-group', methods=['GET'])
def create_group():
    user = get_user()
    if not user:
        return redirect(url_for('login'))
    return render_template('creategroup.html', user=user)

@app.route('/create-group', methods=['POST'])
def create_group_post():
    user = get_user()
    if not user:
        return jsonify({'success': False}), 401

    # Try JSON first, fallback to form data if needed
    data = request.get_json(silent=True) or {}
    if not data:
        # fallback to form-encoded request (e.g., older clients)
        name = request.form.get('name')
        # attempt to parse 'syllabus' if provided as JSON string
        syllabus = []
        raw = request.form.get('syllabus')
        if raw:
            try:
                import json
                syllabus = json.loads(raw)
            except Exception:
                syllabus = []
    else:
        name = data.get('name')
        syllabus = data.get('syllabus', [])

    if not name:
        return jsonify({'success': False, 'error': 'missing group name'}), 400

    invite_code = secrets.token_hex(4)
    group_obj = {
        "name": name,
        "owner_id": user['_id'],
        "members": [user['_id']],
        "invite_code": invite_code,
        "syllabus": syllabus,
        "tests": [],
        "resources": [],
        "pending_resources": []
    }
    gid = mongo.db.groups.insert_one(group_obj).inserted_id
    return jsonify({"success": True, "group_id": str(gid)})

@app.route('/join-group', methods=['POST'])
def join_group():
    user = get_user()
    if not user:
        return redirect(url_for('login'))
    code = request.form.get('code')
    group = mongo.db.groups.find_one({"invite_code": code})
    if not group:
        return redirect(url_for('index'))
    mongo.db.groups.update_one({"_id": group['_id']}, {"$addToSet": {"members": user['_id']}})
    return redirect(url_for('group_view', group_id=str(group['_id'])))

@app.route('/update-progress', methods=['POST'])
def update_progress():
    user = get_user()
    data = request.json
    status = data['status']
    concept = data['concept']
    gid = ObjectId(data['group_id'])

    if status:
        # Add to history with timestamp to support "Earliest Finished" logic
        mongo.db.progress.update_one(
            {"user_id": user['_id'], "group_id": gid},
            {"$push": {"history": {"concept": concept, "at": datetime.now()}}, 
             "$set": {"username": user['username']}}, 
            upsert=True
        )
        # Emit a lightweight notification with username and topic name only
        topic_name = concept.split('||')[-1] if '||' in concept else concept.split('-')[-1]
        socketio.emit('notification', {'username': user['username'], 'topic': topic_name}, to=data['group_id'])
    else:
        # Remove from history
        mongo.db.progress.update_one(
            {"user_id": user['_id'], "group_id": gid},
            {"$pull": {"history": {"concept": concept}}}
        )
    
    return jsonify({"success": True})

# --- Test logic (unchanged) ---
@app.route('/add-test', methods=['POST'])
def add_test():
    gid = request.form.get('group_id')
    covered_topics = request.form.getlist('covered_topics')
    portion_detail = request.form.get('portion', '')
    
    test_obj = {
        "name": request.form.get('test_name'),
        "date": request.form.get('test_date'),
        "type": request.form.get('test_type'),
        "subject_name": request.form.get('subject_name'),
        "covered_topics": covered_topics,
        "portion": portion_detail if portion_detail else ', '.join(covered_topics) if covered_topics else "Full Syllabus"
    }
    
    mongo.db.groups.update_one({"_id": ObjectId(gid)}, {"$push": {"tests": test_obj}})
    return redirect(url_for('group_view', group_id=gid))

@socketio.on('join')
def on_join(data):
    join_room(data['group_id'])

@app.route('/get-concepts-by-subject', methods=['POST'])
def get_concepts_by_subject():
    data = request.json
    subject_name = data.get('subject_name')
    group_id = data.get('group_id')
    
    group = mongo.db.groups.find_one({"_id": ObjectId(group_id)})
    units = []
    
    for sub in group.get('syllabus', []):
        if sub['subject_name'] == subject_name:
            units = sub.get('units', [])
            break
    
    return jsonify({"units": units})

# --- Resource Logic ---
@app.route('/add-resource', methods=['POST'])
def add_resource():
    user = get_user()
    gid = ObjectId(request.form.get('group_id'))
    group = mongo.db.groups.find_one({"_id": gid})
    
    resource_obj = {
        "_id": ObjectId(),
        "title": request.form.get('title'),
        "description": request.form.get('description', ''),
        "type": request.form.get('type'),
        "link": request.form.get('link'),
        "added_by": user['username'],
        "added_at": datetime.now()
    }
    
    mongo.db.groups.update_one(
        {"_id": gid},
        {"$push": {"pending_resources": resource_obj}}
    )
    
    return redirect(request.referrer)

@app.route('/approve-resource', methods=['POST'])
def approve_resource():
    user = get_user()
    data = request.json
    gid = ObjectId(data['group_id'])
    rid = ObjectId(data['resource_id'])
    
    group = mongo.db.groups.find_one({"_id": gid})
    
    # Find and move resource from pending to approved
    pending = group.get('pending_resources', [])
    approved = group.get('resources', [])
    
    for res in pending:
        if res['_id'] == rid:
            approved.append(res)
            pending.remove(res)
            break
    
    mongo.db.groups.update_one(
        {"_id": gid},
        {"$set": {"resources": approved, "pending_resources": pending}}
    )
    
    return jsonify({"success": True})

@app.route('/reject-resource', methods=['POST'])
def reject_resource():
    data = request.json
    gid = ObjectId(data['group_id'])
    rid = ObjectId(data['resource_id'])
    
    mongo.db.groups.update_one(
        {"_id": gid},
        {"$pull": {"pending_resources": {"_id": rid}}}
    )
    
    return jsonify({"success": True})

if __name__ == '__main__':
    # Disable the Flask reloader to avoid double-binding the socket on Windows
    # (the reloader spawns a child process which can cause the "address already in use" error)
    socketio.run(app, debug=True, use_reloader=False)