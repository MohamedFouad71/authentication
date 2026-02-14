# --- 1. إصلاح مشكلة SQLite (ضروري جداً لمكتبة ChromaDB) ---
try:
    __import__('pysqlite3')
    import sys
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ImportError:
    pass
# -----------------------------------------------------------

import os
import io
import uuid
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file
import google.generativeai as genai
from app.models.patient import Patient
from app.utils.jwt import jwt_required
from app import db
import speech_recognition as sr
from gtts import gTTS
from pydub import AudioSegment

# مكتبات الذاكرة
 import chromadb
 from sentence_transformers import SentenceTransformer

chat_bp = Blueprint('chat', __name__)

# --- 2. إعداد المسار الكامل لقاعدة البيانات (Vector DB) ---
# نستخدم المسار الكامل لتجنب أخطاء السيرفر (Systemd)
DB_PATH = "/home/ubuntu/mobile/authentication/vector_db"

# إعداد ChromaDB مع معالجة الأخطاء
try:
    if not os.path.exists(DB_PATH):
        os.makedirs(DB_PATH, exist_ok=True)
        
    chroma_client = chromadb.PersistentClient(path=DB_PATH)
    collection = chroma_client.get_or_create_collection("patients")
    
    # تحميل موديل التضمين (Embeddings)
    embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
    print("[OK] Vector DB & Model Loaded Successfully")
    
except Exception as e:
    print(f"[ERROR] Critical Warning: Vector DB could not load: {e}")
    chroma_client = None
    collection = None
    embedding_model = None


# --- إعداد Gemini API ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
genai.configure(api_key=GOOGLE_API_KEY)

# استخدام الموديل المتاح والسريع
try:
    model = genai.GenerativeModel('gemini-2.5-flash')
except:
    model = genai.GenerativeModel('gemini-1.5-flash')

# إعدادات الأمان لتقليل الحجب
safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]


# ==========================================
# ===          دوال الذاكرة (RAG)        ===
# ==========================================

def embed_text(text: str):
    if not embedding_model: return []
    return embedding_model.encode(text).tolist()

def store_patient_vector(patient_id: str, text: str):
    """تخزين السياق في الذاكرة طويلة المدى"""
    if not collection or not embedding_model: return
    try:
        vector = embed_text(text)
        collection.upsert(
            ids=[str(patient_id)],
            embeddings=[vector],
            documents=[text],
            metadatas=[{"patient_id": str(patient_id)}]
        )
    except Exception as e:
        print(f"Store Vector Error: {e}")

def search_patient_vectors(patient_id: str, query: str, k: int = 3):
    """البحث عن معلومات سابقة ذات صلة"""
    if not collection or not embedding_model: return "Memory system inactive."
    try:
        query_vector = embed_text(query)
        results = collection.query(
            query_embeddings=[query_vector],
            n_results=k,
            where={"patient_id": str(patient_id)}
        )
        if not results or not results.get("documents") or not results["documents"][0]:
            return "No relevant memory found."
        docs = results["documents"][0]
        return "\n".join(docs)
    except Exception as e:
        print(f"Search Vector Error: {e}")
        return "Error retrieving memory."


# ==========================================
# ===       دالة جلب البيانات الشاملة      ===
# ==========================================

def get_patient_context(patient_id):
    """
    جلب كل شيء عن المريض: (شخصي - دكتور - أدوية - ألعاب)
    """
    patient = Patient.query.filter_by(patient_id=patient_id).first()

    if not patient:
        return None

    # 1. البيانات الشخصية
    info = (
        f"=== PERSONAL INFO ===\n"
        f"Name: {patient.name}\n"
        f"Age: {patient.age}\n"
        f"Gender: {getattr(patient, 'gender', 'Not specified')}\n"
        f"Condition: {getattr(patient, 'condition', 'Alzheimer')}\n"
    )

    # 2. الدكتور والمرافق
    doctor_name = "Not Assigned"
    doctor_phone = "N/A"
    if hasattr(patient, 'doctor') and patient.doctor:
        doctor_name = patient.doctor.name
        # لو الدكتور له حقل phone في الجدول
        doctor_phone = getattr(patient.doctor, 'phone', 'N/A')
    
    caregiver_name = getattr(patient, 'caregiver_name', 'Not Assigned')
    emergency_phone = getattr(patient, 'emergency_phone', 'N/A')

    info += (
        f"\n=== CONTACTS ===\n"
        f"Treating Doctor: {doctor_name} (Phone: {doctor_phone})\n"
        f"Caregiver/Emergency: {caregiver_name} (Phone: {emergency_phone})\n"
    )

    # 3. الأدوية
    if patient.prescriptions:
        info += "\n=== MEDICATION SCHEDULE ===\n"
        for presc in patient.prescriptions:
            med_name = "Unknown"
            if hasattr(presc, 'medicine') and presc.medicine:
                med_name = presc.medicine.name
            elif hasattr(presc, 'medicine_name'):
                med_name = presc.medicine_name
            
            time_str = str(presc.schedule_time) if presc.schedule_time else "Any time"
            notes = presc.notes if presc.notes else "-"
            info += f"- Drug: {med_name} | Time: {time_str} | Note: {notes}\n"
    else:
        info += "\n=== MEDICATION SCHEDULE ===\nNo active prescriptions.\n"

    # 4. الألعاب (Games Scores)
    # نحاول البحث عن علاقة باسم game_scores أو games
    found_games = False
    game_info = "\n=== GAME SCORES (MEMORY EXERCISES) ===\n"
    
    if hasattr(patient, 'game_scores') and patient.game_scores:
        found_games = True
        for score in patient.game_scores:
            # نتأكد من أسماء الأعمدة حسب الموديل الخاص بك
            g_name = getattr(score, 'game_name', 'Memory Game')
            g_score = getattr(score, 'score', 0)
            game_info += f"- Game: {g_name} | Score: {g_score}\n"
            
    elif hasattr(patient, 'games') and patient.games:
        found_games = True
        for game in patient.games:
            g_name = getattr(game, 'name', 'Game')
            g_score = getattr(game, 'high_score', 0)
            game_info += f"- Game: {g_name} | Score: {g_score}\n"
    
    if not found_games:
        game_info += "No game records found yet.\n"
        
    info += game_info

    return info


