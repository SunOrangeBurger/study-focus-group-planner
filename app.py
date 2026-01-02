from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_pymongo import PyMongo
from flask_socketio import SocketIO, emit, join_room
from bson.objectid import ObjectId
import bcrypt
import secrets

app = Flask(__name__)
app.secret_key = "studysync_2026_key"
app.config["MONGO_URI"] = "mongodb://localhost:27017/studyGroupDB"

mongo = PyMongo(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# --- HELPERS ---
def get_user():
    if 'user_id' in session:
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
        done = sum(1 for v in p.get('completed', {}).values() if v is True)
        perc = (done / total_concepts * 100) if total_concepts > 0 else 0
        stats.append({"username": p['username'], "percentage": round(perc, 1), "count": done})
    return stats, total_concepts

# --- AUTH ---
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

@app.route('/create-group', methods=['GET', 'POST'])
def create_group():
    user = get_user()
    if request.method == 'POST':
        data = request.json
        group_id = mongo.db.groups.insert_one({
            "name": data['name'],
            "owner_id": user['_id'],
            "invite_code": secrets.token_hex(3).upper(),
            "syllabus": data['syllabus'], 
            "members": [user['_id']],
            "tests": []
        }).inserted_id
        return jsonify({"success": True})
    return render_template('create_group.html')

@app.route('/join-group', methods=['POST'])
def join_group():
    user = get_user()
    code = request.form['code'].strip().upper()
    group = mongo.db.groups.find_one({"invite_code": code})
    if group:
        mongo.db.groups.update_one({"_id": group['_id']}, {"$addToSet": {"members": user['_id']}})
    return redirect(url_for('index'))

@app.route('/group/<group_id>')
def group_view(group_id):
    user = get_user()
    if not user: return redirect(url_for('login'))
    group = mongo.db.groups.find_one({"_id": ObjectId(group_id)})
    stats, total = calculate_progress(group_id)
    user_prog = mongo.db.progress.find_one({"user_id": user['_id'], "group_id": ObjectId(group_id)})
    completed_dict = user_prog.get('completed', {}) if user_prog else {}
    return render_template('group_view.html', group=group, user=user, is_owner=(group['owner_id']==user['_id']), peers_progress=stats, total_concepts=total, completed_dict=completed_dict)

@app.route('/add-test', methods=['POST'])
def add_test():
    user = get_user()
    gid = request.form.get('group_id')
    mongo.db.groups.update_one({"_id": ObjectId(gid)}, {"$push": {"tests": {
        "name": request.form.get('test_name'), "date": request.form.get('test_date'),
        "type": request.form.get('test_type'), "portion": request.form.get('portion')
    }}})
    return redirect(url_for('group_view', group_id=gid))

@app.route('/update-progress', methods=['POST'])
def update_progress():
    user = get_user()
    data = request.json
    status = data['status']
    mongo.db.progress.update_one(
        {"user_id": user['_id'], "group_id": ObjectId(data['group_id'])},
        {"$set": {f"completed.{data['concept']}": status, "username": user['username']}}, upsert=True
    )
    if status:
        concept_clean = data['concept'].split('-')[-1]
        socketio.emit('notification', {'msg': f"🔥 {user['username']} finished: {concept_clean}!"}, to=data['group_id'])
    return jsonify({"success": True})

@socketio.on('join')
def on_join(data):
    join_room(data['group_id'])

if __name__ == '__main__':
    socketio.run(app, debug=True)