# ==========================================
# ===            Audio Helpers           ===
# ==========================================

def speech_to_text(audio_file_path):
    recognizer = sr.Recognizer()
    try:
        with sr.AudioFile(audio_file_path) as source:
            audio_data = recognizer.record(source)
            return recognizer.recognize_google(audio_data, language="ar-EG")
    except: return None

def text_to_speech(text, output_path):
    try:
        tts = gTTS(text=text, lang='ar')
        tts.save(output_path)
        return True
    except: return False


# ==========================================
# ===            Endpoints               ===
# ==========================================

@chat_bp.route('/ask', methods=['POST'])
@jwt_required()
def ask_text():
    """شات نصي ذكي شامل"""
    payload = getattr(request, 'current_user_payload', None)
    if not payload or payload.get('role') != 'patient':
        return jsonify({"error": "Access denied."}), 403

    patient_id = payload.get('sub')
    data = request.get_json()
    question = data.get('message')
    if not question: return jsonify({"error": "Message required"}), 400

    # 1. جلب البيانات الشاملة
    patient_context = get_patient_context(patient_id) or "No structured data."
    
    # 2. تحديث الذاكرة (دون إيقاف السيرفر لو فشل)
    store_patient_vector(patient_id, patient_context)
    
    # 3. البحث في الذاكرة عن سياق قديم
    vector_context = search_patient_vectors(patient_id, question)

    current_time = datetime.now().strftime("%Y-%m-%d %I:%M %p")

    final_context = f"""
    Current Time: {current_time}
    
    Structured Database Info (Doctor, Meds, Games):
    {patient_context}

    Relevant Memory (Previous Chats):
    {vector_context}
    """

    system_prompt = f"""
    You are a smart, kind, and comprehensive medical assistant for an Alzheimer's patient.
    
    You have full access to the patient's records below. 
    Use ONLY this data. Do not hallucinate.

    {final_context}

    Instructions:
    1. **Doctor/Contact:** If asked about doctor or help, check the "CONTACTS" section.
    2. **Medicines:** If asked about meds or time, check "MEDICATION SCHEDULE". Compare with "Current Time".
    3. **Games:** If asked about performance, check "GAME SCORES".
    4. **Language:** Reply in the SAME language as the user (Arabic/English).
    
    User Question: "{question}"
    """

    try:
        response = model.generate_content(system_prompt, safety_settings=safety_settings)
        try:
            reply_text = response.text
        except ValueError:
            reply_text = "عذراً، لا يمكنني الإجابة لأسباب أمنية."

        return jsonify({"response": reply_text, "source": "Gemini RAG + DB"}), 200

    except Exception as e:
        print(f"Gemini Error: {e}")
        return jsonify({"error": "AI Error"}), 500


@chat_bp.route('/voice', methods=['POST'])
@jwt_required()
def ask_voice():
    """شات صوتي ذكي شامل"""
    payload = getattr(request, 'current_user_payload', None)
    if not payload or payload.get('role') != 'patient': return jsonify({"error": "Access denied."}), 403
    patient_id = payload.get('sub')

    if 'audio' not in request.files: return jsonify({"error": "No audio"}), 400
    
    audio_file = request.files['audio']
    unique_id = uuid.uuid4()
    input_path = f"/tmp/{unique_id}_in"
    wav_path = f"/tmp/{unique_id}.wav"
    output_path = f"/tmp/{unique_id}_out.mp3"
    
    try:
        audio_file.save(input_path)
        sound = AudioSegment.from_file(input_path)
        sound.export(wav_path, format="wav")
        user_text = speech_to_text(wav_path)
        
        if not user_text: return jsonify({"error": "Could not understand audio"}), 400

        # عمليات البيانات
        patient_context = get_patient_context(patient_id) or "No data."
        store_patient_vector(patient_id, patient_context)
        vector_context = search_patient_vectors(patient_id, user_text)

        current_time = datetime.now().strftime("%I:%M %p")

        final_context = f"""
        Time: {current_time}
        DB Data: {patient_context}
        Memory: {vector_context}
        """

        system_prompt = f"""
        You are a voice assistant.
        Context: {final_context}
        User said: "{user_text}"
        Instructions:
        - If asked about Doctor, Games, or Meds, look at DB Data.
        - Reply concisely in Arabic (Egyptian dialect preferred).
        """
        
        response = model.generate_content(system_prompt, safety_settings=safety_settings)
        try:
            ai_text = response.text
        except ValueError:
            ai_text = "عذراً، لا يمكنني الرد حالياً."

        if text_to_speech(ai_text, output_path):
            return send_file(output_path, mimetype="audio/mpeg", as_attachment=True, download_name="reply.mp3")
        else:
            return jsonify({"error": "TTS Failed"}), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500
        
    finally:
        for p in [input_path, wav_path, output_path]:
            if os.path.exists(p): os.remove(p)
