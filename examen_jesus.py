from __future__ import annotations
import asyncio
import copy
import json
import inspect
import os
import random
import re
import tempfile
import threading
import time
import traceback
import uuid
from typing import List
import flet as ft
from google import genai
from pydantic import BaseModel, Field
import requests
from pypdf import PdfReader

# ==================== CONFIGURACIÓN PARA WEB / RENDER ====================
# La API key NUNCA va en el código: se lee de la variable de entorno GEMINI_API_KEY
# (en Render: Dashboard > tu servicio > Environment > Add Environment Variable).
SERVER_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Respaldo local (solo se usa si no hay Turso configurado, p. ej. al probar en tu PC)
ERRORS_FILE = os.path.join(BASE_DIR, "error_test.json")

# Turso: variables de entorno (en Render > Environment)
#   TURSO_DATABASE_URL  ->  libsql://tu-base-tu-org.turso.io
#   TURSO_AUTH_TOKEN    ->  token de la base de datos
TURSO_URL = (os.environ.get("TURSO_DATABASE_URL") or "").strip()
TURSO_TOKEN = (os.environ.get("TURSO_AUTH_TOKEN") or "").strip()
if TURSO_URL.startswith("libsql://"):
    TURSO_URL = "https://" + TURSO_URL[len("libsql://"):]
TURSO_URL = TURSO_URL.rstrip("/")
USE_TURSO = bool(TURSO_URL and TURSO_TOKEN)

# Carpeta temporal donde Flet deja los PDFs subidos desde el navegador
UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "examen_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
_UPLOAD_PREFIX_RE = re.compile(r"^[0-9a-f]{8}__")


def _pdf_display_name(path: str) -> str:
    """Nombre legible del PDF (quita el prefijo único añadido al subirlo)."""
    return _UPLOAD_PREFIX_RE.sub("", os.path.basename(path))


def _purge_old_uploads(max_age_seconds: int = 6 * 3600):
    now = time.time()
    try:
        for name in os.listdir(UPLOAD_DIR):
            p = os.path.join(UPLOAD_DIR, name)
            try:
                if os.path.isfile(p) and now - os.path.getmtime(p) > max_age_seconds:
                    os.remove(p)
            except Exception:
                pass
    except Exception:
        pass


_HISTORY_LOCK = threading.Lock()
LETTERS = ["A", "B", "C", "D"]
AVAILABLE_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]

# Paleta de colores Dark Bento UI
BG_OUTER = "#0B0C10"
BG_CENTER = "#16181E"
BG_CARD = "#1D2028"
BG_INPUT = "#101217"
TEXT_WHITE = "#FFFFFF"
TEXT_MUTED = "#8A8F9E"
ACCENT_TEAL = "#10B981"
ACCENT_AMBER = "#F59E0B"
ACCENT_CORAL = "#EF4444"
ACCENT_BLUE = "#3B82F6"
ACCENT_PURPLE = "#8B5CF6"
BORDER_SUBTLE = "#282C37"

CARD_THEMES = [
    {"glow": "#2810B981", "border": "#5010B981", "accent": ACCENT_TEAL},
    {"glow": "#28F59E0B", "border": "#50F59E0B", "accent": ACCENT_AMBER},
    {"glow": "#28EF4444", "border": "#50EF4444", "accent": ACCENT_CORAL},
    {"glow": "#283B82F6", "border": "#503B82F6", "accent": ACCENT_BLUE},
    {"glow": "#288B5CF6", "border": "#508B5CF6", "accent": ACCENT_PURPLE},
]


# ==================== FRASES INGENIOSAS PARA FALLOS ====================
FRASES_FALLO = [
    # --- Tus frases originales ---
    "Oye, pues ni tan bien.",
    "Ve preparando el CV para el Mercadona.",
    "Me pongo el sombrero.",
    "Menos luces que un barco pirata, muchacho.",
    "Menudo imbécil tú.",
    "No te van a contratar ni en la ONCE.",
    "Fallas más que una escopeta de feria.",
    "Como tengas esa puntería con todo...",

    # --- Las de la primera tanda ---
    "Ni haciendo pito pito gorgorito lo hacías peor.",
    "Tienes menos futuro que un submarino descapotable.",
    "El conocimiento te persigue, pero tú eres más rápido.",
    "Si el examen fuera de dar pena, sacabas matrícula de honor.",
    "Menos mal que respirar es un acto reflejo, que si no...",
    "Tus dos neuronas no se hablan entre ellas.",
    "Hasta un mono con los ojos vendados acertaba más por pura estadística.",
    "Vete mirando puentes cómodos para vivir debajo.",
    "Acabas de desbloquear el logro de decepcionar a toda tu familia.",
    "La inteligencia artificial avanza y la tuya va marcha atrás.",
    "¿Has estudiado con el libro cerrado o qué ha pasado aquí?",
    "Te dan a elegir entre A y B y respondes que el jueves.",
    "Ni el Rincón del Vago te acoge después de esto.",
    "Estás a dos fallos más de que te convaliden el título de jardín de infancia.",
    "Mucho texto para tan poca comprensión lectora.",

    # --- Nuevas con el mismo tono ácido / español ---
    "Tienes menos luces que el salpicadero de un Seat Panda.",
    "Vete sacando el carnet de carretillero, que esto no es lo tuyo.",
    "Si la ignorancia diera puntos, ya serías catedrático.",
    "Te ponen un examen de colorear y te sales de la línea.",
    "Con ese nivel, no pasas ni el psicotécnico para cruzar la calle.",
    "Tu cerebro está ahora mismo en modo avión.",
    "Le das una patada al diccionario y otra al temario, todo en uno.",
    "Tienes la misma capacidad de deducción que una ameba dormida.",
    "¿Te pagan por fallar o lo haces por amor al arte?",
    "Eres el motivo por el que los champús traen instrucciones.",
    "Ni rezándole a Santa Rita sacas tú esta plaza.",
    "Apuntas maneras... maneras de repetir el año que viene.",
    "Menudo festival del humor tienes montado en la cabeza.",
    "Tus padres aún guardan el ticket de la cigüeña por si acaso.",
    "Tienes el intelecto al ralentí, campeón.",
    "Si juntamos tus aciertos, no da ni para pipas.",
    "¿Estás respondiendo con los codos o es tu nivel normal?",
    "En la repartición de cerebros tú llegaste tarde y pillaste las sobras.",
    "Más perdido que un pulpo en un garaje, hulio.",
    "Dedícate a las chapas, que ahí igual tienes más futuro.",
    "No das una ni aunque todas las opciones sean la correcta.",
    "Estás tú como para desactivar una bomba eligiendo el cable correcto.",
    "Tu neurona acaba de pedir la baja por depresión.",
    "Con esa vista que tienes, seguro que saludas a los maniquíes.",
    "El Burger King está contratando, ahí lo dejo.",
    "Eres la prueba viviente de que se puede vivir sin usar la materia gris.",
    "Si fueras más corto, te tendrían que regar para que crezcas.",
    "Has leído la pregunta en diagonal y con un ojo cerrado, ¿no?",
    "Para hacer esto, mejor te habías quedado durmiendo la siesta.",
    "Tienes menos sangre en las venas que un gazpacho de bote.",
    "Te falta un hervor, dos veranos y medio temario.",
    "Ni comprando las respuestas en Wallapop apruebas tú esto.",
    "Acabas de inventar un nuevo nivel de incompetencia, felicidades.",
    "A este paso te jubilas antes de aprobar el test.",
    "Tienes menos reflejos mentales que un perezoso anestesiado.",
"Si la estupidez cotizara en bolsa, con esta respuesta nos hacías millonarios.",
    "¿Te has dado un golpe en la cabeza de pequeño o vienes así de fábrica?",
    "Tu certificado de nacimiento es una carta de disculpas de la fábrica de condones.",
    "He visto piedras en el campo tomar mejores decisiones que tú ahora mismo.",
    "Si te hacen una radiografía en la cabeza, sale el eco.",
    "Eres el argumento viviente de por qué Darwin estaba equivocado.",
    "¿Tus padres son hermanos o es que te amamantaron con lejía?",
    "No eres más tonto porque no entrenas, aunque viendo este test tengo mis dudas.",
    "Deberías donar tu cerebro a la ciencia; está nuevo, sin estrenar.",
    "Si te secuestran y piden rescate por tu intelecto, te devuelven pagando ellos.",
    "Hasta un hámster con embolia tiene más criterio seleccionando opciones.",
    "Cada vez que respondes, muere un catedrático de un infarto.",
    "Lo tuyo no es falta de estudio, es un déficit severo de riego sanguíneo.",
    "Tienes la misma agilidad mental que un ladrillo de hormigón.",
    "¿Has probado a encender la pantalla antes de ponerte a responder, pedazo de inútil?",
    "Eres tan corto que si te asomas a un balcón no ves la calle.",
    "Por gente como tú las cajas de pizza llevan escrito 'abrir antes de comer'.",
    "La única neurona que te queda está buscando la salida de emergencia.",
    "Si fueras un perro, ya te habrían puesto el collar isabelino para que dejes de hacerte daño.",
    "Menudo desperdicio de oxígeno y de matrícula estás hecho.",
    "Tu coeficiente intelectual compite de tú a tú con la temperatura ambiente en enero.",
    "Viendo cómo respondes, entiendo perfectamente por qué tus padres cambian de tema cuando preguntan por ti.",
    "Si ponen tu cerebro en el cuerpo de un pájaro, vuela hacia atrás.",
    "No sirves ni para estar escondido, macho.",
    "¿Seguro que sabes leer o solo estás dándole a los botones que te parecen bonitos?",
    "Tienes menos luces que el sótano de Josef Fritzl.",
    "Para ser tan tonto hay que echarle horas y dedicación, mis respetos.",
    "Si el examen fuera de chupar cristales, ibas primero de tu promoción.",
    "Vete a pedir una paguita, que con esta captura de pantalla te la conceden en el acto.",
    "Tu nivel de ignorancia es tan puro que debería estar protegido por la UNESCO.",
    "Has nacido cansado de pensar y hoy no iba a ser la excepción.",
    "Le haces la competencia al vacío absoluto con lo que tienes entre las orejas.",
    "Ni con un embudo y a martillazos te entra el temario en esa cabeza hueca.",
    "Eres la oveja negra de la evolución humana.",
    "Si te ponen un plato de sopa y un tenedor, te mueres de hambre antes de pillar la indirecta.",
    "¿Te cuesta mucho ser así de limitado o te sale natural?",
    "Acabas de bajar la media nacional de inteligencia tú solito con ese clic.",
    "Tienes la comprensión lectora de una fregona escurrida.",
    "Mejor deja los estudios y dedícate a sujetar puertas, que ahí hay menos margen de error.",
    "Si fueras un poco más lerdo, necesitarías que te recordaran cuándo parpadear.",
    "Tu árbol genealógico debe ser un círculo perfecto para que hayas respondido semejante barbaridad.",
    "¿Estás intentando suspender a propósito o es que genuinamente no das para más?",
    "Da gracias de que la estupidez no duele, si no estarías gritando en el suelo ahora mismo.",
    "Ni haciendo trampas con el libro abierto te da el cerebro para marcar la buena.",
    "Eres como un ordenador con Windows 95 intentando correr el GTA VI: no te da la RAM, asúmelo.",
    "Te sueltan en un laberinto sin paredes y te pierdes igual.",
    "Qué pena de dinero invertido en tu educación, habría rendido más quemándolo en una estufa.",
    "Tienes el encefalograma más plano que una tabla de planchar.",
    "Por favor, no te reproduzcas nunca.",
    "Deja el ratón en el suelo y aléjate despacio con las manos en alto, terrorista académico.",
]

_FRASES_USADAS = set()
_FRASES_LOCK = threading.Lock()


def _get_frase_fallo() -> str:
    """Devuelve una frase de fallo aleatoria sin repetir hasta agotar el pool."""
    global _FRASES_USADAS
    with _FRASES_LOCK:
        disponibles = [f for f in FRASES_FALLO if f not in _FRASES_USADAS]
        if not disponibles:
            _FRASES_USADAS = set()
            disponibles = list(FRASES_FALLO)
        frase = random.choice(disponibles)
        _FRASES_USADAS.add(frase)
        return frase


# ==================== PERSISTENCIA Y MODELOS PYDANTIC ====================

def _turso_execute(sql: str, args=None):
    """Ejecuta una sentencia SQL en Turso vía HTTP (protocolo Hrana v2)."""
    stmt = {"sql": sql}
    if args:
        stmt["args"] = [{"type": "text", "value": str(a)} for a in args]
    resp = requests.post(
        f"{TURSO_URL}/v2/pipeline",
        headers={"Authorization": f"Bearer {TURSO_TOKEN}", "Content-Type": "application/json"},
        json={"requests": [{"type": "execute", "stmt": stmt}, {"type": "close"}]},
        timeout=15,
    )
    resp.raise_for_status()
    first = resp.json()["results"][0]
    if first.get("type") == "error":
        raise RuntimeError(first["error"].get("message", "Error de Turso"))
    return first["response"]["result"]


def _turso_init():
    _turso_execute(
        "CREATE TABLE IF NOT EXISTS app_state ("
        "key TEXT PRIMARY KEY, value TEXT NOT NULL, "
        "updated_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )


def _read_local_errors():
    if os.path.exists(ERRORS_FILE):
        try:
            with open(ERRORS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


_ERRORS_CACHE = None
_REMOTE_READY = False  # True cuando hemos leído Turso con éxito al menos una vez
_ERRORS_LOCK = threading.Lock()


def _ensure_errors_loaded():
    """Carga los errores (Turso si está configurado, si no el JSON local). Llamar con el lock."""
    global _ERRORS_CACHE, _REMOTE_READY
    if not USE_TURSO:
        if _ERRORS_CACHE is None:
            _ERRORS_CACHE = _read_local_errors()
        return
    if _REMOTE_READY:
        return
    try:
        _turso_init()
        res = _turso_execute("SELECT value FROM app_state WHERE key = ?", ["errors"])
        rows = res.get("rows", [])
        if rows:
            _ERRORS_CACHE = json.loads(rows[0][0]["value"])
        else:
            # Primera vez: migra lo que hubiera en el JSON local (si existe)
            _ERRORS_CACHE = _read_local_errors()
            if _ERRORS_CACHE:
                _write_errors_remote(_ERRORS_CACHE)
        _REMOTE_READY = True
    except Exception:
        traceback.print_exc()
        if _ERRORS_CACHE is None:
            _ERRORS_CACHE = []


def _write_errors_remote(errors):
    _turso_execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
        ["errors", json.dumps(errors, ensure_ascii=False)],
    )


def load_errors():
    with _ERRORS_LOCK:
        _ensure_errors_loaded()
        return copy.deepcopy(_ERRORS_CACHE or [])


def save_errors(errors):
    global _ERRORS_CACHE
    with _ERRORS_LOCK:
        _ensure_errors_loaded()
        _ERRORS_CACHE = copy.deepcopy(errors)
        if not USE_TURSO:
            with open(ERRORS_FILE, "w", encoding="utf-8") as f:
                json.dump(_ERRORS_CACHE, f, ensure_ascii=False, indent=4)
            return
        if not _REMOTE_READY:
            # No pudimos leer Turso: no escribimos para no pisar datos que no hemos visto
            print("⚠️ Turso no disponible: los errores no se han guardado en la base de datos.")
            return
        try:
            _write_errors_remote(_ERRORS_CACHE)
        except Exception:
            traceback.print_exc()


class QuestionMCQ(BaseModel):
    topic: str = Field(description="Concepto o sub-tema CONCRETO que evalúa esta pregunta, en 3-8 palabras.")
    question: str = Field(description="Enunciado de la pregunta")
    options: List[str] = Field(description="Lista de EXACTAMENTE 4 opciones sin letra ni prefijo.")
    correct_letter: str = Field(description="Letra de la opción correcta: 'A', 'B', 'C' o 'D'.")
    explanation: str = Field(description="Explicación detallada pero concisa (máximo 3 líneas) de por qué es correcta y por qué las otras fallan")


class QuestionShort(BaseModel):
    topic: str = Field(description="Concepto o sub-tema CONCRETO que evalúa esta pregunta, en 3-8 palabras.")
    question: str = Field(description="Enunciado de la pregunta corta de desarrollo")
    model_answer: str = Field(description="Respuesta modelo concisa de máximo 4 líneas")


class QuestionDefinition(BaseModel):
    topic: str = Field(description="Área o bloque temático del concepto (3-6 palabras).")
    term: str = Field(description="Nombre COMPLETO, ÍNTEGRO y EXACTO del concepto, técnica, método o fenómeno del temario (incluyendo todos sus apellidos o adjetivos técnicos, ej. 'Enfoque isoeléctrico' y NUNCA solo 'Enfoque'). Primera letra en mayúscula. Debe ser relevante en el temario y no autoevidente.")
    definition: str = Field(description="Definición académica, técnica y rigurosa extraída del temario (máximo 4 líneas), sin repetir las palabras del término definido al empezar.")
    importance_reason: str = Field(description="Por qué ESTE concepto completo es relevante y estructural en el temario y requiere estudio (1-2 líneas).")


class IndustrialCase(BaseModel):
    topic: str = Field(description="Bloque o conceptos del temario que se combinan o aplican (3-8 palabras).")
    title: str = Field(description="Título descriptivo y realista del caso práctico.")
    technique: str = Field(description="Conceptos, principios, arquitecturas o métodos del temario que deben conectarse y aplicarse para resolver, diseñar o diagnosticar el problema.")
    approach_type: str = Field(description="Debe ser EXACTAMENTE 'CUALITATIVO' si el temario no requiere cálculo, o 'CUANTITATIVO' SOLO si el temario incluye explícitamente fórmulas matemáticas y problemas de cálculo.")
    scenario: str = Field(description="Planteamiento realista y con sentido de un problema, fallo en un sistema, necesidad de diseño o situación compleja donde se mezclen o apliquen conceptos del temario sin salirse de él (5-8 líneas). Prohibido inventar datos numéricos o pedir cálculos si el temario no los tiene.")
    challenge: str = Field(description="Preguntas concretas donde el alumno deba diagnosticar por qué ocurre el problema, diseñar una solución o solventar la situación justificando con los conceptos del temario.")
    rubric_keywords: List[str] = Field(description="Exactamente 4 conceptos o términos técnicos del temario que deben aparecer o aplicarse en una solución correcta.")
    step_by_step_solution: str = Field(description="Resolución paso a paso explicando el diagnóstico, el diseño propuesto o cómo se solventa el problema combinando los conceptos del temario. Sirve de guía de corrección (8-12 líneas).")


class MediumPackage(BaseModel):
    medium_mcq: List[QuestionMCQ] = Field(description="Exactamente 10 preguntas de nivel medio variadas")


class HardPackage(BaseModel):
    hard_mcq: List[QuestionMCQ] = Field(description="Exactamente 10 preguntas donde la dificultad extrema reside en discernir la respuesta correcta entre opciones muy similares y sutiles")


class ShortPackage(BaseModel):
    short_answers: List[QuestionShort] = Field(description="Exactamente 10 preguntas cortas variadas")


class DefinitionPackage(BaseModel):
    definitions: List[QuestionDefinition] = Field(description="Exactamente 10 términos o conceptos COMPLETOS e ÍNTEGROS relevantes del temario con complejidad mínima y no autodefinidos")


class CasePackage(BaseModel):
    cases: List[IndustrialCase] = Field(description="Exactamente 3 casos prácticos realistas de diagnóstico, diseño o resolución de problemas basados estrictamente en el temario")


class OfficialExamPackage(BaseModel):
    medium_mcq: List[QuestionMCQ] = Field(description="Exactamente 2 preguntas tipo test de nivel medio-alto exclusivas para este examen.")
    hard_mcq: List[QuestionMCQ] = Field(description="Exactamente 2 preguntas tipo test de nivel difícil/experto exclusivas para este examen.")
    short_answers: List[QuestionShort] = Field(description="Exactamente 2 preguntas cortas de desarrollo exigentes exclusivas para este examen.")
    definitions: List[QuestionDefinition] = Field(description="Exactamente 2 términos o conceptos clave COMPLETOS del temario (con complejidad y no autoevidentes) exclusivos para este examen.")
    industrial_case: IndustrialCase = Field(description="Exactamente 1 caso práctico realista y completo exclusivo para este examen.")


class CaseEvaluation(BaseModel):
    score: float = Field(description="Nota numérica de 0 a 10.")
    strengths: str = Field(description="Qué conceptos del temario se han aplicado correctamente en la respuesta (máximo 4 líneas).")
    blind_spots: str = Field(description="Qué lagunas, errores o conceptos clave del temario se han omitido o aplicado mal (máximo 4 líneas).")
    expert_model: str = Field(description="Solución experta ideal breve para referencia.")


class SingleWrittenEvaluation(BaseModel):
    score: int = Field(description="Nota numérica entera del 1 al 10 evaluando la precisión de la respuesta respecto al temario.")
    feedback: str = Field(description="Explicación clara y directa (máximo 3 líneas) de qué ha acertado el alumno y qué le ha faltado o ha confundido respecto a la respuesta oficial.")


class WrittenItemGrading(BaseModel):
    index: int = Field(description="Índice numérico exacto de la pregunta evaluada.")
    score: float = Field(description="Nota numérica rigurosa de 0 a 10.")
    feedback: str = Field(description="Corrección explicando aciertos, fallos y qué faltó respecto al temario (máximo 3 líneas).")


class BatchExamEvaluation(BaseModel):
    evaluations: List[WrittenItemGrading] = Field(description="Lista con la evaluación de cada pregunta escrita del examen.")


# ==================== UTILIDADES DE PROCESAMIENTO ====================

def _shuffle_options(mcq: dict) -> dict:
    opts = mcq.get("options", [])
    correct_letter = str(mcq.get("correct_letter", "")).strip().upper()
    if len(opts) != 4 or correct_letter not in LETTERS:
        return mcq
    correct_idx = LETTERS.index(correct_letter)
    correct_text = opts[correct_idx]
    indexed = list(enumerate(opts))
    random.shuffle(indexed)
    new_opts = [text for _, text in indexed]
    new_correct_idx = next(i for i, (orig_idx, _) in enumerate(indexed) if orig_idx == correct_idx)
    assert new_opts[new_correct_idx] == correct_text
    mcq["options"] = new_opts
    mcq["correct_letter"] = LETTERS[new_correct_idx]
    return mcq


def _normalize_mcq(mcq: dict) -> dict:
    opts = mcq.get("options", [])
    cleaned = []
    for o in opts:
        s = str(o).strip()
        if len(s) >= 2 and s[0].upper() in LETTERS and s[1] in ").:- ":
            s = s[2:].strip()
        elif len(s) >= 3 and s[0] == "(" and s[1].upper() in LETTERS and s[2] == ")":
            s = s[3:].strip()
        cleaned.append(s)
    mcq["options"] = cleaned
    return _shuffle_options(mcq)


def _format_capitalized_term(term: str) -> str:
    t = term.strip()
    if not t:
        return t
    return t[0].upper() + t[1:]


# ==================== APLICACIÓN PRINCIPAL EN FLET ====================

class NexusExamFletApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.page.title = "ismael_prd — Exam Suite"
        self.page.bgcolor = BG_OUTER
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.padding = 0
        if not self.page.web:
            self.page.window.width = 1380
            self.page.window.height = 900
            self.page.window.min_width = 1100
            self.page.window.min_height = 750

        # Variables de estado
        self.api_key_value = ""  # clave opcional escrita por el usuario (la del servidor va en SERVER_API_KEY)
        self.selected_model = AVAILABLE_MODELS[0]
        self.pdf_paths = []
        self.pdf_text = ""
        self.exams_data = {
            "medium_mcq": [],
            "hard_mcq": [],
            "short_answers": [],
            "definitions": [],
            "industrial_cases": [],
        }
        self.global_history = []
        self.user_mcq_answers = {}
        self.user_short_answers = {}
        self.user_def_answers = {}
        self.user_case_answers = {}
        self.short_evaluations = {}
        self.def_evaluations = {}
        self.case_evaluations = {}
        self.error_save_threshold = 5

        self.test_indices = {
            "Test Medio": 0,
            "Test Difícil": 0,
            "Preguntas Cortas": 0,
            "Definiciones": 0,
            "Casos Prácticos": 0,
        }
        self.test_finished = {
            "Test Medio": False,
            "Test Difícil": False,
            "Preguntas Cortas": False,
            "Definiciones": False,
            "Casos Prácticos": False,
        }

        # Estado del selector de qué generar
        self.gen_all = True
        self.gen_medium = True
        self.gen_hard = True
        self.gen_short = True
        self.gen_defs = True
        self.gen_cases = True

        # Estado para el modo Test de Errores interactivo
        self.error_quiz_mode = False
        self.error_quiz_questions = []
        self.error_quiz_idx = 0
        self.error_quiz_user_answers = {}
        self.error_quiz_finished = False
        self.auto_remove_resolved_errors = True

        # Estado para el modo SIMULACRO OFICIAL EXAMINAR (15 min)
        self.exam_mode_active = False
        self.exam_is_generating = False
        self.exam_questions = []
        self.exam_user_answers = {}
        self.exam_time_left = 15 * 60
        self.exam_timer_task = None
        self.exam_timer_started = False
        self.exam_finished = False
        self.exam_results_data = None

        self.current_nav_index = 0
        self.status_message = "Sistema en espera. Configura tu API Key y selecciona un PDF."
        self.progress_val = 0.0
        self.progress_target_ceiling = 0.0
        self.is_generating = False

        # Widgets de progreso
        self.progress_status_text = ft.Text(self.status_message, size=12, color=ACCENT_TEAL, weight=ft.FontWeight.W_600)
        self.progress_pct_text = ft.Text("0%", size=12, color=TEXT_WHITE, weight=ft.FontWeight.BOLD)
        self.progress_bar_widget = ft.ProgressBar(value=0.0, color=ACCENT_TEAL, bgcolor="#232733", bar_height=6, border_radius=4)
        self.progress_container = ft.Container(
            visible=False,
            padding=ft.Padding.symmetric(horizontal=16, vertical=10),
            border_radius=14,
            bgcolor=BG_INPUT,
            border=ft.Border.all(1, "#3510B981"),
            content=ft.Column(
                spacing=6,
                controls=[
                    ft.Row(alignment=ft.MainAxisAlignment.SPACE_BETWEEN, controls=[self.progress_status_text, self.progress_pct_text]),
                    self.progress_bar_widget,
                ],
            ),
        )

        self.badge_med_text = ft.Text("0 preguntas", size=11, color=TEXT_WHITE, weight=ft.FontWeight.W_500)
        self.badge_hard_text = ft.Text("0 preguntas", size=11, color=TEXT_WHITE, weight=ft.FontWeight.W_500)
        self.badge_short_text = ft.Text("0 cuestiones", size=11, color=TEXT_WHITE, weight=ft.FontWeight.W_500)
        self.badge_def_text = ft.Text("0 conceptos", size=11, color=TEXT_WHITE, weight=ft.FontWeight.W_500)
        self.badge_case_text = ft.Text("0 casos", size=11, color=TEXT_WHITE, weight=ft.FontWeight.W_500)
        self.unique_topics_text = ft.Text("Temas únicos: 0", size=12, color=TEXT_MUTED)
        self.pdf_label_text = ft.Text("Ningún PDF seleccionado", size=13, color=TEXT_MUTED, expand=True)
        self.gen_summary_text = ft.Text(
            self._summarize_gen_selection(),
            size=11,
            color=TEXT_MUTED,
            width=150,
            text_align=ft.TextAlign.CENTER,
        )

        # Selector de archivos nativo de Flet (válido en navegador)
        self.file_picker = ft.FilePicker()
        try:
            self.page.services.append(self.file_picker)
        except Exception:
            self.page.overlay.append(self.file_picker)

        self.build_main_layout()

    async def _async_progress_ticker(self):
        while self.is_generating:
            await asyncio.sleep(0.12)
            if self.progress_val < self.progress_target_ceiling:
                diff = self.progress_target_ceiling - self.progress_val
                step = max(0.006, diff * 0.09)
                self.progress_val = min(self.progress_target_ceiling, self.progress_val + step)
            self._sync_live_widgets()
            self.page.update()
        self._sync_live_widgets()
        self.render_center_content()
        self.page.update()

    def _sync_live_widgets(self):
        pct = int(self.progress_val * 100)
        self.progress_container.visible = self.is_generating or self.progress_val > 0
        self.progress_bar_widget.value = self.progress_val
        self.progress_pct_text.value = f"{pct}%"
        self.progress_status_text.value = self.status_message

        self.badge_med_text.value = f"{len(self.exams_data['medium_mcq'])} preguntas"
        self.badge_hard_text.value = f"{len(self.exams_data['hard_mcq'])} preguntas"
        self.badge_short_text.value = f"{len(self.exams_data['short_answers'])} cuestiones"
        self.badge_def_text.value = f"{len(self.exams_data['definitions'])} conceptos"
        self.badge_case_text.value = f"{len(self.exams_data['industrial_cases'])} casos"
        self.unique_topics_text.value = f"Temas únicos: {len(self.global_history)}"
        if self.pdf_paths:
            if len(self.pdf_paths) == 1:
                self.pdf_label_text.value = f"Temario: {_pdf_display_name(self.pdf_paths[0])}"
            else:
                self.pdf_label_text.value = f"Temario: {len(self.pdf_paths)} PDFs combinados"
            self.pdf_label_text.color = ACCENT_TEAL

    def _get_api_key(self) -> str:
        """Clave escrita por el usuario; si está vacía, la variable de entorno del servidor."""
        return (self.api_key_value or "").strip() or SERVER_API_KEY

    def _make_api_textfield(self):
        return ft.TextField(
            value=self.api_key_value,
            password=True,
            can_reveal_password=True,
            hint_text=("Usando la API Key del servidor (opcional: pega la tuya)" if SERVER_API_KEY else "Pega aquí tu API Key de Google Gemini..."),
            bgcolor=BG_INPUT,
            border_color=BORDER_SUBTLE,
            focused_border_color=ACCENT_TEAL,
            border_radius=12,
            text_size=13,
            color=TEXT_WHITE,
            expand=True,
            content_padding=ft.Padding.symmetric(horizontal=14, vertical=12),
            on_change=self._on_api_key_change,
        )

    def _on_api_key_change(self, e):
        self.api_key_value = e.control.value

    def _on_manual_model_change(self, e):
        val = (e.control.value or "").strip()
        if val:
            self.selected_model = val
            self.top_model_badge.value = f"IA: {self.selected_model}"
            self.page.update()

    def _make_model_selector(self):
        self.model_textfield = ft.TextField(
            value=self.selected_model,
            hint_text="Ej: gemini-3.5-flash-lite",
            bgcolor=BG_INPUT,
            border_color=ACCENT_BLUE,
            focused_border_color=ACCENT_TEAL,
            border_radius=12,
            text_size=13,
            color=TEXT_WHITE,
            width=210,
            content_padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            on_change=self._on_manual_model_change,
        )

        preset_menu = ft.PopupMenuButton(
            tooltip="Elegir modelo predefinido de la lista",
            content=ft.Container(
                bgcolor=BG_INPUT,
                border=ft.Border.all(1, ACCENT_BLUE),
                border_radius=12,
                padding=ft.Padding.symmetric(horizontal=10, vertical=9),
                content=ft.Row(
                    spacing=4,
                    controls=[
                        ft.Icon(ft.Icons.MEMORY_ROUNDED, color=ACCENT_BLUE, size=17),
                        ft.Icon(ft.Icons.ARROW_DROP_DOWN_ROUNDED, color=TEXT_WHITE, size=20),
                    ],
                ),
            ),
            items=[
                ft.PopupMenuItem(
                    content=ft.Row(
                        spacing=10,
                        controls=[
                            ft.Icon(
                                ft.Icons.RADIO_BUTTON_CHECKED if m == self.selected_model else ft.Icons.RADIO_BUTTON_UNCHECKED,
                                color=ACCENT_TEAL if m == self.selected_model else TEXT_MUTED,
                                size=16,
                            ),
                            ft.Text(m, color=TEXT_WHITE, weight=ft.FontWeight.BOLD if m == self.selected_model else ft.FontWeight.NORMAL),
                        ],
                    ),
                    on_click=lambda e, model_name=m: self._select_model(model_name),
                )
                for m in AVAILABLE_MODELS
            ],
        )

        return ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[self.model_textfield, preset_menu])

    def _select_model(self, model_name: str):
        self.selected_model = model_name
        if model_name not in AVAILABLE_MODELS:
            AVAILABLE_MODELS.insert(0, model_name)
        self.top_model_badge.value = f"IA: {self.selected_model}"
        self.notify(f"Modelo cambiado a: {self.selected_model}", ACCENT_BLUE)
        self.render_center_content()
        self.page.update()

    def _build_threshold_selector(self, accent_color: str):
        def set_thresh(val: int):
            self.error_save_threshold = val
            self.notify(f"Se guardará en Test de Errores si la nota es menor a {val}/10", accent_color)
            self.render_center_content()
            self.page.update()

        return ft.Row(
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Text("Guardar error si nota <", size=11.5, color=TEXT_MUTED),
                ft.PopupMenuButton(
                    tooltip="Elige a partir de qué nota inferior se guarda la pregunta en el Test de Errores",
                    content=ft.Container(
                        bgcolor=BG_INPUT,
                        border=ft.Border.all(1, accent_color),
                        border_radius=8,
                        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                        content=ft.Row(
                            spacing=2,
                            controls=[
                                ft.Text(f"{self.error_save_threshold}", size=12, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                                ft.Icon(ft.Icons.ARROW_DROP_DOWN_ROUNDED, color=TEXT_WHITE, size=16),
                            ],
                        ),
                    ),
                    items=[
                        ft.PopupMenuItem(
                            content=ft.Text(f"Nota < {i} / 10", color=TEXT_WHITE, weight=ft.FontWeight.BOLD if i == self.error_save_threshold else ft.FontWeight.NORMAL),
                            on_click=lambda e, v=i: set_thresh(v),
                        )
                        for i in range(1, 11)
                    ],
                ),
            ],
        )

    def open_settings_dialog(self, e=None):
        dlg_api = ft.TextField(
            label="API Key de Google Gemini",
            hint_text=("Vacío = usar la API Key del servidor" if SERVER_API_KEY else None),
            value=self.api_key_value,
            password=True,
            can_reveal_password=True,
            bgcolor=BG_INPUT,
            border_color=ACCENT_TEAL,
            border_radius=12,
            color=TEXT_WHITE,
            on_change=self._on_api_key_change,
        )

        dlg_custom_model = ft.TextField(
            label="Nombre del Modelo IA (puedes escribir uno nuevo a mano)",
            value=self.selected_model,
            hint_text="Ej: gemini-3.5-flash-lite",
            bgcolor=BG_INPUT,
            border_color=ACCENT_BLUE,
            focused_border_color=ACCENT_TEAL,
            border_radius=12,
            color=TEXT_WHITE,
        )

        def on_radio_model_change(ev):
            dlg_custom_model.value = ev.control.value
            self.page.update()

        model_radios = ft.RadioGroup(
            value=self.selected_model if self.selected_model in AVAILABLE_MODELS else None,
            on_change=on_radio_model_change,
            content=ft.Column(
                spacing=6,
                controls=[
                    ft.Radio(value=m, label=m, fill_color=ACCENT_TEAL, label_style=ft.TextStyle(color=TEXT_WHITE))
                    for m in AVAILABLE_MODELS
                ],
            ),
        )

        def save_and_close(ev):
            self.api_key_value = (dlg_api.value or "").strip()
            custom_m = (dlg_custom_model.value or "").strip()
            self.selected_model = custom_m if custom_m else AVAILABLE_MODELS[0]
            if self.selected_model not in AVAILABLE_MODELS:
                AVAILABLE_MODELS.insert(0, self.selected_model)
            self.top_model_badge.value = f"IA: {self.selected_model}"
            try:
                self.page.pop_dialog()
            except Exception:
                dlg.open = False
            self.render_center_content()
            self.notify(f"Configuración guardada (Modelo: {self.selected_model}).", ACCENT_TEAL)
            self.page.update()

        dlg = ft.AlertDialog(
            modal=True,
            bgcolor=BG_CARD,
            title=ft.Row(
                spacing=10,
                controls=[
                    ft.Icon(ft.Icons.SETTINGS_SUGGEST_ROUNDED, color=ACCENT_TEAL),
                    ft.Text("Configuración de IA y API Key", size=18, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                ],
            ),
            content=ft.Container(
                width=480,
                content=ft.Column(
                    tight=True,
                    spacing=14,
                    controls=[
                        ft.Text("Introduce tu clave de Google AI Studio y elige o escribe el modelo:", size=13, color=TEXT_MUTED),
                        dlg_api,
                        dlg_custom_model,
                        ft.Text("Modelos rápidos predefinidos:", size=13, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                        model_radios,
                    ],
                ),
            ),
            actions=[
                ft.Button(
                    content="Guardar y Cerrar",
                    bgcolor=ACCENT_TEAL,
                    color=BG_OUTER,
                    on_click=save_and_close,
                ),
            ],
        )

        try:
            self.page.show_dialog(dlg)
        except Exception:
            self.page.overlay.append(dlg)
            dlg.open = True
            self.page.update()

    # -------------------- REGLAS DE PROMPTS Y ANTIRREPETICIÓN --------------------

    def _register_topics(self, questions, kind):
        with _HISTORY_LOCK:
            for q in questions:
                topic_val = q.get("topic", "") or q.get("term", "") or q.get("title", "")
                q_val = q.get("question", "") or q.get("term", "") or q.get("scenario", "")
                self.global_history.append({
                    "kind": kind,
                    "topic": topic_val.strip().lower(),
                    "question": q_val.strip(),
                })

    def _build_exclusion_prompt(self, extra_topics=None):
        with _HISTORY_LOCK:
            entries = list(self.global_history)
        if not entries:
            return "  (vacío — es la primera generación, no hay restricciones previas)"
        lines, seen = [], set()
        for e in entries:
            t = e.get("topic", "")
            if not t or t in seen:
                continue
            seen.add(t)
            lines.append(f"  - [{e['kind']}] {t}")
        if extra_topics:
            for t in extra_topics:
                t = t.strip().lower()
                if t and t not in seen:
                    seen.add(t)
                    lines.append(f"  - [extra] {t}")
        return "\n".join(lines) if lines else "  (vacío)"

    def _get_all_topics(self):
        with _HISTORY_LOCK:
            return {e["topic"] for e in self.global_history if e.get("topic")}

    def _common_rules(self, excluded_block):
        return f"""
REGLAS ESTRICTAS Y ANTIRREPETICIÓN (CRÍTICO):
1. NO repitas NINGÚN concepto, sub-tema, definición o idea ya evaluada previamente en este examen o en otras secciones.
2. Si un concepto ya se preguntó, está TOTALMENTE PROHIBIDO volver a preguntar sobre él. Cada pregunta debe ser absolutamente única.
3. Lista exhaustiva de conceptos ya usados que DEBES EVITAR ABSOLUTAMENTE:
{excluded_block}
4. Rellena obligatoriamente "topic" con el concepto CONCRETO y específico (3-8 palabras) evaluado.
5. EXACTAMENTE 4 opciones por pregunta, sin letras ni prefijos en el texto de las opciones.
6. "correct_letter" debe ser 'A', 'B', 'C' o 'D'.
7. Distribuye las respuestas correctas de forma equilibrada entre A, B, C y D.
"""

    def _hard_difficulty_instructions(self):
        return """
FILOSOFÍA DE DIFICULTAD EXTREMA (CRÍTICO — LEE CON ATENCIÓN):
- El objetivo es que SOLO quien domine el temario al 100% acierte con seguridad; el resto debe dudar entre varias opciones.
- PROHIBIDO que la correcta sea identificable por descarte rápido, por ser la más larga, la más técnica o la más "bonita". Las 4 opciones deben tener extensión, tono y nivel técnico equivalentes.
- Las 4 opciones deben ser MUY PARECIDAS entre sí: mismos términos del temario, misma estructura gramatical, mismas unidades. La diferencia debe estar en un matiz técnico sutil (una condición, una excepción, un orden, una inversión de causa-efecto, un "siempre/nunca" mal puesto).
- Usa distractores de alto nivel:
  • Conceptos hermanos del temario intercambiados (A en el lugar de B).
  • Condición necesaria presentada como suficiente (o viceversa).
  • Excepción presentada como regla general.
  • Definición correcta pero aplicada al fenómeno equivocado.
  • Inversión de la relación causa-efecto.
  • Un detalle numérico/cualitativo cambiado por otro plausible del mismo temario.
- Varía el formato: al menos la mitad de las preguntas deben ser del tipo "¿Cuál de las siguientes afirmaciones es FALSA / INCORRECTA?" o "¿Cuál NO se corresponde con...?", donde 3 opciones son verdaderas y solo 1 es falsa, y las 3 verdaderas deben ser igual de creíbles.
- PROHIBIDO usar pistas léxicas: nada de "siempre", "nunca", "todas", "ninguna" solo en la incorrecta; nada de opciones absurdas o fuera del temario; nada de opciones que se contradigan entre sí de forma obvia.
- NO inventes contenido fuera del temario: todos los términos, datos y relaciones deben existir en el PDF.
- La explicación debe justificar por qué la correcta lo es Y por qué cada una de las otras 3 es sutilmente incorrecta, señalando el matiz exacto.
"""

    def _definitions_prompt_rules(self):
        return """
REGLAS ESTRICTAS PARA LAS DEFINICIONES (CRÍTICO):
1. CONCEPTOS COMPUESTOS ÍNTEGROS Y COMPLETOS (PROHIBIDO RECORTAR O PARTIR TÉRMINOS):
   - En "term" debes escribir SIEMPRE el nombre técnico COMPLETO e ÍNTEGRO del concepto tal y como tiene sentido científico/académico en el temario, incluyendo todos sus adjetivos o complementos.
   - PROHIBIDO TERMINANTEMENTE recortar un término compuesto dejando solo la primera palabra genérica.
   - Ejemplo real: Si en el temario aparece "Enfoque isoeléctrico", el término a definir DEBE SER "Enfoque isoeléctrico" completo, y NUNCA jamás "Enfoque" a secas. Si aparece "Cromatografía de afinidad", pon "Cromatografía de afinidad" y NO "Cromatografía" ni "Afinidad".
2. RELEVANCIA Y PESO EN EL TEMARIO:
   - Selecciona únicamente conceptos nucleares, técnicas importantes, principios fundamentales, arquitecturas, mecanismos o fenómenos relevantes que tengan verdadero peso académico en el temario y merezcan ser preguntados en un examen.
   - PROHIBIDO escoger palabras genéricas del lenguaje común, verbos sueltos, sustantivos vagos (como "Proceso", "Sistema", "Método", "Enfoque", "Análisis", "Factor") o datos anecdóticos.
3. PROHIBIDO TÉRMINOS AUTODEFINIDOS O EVIDENTES:
   - La definición del término NO puede ser deducible por sentido común sin haber estudiado el temario.
4. En "term", escribe el concepto completo con la primera letra en mayúscula.
5. En "definition", redacta una definición académica rigurosa y completa basada fielmente en el temario (máximo 4 líneas), SIN repetir las palabras del propio término al empezar a definirlo.
"""

    def _cases_prompt_rules(self):
        return """
REGLAS ESTRICTAS PARA LOS CASOS PRÁCTICOS (CRÍTICO):
1. PROHIBIDO INVENTAR CÁLCULOS SI EL TEMARIO NO LOS IMPLICA:
   - Analiza primero si el temario contiene fórmulas matemáticas y problemas numéricos reales.
   - Si el temario NO implica cálculos matemáticos (o es conceptual, de ingeniería/arquitectura teórica, procesos, normativa, sistemas, gestión, etc.), está TOTALMENTE PROHIBIDO inventar datos numéricos o pedir calcular nada. Marca approach_type='CUALITATIVO'.
   - Solo usa approach_type='CUANTITATIVO' si el temario enseña explícitamente fórmulas de cálculo y el problema se resuelve calculando con ellas.
2. NATURALEZA DEL CASO PRÁCTICO (REALISTA Y CON SENTIDO):
   - Plantea un problema realista, verosímil y con todo el sentido práctico dentro del ámbito del temario.
   - El caso debe requerir que el alumno, a partir del temario, logre SOLVENTAR un problema real, DISEÑAR una solución/sistema/procedimiento, o IDENTIFICAR/DIAGNOSTICAR por qué está ocurriendo un fallo o situación anómala.
   - Se pueden (y es recomendable) MEZCLAR DIFERENTES CONCEPTOS del temario para que el alumno tenga que relacionarlos e integrarlos, pero SIEMPRE SIN SALIRSE DE LOS TEMAS del documento.
3. ESTRUCTURA DEL CASO:
   - "scenario": Describe el contexto realista, qué está pasando, qué síntomas o restricciones existen o qué se necesita diseñar (5-8 líneas).
   - "challenge": Plantea las preguntas concretas (ej. "¿Por qué está ocurriendo X y qué concepto del temario lo explica?", "¿Cómo diseñarías Y para solventar este cuello de botella cumpliendo Z?", "¿Qué fallo conceptual se ha cometido y cómo se corrige según el temario?").
   - "step_by_step_solution": Explica detalladamente el diagnóstico, diseño o solución aplicando y conectando los conceptos del temario paso a paso.
   - "rubric_keywords": 4 conceptos clave del temario que el alumno debe mencionar y aplicar.
"""

    def _postprocess_mcq(self, questions, ignore_seen=False):
        seen = set() if ignore_seen else self._get_all_topics()
        cleaned = []
        for q in questions:
            try:
                q = _normalize_mcq(dict(q))
            except Exception:
                continue
            opts = q.get("options", [])
            letter = str(q.get("correct_letter", "")).strip().upper()
            if len(opts) != 4 or letter not in LETTERS:
                continue
            if len({str(o).strip().lower() for o in opts}) != 4:
                continue
            t = q.get("topic", "").strip().lower()
            if not ignore_seen:
                if not t or t in seen:
                    continue
                seen.add(t)
            q["correct_letter"] = letter
            cleaned.append(q)
        return cleaned

    def notify(self, message: str, color: str = ACCENT_TEAL):
        snack = ft.SnackBar(
            content=ft.Text(message, color=TEXT_WHITE, weight=ft.FontWeight.W_600),
            bgcolor=color,
            behavior=ft.SnackBarBehavior.FLOATING,
            shape=ft.RoundedRectangleBorder(radius=12),
            margin=20,
        )
        try:
            self.page.show_dialog(snack)
        except Exception:
            try:
                self.page.open(snack)
            except Exception:
                pass

    def create_glow_card(self, content: ft.Control, theme_idx: int = 0, padding: int = 20, expand: bool = False):
        theme = CARD_THEMES[theme_idx % len(CARD_THEMES)]
        return ft.Container(
            expand=expand,
            padding=padding,
            border_radius=24,
            bgcolor=BG_CARD,
            border=ft.Border.all(1, theme["border"]),
            gradient=ft.LinearGradient(
                begin=ft.Alignment(1.0, -1.0),
                end=ft.Alignment(-1.0, 1.0),
                colors=[theme["glow"], BG_CARD, BG_CARD],
                stops=[0.0, 0.45, 1.0],
            ),
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=18,
                color="#40000000",
                offset=ft.Offset(0, 8),
            ),
            content=content,
        )

    def build_main_layout(self):
        self.top_model_badge = ft.Text(
            f"IA: {self.selected_model}",
            size=12,
            weight=ft.FontWeight.W_600,
            color=ACCENT_TEAL,
        )

        self.top_bar = ft.Container(
            height=64,
            padding=ft.Padding.symmetric(horizontal=28),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(
                        spacing=18,
                        controls=[
                            ft.IconButton(
                                icon=ft.Icons.NOTES_ROUNDED,
                                icon_color=TEXT_WHITE,
                                icon_size=24,
                                tooltip="Dashboard Principal",
                                on_click=lambda _: self.switch_tab(0),
                            ),
                            ft.Text("ismael_prd", size=21, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                        ],
                    ),
                    ft.Row(
                        spacing=14,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Container(
                                padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                                border_radius=14,
                                bgcolor="#161922",
                                border=ft.Border.all(1, "#3510B981"),
                                on_click=self.open_settings_dialog,
                                tooltip="Haz clic para configurar API Key o modelo",
                                content=ft.Row(
                                    spacing=8,
                                    controls=[
                                        ft.Container(width=8, height=8, border_radius=4, bgcolor=ACCENT_TEAL),
                                        self.top_model_badge,
                                        ft.Icon(ft.Icons.SETTINGS_OUTLINED, size=15, color=TEXT_MUTED),
                                    ],
                                ),
                            ),
                            ft.CircleAvatar(
                                bgcolor=ACCENT_AMBER,
                                radius=17,
                                content=ft.Icon(ft.Icons.SCHOOL_ROUNDED, color=BG_OUTER, size=18),
                            ),
                            ft.Text("Estudio IA", size=14, weight=ft.FontWeight.W_600, color=TEXT_WHITE),
                        ],
                    ),
                ],
            ),
        )

        self.sidebar_column = ft.Column(
            spacing=12,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self.update_sidebar()

        left_sidebar = ft.Container(
            width=78,
            padding=ft.Padding.only(top=16, bottom=25),
            content=self.sidebar_column,
        )

        self.center_view_container = ft.Container(expand=True)

        center_well = ft.Container(
            expand=7,
            bgcolor=BG_CENTER,
            border_radius=ft.BorderRadius.all(32),
            padding=ft.Padding.symmetric(horizontal=22, vertical=18),
            margin=ft.Margin.only(bottom=14),
            content=self.center_view_container,
        )

        self.right_feed_column = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)

        right_panel = ft.Container(
            expand=3,
            padding=ft.Padding.only(left=22, right=22, top=8, bottom=20),
            content=ft.Column(
                expand=True,
                spacing=14,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Text("Error Feed & Repaso", size=17, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                            ft.Row(
                                spacing=4,
                                controls=[
                                    ft.IconButton(
                                        icon=ft.Icons.PLAY_CIRCLE_FILL_ROUNDED,
                                        icon_size=20,
                                        icon_color=ACCENT_TEAL,
                                        tooltip="Iniciar Test de Errores interactivo",
                                        on_click=self.start_error_quiz_mode,
                                    ),
                                    ft.IconButton(
                                        icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                                        icon_size=18,
                                        icon_color=TEXT_MUTED,
                                        tooltip="Abrir pestaña de Errores",
                                        on_click=lambda _: self.switch_tab(7),
                                    ),
                                    ft.IconButton(
                                        icon=ft.Icons.DELETE_SWEEP_OUTLINED,
                                        icon_size=18,
                                        icon_color=TEXT_MUTED,
                                        tooltip="Vaciar errores",
                                        on_click=self.clear_all_errors,
                                    ),
                                ],
                            ),
                        ],
                    ),
                    ft.Divider(color=BORDER_SUBTLE, height=1),
                    self.right_feed_column,
                ],
            ),
        )

        self.page.add(
            ft.Column(
                expand=True,
                spacing=0,
                controls=[
                    self.top_bar,
                    ft.Row(
                        expand=True,
                        spacing=0,
                        controls=[
                            left_sidebar,
                            center_well,
                            right_panel,
                        ],
                    ),
                ],
            )
        )

        self.refresh_right_error_feed()
        self.render_center_content()

    def update_sidebar(self):
        nav_items = [
            (0, ft.Icons.HOME_ROUNDED, "Dashboard & Configuración"),
            (1, ft.Icons.ASSIGNMENT_OUTLINED, "Test Nivel Medio"),
            (2, ft.Icons.LOCAL_FIRE_DEPARTMENT_ROUNDED, "Test Nivel Difícil"),
            (3, ft.Icons.EDIT_NOTE_ROUNDED, "Preguntas Cortas"),
            (4, ft.Icons.MENU_BOOK_ROUNDED, "Definiciones"),
            (5, ft.Icons.BUSINESS_CENTER_ROUNDED, "Casos Prácticos Industria"),
            (6, ft.Icons.TIMER_ROUNDED, "EXAMINAR — Simulacro Oficial (15 min)"),
            (7, ft.Icons.WARNING_AMBER_ROUNDED, "Test y Repaso de Errores"),
        ]
        controls = []
        for idx, icon_data, tooltip_text in nav_items:
            is_selected = self.current_nav_index == idx
            controls.append(
                ft.Container(
                    width=48,
                    height=48,
                    border_radius=24,
                    bgcolor="#232631" if is_selected else ft.Colors.TRANSPARENT,
                    border=ft.Border.all(1, BORDER_SUBTLE) if is_selected else None,
                    alignment=ft.Alignment(0.0, 0.0),
                    tooltip=tooltip_text,
                    on_click=lambda e, i=idx: self.switch_tab(i),
                    content=ft.Icon(
                        icon_data,
                        color=ACCENT_TEAL if (idx == 6 and is_selected) else (TEXT_WHITE if is_selected else TEXT_MUTED),
                        size=22,
                    ),
                )
            )
        controls.append(
            ft.Container(
                width=48,
                height=48,
                border_radius=24,
                alignment=ft.Alignment(0.0, 0.0),
                tooltip="Configurar API Key y Modelo IA",
                on_click=self.open_settings_dialog,
                content=ft.Icon(ft.Icons.SETTINGS_OUTLINED, color=TEXT_MUTED, size=22),
            )
        )
        self.sidebar_column.controls = controls

    def switch_tab(self, index: int):
        self.current_nav_index = index
        self.update_sidebar()
        self.render_center_content()
        self.page.update()

    def refresh_right_error_feed(self):
        errors = load_errors()
        self.right_feed_column.controls.clear()

        if not errors:
            self.right_feed_column.controls.append(
                ft.Container(
                    padding=20,
                    border_radius=20,
                    bgcolor="#14161C",
                    border=ft.Border.all(1, BORDER_SUBTLE),
                    content=ft.Row(
                        spacing=14,
                        controls=[
                            ft.CircleAvatar(
                                bgcolor="#2010B981",
                                content=ft.Icon(ft.Icons.VERIFIED_ROUNDED, color=ACCENT_TEAL, size=20),
                            ),
                            ft.Column(
                                spacing=3,
                                expand=True,
                                controls=[
                                    ft.Text("¡Sin errores activos!", weight=ft.FontWeight.BOLD, size=14, color=TEXT_WHITE),
                                    ft.Text("Tus fallos aparecerán aquí sin desvelar la respuesta para el repaso.", size=12, color=TEXT_MUTED),
                                ],
                            ),
                        ],
                    ),
                )
            )
            return

        self.right_feed_column.controls.append(
            ft.Button(
                content=f"🎯 Hacer Test de Errores ({len(errors)})",
                bgcolor=ACCENT_TEAL,
                color=BG_OUTER,
                height=38,
                style=ft.ButtonStyle(
                    shape=ft.RoundedRectangleBorder(radius=12),
                    text_style=ft.TextStyle(weight=ft.FontWeight.BOLD, size=12.5),
                ),
                on_click=self.start_error_quiz_mode,
            )
        )

        for idx, err in enumerate(reversed(errors)):
            real_index = len(errors) - 1 - idx
            is_featured = idx == 0

            avatar_colors = [ACCENT_TEAL, ACCENT_CORAL, ACCENT_AMBER, ACCENT_BLUE, ACCENT_PURPLE]
            av_color = avatar_colors[idx % len(avatar_colors)]

            card_content = ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.START,
                spacing=12,
                controls=[
                    ft.CircleAvatar(
                        bgcolor=av_color,
                        radius=20,
                        content=ft.Text(
                            err.get("type", "E")[0].upper(),
                            weight=ft.FontWeight.BOLD,
                            color=BG_OUTER,
                        ),
                    ),
                    ft.Column(
                        spacing=4,
                        expand=True,
                        controls=[
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[
                                    ft.Text(err.get("type", "Test"), weight=ft.FontWeight.BOLD, size=14, color=TEXT_WHITE),
                                    ft.IconButton(
                                        icon=ft.Icons.CHECK_CIRCLE_OUTLINE_ROUNDED,
                                        icon_size=17,
                                        icon_color=ACCENT_TEAL if is_featured else TEXT_MUTED,
                                        tooltip="Marcar como superado",
                                        on_click=lambda e, r_idx=real_index: self.remove_single_error(r_idx),
                                    ),
                                ],
                            ),
                            ft.Text(
                                err.get("question", ""),
                                size=12,
                                color="#D1D5DB" if is_featured else TEXT_MUTED,
                                max_lines=2,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[
                                    ft.Text(
                                        "🔒 Respuesta oculta para test",
                                        size=11,
                                        color=ACCENT_AMBER,
                                        weight=ft.FontWeight.W_600,
                                    ),
                                    ft.Text("Pendiente", size=10, color=TEXT_MUTED),
                                ],
                            ),
                        ],
                    ),
                ],
            )

            if is_featured:
                item_container = ft.Container(
                    padding=16,
                    border_radius=24,
                    gradient=ft.LinearGradient(
                        begin=ft.Alignment(-1.0, 0.0),
                        end=ft.Alignment(1.0, 0.0),
                        colors=["#3810B981", "#181B22"],
                    ),
                    border=ft.Border.all(1, "#5010B981"),
                    on_click=lambda _: self.switch_tab(7),
                    content=card_content,
                )
            else:
                item_container = ft.Container(
                    padding=ft.Padding.symmetric(horizontal=10, vertical=10),
                    border=ft.Border.only(bottom=ft.BorderSide(1, BORDER_SUBTLE)),
                    on_click=lambda _: self.switch_tab(7),
                    content=card_content,
                )

            self.right_feed_column.controls.append(item_container)

    def remove_single_error(self, index: int):
        errors = load_errors()
        if 0 <= index < len(errors):
            errors.pop(index)
            save_errors(errors)
            self.refresh_right_error_feed()
            if self.current_nav_index == 7:
                self.render_center_content()
            self.page.update()

    def clear_all_errors(self, e=None):
        save_errors([])
        self.error_quiz_mode = False
        self.refresh_right_error_feed()
        if self.current_nav_index == 7:
            self.render_center_content()
        self.notify("Historial de errores vaciado correctamente.", ACCENT_BLUE)
        self.page.update()

    def render_center_content(self):
        if self.current_nav_index == 0:
            self.center_view_container.content = self.build_dashboard_view()
        elif self.current_nav_index == 1:
            if self.test_finished["Test Medio"]:
                self.center_view_container.content = self.build_score_view("Test Medio", "medium_mcq", 0)
            else:
                self.center_view_container.content = self.build_single_mcq_view("Test Medio", "medium_mcq", 0)
        elif self.current_nav_index == 2:
            if self.test_finished["Test Difícil"]:
                self.center_view_container.content = self.build_score_view("Test Difícil", "hard_mcq", 1)
            else:
                self.center_view_container.content = self.build_single_mcq_view("Test Difícil", "hard_mcq", 1)
        elif self.current_nav_index == 3:
            if self.test_finished["Preguntas Cortas"]:
                self.center_view_container.content = self.build_score_short_view()
            else:
                self.center_view_container.content = self.build_single_short_view()
        elif self.current_nav_index == 4:
            if self.test_finished["Definiciones"]:
                self.center_view_container.content = self.build_score_definitions_view()
            else:
                self.center_view_container.content = self.build_single_definition_view()
        elif self.current_nav_index == 5:
            self.center_view_container.content = self.build_single_case_view()
        elif self.current_nav_index == 6:
            self.center_view_container.content = self.build_exam_mode_view()
        elif self.current_nav_index == 7:
            self.center_view_container.content = self.build_errors_full_view()

    # -------------------- VISTA 0: DASHBOARD BENTO --------------------

    def build_dashboard_view(self):
        med_count = len(self.exams_data["medium_mcq"])
        hard_count = len(self.exams_data["hard_mcq"])
        short_count = len(self.exams_data["short_answers"])
        def_count = len(self.exams_data["definitions"])
        case_count = len(self.exams_data["industrial_cases"])
        err_count = len(load_errors())

        med_done = sum(1 for (t, _), v in self.user_mcq_answers.items() if t == "Test Medio" and v)
        hard_done = sum(1 for (t, _), v in self.user_mcq_answers.items() if t == "Test Difícil" and v)
        short_done = sum(1 for _, v in self.user_short_answers.items() if str(v).strip())
        def_done = sum(1 for _, v in self.user_def_answers.items() if str(v).strip())
        case_done = sum(1 for _, v in self.user_case_answers.items() if str(v).strip())

        med_pct = int((med_done / med_count) * 100) if med_count else 0
        hard_pct = int((hard_done / hard_count) * 100) if hard_count else 0
        short_pct = int((short_done / short_count) * 100) if short_count else 0
        def_pct = int((def_done / def_count) * 100) if def_count else 0
        case_pct = int((case_done / case_count) * 100) if case_count else 0

        self._sync_live_widgets()

        def make_bento_project_card(title, subtitle, pct, badge_control, theme_idx, nav_target, count_label):
            theme = CARD_THEMES[theme_idx % len(CARD_THEMES)]
            return ft.Container(
                expand=True,
                on_click=lambda _: self.switch_tab(nav_target),
                content=self.create_glow_card(
                    theme_idx=theme_idx,
                    padding=16,
                    content=ft.Column(
                        spacing=6,
                        controls=[
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[
                                    ft.Text(count_label, size=10.5, color=TEXT_MUTED),
                                    ft.Icon(ft.Icons.MORE_VERT_ROUNDED, size=16, color=TEXT_MUTED),
                                ],
                            ),
                            ft.Column(
                                spacing=2,
                                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[
                                    ft.Text(title, size=14.5, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                                    ft.Text(subtitle, size=11, color=TEXT_MUTED),
                                ],
                            ),
                            ft.Column(
                                spacing=4,
                                controls=[
                                    ft.Text("Progress", size=10.5, weight=ft.FontWeight.W_600, color=TEXT_WHITE),
                                    ft.ProgressBar(
                                        value=pct / 100.0 if pct > 0 else (1.0 if "Errores" in title and err_count > 0 else 0.05),
                                        color=theme["accent"],
                                        bgcolor="#2A2E3B",
                                        bar_height=5,
                                        border_radius=4,
                                    ),
                                    ft.Row(
                                        alignment=ft.MainAxisAlignment.END,
                                        controls=[ft.Text(f"{pct}%", size=10.5, weight=ft.FontWeight.BOLD, color=TEXT_WHITE)],
                                    ),
                                ],
                            ),
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[
                                    ft.Row(
                                        spacing=4,
                                        controls=[
                                            ft.CircleAvatar(radius=10, bgcolor=theme["accent"], content=ft.Icon(ft.Icons.BOLT, size=12, color=BG_OUTER)),
                                            ft.CircleAvatar(radius=10, bgcolor="#2A2E3D", content=ft.Text("+1", size=8.5, color=TEXT_WHITE)),
                                        ],
                                    ),
                                    ft.Container(
                                        padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                                        border_radius=10,
                                        bgcolor="#252834",
                                        content=badge_control,
                                    ),
                                ],
                            ),
                        ],
                    ),
                )
            )

        return ft.Column(
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            spacing=14,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Text("Panel de Exámenes", size=22, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                        ft.Row(
                            spacing=12,
                            controls=[
                                self.unique_topics_text,
                                ft.Button(
                                    content="⏱️EXAMINAR",
                                    bgcolor=ACCENT_TEAL,
                                    color=BG_OUTER,
                                    height=36,
                                    style=ft.ButtonStyle(
                                        shape=ft.RoundedRectangleBorder(radius=12),
                                        text_style=ft.TextStyle(weight=ft.FontWeight.BOLD, size=13),
                                    ),
                                    on_click=lambda _: self.switch_tab(6),
                                ),
                            ],
                        ),
                    ],
                ),
                ft.Container(
                    bgcolor=BG_CARD,
                    border_radius=18,
                    padding=ft.Padding.symmetric(horizontal=18, vertical=10),
                    border=ft.Border.all(1, BORDER_SUBTLE),
                    content=ft.Row(
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Icon(ft.Icons.KEY_ROUNDED, color=ACCENT_AMBER, size=20),
                            ft.Text("API Key:", size=13, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                            self._make_api_textfield(),
                            ft.Text("Modelo IA:", size=13, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                            self._make_model_selector(),
                        ],
                    ),
                ),
                ft.Row(
                    spacing=14,
                    controls=[
                        ft.Container(
                            expand=True,
                            bgcolor=BG_INPUT,
                            border_radius=16,
                            padding=ft.Padding.symmetric(horizontal=18, vertical=10),
                            border=ft.Border.all(1, BORDER_SUBTLE),
                            content=ft.Row(
                                spacing=12,
                                controls=[
                                    ft.Icon(ft.Icons.PICTURE_AS_PDF_ROUNDED, color=ACCENT_TEAL, size=20),
                                    self.pdf_label_text,
                                    ft.Button(
                                        content="Seleccionar PDF(s)",
                                        icon=ft.Icons.FOLDER_OPEN_ROUNDED,
                                        bgcolor="#232733",
                                        color=TEXT_WHITE,
                                        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=12)),
                                        on_click=self.select_pdf_dialog,
                                    ),
                                ],
                            ),
                        ),
                        ft.Row(
                            spacing=8,
                            controls=[
                                self.gen_summary_text,
                                ft.Button(
                                    content="Generar Exámenes" if not self.is_generating else "Generando...",
                                    icon=ft.Icons.ROCKET_LAUNCH_ROUNDED,
                                    bgcolor=ACCENT_TEAL,
                                    color=BG_OUTER,
                                    height=48,
                                    disabled=self.is_generating,
                                    style=ft.ButtonStyle(
                                        shape=ft.RoundedRectangleBorder(radius=16),
                                        text_style=ft.TextStyle(weight=ft.FontWeight.BOLD, size=14),
                                    ),
                                    on_click=self.start_generation_thread,
                                ),
                                self._build_generation_selector(),
                            ],
                        ),
                    ],
                ),
                self.progress_container,
                ft.Row(
                    spacing=14,
                    controls=[
                        make_bento_project_card(
                            "Test Medio",
                            "Comprensión Clave",
                            med_pct,
                            self.badge_med_text,
                            0,
                            1,
                            "Módulo 01 • MCQ",
                        ),
                        make_bento_project_card(
                            "Test Difícil",
                            "",
                            hard_pct,
                            self.badge_hard_text,
                            1,
                            2,
                            "Módulo 02 • Avanzado",
                        ),
                        make_bento_project_card(
                            "Preguntas Cortas",
                            "",
                            short_pct,
                            self.badge_short_text,
                            2,
                            3,
                            "Módulo 03 • Escrito",
                        ),
                    ],
                ),
                ft.Row(
                    spacing=14,
                    controls=[
                        make_bento_project_card(
                            "Definiciones",
                            "",
                            def_pct,
                            self.badge_def_text,
                            3,
                            4,
                            "Módulo 04 • Conceptos",
                        ),
                        make_bento_project_card(
                            "Caso práctico",
                            "Apicación a la industria",
                            case_pct,
                            self.badge_case_text,
                            4,
                            5,
                            "Módulo 05 • Práctico",
                        ),
                        make_bento_project_card(
                            "Exámen",
                            "Duración: 15 minutos",
                            100 if self.exam_finished else 0,
                            ft.Text("Modo oficial", size=10.5, color=TEXT_WHITE),
                            0,
                            6,
                            "Módulo 06 • Examen",
                        ),
                    ],
                ),
            ],
        )

    # -------------------- SELECTOR DE GENERACIÓN --------------------

    def _build_generation_selector(self):
        """Construye el desplegable con todas las combinaciones de generación."""
        def make_check_item(label: str, attr: str):
            return ft.PopupMenuItem(
                content=ft.Row(
                    spacing=10,
                    controls=[
                        ft.Checkbox(
                            value=getattr(self, attr),
                            fill_color=ACCENT_TEAL,
                            on_change=lambda e, a=attr: self._toggle_gen_option(a, e.control.value),
                        ),
                        ft.Text(label, color=TEXT_WHITE),
                    ],
                ),
            )

        return ft.PopupMenuButton(
            tooltip="Elegir qué secciones generar",
            content=ft.Container(
                bgcolor="#232733",
                border=ft.Border.all(1, ACCENT_TEAL),
                border_radius=14,
                padding=ft.Padding.symmetric(horizontal=10, vertical=9),
                content=ft.Row(
                    spacing=4,
                    controls=[
                        ft.Icon(ft.Icons.TUNE_ROUNDED, color=ACCENT_TEAL, size=18),
                        ft.Icon(ft.Icons.ARROW_DROP_DOWN_ROUNDED, color=TEXT_WHITE, size=20),
                    ],
                ),
            ),
            items=[
                ft.PopupMenuItem(
                    content=ft.Row(
                        spacing=10,
                        controls=[
                            ft.Icon(ft.Icons.ALL_INCLUSIVE_ROUNDED, color=ACCENT_TEAL, size=18),
                            ft.Text("Generar TODO", weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                        ],
                    ),
                    on_click=lambda _: self._apply_gen_preset("all"),
                ),
                ft.PopupMenuItem(
                    content=ft.Row(
                        spacing=10,
                        controls=[
                            ft.Icon(ft.Icons.QUIZ_ROUNDED, color=ACCENT_AMBER, size=18),
                            ft.Text("Solo Tests (Medio + Difícil)", color=TEXT_WHITE),
                        ],
                    ),
                    on_click=lambda _: self._apply_gen_preset("tests"),
                ),
                ft.PopupMenuItem(
                    content=ft.Row(
                        spacing=10,
                        controls=[
                            ft.Icon(ft.Icons.EDIT_NOTE_ROUNDED, color=ACCENT_AMBER, size=18),
                            ft.Text("Solo Preguntas Cortas", color=TEXT_WHITE),
                        ],
                    ),
                    on_click=lambda _: self._apply_gen_preset("short"),
                ),
                ft.PopupMenuItem(
                    content=ft.Row(
                        spacing=10,
                        controls=[
                            ft.Icon(ft.Icons.MENU_BOOK_ROUNDED, color=ACCENT_PURPLE, size=18),
                            ft.Text("Solo Definiciones", color=TEXT_WHITE),
                        ],
                    ),
                    on_click=lambda _: self._apply_gen_preset("defs"),
                ),
                ft.PopupMenuItem(
                    content=ft.Row(
                        spacing=10,
                        controls=[
                            ft.Icon(ft.Icons.BUSINESS_CENTER_ROUNDED, color=ACCENT_BLUE, size=18),
                            ft.Text("Solo Casos Prácticos", color=TEXT_WHITE),
                        ],
                    ),
                    on_click=lambda _: self._apply_gen_preset("cases"),
                ),
                ft.Divider(color=BORDER_SUBTLE, height=1),
                ft.PopupMenuItem(
                    content=ft.Text("Personalizado (marca abajo):", size=11, color=TEXT_MUTED, weight=ft.FontWeight.BOLD),
                    disabled=True,
                ),
                make_check_item("Test Nivel Medio", "gen_medium"),
                make_check_item("Test Nivel Difícil", "gen_hard"),
                make_check_item("Preguntas Cortas", "gen_short"),
                make_check_item("Definiciones", "gen_defs"),
                make_check_item("Casos Prácticos", "gen_cases"),
            ],
        )

    def _toggle_gen_option(self, attr: str, value: bool):
        setattr(self, attr, value)
        self.gen_all = all([
            self.gen_medium, self.gen_hard, self.gen_short,
            self.gen_defs, self.gen_cases,
        ])
        self.gen_summary_text.value = self._summarize_gen_selection()
        self.notify(
            f"Selección actualizada: {self._summarize_gen_selection()}",
            ACCENT_BLUE,
        )
        self.page.update()

    def _apply_gen_preset(self, preset: str):
        presets = {
            "all":   {"gen_medium": True,  "gen_hard": True,  "gen_short": True,  "gen_defs": True,  "gen_cases": True},
            "tests": {"gen_medium": True,  "gen_hard": True,  "gen_short": False, "gen_defs": False, "gen_cases": False},
            "short": {"gen_medium": False, "gen_hard": False, "gen_short": True,  "gen_defs": False, "gen_cases": False},
            "defs":  {"gen_medium": False, "gen_hard": False, "gen_short": False, "gen_defs": True,  "gen_cases": False},
            "cases": {"gen_medium": False, "gen_hard": False, "gen_short": False, "gen_defs": False, "gen_cases": True},
        }
        for k, v in presets[preset].items():
            setattr(self, k, v)
        self.gen_all = preset == "all"
        self.gen_summary_text.value = self._summarize_gen_selection()
        self.notify(f"Preset aplicado: {self._summarize_gen_selection()}", ACCENT_TEAL)
        self.render_center_content()
        self.page.update()

    def _summarize_gen_selection(self) -> str:
        parts = []
        if self.gen_medium: parts.append("Medio")
        if self.gen_hard: parts.append("Difícil")
        if self.gen_short: parts.append("Cortas")
        if self.gen_defs: parts.append("Definiciones")
        if self.gen_cases: parts.append("Casos")
        return ", ".join(parts) if parts else "Nada seleccionado (⚠️)"

    # -------------------- MODO PREGUNTA ÚNICA (MCQ MEDIO / DIFÍCIL) --------------------

    def build_single_mcq_view(self, test_type: str, data_key: str, theme_idx: int):
        questions = self.exams_data.get(data_key, [])
        theme = CARD_THEMES[theme_idx % len(CARD_THEMES)]

        if not questions:
            return ft.Column(
                expand=True,
                controls=[
                    ft.Text(f"Evaluación — {test_type}", size=20, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                    ft.Container(
                        expand=True,
                        alignment=ft.Alignment(0.0, 0.0),
                        content=ft.Column(
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=12,
                            controls=[
                                ft.Icon(ft.Icons.QUIZ_OUTLINED, size=54, color=TEXT_MUTED),
                                ft.Text("Aún no hay preguntas generadas. Genera el examen en el Dashboard.", size=16, color=TEXT_MUTED),
                                ft.Button(
                                    content="Ir al Dashboard",
                                    bgcolor=ACCENT_TEAL,
                                    color=BG_OUTER,
                                    on_click=lambda _: self.switch_tab(0),
                                ),
                            ],
                        ),
                    ),
                ],
            )

        curr_idx = self.test_indices[test_type]
        if curr_idx >= len(questions):
            curr_idx = len(questions) - 1
            self.test_indices[test_type] = curr_idx

        q = questions[curr_idx]
        saved_val = self.user_mcq_answers.get((test_type, curr_idx), "")
        options = q.get("options", [])
        correct_letter = str(q.get("correct_letter", "")).strip().upper()

        feedback_text = ft.Text(
            "",
            size=12.5,
            weight=ft.FontWeight.W_600,
            selectable=True,
        )
        feedback_container = ft.Container(
            key="feedback_box",
            visible=False,
            padding=ft.Padding.symmetric(horizontal=14, vertical=10),
            border_radius=12,
            bgcolor=BG_INPUT,
            content=feedback_text,
        )

        radio_group = ft.RadioGroup(
            value=saved_val,
            on_change=lambda e, t=test_type, i=curr_idx: self.user_mcq_answers.update({(t, i): e.control.value}),
            content=ft.Column(spacing=6, controls=[]),
        )

        def select_option_click(letter_val: str):
            radio_group.value = letter_val
            self.user_mcq_answers[(test_type, curr_idx)] = letter_val
            self.page.update()

        option_cards = []
        for letter, opt_text in zip(LETTERS, options):
            option_cards.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                    border_radius=12,
                    bgcolor=BG_INPUT,
                    border=ft.Border.all(1, BORDER_SUBTLE),
                    on_click=lambda e, l=letter: select_option_click(l),
                    content=ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Radio(
                                value=letter,
                                fill_color=theme["accent"],
                            ),
                            ft.Text(
                                f"{letter})  {opt_text}",
                                color=TEXT_WHITE,
                                size=13,
                                expand=True,
                            ),
                        ],
                    ),
                )
            )
        radio_group.content.controls = option_cards

        scrollable_question_body = ft.Column(
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            spacing=8,
            controls=[],
        )

        def verify_current(e):
            chosen = (radio_group.value or "").strip().upper()
            self.user_mcq_answers[(test_type, curr_idx)] = chosen
            if chosen not in LETTERS:
                self.notify("Selecciona una opción antes de comprobar.", ACCENT_AMBER)
                return

            feedback_container.visible = True
            if chosen == correct_letter:
                feedback_text.value = f"¡Respuesta Correcta! 🎯 — {q.get('explanation', '')}"
                feedback_text.color = ACCENT_TEAL
                feedback_container.border = ft.Border.all(1, ACCENT_TEAL)
            else:
                try:
                    corr_text = options[LETTERS.index(correct_letter)]
                except Exception:
                    corr_text = ""
                frase = _get_frase_fallo()
                feedback_text.value = (
                    f"❌ {frase}\n"
                    f"Respuesta correcta: {correct_letter}) {corr_text}\n"
                    f"Explicación: {q.get('explanation', '')}"
                )
                feedback_text.color = ACCENT_CORAL
                feedback_container.border = ft.Border.all(1, ACCENT_CORAL)

                errors = load_errors()
                err_item = {
                    "type": test_type,
                    "topic": q.get("topic", ""),
                    "question": q.get("question", ""),
                    "options": list(options),
                    "correct_letter": correct_letter,
                    "correct": f"{correct_letter}) {corr_text}",
                    "explanation": q.get("explanation", ""),
                }
                if not any(x.get("question") == err_item["question"] for x in errors):
                    errors.append(err_item)
                    save_errors(errors)
                    self.refresh_right_error_feed()

            self.page.update()
            try:
                scrollable_question_body.scroll_to(key="feedback_box", duration=250)
            except Exception:
                pass

        def go_prev(e):
            if self.test_indices[test_type] > 0:
                self.test_indices[test_type] -= 1
                self.render_center_content()
                self.page.update()

        def go_next(e):
            if self.test_indices[test_type] < len(questions) - 1:
                self.test_indices[test_type] += 1
                self.render_center_content()
                self.page.update()
            else:
                self.test_finished[test_type] = True
                self.render_center_content()
                self.page.update()

        nav_bar = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Text(f"{test_type}", size=17, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                        ft.Button(
                            content="🔄 +10",
                            tooltip="Generar 10 preguntas adicionales",
                            bgcolor="#252733",
                            color=TEXT_WHITE,
                            height=34,
                            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
                            on_click=lambda e: self.regenerate_more_questions(test_type, e.control),
                        ),
                    ],
                ),
                ft.Row(
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Button(
                            content="Anterior",
                            icon=ft.Icons.ARROW_BACK_ROUNDED,
                            bgcolor="#232733",
                            color=TEXT_WHITE,
                            height=36,
                            disabled=curr_idx == 0,
                            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
                            on_click=go_prev,
                        ),
                        ft.Text(f"Pregunta {curr_idx + 1} de {len(questions)}", size=13.5, weight=ft.FontWeight.BOLD, color=theme["accent"]),
                        ft.Button(
                            content="Siguiente ➔" if curr_idx < len(questions) - 1 else "Finalizar y Ver Nota 🏆",
                            icon=ft.Icons.ARROW_FORWARD_ROUNDED if curr_idx < len(questions) - 1 else ft.Icons.GRADING_ROUNDED,
                            bgcolor=theme["accent"],
                            color=BG_OUTER,
                            height=36,
                            style=ft.ButtonStyle(
                                shape=ft.RoundedRectangleBorder(radius=10),
                                text_style=ft.TextStyle(weight=ft.FontWeight.BOLD),
                            ),
                            on_click=go_next,
                        ),
                    ],
                ),
            ],
        )

        scrollable_question_body.controls = [
            ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Text(f"Tema: {q.get('topic', '')}", size=11.5, color=TEXT_MUTED),
                    ft.Button(
                        content="✓ Comprobar Respuesta",
                        bgcolor=ACCENT_BLUE,
                        color=TEXT_WHITE,
                        height=34,
                        style=ft.ButtonStyle(
                            shape=ft.RoundedRectangleBorder(radius=10),
                            text_style=ft.TextStyle(weight=ft.FontWeight.BOLD, size=12.5),
                        ),
                        on_click=verify_current,
                    ),
                ],
            ),
            ft.Text(q.get("question", ""), size=14.5, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
            radio_group,
            feedback_container,
        ]

        return self.create_glow_card(
            theme_idx=curr_idx % 4,
            padding=18,
            expand=True,
            content=ft.Column(
                expand=True,
                spacing=10,
                controls=[
                    nav_bar,
                    ft.Divider(color=BORDER_SUBTLE, height=1),
                    scrollable_question_body,
                ],
            ),
        )

    # -------------------- MODO PREGUNTA ÚNICA (PREGUNTAS CORTAS) --------------------

    def build_single_short_view(self):
        questions = self.exams_data.get("short_answers", [])

        if not questions:
            return ft.Column(
                expand=True,
                controls=[
                    ft.Text("Preguntas Cortas y Desarrollo", size=20, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                    ft.Container(
                        expand=True,
                        alignment=ft.Alignment(0.0, 0.0),
                        content=ft.Text("Aún no hay preguntas cortas generadas.", size=16, color=TEXT_MUTED),
                    ),
                ],
            )

        curr_idx = self.test_indices["Preguntas Cortas"]
        if curr_idx >= len(questions):
            curr_idx = len(questions) - 1
            self.test_indices["Preguntas Cortas"] = curr_idx

        q = questions[curr_idx]
        saved_ans = self.user_short_answers.get(curr_idx, "")
        eval_res = self.short_evaluations.get(curr_idx)

        model_ans_text = ft.Text(
            f"Respuesta Modelo:\n{q.get('model_answer', '')}",
            size=13,
            color=ACCENT_TEAL,
            weight=ft.FontWeight.W_600,
            selectable=True,
        )
        model_container = ft.Container(
            key="short_model_box",
            visible=False,
            padding=14,
            border_radius=12,
            bgcolor=BG_INPUT,
            border=ft.Border.all(1, ACCENT_TEAL),
            content=model_ans_text,
        )

        ai_eval_container = ft.Container(
            key=f"short_eval_box_{curr_idx}",
            visible=bool(eval_res),
            padding=14,
            border_radius=12,
            bgcolor=BG_INPUT,
            border=ft.Border.all(
                1,
                ACCENT_TEAL if eval_res and eval_res.get("score", 0) >= self.error_save_threshold else ACCENT_CORAL,
            ),
            content=ft.Column(spacing=6, controls=[]),
        )

        def populate_short_eval(ev_data):
            if not ev_data:
                return
            sc = int(ev_data.get("score", 1))
            passed = sc >= self.error_save_threshold
            c_color = ACCENT_TEAL if passed else ACCENT_CORAL
            status_tag = (
                f"✅ Superada (Nota ≥ {self.error_save_threshold})"
                if passed
                else f"❌ Guardada en Test de Errores (Nota < {self.error_save_threshold})"
            )
            ai_eval_container.border = ft.Border.all(1, c_color)
            feedback_final = ev_data.get("feedback", "")
            if not passed:
                feedback_final = f"{_get_frase_fallo()}\n\n{feedback_final}"
            ai_eval_container.content.controls = [
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Text(f"🤖 Nota IA: {sc} / 10", size=15, weight=ft.FontWeight.BOLD, color=c_color),
                        ft.Text(status_tag, size=11.5, weight=ft.FontWeight.W_600, color=c_color),
                    ],
                ),
                ft.Text(feedback_final, size=12.5, color=TEXT_WHITE, selectable=True),
            ]

        if eval_res:
            populate_short_eval(eval_res)

        tf = ft.TextField(
            value=saved_ans,
            multiline=True,
            min_lines=3,
            max_lines=4,
            hint_text="Escribe tu respuesta de desarrollo aquí...",
            bgcolor=BG_INPUT,
            border_color=BORDER_SUBTLE,
            focused_border_color=ACCENT_AMBER,
            border_radius=12,
            color=TEXT_WHITE,
            text_size=13,
            on_change=lambda e, i=curr_idx: self.user_short_answers.update({i: e.control.value}),
        )

        scrollable_short_body = ft.Column(
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            spacing=10,
            controls=[],
        )

        def toggle_model(e):
            model_container.visible = not model_container.visible
            self.page.update()
            if model_container.visible:
                try:
                    scrollable_short_body.scroll_to(key="short_model_box", duration=250)
                except Exception:
                    pass

        def mark_short(is_ok: bool):
            if not is_ok:
                errors = load_errors()
                err_item = {
                    "type": "Pregunta Corta",
                    "topic": q.get("topic", ""),
                    "question": q.get("question", ""),
                    "model_answer": q.get("model_answer", ""),
                }
                if not any(x.get("question") == err_item["question"] for x in errors):
                    errors.append(err_item)
                    save_errors(errors)
                    self.refresh_right_error_feed()
                self.notify("Añadida al Test de Errores para repasar.", ACCENT_CORAL)
            else:
                self.notify("¡Excelente trabajo en esta pregunta!", ACCENT_TEAL)
            self.page.update()

        def evaluate_short_with_ai(e):
            ans_text = (tf.value or "").strip()
            self.user_short_answers[curr_idx] = ans_text
            if not ans_text:
                self.notify("Escribe tu respuesta antes de corregir con IA.", ACCENT_AMBER)
                return

            api_key = self._get_api_key()
            if not api_key:
                self.notify("Falta la API Key de Gemini.", ACCENT_CORAL)
                return

            btn = e.control
            btn.disabled = True
            btn.content = "⏳ Corrigiendo..."
            self.page.update()

            async def _async_eval_short():
                try:
                    def _sync_call():
                        client = genai.Client(api_key=api_key)
                        prompt = f"""
Actúa como un profesor riguroso corrigiendo una pregunta corta de examen.
Evalúa la respuesta del alumno comparándola con la Respuesta Modelo del temario y asígnale una nota entera del 1 al 10 (1 = totalmente errónea o irrelevante, 10 = perfecta y completa).

PREGUNTA:
{q.get('question', '')}

RESPUESTA MODELO DEL TEMARIO:
{q.get('model_answer', '')}

RESPUESTA DEL ALUMNO:
{ans_text}

Devuelve un JSON con 'score' (entero de 1 a 10) y 'feedback' (explicación breve de aciertos y qué faltó).
"""
                        return self.call_gemini_with_retry(client, self.selected_model, prompt, SingleWrittenEvaluation)

                    res_eval = await asyncio.to_thread(_sync_call)
                    sc = max(1, min(10, int(round(float(res_eval.get("score", 1))))))
                    res_eval["score"] = sc
                    self.short_evaluations[curr_idx] = res_eval

                    populate_short_eval(res_eval)
                    ai_eval_container.visible = True

                    if sc < self.error_save_threshold:
                        errors = load_errors()
                        err_item = {
                            "type": "Pregunta Corta",
                            "topic": q.get("topic", ""),
                            "question": q.get("question", ""),
                            "model_answer": q.get("model_answer", ""),
                        }
                        if not any(x.get("question") == err_item["question"] for x in errors):
                            errors.append(err_item)
                            save_errors(errors)
                            self.refresh_right_error_feed()
                        self.notify(f"Nota IA: {sc}/10 (< {self.error_save_threshold}). Guardada en Test de Errores.", ACCENT_CORAL)
                    else:
                        self.notify(f"¡Nota IA: {sc}/10! Supera el corte ({self.error_save_threshold}/10).", ACCENT_TEAL)

                except Exception as ex:
                    traceback.print_exc()
                    self.notify(f"Error al corregir con IA: {ex}", ACCENT_CORAL)
                finally:
                    btn.disabled = False
                    btn.content = "🤖 Corregir con IA"
                    self.page.update()
                    try:
                        scrollable_short_body.scroll_to(key=f"short_eval_box_{curr_idx}", duration=250)
                    except Exception:
                        pass

            self.page.run_task(_async_eval_short)

        def go_prev(e):
            if self.test_indices["Preguntas Cortas"] > 0:
                self.test_indices["Preguntas Cortas"] -= 1
                self.render_center_content()
                self.page.update()

        def go_next(e):
            if self.test_indices["Preguntas Cortas"] < len(questions) - 1:
                self.test_indices["Preguntas Cortas"] += 1
                self.render_center_content()
                self.page.update()
            else:
                self.test_finished["Preguntas Cortas"] = True
                self.render_center_content()
                self.page.update()

        nav_bar = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Text("Preguntas Cortas", size=17, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                        ft.Button(
                            content="🔄 +10",
                            bgcolor="#252733",
                            color=TEXT_WHITE,
                            height=34,
                            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
                            on_click=lambda e: self.regenerate_more_questions("Preguntas Cortas", e.control),
                        ),
                    ],
                ),
                ft.Row(
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Button(
                            content="Anterior",
                            icon=ft.Icons.ARROW_BACK_ROUNDED,
                            bgcolor="#232733",
                            color=TEXT_WHITE,
                            height=36,
                            disabled=curr_idx == 0,
                            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
                            on_click=go_prev,
                        ),
                        ft.Text(f"Pregunta {curr_idx + 1} de {len(questions)}", size=13.5, weight=ft.FontWeight.BOLD, color=ACCENT_AMBER),
                        ft.Button(
                            content="Siguiente ➔" if curr_idx < len(questions) - 1 else "Finalizar y Ver Nota 🏆",
                            icon=ft.Icons.ARROW_FORWARD_ROUNDED if curr_idx < len(questions) - 1 else ft.Icons.GRADING_ROUNDED,
                            bgcolor=ACCENT_AMBER,
                            color=BG_OUTER,
                            height=36,
                            style=ft.ButtonStyle(
                                shape=ft.RoundedRectangleBorder(radius=10),
                                text_style=ft.TextStyle(weight=ft.FontWeight.BOLD),
                            ),
                            on_click=go_next,
                        ),
                    ],
                ),
            ],
        )

        scrollable_short_body.controls = [
            ft.Text(f"Tema: {q.get('topic', '')}", size=11.5, color=TEXT_MUTED),
            ft.Text(q.get("question", ""), size=15, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
            tf,
            ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(
                        spacing=8,
                        controls=[
                            ft.Button(
                                content="Ver Respuesta Modelo",
                                icon=ft.Icons.VISIBILITY_ROUNDED,
                                bgcolor=ACCENT_BLUE,
                                color=TEXT_WHITE,
                                height=38,
                                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
                                on_click=toggle_model,
                            ),
                            ft.Button(
                                content="🤖 Corregir con IA",
                                bgcolor=ACCENT_TEAL,
                                color=BG_OUTER,
                                height=38,
                                style=ft.ButtonStyle(
                                    shape=ft.RoundedRectangleBorder(radius=10),
                                    text_style=ft.TextStyle(weight=ft.FontWeight.BOLD),
                                ),
                                on_click=evaluate_short_with_ai,
                            ),
                        ],
                    ),
                    ft.Row(
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            self._build_threshold_selector(ACCENT_AMBER),
                            ft.OutlinedButton(
                                content="Bien ✅",
                                style=ft.ButtonStyle(color=ACCENT_TEAL, shape=ft.RoundedRectangleBorder(radius=10)),
                                on_click=lambda _: mark_short(True),
                            ),
                            ft.OutlinedButton(
                                content="Mal ❌",
                                style=ft.ButtonStyle(color=ACCENT_CORAL, shape=ft.RoundedRectangleBorder(radius=10)),
                                on_click=lambda _: mark_short(False),
                            ),
                        ],
                    ),
                ],
            ),
            ai_eval_container,
            model_container,
        ]

        return self.create_glow_card(
            theme_idx=2,
            padding=18,
            expand=True,
            content=ft.Column(
                expand=True,
                spacing=10,
                controls=[
                    nav_bar,
                    ft.Divider(color=BORDER_SUBTLE, height=1),
                    scrollable_short_body,
                ],
            ),
        )

    # -------------------- VISTA 4: MÓDULO DE DEFINICIONES (MAYÚSCULA OBLIGATORIA) --------------------

    def build_single_definition_view(self):
        definitions = self.exams_data.get("definitions", [])

        if not definitions:
            return ft.Column(
                expand=True,
                controls=[
                    ft.Text("Entrenamiento de Definiciones", size=20, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                    ft.Container(
                        expand=True,
                        alignment=ft.Alignment(0.0, 0.0),
                        content=ft.Column(
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=14,
                            controls=[
                                ft.Icon(ft.Icons.MENU_BOOK_ROUNDED, size=56, color=ACCENT_PURPLE),
                                ft.Text(
                                    "Aún no hay conceptos cargados. Genera el examen desde el Dashboard seleccionando un PDF.",
                                    size=15,
                                    color=TEXT_MUTED,
                                    text_align=ft.TextAlign.CENTER,
                                ),
                                ft.Button(
                                    content="Ir al Dashboard",
                                    bgcolor=ACCENT_PURPLE,
                                    color=TEXT_WHITE,
                                    on_click=lambda _: self.switch_tab(0),
                                ),
                            ],
                        ),
                    ),
                ],
            )

        curr_idx = self.test_indices["Definiciones"]
        if curr_idx >= len(definitions):
            curr_idx = len(definitions) - 1
            self.test_indices["Definiciones"] = curr_idx

        d_item = definitions[curr_idx]
        saved_def = self.user_def_answers.get(curr_idx, "")
        eval_res = self.def_evaluations.get(curr_idx)
        capitalized_term = _format_capitalized_term(d_item.get('term', ''))

        official_def_text = ft.Text(
            f"Definición Oficial del Temario:\n{d_item.get('definition', '')}",
            size=13,
            color=ACCENT_TEAL,
            weight=ft.FontWeight.W_600,
            selectable=True,
        )
        official_def_container = ft.Container(
            key="def_model_box",
            visible=False,
            padding=14,
            border_radius=12,
            bgcolor=BG_INPUT,
            border=ft.Border.all(1, ACCENT_TEAL),
            content=official_def_text,
        )

        ai_eval_container = ft.Container(
            key=f"def_eval_box_{curr_idx}",
            visible=bool(eval_res),
            padding=14,
            border_radius=12,
            bgcolor=BG_INPUT,
            border=ft.Border.all(
                1,
                ACCENT_TEAL if eval_res and eval_res.get("score", 0) >= self.error_save_threshold else ACCENT_CORAL,
            ),
            content=ft.Column(spacing=6, controls=[]),
        )

        def populate_def_eval(ev_data):
            if not ev_data:
                return
            sc = int(ev_data.get("score", 1))
            passed = sc >= self.error_save_threshold
            c_color = ACCENT_TEAL if passed else ACCENT_CORAL
            status_tag = (
                f"✅ Dominada (Nota ≥ {self.error_save_threshold})"
                if passed
                else f"❌ Guardada en Test de Errores (Nota < {self.error_save_threshold})"
            )
            ai_eval_container.border = ft.Border.all(1, c_color)
            feedback_final = ev_data.get("feedback", "")
            if not passed:
                feedback_final = f"{_get_frase_fallo()}\n\n{feedback_final}"
            ai_eval_container.content.controls = [
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Text(f"🤖 Nota IA: {sc} / 10", size=15, weight=ft.FontWeight.BOLD, color=c_color),
                        ft.Text(status_tag, size=11.5, weight=ft.FontWeight.W_600, color=c_color),
                    ],
                ),
                ft.Text(feedback_final, size=12.5, color=TEXT_WHITE, selectable=True),
            ]

        if eval_res:
            populate_def_eval(eval_res)

        tf = ft.TextField(
            value=saved_def,
            multiline=True,
            min_lines=3,
            max_lines=5,
            hint_text=f"Escribe aquí la definición completa de «{capitalized_term}»...",
            bgcolor=BG_INPUT,
            border_color=BORDER_SUBTLE,
            focused_border_color=ACCENT_PURPLE,
            border_radius=12,
            color=TEXT_WHITE,
            text_size=13,
            on_change=lambda e, i=curr_idx: self.user_def_answers.update({i: e.control.value}),
        )

        scrollable_def_body = ft.Column(
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            spacing=12,
            controls=[],
        )

        def toggle_official_def(e):
            official_def_container.visible = not official_def_container.visible
            self.page.update()
            if official_def_container.visible:
                try:
                    scrollable_def_body.scroll_to(key="def_model_box", duration=250)
                except Exception:
                    pass

        def mark_definition(is_ok: bool):
            if not is_ok:
                errors = load_errors()
                q_label = f"Define el concepto: {capitalized_term}"
                err_item = {
                    "type": "Definición",
                    "topic": d_item.get("topic", ""),
                    "question": q_label,
                    "model_answer": d_item.get("definition", ""),
                }
                if not any(x.get("question") == q_label for x in errors):
                    errors.append(err_item)
                    save_errors(errors)
                    self.refresh_right_error_feed()
                self.notify("Concepto guardado en el Test de Errores.", ACCENT_CORAL)
            else:
                self.notify("¡Definición dominada correctamente!", ACCENT_TEAL)
            self.page.update()

        def evaluate_def_with_ai(e):
            ans_text = (tf.value or "").strip()
            self.user_def_answers[curr_idx] = ans_text
            if not ans_text:
                self.notify("Escribe tu definición antes de corregir con IA.", ACCENT_AMBER)
                return

            api_key = self._get_api_key()
            if not api_key:
                self.notify("Falta la API Key de Gemini.", ACCENT_CORAL)
                return

            btn = e.control
            btn.disabled = True
            btn.content = "⏳ Corrigiendo..."
            self.page.update()

            async def _async_eval_def():
                try:
                    def _sync_call():
                        client = genai.Client(api_key=api_key)
                        prompt = f"""
Actúa como un profesor riguroso corrigiendo una definición de examen.
Evalúa la definición escrita por el alumno para el concepto «{capitalized_term}» comparándola con la Definición Oficial del temario y asígnale una nota entera del 1 al 10 (1 = totalmente errónea o vacía de contenido, 10 = excelente y precisa).

CONCEPTO A DEFINIR:
{capitalized_term}

DEFINICIÓN OFICIAL DEL TEMARIO:
{d_item.get('definition', '')}

DEFINICIÓN DEL ALUMNO:
{ans_text}

Devuelve un JSON con 'score' (entero de 1 a 10) y 'feedback' (explicación breve de aciertos y qué faltó).
"""
                        return self.call_gemini_with_retry(client, self.selected_model, prompt, SingleWrittenEvaluation)

                    res_eval = await asyncio.to_thread(_sync_call)
                    sc = max(1, min(10, int(round(float(res_eval.get("score", 1))))))
                    res_eval["score"] = sc
                    self.def_evaluations[curr_idx] = res_eval

                    populate_def_eval(res_eval)
                    ai_eval_container.visible = True

                    if sc < self.error_save_threshold:
                        errors = load_errors()
                        q_label = f"Define el concepto: {capitalized_term}"
                        err_item = {
                            "type": "Definición",
                            "topic": d_item.get("topic", ""),
                            "question": q_label,
                            "model_answer": d_item.get("definition", ""),
                        }
                        if not any(x.get("question") == q_label for x in errors):
                            errors.append(err_item)
                            save_errors(errors)
                            self.refresh_right_error_feed()
                        self.notify(f"Nota IA: {sc}/10 (< {self.error_save_threshold}). Guardada en Test de Errores.", ACCENT_CORAL)
                    else:
                        self.notify(f"¡Nota IA: {sc}/10! Supera el corte ({self.error_save_threshold}/10).", ACCENT_TEAL)

                except Exception as ex:
                    traceback.print_exc()
                    self.notify(f"Error al corregir con IA: {ex}", ACCENT_CORAL)
                finally:
                    btn.disabled = False
                    btn.content = "🤖 Corregir con IA"
                    self.page.update()
                    try:
                        scrollable_def_body.scroll_to(key=f"def_eval_box_{curr_idx}", duration=250)
                    except Exception:
                        pass

            self.page.run_task(_async_eval_def)

        def go_prev(e):
            if self.test_indices["Definiciones"] > 0:
                self.test_indices["Definiciones"] -= 1
                self.render_center_content()
                self.page.update()

        def go_next(e):
            if self.test_indices["Definiciones"] < len(definitions) - 1:
                self.test_indices["Definiciones"] += 1
                self.render_center_content()
                self.page.update()
            else:
                self.test_finished["Definiciones"] = True
                self.render_center_content()
                self.page.update()

        nav_bar = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Text("Definiciones", size=17, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                        ft.Button(
                            content="🔄 +10 IA",
                            tooltip="Extraer 10 nuevos conceptos clave del PDF con IA",
                            bgcolor="#252733",
                            color=TEXT_WHITE,
                            height=34,
                            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
                            on_click=lambda e: self.regenerate_more_questions("Definiciones", e.control),
                        ),
                    ],
                ),
                ft.Row(
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Button(
                            content="Anterior",
                            icon=ft.Icons.ARROW_BACK_ROUNDED,
                            bgcolor="#232733",
                            color=TEXT_WHITE,
                            height=36,
                            disabled=curr_idx == 0,
                            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
                            on_click=go_prev,
                        ),
                        ft.Text(f"Concepto {curr_idx + 1} de {len(definitions)}", size=13.5, weight=ft.FontWeight.BOLD, color=ACCENT_PURPLE),
                        ft.Button(
                            content="Siguiente ➔" if curr_idx < len(definitions) - 1 else "Finalizar 🏆",
                            icon=ft.Icons.ARROW_FORWARD_ROUNDED if curr_idx < len(definitions) - 1 else ft.Icons.GRADING_ROUNDED,
                            bgcolor=ACCENT_PURPLE,
                            color=TEXT_WHITE,
                            height=36,
                            style=ft.ButtonStyle(
                                shape=ft.RoundedRectangleBorder(radius=10),
                                text_style=ft.TextStyle(weight=ft.FontWeight.BOLD),
                            ),
                            on_click=go_next,
                        ),
                    ],
                ),
            ],
        )

        scrollable_def_body.controls = [
            ft.Text(f"Bloque / Tema: {d_item.get('topic', '')}", size=11.5, color=TEXT_MUTED),
            ft.Container(
                padding=ft.Padding.symmetric(horizontal=16, vertical=12),
                border_radius=14,
                bgcolor="#151821",
                border=ft.Border.all(1, "#508B5CF6"),
                content=ft.Row(
                    spacing=12,
                    controls=[
                        ft.Icon(ft.Icons.AUTO_STORIES_ROUNDED, color=ACCENT_PURPLE, size=22),
                        ft.Column(
                            spacing=2,
                            expand=True,
                            controls=[
                                ft.Text("CONCEPTO CLAVE A DEFINIR (Primera letra mayúscula):", size=10.5, weight=ft.FontWeight.BOLD, color=ACCENT_PURPLE),
                                ft.Text(capitalized_term, size=17, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                                ft.Text(
                                    d_item.get("importance_reason", ""),
                                    size=11,
                                    color=TEXT_MUTED,
                                    italic=True,
                                ) if d_item.get("importance_reason") else ft.Text("", size=1),
                            ],
                        ),
                    ],
                ),
            ),
            tf,
            ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(
                        spacing=8,
                        controls=[
                            ft.Button(
                                content="Ver Definición Oficial",
                                icon=ft.Icons.VISIBILITY_ROUNDED,
                                bgcolor=ACCENT_BLUE,
                                color=TEXT_WHITE,
                                height=38,
                                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
                                on_click=toggle_official_def,
                            ),
                            ft.Button(
                                content="🤖 Corregir con IA",
                                bgcolor=ACCENT_TEAL,
                                color=BG_OUTER,
                                height=38,
                                style=ft.ButtonStyle(
                                    shape=ft.RoundedRectangleBorder(radius=10),
                                    text_style=ft.TextStyle(weight=ft.FontWeight.BOLD),
                                ),
                                on_click=evaluate_def_with_ai,
                            ),
                        ],
                    ),
                    ft.Row(
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            self._build_threshold_selector(ACCENT_PURPLE),
                            ft.OutlinedButton(
                                content="Sabida ✅",
                                style=ft.ButtonStyle(color=ACCENT_TEAL, shape=ft.RoundedRectangleBorder(radius=10)),
                                on_click=lambda _: mark_definition(True),
                            ),
                            ft.OutlinedButton(
                                content="Fallada ❌",
                                style=ft.ButtonStyle(color=ACCENT_CORAL, shape=ft.RoundedRectangleBorder(radius=10)),
                                on_click=lambda _: mark_definition(False),
                            ),
                        ],
                    ),
                ],
            ),
            ai_eval_container,
            official_def_container,
        ]

        return self.create_glow_card(
            theme_idx=4,
            padding=18,
            expand=True,
            content=ft.Column(
                expand=True,
                spacing=10,
                controls=[
                    nav_bar,
                    ft.Divider(color=BORDER_SUBTLE, height=1),
                    scrollable_def_body,
                ],
            ),
        )

    # -------------------- VISTA 5: MÓDULO DE CASOS PRÁCTICOS DE LA INDUSTRIA + IA --------------------

    def build_single_case_view(self):
        cases = self.exams_data.get("industrial_cases", [])

        if not cases:
            return ft.Column(
                expand=True,
                controls=[
                    ft.Text("Casos Prácticos de la Industria", size=20, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                    ft.Container(
                        expand=True,
                        alignment=ft.Alignment(0.0, 0.0),
                        content=ft.Column(
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=14,
                            controls=[
                                ft.Icon(ft.Icons.BUSINESS_CENTER_ROUNDED, size=56, color=ACCENT_BLUE),
                                ft.Text(
                                    "Aún no hay casos prácticos generados. Genera el examen en el Dashboard con un PDF.",
                                    size=15,
                                    color=TEXT_MUTED,
                                    text_align=ft.TextAlign.CENTER,
                                ),
                                ft.Button(
                                    content="Ir al Dashboard",
                                    bgcolor=ACCENT_BLUE,
                                    color=TEXT_WHITE,
                                    on_click=lambda _: self.switch_tab(0),
                                ),
                            ],
                        ),
                    ),
                ],
            )

        curr_idx = self.test_indices["Casos Prácticos"]
        if curr_idx >= len(cases):
            curr_idx = len(cases) - 1
            self.test_indices["Casos Prácticos"] = curr_idx

        c_item = cases[curr_idx]
        saved_ans = self.user_case_answers.get(curr_idx, "")
        eval_res = self.case_evaluations.get(curr_idx)

        eval_container = ft.Container(
            key=f"eval_box_{curr_idx}",
            visible=bool(eval_res),
            padding=16,
            border_radius=14,
            bgcolor=BG_INPUT,
            border=ft.Border.all(1, ACCENT_TEAL if eval_res and eval_res.get("score", 0) >= 5 else ACCENT_CORAL),
            content=ft.Column(spacing=8, controls=[]),
        )

        def populate_eval_content(ev_data):
            if not ev_data:
                return
            sc = ev_data.get("score", 0)
            c_color = ACCENT_TEAL if sc >= 5 else ACCENT_CORAL
            eval_container.border = ft.Border.all(1, c_color)
            blind = ev_data.get('blind_spots', '')
            if sc < 5:
                blind = f"{_get_frase_fallo()}\n\n{blind}"
            eval_container.content.controls = [
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Text(f"🤖 Calificación de la IA: {sc:.1f} / 10", size=16, weight=ft.FontWeight.BOLD, color=c_color),
                        ft.Text("Evaluado con rúbrica oficial", size=11, color=TEXT_MUTED),
                    ],
                ),
                ft.Divider(color=BORDER_SUBTLE, height=1),
                ft.Text(f"✅ Puntos Fuertes (Aciertos de aplicación):\n{ev_data.get('strengths', '')}", size=12.5, color=TEXT_WHITE),
                ft.Text(f"⚠️ Puntos Ciegos / Omisiones:\n{blind}", size=12.5, color=ACCENT_AMBER),
                ft.Text(f"💡 Resolución paso a paso esperada:\n{ev_data.get('expert_model', '')}", size=12.5, color=ACCENT_TEAL),
            ]

        if eval_res:
            populate_eval_content(eval_res)

        tf = ft.TextField(
            value=saved_ans,
            multiline=True,
            min_lines=6,
            max_lines=9,
            hint_text="Redacta tu solución al problema aplicando los apuntes y justificando tus decisiones técnicas...",
            bgcolor=BG_INPUT,
            border_color=BORDER_SUBTLE,
            focused_border_color=ACCENT_BLUE,
            border_radius=12,
            color=TEXT_WHITE,
            text_size=13,
            on_change=lambda e, i=curr_idx: self.user_case_answers.update({i: e.control.value}),
        )

        scrollable_case_body = ft.Column(
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            spacing=12,
            controls=[],
        )

        def evaluate_case_with_ai(e):
            ans_text = (tf.value or "").strip()
            self.user_case_answers[curr_idx] = ans_text
            if not ans_text:
                self.notify("Escribe tu solución antes de corregir con IA.", ACCENT_AMBER)
                return

            api_key = self._get_api_key()
            if not api_key:
                self.notify("Falta la API Key de Gemini.", ACCENT_CORAL)
                return

            btn = e.control
            btn.disabled = True
            btn.content = "⏳ Evaluando..."
            self.notify("⏳ Analizando tu caso práctico con Gemini...", ACCENT_BLUE)
            self.page.update()

            async def _async_eval_case():
                try:
                    def _sync_call():
                        client = genai.Client(api_key=api_key)
                        prompt = f"""
Actúa como un tribunal de oposición experto y profesor senior de la industria.
Evalúa la respuesta del alumno al siguiente caso práctico real contrastándola con el temario oficial.
Tipo de caso: {c_item.get('approach_type', '')}
CASO PRÁCTICO:
Título: {c_item.get('title')}
Técnica a aplicar: {c_item.get('technique', '')}
Escenario: {c_item.get('scenario')}
Reto: {c_item.get('challenge')}
Palabras clave de la rúbrica esperadas: {', '.join(c_item.get('rubric_keywords', []))}
Resolución paso a paso esperada: {c_item.get('step_by_step_solution', '')}

RESPUESTA DEL ALUMNO:
{ans_text}

Devuelve una evaluación estricta en JSON según el esquema CaseEvaluation (nota de 0 a 10, puntos fuertes, puntos ciegos y solución experta).
"""
                        return self.call_gemini_with_retry(client, self.selected_model, prompt, CaseEvaluation)

                    res_eval = await asyncio.to_thread(_sync_call)
                    self.case_evaluations[curr_idx] = res_eval
                    populate_eval_content(res_eval)
                    eval_container.visible = True

                    if res_eval.get("score", 0) < 5:
                        errors = load_errors()
                        err_item = {
                            "type": "Caso Práctico",
                            "topic": c_item.get("topic", ""),
                            "question": f"Caso: {c_item.get('title')}",
                            "model_answer": c_item.get("step_by_step_solution", ""),
                        }
                        if not any(x.get("question") == err_item["question"] for x in errors):
                            errors.append(err_item)
                            save_errors(errors)
                            self.refresh_right_error_feed()

                    self.notify(f"¡Caso evaluado! Nota: {res_eval.get('score', 0):.1f}/10", ACCENT_TEAL)
                except Exception as ex:
                    traceback.print_exc()
                    self.notify(f"Error al evaluar con IA: {ex}", ACCENT_CORAL)
                finally:
                    btn.disabled = False
                    btn.content = "🤖 Evaluar con Corrector IA"
                    self.page.update()
                    try:
                        scrollable_case_body.scroll_to(key=f"eval_box_{curr_idx}", duration=250)
                    except Exception:
                        pass

            self.page.run_task(_async_eval_case)

        def go_prev(e):
            if self.test_indices["Casos Prácticos"] > 0:
                self.test_indices["Casos Prácticos"] -= 1
                self.render_center_content()
                self.page.update()

        def go_next(e):
            if self.test_indices["Casos Prácticos"] < len(cases) - 1:
                self.test_indices["Casos Prácticos"] += 1
                self.render_center_content()
                self.page.update()
            else:
                self.test_finished["Casos Prácticos"] = True
                self.render_center_content()
                self.page.update()

        nav_bar = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Text("Casos Prácticos Industria", size=17, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                        ft.Button(
                            content="🔄 +3 Casos IA",
                            tooltip="Generar 3 nuevos casos reales del PDF",
                            bgcolor="#252733",
                            color=TEXT_WHITE,
                            height=34,
                            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
                            on_click=lambda e: self.regenerate_more_questions("Casos Prácticos", e.control),
                        ),
                    ],
                ),
                ft.Row(
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Button(
                            content="Anterior",
                            icon=ft.Icons.ARROW_BACK_ROUNDED,
                            bgcolor="#232733",
                            color=TEXT_WHITE,
                            height=36,
                            disabled=curr_idx == 0,
                            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
                            on_click=go_prev,
                        ),
                        ft.Text(f"Caso {curr_idx + 1} de {len(cases)}", size=13.5, weight=ft.FontWeight.BOLD, color=ACCENT_BLUE),
                        ft.Button(
                            content="Siguiente ➔" if curr_idx < len(cases) - 1 else "Finalizar Casos 🏆",
                            icon=ft.Icons.ARROW_FORWARD_ROUNDED if curr_idx < len(cases) - 1 else ft.Icons.GRADING_ROUNDED,
                            bgcolor=ACCENT_BLUE,
                            color=TEXT_WHITE,
                            height=36,
                            style=ft.ButtonStyle(
                                shape=ft.RoundedRectangleBorder(radius=10),
                                text_style=ft.TextStyle(weight=ft.FontWeight.BOLD),
                            ),
                            on_click=go_next,
                        ),
                    ],
                ),
            ],
        )

        scrollable_case_body.controls = [
            ft.Text(f"Bloque: {c_item.get('topic', '')}", size=11.5, color=TEXT_MUTED),
            ft.Container(
                padding=16,
                border_radius=14,
                bgcolor="#151922",
                border=ft.Border.all(1, "#503B82F6"),
                content=ft.Column(
                    spacing=8,
                    controls=[
                        ft.Row(
                            spacing=8,
                            controls=[
                                ft.Icon(ft.Icons.BUSINESS_ROUNDED, color=ACCENT_BLUE, size=20),
                                ft.Text(c_item.get("title", ""), size=16, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                            ],
                        ),
                        ft.Text(
                            f"🔧 TÉCNICA A APLICAR: {c_item.get('technique', '')}"
                            + (f"   •   Tipo: {c_item.get('approach_type', '')}" if c_item.get('approach_type') else ""),
                            size=13,
                            weight=ft.FontWeight.W_600,
                            color=ACCENT_PURPLE,
                        ),
                        ft.Text(f"🏢 ESCENARIO:\n{c_item.get('scenario', '')}", size=13, color="#D1D5DB"),
                        ft.Text(f"🎯 RETO A RESOLVER:\n{c_item.get('challenge', '')}", size=13, weight=ft.FontWeight.W_600, color=ACCENT_AMBER),
                    ],
                ),
            ),
            tf,
            ft.Row(
                alignment=ft.MainAxisAlignment.END,
                controls=[
                    ft.Button(
                        content="🤖 Evaluar con Corrector IA",
                        icon=ft.Icons.PSYCHOLOGY_ROUNDED,
                        bgcolor=ACCENT_TEAL,
                        color=BG_OUTER,
                        height=42,
                        style=ft.ButtonStyle(
                            shape=ft.RoundedRectangleBorder(radius=12),
                            text_style=ft.TextStyle(weight=ft.FontWeight.BOLD),
                        ),
                        on_click=evaluate_case_with_ai,
                    ),
                ],
            ),
            eval_container,
        ]

        return self.create_glow_card(
            theme_idx=3,
            padding=18,
            expand=True,
            content=ft.Column(
                expand=True,
                spacing=10,
                controls=[
                    nav_bar,
                    ft.Divider(color=BORDER_SUBTLE, height=1),
                    scrollable_case_body,
                ],
            ),
        )

    # -------------------- VISTA 6: EXAMINAR — SIMULACRO OFICIAL INDEPENDIENTE (15 MIN) --------------------

    def start_exam_mode(self, e=None):
        if self.exam_is_generating:
            return

        api_key = self._get_api_key()
        selected_model = (self.selected_model or "").strip()

        if not api_key:
            self.notify("Introduce tu API Key de Google Gemini.", ACCENT_CORAL)
            return
        if not self.pdf_paths and not self.pdf_text:
            self.notify("Selecciona uno o varios PDFs con el temario primero en el Dashboard.", ACCENT_CORAL)
            return

        self.exam_is_generating = True
        self.exam_mode_active = False
        self.exam_timer_started = False
        self.exam_finished = False
        self.exam_results_data = None
        self.current_nav_index = 6
        self.update_sidebar()
        self.render_center_content()
        self.page.update()
        self.notify("⏳ Generando examen independiente con IA...", ACCENT_TEAL)

        async def _async_generate_independent_exam():
            try:
                def _sync_work():
                    if not self.pdf_text and self.pdf_paths:
                        self._extract_pdf_text()

                    client = genai.Client(api_key=api_key)
                    excluded = self._build_exclusion_prompt()

                    prompt_exam = f"""
Genera un EXAMEN OFICIAL COMPLETO E INDEPENDIENTE a partir del temario, con un nivel de exigencia alto (un punto más difícil y riguroso que los cuestionarios de práctica habituales) y evaluando conceptos distintos a los ya preguntados.

ESTRUCTURA EXACTA OBLIGATORIA DEL EXAMEN:
1. "medium_mcq": Exactamente 2 preguntas tipo test de nivel medio-alto (comprensión profunda y relación de conceptos clave).
2. "hard_mcq": Exactamente 2 preguntas tipo test de nivel difícil/experto.
{self._hard_difficulty_instructions()}
3. "short_answers": Exactamente 2 preguntas cortas de desarrollo que exijan razonar, explicar mecanismos o relacionar partes del temario con precisión.
4. "definitions": Exactamente 2 términos o conceptos clave COMPLETOS del temario para definir.
{self._definitions_prompt_rules()}
5. "industrial_case": Exactamente 1 caso práctico realista y completo.
{self._cases_prompt_rules()}

{self._common_rules(excluded)}

TEMARIO:
{self.pdf_text}
"""
                    return self.call_gemini_with_retry(client, selected_model, prompt_exam, OfficialExamPackage)

                res_exam = await asyncio.to_thread(_sync_work)

                med_list = self._postprocess_mcq(res_exam.get("medium_mcq", []), ignore_seen=True)[:2]
                hard_list = self._postprocess_mcq(res_exam.get("hard_mcq", []), ignore_seen=True)[:2]
                short_list = res_exam.get("short_answers", [])[:2]
                def_list = res_exam.get("definitions", [])[:2]
                for d in def_list:
                    d["term"] = _format_capitalized_term(d.get("term", ""))
                case_obj = res_exam.get("industrial_case")

                self._register_topics(med_list, "Examen-Medio")
                self._register_topics(hard_list, "Examen-Difícil")
                self._register_topics(short_list, "Examen-Corta")
                self._register_topics(def_list, "Examen-Definición")
                if case_obj:
                    self._register_topics([case_obj], "Examen-Caso")

                selected_items = []
                for item in med_list:
                    selected_items.append({"type": "Test Medio", "data": item})
                for item in hard_list:
                    selected_items.append({"type": "Test Difícil", "data": item})
                for item in short_list:
                    selected_items.append({"type": "Pregunta Corta", "data": item})
                for item in def_list:
                    selected_items.append({"type": "Definición", "data": item})
                if case_obj:
                    selected_items.append({"type": "Caso Práctico", "data": case_obj})

                self.exam_questions = selected_items
                self.exam_user_answers = {}
                self.exam_time_left = 15 * 60
                self.exam_finished = False
                self.exam_results_data = None
                self.exam_is_generating = False
                self.exam_mode_active = True
                self.exam_timer_started = False

                self.render_center_content()
                self.page.update()
                self.notify("✅ Simulacro oficial generado. Pulsa 'Iniciar Tiempo' cuando estés listo.", ACCENT_TEAL)

            except Exception as ex:
                traceback.print_exc()
                self.exam_is_generating = False
                self.exam_mode_active = False
                self.notify(f"Error generando el examen oficial: {ex}", ACCENT_CORAL)
                self.render_center_content()
                self.page.update()

        self.page.run_task(_async_generate_independent_exam)

    def start_exam_timer(self, e=None):
        if self.exam_timer_started or self.exam_finished or not self.exam_mode_active:
            return
        self.exam_timer_started = True
        self.exam_timer_task = self.page.run_task(self._exam_timer_ticker)
        self.notify("⏱️ ¡Tiempo iniciado! Dispones de 15 minutos.", ACCENT_TEAL)
        self.render_center_content()
        self.page.update()

    async def _exam_timer_ticker(self):
        while self.exam_mode_active and not self.exam_finished and self.exam_time_left > 0:
            await asyncio.sleep(1)
            self.exam_time_left -= 1
            try:
                self.exam_timer_label.value = self._format_time(self.exam_time_left)
                if self.exam_time_left <= 60:
                    self.exam_timer_label.color = ACCENT_CORAL
                elif self.exam_time_left <= 180:
                    self.exam_timer_label.color = ACCENT_AMBER
                self.page.update()
            except Exception:
                pass

        if self.exam_mode_active and not self.exam_finished and self.exam_time_left <= 0:
            self.finish_exam_session()

    def _format_time(self, seconds: int) -> str:
        m = seconds // 60
        s = seconds % 60
        return f"{m:02d}:{s:02d}"

    def _select_exam_option(self, group_ref: dict, idx: int, letter_val: str):
        grp = group_ref.get("group")
        if grp is not None:
            grp.value = letter_val
        self.exam_user_answers[idx] = letter_val
        self.page.update()

    def finish_exam_session(self, e=None):
        if self.exam_finished:
            return
        self.exam_finished = True
        self.exam_mode_active = False

        if self.exam_timer_task:
            try:
                self.exam_timer_task.cancel()
            except Exception:
                pass

        self.notify("⏳ Tiempo agotado o examen entregado. Corrigiendo todo el examen con IA...", ACCENT_BLUE)
        self.render_center_content()
        self.page.update()

        async def _async_grade_exam():
            try:
                def _sync_grade():
                    api_key = self._get_api_key()
                    client = genai.Client(api_key=api_key) if api_key else None

                    test_correct = 0
                    test_total = 0
                    written_score_sum = 0.0
                    written_total = 0
                    details = [None] * len(self.exam_questions)
                    written_batch_payload = []

                    errors = load_errors()

                    for idx, ex_item in enumerate(self.exam_questions):
                        q_type = ex_item["type"]
                        q_data = ex_item["data"]
                        user_ans = self.exam_user_answers.get(idx, "")

                        if q_type in ("Test Medio", "Test Difícil"):
                            test_total += 1
                            corr_let = str(q_data.get("correct_letter", "")).strip().upper()
                            chosen = str(user_ans).strip().upper()
                            is_ok = chosen == corr_let
                            opts = q_data.get("options", [])
                            try:
                                corr_txt = opts[LETTERS.index(corr_let)]
                            except Exception:
                                corr_txt = ""

                            if is_ok:
                                test_correct += 1
                            else:
                                err_item = {
                                    "type": q_type,
                                    "topic": q_data.get("topic", ""),
                                    "question": q_data.get("question", ""),
                                    "options": list(opts),
                                    "correct_letter": corr_let,
                                    "correct": f"{corr_let}) {corr_txt}",
                                    "explanation": q_data.get("explanation", ""),
                                }
                                if not any(x.get("question") == err_item["question"] for x in errors):
                                    errors.append(err_item)

                            fb = q_data.get("explanation", "")
                            if not is_ok:
                                fb = f"{_get_frase_fallo()}\n\n{fb}"
                            details[idx] = {
                                "type": q_type,
                                "question": q_data.get("question", ""),
                                "user_answer": chosen if chosen else "(En blanco)",
                                "correct_answer": f"{corr_let}) {corr_txt}",
                                "is_correct": is_ok,
                                "feedback": fb,
                            }
                        else:
                            written_total += 1
                            if q_type == "Pregunta Corta":
                                q_title = q_data.get("question", "")
                                expected = q_data.get("model_answer", "")
                            elif q_type == "Definición":
                                q_title = f"Define: {_format_capitalized_term(q_data.get('term', ''))}"
                                expected = q_data.get("definition", "")
                            else:
                                q_title = f"{q_data.get('title', '')} — {q_data.get('challenge', '')}"
                                expected = (
                                    f"Técnica/Conceptos: {q_data.get('technique', '')}. "
                                    f"Keywords: {', '.join(q_data.get('rubric_keywords', []))}. "
                                    f"Solución esperada: {q_data.get('step_by_step_solution', '')}"
                                )

                            written_batch_payload.append({
                                "index": idx,
                                "type": q_type,
                                "question_or_prompt": q_title,
                                "expected_model_answer": expected,
                                "student_answer": str(user_ans).strip(),
                                "raw_data": q_data,
                            })

                    ai_eval_map = {}
                    non_empty_written = [w for w in written_batch_payload if w["student_answer"]]
                    if client and non_empty_written:
                        try:
                            items_desc = "\n\n".join(
                                f"ÍNDICE: {w['index']}\n"
                                f"TIPO: {w['type']}\n"
                                f"ENUNCIADO: {w['question_or_prompt']}\n"
                                f"SOLUCIÓN OFICIAL DEL TEMARIO: {w['expected_model_answer']}\n"
                                f"RESPUESTA DEL ALUMNO: {w['student_answer']}"
                                for w in non_empty_written
                            )
                            prompt_batch = f"""
Actúa como un tribunal examinador riguroso. Corrige y puntúa de 0 a 10 cada una de las siguientes respuestas escritas del alumno (Preguntas Cortas, Definiciones y Caso Práctico) comparándolas estrictamente con la solución oficial del temario.
Devuelve para cada elemento su 'index' exacto, su 'score' (0 a 10) y un 'feedback' claro y directo indicando qué está bien y qué ha faltado o fallado.

ELEMENTOS A CORREGIR:
{items_desc}
"""
                            batch_res = self.call_gemini_with_retry(
                                client,
                                self.selected_model,
                                prompt_batch,
                                BatchExamEvaluation,
                            )
                            for ev in batch_res.get("evaluations", []):
                                ai_eval_map[int(ev.get("index", -1))] = ev
                        except Exception:
                            traceback.print_exc()

                    for w in written_batch_payload:
                        idx = w["index"]
                        q_type = w["type"]
                        q_data = w["raw_data"]
                        stu_ans = w["student_answer"]

                        if not stu_ans:
                            score_item = 0.0
                            fb_text = "Pregunta dejada en blanco."
                        elif idx in ai_eval_map:
                            score_item = float(ai_eval_map[idx].get("score", 0.0))
                            fb_text = ai_eval_map[idx].get("feedback", "Evaluado por IA.")
                        else:
                            score_item = 5.0
                            fb_text = f"Solución de referencia: {w['expected_model_answer']}"

                        written_score_sum += score_item

                        if score_item < self.error_save_threshold:
                            if q_type == "Caso Práctico":
                                err_q = f"Caso: {q_data.get('title', '')}"
                                err_ans = q_data.get("step_by_step_solution", "")
                            elif q_type == "Definición":
                                err_q = f"Define el concepto: {_format_capitalized_term(q_data.get('term', ''))}"
                                err_ans = q_data.get("definition", "")
                            else:
                                err_q = q_data.get("question", "")
                                err_ans = q_data.get("model_answer", "")

                            err_item = {
                                "type": q_type,
                                "topic": q_data.get("topic", ""),
                                "question": err_q,
                                "model_answer": err_ans,
                            }
                            if not any(x.get("question") == err_item["question"] for x in errors):
                                errors.append(err_item)

                        if score_item < 5.0 and stu_ans:
                            fb_text = f"{_get_frase_fallo()}\n\n{fb_text}"

                        details[idx] = {
                            "type": q_type,
                            "question": w["question_or_prompt"],
                            "user_answer": stu_ans if stu_ans else "(En blanco)",
                            "score": score_item,
                            "is_correct": score_item >= 5.0,
                            "feedback": f"[Nota IA: {score_item:.1f}/10] {fb_text}",
                        }

                    save_errors(errors)

                    test_grade = (test_correct / test_total * 10) if test_total > 0 else 0.0
                    written_grade = (written_score_sum / written_total) if written_total > 0 else 0.0
                    final_grade = (test_grade * 0.5) + (written_grade * 0.5)

                    return {
                        "final_grade": final_grade,
                        "test_grade": test_grade,
                        "written_grade": written_grade,
                        "details": [d for d in details if d is not None],
                    }

                self.exam_results_data = await asyncio.to_thread(_sync_grade)
                self.refresh_right_error_feed()
            except Exception as ex:
                traceback.print_exc()
                self.notify(f"Error evaluando simulacro: {ex}", ACCENT_CORAL)

            self.render_center_content()
            self.page.update()

        self.page.run_task(_async_grade_exam)

    def build_exam_mode_view(self):
        if self.exam_is_generating:
            return ft.Container(
                expand=True,
                alignment=ft.Alignment(0.0, 0.0),
                content=ft.Column(
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=16,
                    controls=[
                        ft.ProgressRing(color=ACCENT_TEAL),
                        ft.Text("Generando examen oficial con IA...", size=16, weight=ft.FontWeight.BOLD, color=ACCENT_TEAL),
                    ],
                ),
            )

        if not self.exam_mode_active and not self.exam_finished:
            # Pantalla de presentación del Simulacro
            return ft.Column(
                expand=True,
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=20,
                controls=[
                    ft.Icon(ft.Icons.TIMER_ROUNDED, size=64, color=ACCENT_TEAL),
                    ft.Text("Simulacro Oficial — EXAMINAR (15 min)", size=24, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                    ft.Text(
                        "Este modo simula un examen real:\n"
                        "• Cuenta atrás estricta de 15 minutos.\n"
                        "• Preguntas mezcladas (Test Medio, Difícil, Cortas, Definiciones y Caso Práctico).\n"
                        "• Sin correcciones parciales: responde todo y entrégalo al final.\n"
                        "• Corrección global ponderada e incorporación automática de fallos al Test de Errores.",
                        size=14,
                        color=TEXT_MUTED,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Button(
                        content="🚀 Comenzar Examen Ahora",
                        bgcolor=ACCENT_TEAL,
                        color=BG_OUTER,
                        height=48,
                        style=ft.ButtonStyle(
                            shape=ft.RoundedRectangleBorder(radius=16),
                            text_style=ft.TextStyle(weight=ft.FontWeight.BOLD, size=15),
                        ),
                        on_click=self.start_exam_mode,
                    ),
                ],
            )

        if self.exam_finished:
            res = self.exam_results_data
            if not res:
                return ft.Container(
                    expand=True,
                    alignment=ft.Alignment(0.0, 0.0),
                    content=ft.Text("Calculando calificación final del simulacro...", size=16, color=ACCENT_TEAL),
                )

            fg = res["final_grade"]
            is_ok = fg >= 5.0

            details_col = ft.Column(spacing=12, scroll=ft.ScrollMode.ALWAYS, expand=True)
            for d in res["details"]:
                det_card = ft.Container(
                    padding=14,
                    border_radius=12,
                    bgcolor=BG_INPUT,
                    border=ft.Border.all(1, ACCENT_TEAL if d.get("is_correct", d.get("score", 0) >= 5) else ACCENT_CORAL),
                    content=ft.Column(
                        spacing=4,
                        controls=[
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[
                                    ft.Text(f"[{d['type']}] {d['question'][:60]}...", size=12, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                                    ft.Text("Correcto ✅" if d.get("is_correct", d.get("score", 0) >= 5) else "Revisar ❌", size=12, weight=ft.FontWeight.BOLD, color=ACCENT_TEAL if d.get("is_correct", d.get("score", 0) >= 5) else ACCENT_CORAL),
                                ],
                            ),
                            ft.Text(f"Tu respuesta: {str(d['user_answer'])[:100]}", size=12, color=TEXT_MUTED),
                            ft.Text(f"Feedback / Explicación: {d['feedback']}", size=12, color=TEXT_WHITE),
                        ],
                    ),
                )
                details_col.controls.append(det_card)

            return ft.Column(
                expand=True,
                spacing=14,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Text("Resultados del Simulacro Oficial", size=20, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                            ft.Button(
                                content="🔄 Nuevo Simulacro",
                                bgcolor=ACCENT_TEAL,
                                color=BG_OUTER,
                                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
                                on_click=self.start_exam_mode,
                            ),
                        ],
                    ),
                    self.create_glow_card(
                        theme_idx=0,
                        padding=22,
                        content=ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_AROUND,
                            controls=[
                                ft.Column(
                                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                    controls=[
                                        ft.Text("NOTA FINAL", size=11, color=TEXT_MUTED),
                                        ft.Text(f"{fg:.1f} / 10", size=36, weight=ft.FontWeight.BOLD, color=ACCENT_TEAL if is_ok else ACCENT_CORAL),
                                    ],
                                ),
                                ft.Column(
                                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                    controls=[
                                        ft.Text("Bloque Test", size=11, color=TEXT_MUTED),
                                        ft.Text(f"{res['test_grade']:.1f}", size=24, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                                    ],
                                ),
                                ft.Column(
                                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                    controls=[
                                        ft.Text("Bloque Escrito / Práctico", size=11, color=TEXT_MUTED),
                                        ft.Text(f"{res['written_grade']:.1f}", size=24, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                                    ],
                                ),
                            ],
                        ),
                    ),
                    ft.Text("Desglose detallado de tus respuestas:", size=14, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                    details_col,
                ],
            )

        # Durante el Examen Activo
        self.exam_timer_label = ft.Text(self._format_time(self.exam_time_left), size=16, weight=ft.FontWeight.BOLD, color=ACCENT_TEAL)

        exam_body = ft.Column(spacing=20, scroll=ft.ScrollMode.ALWAYS, expand=True)

        for idx, ex_item in enumerate(self.exam_questions):
            q_type = ex_item["type"]
            q_data = ex_item["data"]
            saved_ans = self.exam_user_answers.get(idx, "")

            if q_type in ("Test Medio", "Test Difícil"):
                opts = q_data.get("options", [])
                radio_g_ref = {"group": None}
                radio_g = ft.RadioGroup(
                    value=saved_ans,
                    on_change=lambda e, i=idx: self.exam_user_answers.update({i: e.control.value}),
                    content=ft.Column(
                        spacing=4,
                        controls=[
                            ft.Container(
                                padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                                border_radius=12,
                                bgcolor=BG_INPUT,
                                border=ft.Border.all(1, BORDER_SUBTLE),
                                on_click=lambda e, l=let: self._select_exam_option(radio_g_ref, idx, l),
                                content=ft.Row(
                                    spacing=8,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    controls=[
                                        ft.Radio(value=let, fill_color=ACCENT_TEAL),
                                        ft.Text(
                                            f"{let}) {opt}",
                                            color=TEXT_WHITE,
                                            size=12.5,
                                            expand=True,
                                        ),
                                    ],
                                ),
                            )
                            for let, opt in zip(LETTERS, opts)
                        ],
                    ),
                )
                radio_g_ref["group"] = radio_g
                card_content = ft.Column(
                    spacing=8,
                    controls=[
                        ft.Text(f"Pregunta {idx + 1} • [{q_type}] (Tema: {q_data.get('topic', '')})", size=11.5, color=ACCENT_AMBER),
                        ft.Text(q_data.get("question", ""), size=14, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                        radio_g,
                    ],
                )
            elif q_type in ("Pregunta Corta", "Definición"):
                term_or_q = q_data.get("question", _format_capitalized_term(q_data.get("term", "")))
                tf = ft.TextField(
                    value=saved_ans,
                    multiline=True,
                    min_lines=2,
                    max_lines=3,
                    hint_text="Escribe tu respuesta aquí...",
                    bgcolor=BG_INPUT,
                    border_color=BORDER_SUBTLE,
                    focused_border_color=ACCENT_TEAL,
                    border_radius=10,
                    color=TEXT_WHITE,
                    text_size=12.5,
                    on_change=lambda e, i=idx: self.exam_user_answers.update({i: e.control.value}),
                )
                card_content = ft.Column(
                    spacing=8,
                    controls=[
                        ft.Text(f"Pregunta {idx + 1} • [{q_type}]", size=11.5, color=ACCENT_PURPLE),
                        ft.Text(term_or_q, size=14, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                        tf,
                    ],
                )
            else:
                # Caso Práctico
                tf = ft.TextField(
                    value=saved_ans,
                    multiline=True,
                    min_lines=4,
                    max_lines=6,
                    hint_text="Desarrolla tu solución al caso práctico...",
                    bgcolor=BG_INPUT,
                    border_color=BORDER_SUBTLE,
                    focused_border_color=ACCENT_BLUE,
                    border_radius=10,
                    color=TEXT_WHITE,
                    text_size=12.5,
                    on_change=lambda e, i=idx: self.exam_user_answers.update({i: e.control.value}),
                )
                card_content = ft.Column(
                    spacing=8,
                    controls=[
                        ft.Text(f"Pregunta {idx + 1} • [Caso Práctico]", size=11.5, color=ACCENT_BLUE),
                        ft.Text(q_data.get("title", ""), size=14, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                        ft.Text(
                            f"🔧 Técnica: {q_data.get('technique', '')}"
                            + (f"  •  Tipo: {q_data.get('approach_type', '')}" if q_data.get('approach_type') else ""),
                            size=12,
                            weight=ft.FontWeight.W_600,
                            color=ACCENT_PURPLE,
                        ),
                        ft.Text(q_data.get("scenario", ""), size=12, color=TEXT_MUTED),
                        ft.Text(q_data.get("challenge", ""), size=12.5, weight=ft.FontWeight.W_600, color=ACCENT_AMBER),
                        tf,
                    ],
                )

            exam_body.controls.append(
                ft.Container(
                    padding=16,
                    border_radius=16,
                    bgcolor=BG_CARD,
                    border=ft.Border.all(1, BORDER_SUBTLE),
                    content=card_content,
                )
            )

        top_exam_bar = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    spacing=10,
                    controls=[
                        ft.Icon(ft.Icons.TIMER, color=ACCENT_TEAL, size=22),
                        ft.Text("Tiempo Restante:", size=14, color=TEXT_MUTED),
                        self.exam_timer_label,
                        ft.Button(
                            content="▶️ Iniciar Tiempo",
                            bgcolor=ACCENT_AMBER,
                            color=BG_OUTER,
                            height=34,
                            visible=not self.exam_timer_started,
                            style=ft.ButtonStyle(
                                shape=ft.RoundedRectangleBorder(radius=10),
                                text_style=ft.TextStyle(weight=ft.FontWeight.BOLD, size=12.5),
                            ),
                            on_click=self.start_exam_timer,
                        ),
                    ],
                ),
                ft.Button(
                    content="📥 Entregar Examen y Calificar",
                    bgcolor=ACCENT_TEAL,
                    color=BG_OUTER,
                    height=38,
                    style=ft.ButtonStyle(
                        shape=ft.RoundedRectangleBorder(radius=12),
                        text_style=ft.TextStyle(weight=ft.FontWeight.BOLD),
                    ),
                    on_click=self.finish_exam_session,
                ),
            ],
        )

        return ft.Column(
            expand=True,
            spacing=14,
            controls=[
                top_exam_bar,
                ft.Divider(color=BORDER_SUBTLE, height=1),
                exam_body,
            ],
        )

    # -------------------- VISTA 7: TEST DE ERRORES INTERACTIVO --------------------

    def _prepare_error_quiz_item(self, err: dict, all_errors: list) -> dict:
        q_copy = dict(err)
        if "options" in q_copy and len(q_copy.get("options", [])) == 4 and q_copy.get("correct_letter") in LETTERS:
            return _shuffle_options(q_copy)

        if "correct" in q_copy and "options" not in q_copy:
            raw_corr = str(q_copy["correct"]).strip()
            if len(raw_corr) >= 3 and raw_corr[0].upper() in LETTERS and raw_corr[1] in ").:":
                corr_text = raw_corr[2:].strip()
            else:
                corr_text = raw_corr

            pool = []
            for k in ("medium_mcq", "hard_mcq"):
                for item in self.exams_data.get(k, []):
                    for o in item.get("options", []):
                        if o != corr_text and o not in pool:
                            pool.append(o)
            while len(pool) < 3:
                pool.append(f"Opción alternativa generada ({len(pool) + 1}).")

            distractors = random.sample(pool, 3)
            opts = [corr_text] + distractors
            q_copy["options"] = opts
            q_copy["correct_letter"] = "A"
            return _shuffle_options(q_copy)

        return q_copy

    def start_error_quiz_mode(self, e=None):
        errors = load_errors()
        if not errors:
            self.notify("No hay errores acumulados para iniciar un test.", ACCENT_TEAL)
            return

        prepared = [self._prepare_error_quiz_item(err, errors) for err in errors]
        random.shuffle(prepared)
        self.error_quiz_questions = prepared
        self.error_quiz_idx = 0
        self.error_quiz_user_answers = {}
        self.error_quiz_finished = False
        self.error_quiz_mode = True
        self.current_nav_index = 7
        self.update_sidebar()
        self.render_center_content()
        self.page.update()

    def build_errors_full_view(self):
        errors = load_errors()

        if self.error_quiz_mode:
            if self.error_quiz_finished:
                return self._build_error_quiz_score_view()
            return self._build_error_quiz_question_view()

        header = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Column(
                    spacing=2,
                    controls=[
                        ft.Text("Test de Errores & Historial", size=22, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                        ft.Text("Las respuestas están ocultas para permitirte examinarte como en un test normal", size=12, color=TEXT_MUTED),
                    ],
                ),
                ft.Row(
                    spacing=10,
                    controls=[
                        ft.Button(
                            content=f"🎯 Test de Errores ({len(errors)})",
                            icon=ft.Icons.PLAY_ARROW_ROUNDED,
                            bgcolor=ACCENT_TEAL,
                            color=BG_OUTER,
                            disabled=not bool(errors),
                            style=ft.ButtonStyle(
                                shape=ft.RoundedRectangleBorder(radius=12),
                                text_style=ft.TextStyle(weight=ft.FontWeight.BOLD),
                            ),
                            on_click=self.start_error_quiz_mode,
                        ),
                        ft.Button(
                            content="🗑️ Vaciar Todos",
                            bgcolor=ACCENT_CORAL,
                            color=TEXT_WHITE,
                            disabled=not bool(errors),
                            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=12)),
                            on_click=self.clear_all_errors,
                        ),
                    ],
                ),
            ],
        )

        if not errors:
            return ft.Column(
                expand=True,
                controls=[
                    header,
                    ft.Container(
                        expand=True,
                        alignment=ft.Alignment(0.0, 0.0),
                        content=ft.Column(
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=10,
                            controls=[
                                ft.Icon(ft.Icons.EMOJI_EVENTS_ROUNDED, size=60, color=ACCENT_TEAL),
                                ft.Text("🎉 ¡No tienes errores pendientes de repaso!", size=18, weight=ft.FontWeight.BOLD, color=ACCENT_TEAL),
                            ],
                        ),
                    ),
                ],
            )

        err_list = ft.Column(spacing=14, scroll=ft.ScrollMode.ALWAYS, expand=True)
        for idx, err in enumerate(errors):
            body_text = (
                f"Respuesta Correcta: {err['correct']}\n\nExplicación: {err.get('explanation', '')}"
                if "correct" in err
                else f"Respuesta Modelo: {err.get('model_answer', '')}"
            )
            solution_box = ft.Container(
                visible=False,
                padding=12,
                border_radius=12,
                bgcolor=BG_INPUT,
                border=ft.Border.all(1, BORDER_SUBTLE),
                content=ft.Text(body_text, size=13, color="#D1D5DB", selectable=True),
            )

            def toggle_sol(ev, box=solution_box):
                box.visible = not box.visible
                self.page.update()

            card = self.create_glow_card(
                theme_idx=2,
                padding=18,
                content=ft.Column(
                    spacing=8,
                    controls=[
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            controls=[
                                ft.Text(f"Error #{idx + 1} • {err.get('type', 'General')}", size=12, weight=ft.FontWeight.BOLD, color=ACCENT_CORAL),
                                ft.Row(
                                    spacing=8,
                                    controls=[
                                        ft.OutlinedButton(
                                            content="👁️ Ver Solución",
                                            style=ft.ButtonStyle(color=TEXT_MUTED, shape=ft.RoundedRectangleBorder(radius=10)),
                                            on_click=toggle_sol,
                                        ),
                                        ft.Button(
                                            content="Superado ✅",
                                            bgcolor=ACCENT_TEAL,
                                            color=BG_OUTER,
                                            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
                                            on_click=lambda e, i=idx: self.remove_single_error(i),
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        ft.Text(err.get("question", ""), size=14.5, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                        solution_box,
                    ],
                ),
            )
            err_list.controls.append(card)

        return ft.Column(expand=True, spacing=16, controls=[header, err_list])

    def _build_error_quiz_question_view(self):
        questions = self.error_quiz_questions
        if not questions:
            self.error_quiz_mode = False
            return self.build_errors_full_view()

        curr_idx = self.error_quiz_idx
        if curr_idx >= len(questions):
            curr_idx = len(questions) - 1
            self.error_quiz_idx = curr_idx

        q = questions[curr_idx]
        is_mcq = "options" in q and len(q.get("options", [])) == 4
        auto_remove_switch = ft.Switch(
            label=ft.Text(
                "Eliminar del historial automáticamente al acertar",
                size=12,
                color=TEXT_MUTED
            ),
            value=self.auto_remove_resolved_errors,
            active_color=ACCENT_TEAL,
            on_change=lambda e: setattr(self, "auto_remove_resolved_errors", e.control.value),
        )

        def go_prev(e):
            if self.error_quiz_idx > 0:
                self.error_quiz_idx -= 1
                self.render_center_content()
                self.page.update()

        def go_next(e):
            if self.error_quiz_idx < len(questions) - 1:
                self.error_quiz_idx += 1
                self.render_center_content()
                self.page.update()
            else:
                self.error_quiz_finished = True
                self.render_center_content()
                self.page.update()

        nav_bar = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Button(
                            content="Salir al Listado",
                            icon=ft.Icons.CLOSE_ROUNDED,
                            bgcolor="#252733",
                            color=TEXT_WHITE,
                            height=34,
                            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
                            on_click=lambda _: [
                                setattr(self, "error_quiz_mode", False),
                                self.render_center_content(),
                                self.page.update(),
                            ],
                        ),
                        ft.Text(f"Test de Errores ({q.get('type', 'Repaso')})", size=16, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                    ],
                ),
                ft.Row(
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Button(
                            content="Anterior",
                            icon=ft.Icons.ARROW_BACK_ROUNDED,
                            bgcolor="#232733",
                            color=TEXT_WHITE,
                            height=36,
                            disabled=curr_idx == 0,
                            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
                            on_click=go_prev,
                        ),
                        ft.Text(f"Error {curr_idx + 1} de {len(questions)}", size=13.5, weight=ft.FontWeight.BOLD, color=ACCENT_CORAL),
                        ft.Button(
                            content="Siguiente ➔" if curr_idx < len(questions) - 1 else "Finalizar Test 🏆",
                            icon=ft.Icons.ARROW_FORWARD_ROUNDED if curr_idx < len(questions) - 1 else ft.Icons.GRADING_ROUNDED,
                            bgcolor=ACCENT_TEAL,
                            color=BG_OUTER,
                            height=36,
                            style=ft.ButtonStyle(
                                shape=ft.RoundedRectangleBorder(radius=10),
                                text_style=ft.TextStyle(weight=ft.FontWeight.BOLD),
                            ),
                            on_click=go_next,
                        ),
                    ],
                ),
            ],
        )

        scrollable_body = ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, spacing=10)

        if is_mcq:
            options = q.get("options", [])
            correct_letter = str(q.get("correct_letter", "")).strip().upper()
            saved_ans = self.error_quiz_user_answers.get(curr_idx, "")

            feedback_text = ft.Text("", size=12.5, weight=ft.FontWeight.W_600, selectable=True)
            feedback_container = ft.Container(
                key="err_feedback_box",
                visible=False,
                padding=ft.Padding.symmetric(horizontal=14, vertical=10),
                border_radius=12,
                bgcolor=BG_INPUT,
                content=feedback_text,
            )

            radio_group = ft.RadioGroup(
                value=saved_ans,
                on_change=lambda e, i=curr_idx: self.error_quiz_user_answers.update({i: e.control.value}),
                content=ft.Column(spacing=6, controls=[]),
            )

            def select_opt(letter_val: str):
                radio_group.value = letter_val
                self.error_quiz_user_answers[curr_idx] = letter_val
                self.page.update()

            opt_cards = []
            for letter, opt_text in zip(LETTERS, options):
                opt_cards.append(
                    ft.Container(
                        padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                        border_radius=12,
                        bgcolor=BG_INPUT,
                        border=ft.Border.all(1, BORDER_SUBTLE),
                        on_click=lambda e, l=letter: select_opt(l),
                        content=ft.Row(
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Radio(value=letter, fill_color=ACCENT_TEAL),
                                ft.Text(f"{letter})  {opt_text}", color=TEXT_WHITE, size=13, expand=True),
                            ],
                        ),
                    )
                )
            radio_group.content.controls = opt_cards

            def verify_err_mcq(e):
                chosen = (radio_group.value or "").strip().upper()
                self.error_quiz_user_answers[curr_idx] = chosen
                if chosen not in LETTERS:
                    self.notify("Selecciona una opción antes de comprobar.", ACCENT_AMBER)
                    return

                feedback_container.visible = True
                if chosen == correct_letter:
                    q["_resolved"] = True
                    feedback_text.value = f"¡Respuesta Correcta! 🎯 — {q.get('explanation', '')}"
                    feedback_text.color = ACCENT_TEAL
                    feedback_container.border = ft.Border.all(1, ACCENT_TEAL)
                    if self.auto_remove_resolved_errors:
                        all_errs = load_errors()
                        all_errs = [x for x in all_errs if x.get("question") != q.get("question")]
                        save_errors(all_errs)
                        self.refresh_right_error_feed()
                else:
                    q["_resolved"] = False
                    try:
                        corr_text = options[LETTERS.index(correct_letter)]
                    except Exception:
                        corr_text = ""
                    frase = _get_frase_fallo()
                    feedback_text.value = (
                        f"❌ {frase}\n"
                        f"Correcta: {correct_letter}) {corr_text}\n"
                        f"Explicación: {q.get('explanation', '')}"
                    )
                    feedback_text.color = ACCENT_CORAL
                    feedback_container.border = ft.Border.all(1, ACCENT_CORAL)

                self.page.update()
                try:
                    scrollable_body.scroll_to(key="err_feedback_box", duration=250)
                except Exception:
                    pass

            scrollable_body.controls = [
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        auto_remove_switch,
                        ft.Button(
                            content="✓ Comprobar Respuesta",
                            bgcolor=ACCENT_BLUE,
                            color=TEXT_WHITE,
                            height=34,
                            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
                            on_click=verify_err_mcq,
                        ),
                    ],
                ),
                ft.Text(q.get("question", ""), size=14.5, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                radio_group,
                feedback_container,
            ]
        else:
            saved_text = self.error_quiz_user_answers.get(curr_idx, "")
            tf = ft.TextField(
                value=saved_text,
                multiline=True,
                min_lines=3,
                max_lines=5,
                hint_text="Escribe tu respuesta...",
                bgcolor=BG_INPUT,
                border_color=BORDER_SUBTLE,
                focused_border_color=ACCENT_TEAL,
                border_radius=12,
                color=TEXT_WHITE,
                text_size=13,
                on_change=lambda e, i=curr_idx: self.error_quiz_user_answers.update({i: e.control.value}),
            )

            model_box = ft.Container(
                key="err_short_box",
                visible=False,
                padding=14,
                border_radius=12,
                bgcolor=BG_INPUT,
                border=ft.Border.all(1, ACCENT_TEAL),
                content=ft.Text(
                    f"Respuesta Correcta / Modelo:\n{q.get('model_answer', q.get('correct', ''))}",
                    size=13,
                    color=ACCENT_TEAL,
                    weight=ft.FontWeight.W_600,
                    selectable=True,
                ),
            )

            def show_model(e):
                model_box.visible = not model_box.visible
                self.page.update()

            def mark_written_err(resolved: bool):
                q["_resolved"] = resolved
                if resolved and self.auto_remove_resolved_errors:
                    all_errs = load_errors()
                    all_errs = [x for x in all_errs if x.get("question") != q.get("question")]
                    save_errors(all_errs)
                    self.refresh_right_error_feed()
                    self.notify("¡Error superado y eliminado del historial!", ACCENT_TEAL)
                elif resolved:
                    self.notify("Marcada como acertada en este test.", ACCENT_TEAL)
                else:
                    self.notify("Se mantiene en el historial.", ACCENT_CORAL)
                self.page.update()

            scrollable_body.controls = [
                auto_remove_switch,
                ft.Text(q.get("question", ""), size=15, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                tf,
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Button(
                            content="Comprobar Solución",
                            icon=ft.Icons.VISIBILITY_ROUNDED,
                            bgcolor=ACCENT_BLUE,
                            color=TEXT_WHITE,
                            height=36,
                            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
                            on_click=show_model,
                        ),
                        ft.Row(
                            spacing=10,
                            controls=[
                                ft.OutlinedButton(
                                    content="Superada ✅",
                                    style=ft.ButtonStyle(color=ACCENT_TEAL, shape=ft.RoundedRectangleBorder(radius=10)),
                                    on_click=lambda _: mark_written_err(True),
                                ),
                                ft.OutlinedButton(
                                    content="Seguir Repasando ❌",
                                    style=ft.ButtonStyle(color=ACCENT_CORAL, shape=ft.RoundedRectangleBorder(radius=10)),
                                    on_click=lambda _: mark_written_err(False),
                                ),
                            ],
                        ),
                    ],
                ),
                model_box,
            ]

        return self.create_glow_card(
            theme_idx=3,
            padding=18,
            expand=True,
            content=ft.Column(
                expand=True,
                spacing=10,
                controls=[
                    nav_bar,
                    ft.Divider(color=BORDER_SUBTLE, height=1),
                    scrollable_body,
                ],
            ),
        )

    def _build_error_quiz_score_view(self):
        questions = self.error_quiz_questions
        total = len(questions)
        correctas = 0
        for idx, q in enumerate(questions):
            if "options" in q and len(q.get("options", [])) == 4:
                chosen = str(self.error_quiz_user_answers.get(idx, "")).strip().upper()
                if chosen == str(q.get("correct_letter", "")).strip().upper():
                    correctas += 1
            else:
                if q.get("_resolved"):
                    correctas += 1

        nota = (correctas / total * 10) if total > 0 else 0
        is_ok = nota >= 5.0

        card = self.create_glow_card(
            theme_idx=3,
            padding=35,
            content=ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=18,
                controls=[
                    ft.Icon(
                        ft.Icons.EMOJI_EVENTS_ROUNDED if is_ok else ft.Icons.AUTORENEW_ROUNDED,
                        size=64,
                        color=ACCENT_TEAL if is_ok else ACCENT_AMBER,
                    ),
                    ft.Text("Resultado del Test de Errores", size=24, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                    ft.Text(f"{nota:.1f} / 10", size=46, weight=ft.FontWeight.BOLD, color=ACCENT_TEAL if is_ok else ACCENT_CORAL),
                    ft.Text(f"Has superado {correctas} de {total} errores en esta sesión.", size=15, color=TEXT_MUTED),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=14,
                        controls=[
                            ft.Button(
                                content="Repetir Test con Errores",
                                icon=ft.Icons.REFRESH_ROUNDED,
                                bgcolor=ACCENT_TEAL,
                                color=BG_OUTER,
                                style=ft.ButtonStyle(
                                    shape=ft.RoundedRectangleBorder(radius=14),
                                    text_style=ft.TextStyle(weight=ft.FontWeight.BOLD),
                                ),
                                on_click=self.start_error_quiz_mode,
                            ),
                            ft.Button(
                                content="Ver Listado de Errores",
                                icon=ft.Icons.LIST_ROUNDED,
                                bgcolor="#252936",
                                color=TEXT_WHITE,
                                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=14)),
                                on_click=lambda _: [
                                    setattr(self, "error_quiz_mode", False),
                                    self.render_center_content(),
                                    self.page.update(),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        )

        return ft.Column(
            expand=True,
            spacing=20,
            controls=[
                ft.Text("Calificación Final — Test de Errores", size=22, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                card,
            ],
        )

    # -------------------- PANTALLAS DE CALIFICACIÓN (NOTA FINAL) --------------------

    def build_score_view(self, test_type: str, data_key: str, theme_idx: int):
        questions = self.exams_data.get(data_key, [])
        theme = CARD_THEMES[theme_idx % len(CARD_THEMES)]

        correctas = 0
        total = len(questions)
        for idx, q in enumerate(questions):
            chosen = self.user_mcq_answers.get((test_type, idx), "").strip().upper()
            if chosen == str(q.get("correct_letter", "")).strip().upper():
                correctas += 1

        nota = (correctas / total * 10) if total > 0 else 0
        is_aprobed = nota >= 5.0

        def restart_test(e):
            self.test_finished[test_type] = False
            self.test_indices[test_type] = 0
            for i in range(total):
                self.user_mcq_answers.pop((test_type, i), None)
            self.render_center_content()
            self.page.update()

        score_card = self.create_glow_card(
            theme_idx=theme_idx,
            padding=35,
            content=ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=20,
                controls=[
                    ft.Icon(
                        ft.Icons.EMOJI_EVENTS_ROUNDED if is_aprobed else ft.Icons.WARNING_ROUNDED,
                        size=64,
                        color=ACCENT_TEAL if is_aprobed else ACCENT_CORAL,
                    ),
                    ft.Text(f"Resultado de {test_type}", size=24, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                    ft.Text(
                        f"{nota:.1f} / 10",
                        size=48,
                        weight=ft.FontWeight.BOLD,
                        color=ACCENT_TEAL if is_aprobed else ACCENT_CORAL,
                    ),
                    ft.Text(
                        f"Has acertado {correctas} de {total} preguntas.",
                        size=15,
                        color=TEXT_MUTED,
                    ),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=16,
                        controls=[
                            ft.Button(
                                content="Volver",
                                icon=ft.Icons.ARROW_BACK_ROUNDED,
                                bgcolor="#252936",
                                color=TEXT_WHITE,
                                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=14)),
                                on_click=lambda _: [
                                    self.test_finished.update({test_type: False}),
                                    self.render_center_content(),
                                    self.page.update(),
                                ],
                            ),
                            ft.Button(
                                content="Reiniciar",
                                icon=ft.Icons.REFRESH_ROUNDED,
                                bgcolor="#252936",
                                color=TEXT_WHITE,
                                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=14)),
                                on_click=restart_test,
                            ),
                            ft.Button(
                                content="Dashboard",
                                icon=ft.Icons.HOME_ROUNDED,
                                bgcolor=theme["accent"],
                                color=BG_OUTER,
                                style=ft.ButtonStyle(
                                    shape=ft.RoundedRectangleBorder(radius=14),
                                    text_style=ft.TextStyle(weight=ft.FontWeight.BOLD),
                                ),
                                on_click=lambda _: self.switch_tab(0),
                            ),
                        ],
                    ),
                ],
            ),
        )

        return ft.Column(
            expand=True,
            spacing=20,
            controls=[
                ft.Text(f"Calificación Final — {test_type}", size=22, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                score_card,
            ],
        )

    def build_score_short_view(self):
        questions = self.exams_data.get("short_answers", [])
        total = len(questions)

        def restart_test(e):
            self.test_finished["Preguntas Cortas"] = False
            self.test_indices["Preguntas Cortas"] = 0
            for i in range(total):
                self.user_short_answers.pop(i, None)
                self.short_evaluations.pop(i, None)
            self.render_center_content()
            self.page.update()

        score_card = self.create_glow_card(
            theme_idx=2,
            padding=35,
            content=ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=20,
                controls=[
                    ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED, size=64, color=ACCENT_TEAL),
                    ft.Text("Resultado de Preguntas Cortas", size=24, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                    ft.Text("Completado", size=36, weight=ft.FontWeight.BOLD, color=ACCENT_TEAL),
                    ft.Text(f"Has revisado las {total} cuestiones de desarrollo.", size=15, color=TEXT_MUTED),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=16,
                        controls=[
                            ft.Button(
                                content="Volver",
                                icon=ft.Icons.ARROW_BACK_ROUNDED,
                                bgcolor="#252936",
                                color=TEXT_WHITE,
                                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=14)),
                                on_click=lambda _: [
                                    self.test_finished.update({"Preguntas Cortas": False}),
                                    self.render_center_content(),
                                    self.page.update(),
                                ],
                            ),
                            ft.Button(
                                content="Reiniciar",
                                icon=ft.Icons.REFRESH_ROUNDED,
                                bgcolor="#252936",
                                color=TEXT_WHITE,
                                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=14)),
                                on_click=restart_test,
                            ),
                            ft.Button(
                                content="Dashboard",
                                icon=ft.Icons.HOME_ROUNDED,
                                bgcolor=ACCENT_AMBER,
                                color=BG_OUTER,
                                style=ft.ButtonStyle(
                                    shape=ft.RoundedRectangleBorder(radius=14),
                                    text_style=ft.TextStyle(weight=ft.FontWeight.BOLD),
                                ),
                                on_click=lambda _: self.switch_tab(0),
                            ),
                        ],
                    ),
                ],
            ),
        )

        return ft.Column(
            expand=True,
            spacing=20,
            controls=[
                ft.Text("Calificación Final — Preguntas Cortas", size=22, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                score_card,
            ],
        )

    def build_score_definitions_view(self):
        definitions = self.exams_data.get("definitions", [])
        total = len(definitions)

        def restart_defs(e):
            self.test_finished["Definiciones"] = False
            self.test_indices["Definiciones"] = 0
            for i in range(total):
                self.user_def_answers.pop(i, None)
                self.def_evaluations.pop(i, None)
            self.render_center_content()
            self.page.update()

        score_card = self.create_glow_card(
            theme_idx=4,
            padding=35,
            content=ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=20,
                controls=[
                    ft.Icon(ft.Icons.MENU_BOOK_ROUNDED, size=64, color=ACCENT_PURPLE),
                    ft.Text("Repaso de Definiciones Completado", size=24, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                    ft.Text(f"{total} Conceptos", size=36, weight=ft.FontWeight.BOLD, color=ACCENT_PURPLE),
                    ft.Text("Has completado la ronda de definiciones clave.", size=15, color=TEXT_MUTED),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=16,
                        controls=[
                            ft.Button(
                                content="Volver",
                                icon=ft.Icons.ARROW_BACK_ROUNDED,
                                bgcolor="#252936",
                                color=TEXT_WHITE,
                                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=14)),
                                on_click=lambda _: [
                                    self.test_finished.update({"Definiciones": False}),
                                    self.render_center_content(),
                                    self.page.update(),
                                ],
                            ),
                            ft.Button(
                                content="Reiniciar",
                                icon=ft.Icons.REFRESH_ROUNDED,
                                bgcolor="#252936",
                                color=TEXT_WHITE,
                                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=14)),
                                on_click=restart_defs,
                            ),
                            ft.Button(
                                content="Dashboard",
                                icon=ft.Icons.HOME_ROUNDED,
                                bgcolor=ACCENT_PURPLE,
                                color=TEXT_WHITE,
                                style=ft.ButtonStyle(
                                    shape=ft.RoundedRectangleBorder(radius=14),
                                    text_style=ft.TextStyle(weight=ft.FontWeight.BOLD),
                                ),
                                on_click=lambda _: self.switch_tab(0),
                            ),
                        ],
                    ),
                ],
            ),
        )

        return ft.Column(
            expand=True,
            spacing=20,
            controls=[
                ft.Text("Calificación Final — Definiciones", size=22, weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                score_card,
            ],
        )

    # -------------------- BACKEND Y GENERACIÓN --------------------

    def _extract_pdf_text(self):
        """Lee todos los PDFs seleccionados y los une en un único texto."""
        if self.pdf_text:
            return self.pdf_text
        if not self.pdf_paths:
            return ""
        combined = []
        for path in self.pdf_paths:
            reader = PdfReader(path)
            pages_text = [page.extract_text() or "" for page in reader.pages]
            combined.append("\n".join(pages_text))
        self.pdf_text = "\n\n".join(combined)
        # Ya tenemos el texto: borramos los PDFs subidos para no llenar el disco del servidor
        for path in self.pdf_paths:
            try:
                if os.path.abspath(path).startswith(os.path.abspath(UPLOAD_DIR)):
                    os.remove(path)
            except Exception:
                pass
        return self.pdf_text

    async def _wait_for_upload(self, path: str, timeout: float = 180.0) -> bool:
        """Espera a que el archivo exista y su tamaño deje de crecer."""
        waited = 0.0
        last_size = -1
        while waited < timeout:
            if os.path.exists(path):
                size = os.path.getsize(path)
                if size > 0 and size == last_size:
                    return True
                last_size = size
            await asyncio.sleep(0.5)
            waited += 0.5
        return False

    def select_pdf_dialog(self, e):
        async def _async_pick_pdf():
            try:
                picked = await self.file_picker.pick_files(
                    dialog_title="Seleccionar temarios PDF (puedes elegir varios)",
                    allow_multiple=True,
                    file_type=ft.FilePickerFileType.CUSTOM,
                    allowed_extensions=["pdf"],
                )
            except Exception as ex:
                traceback.print_exc()
                self.notify(f"No se pudo abrir el selector de archivos: {ex}", ACCENT_CORAL)
                return
            if not picked:
                return

            chosen = []
            if not self.page.web:
                # Modo escritorio: el selector devuelve rutas locales
                chosen = [f.path for f in picked if getattr(f, "path", None)]
            else:
                # Modo web: hay que subir los archivos al servidor
                _purge_old_uploads()
                self.status_message = "⏳ Subiendo PDF(s) al servidor..."
                self.progress_container.visible = True
                self.page.update()
                try:
                    upload_files, dest_paths = [], []
                    for f in picked:
                        safe_name = os.path.basename(f.name)
                        unique_name = f"{uuid.uuid4().hex[:8]}__{safe_name}"
                        url = self.page.get_upload_url(unique_name, 600)
                        if inspect.isawaitable(url):
                            url = await url
                        upload_files.append(ft.FilePickerUploadFile(name=f.name, upload_url=url))
                        dest_paths.append(os.path.join(UPLOAD_DIR, unique_name))

                    res = self.file_picker.upload(upload_files)
                    if inspect.isawaitable(res):
                        await res

                    for p in dest_paths:
                        if not await self._wait_for_upload(p):
                            raise RuntimeError(f"No se completó la subida de {_pdf_display_name(p)}")
                    chosen = dest_paths
                except Exception as ex:
                    traceback.print_exc()
                    self.status_message = "❌ Error al subir el PDF."
                    self.notify(f"Error al subir el PDF: {ex}", ACCENT_CORAL)
                    self.render_center_content()
                    self.page.update()
                    return

            if chosen:
                self.pdf_paths = chosen
                self.pdf_text = ""
                if len(self.pdf_paths) == 1:
                    self.pdf_label_text.value = f"Temario: {_pdf_display_name(self.pdf_paths[0])}"
                    self.status_message = f"PDF cargado: {_pdf_display_name(self.pdf_paths[0])}"
                else:
                    self.pdf_label_text.value = f"Temario: {len(self.pdf_paths)} PDFs combinados"
                    self.status_message = f"{len(self.pdf_paths)} PDFs cargados y combinados"
                self.pdf_label_text.color = ACCENT_TEAL
                self.notify(self.status_message, ACCENT_TEAL)
                self.render_center_content()
                self.page.update()

        self.page.run_task(_async_pick_pdf)

    def call_gemini_with_retry(self, client, model_name, prompt, schema_class, max_retries=10):
        delay = 3
        for attempt in range(1, max_retries + 1):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": schema_class,
                    },
                )
                return json.loads(response.text)
            except Exception as e:
                err_str = str(e)
                if "503" in err_str or "UNAVAILABLE" in err_str or "high demand" in err_str:
                    if attempt < max_retries:
                        self.status_message = f"⏳ Servidor ocupado (503). Reintentando en {int(delay)}s ({attempt}/{max_retries})..."
                        time.sleep(delay)
                        delay = min(delay * 1.5, 20)
                        continue
                raise e
        raise Exception("Se agotaron los reintentos debido a la alta demanda del servidor.")

    def start_generation_thread(self, e):
        api_key = self._get_api_key()
        selected_model = (self.selected_model or "").strip()

        if not api_key:
            self.notify("Introduce tu API Key de Google Gemini.", ACCENT_CORAL)
            return
        if not selected_model:
            self.notify("Introduce o selecciona un modelo de Gemini válido.", ACCENT_CORAL)
            return
        if not self.pdf_paths:
            self.notify("Selecciona al menos un archivo PDF con el temario.", ACCENT_CORAL)
            return

        if not any([self.gen_medium, self.gen_hard, self.gen_short, self.gen_defs, self.gen_cases]):
            self.notify("Selecciona al menos una sección para generar (usa el desplegable).", ACCENT_CORAL)
            return

        if self.gen_medium:
            self.exams_data["medium_mcq"] = []
        if self.gen_hard:
            self.exams_data["hard_mcq"] = []
        if self.gen_short:
            self.exams_data["short_answers"] = []
        if self.gen_defs:
            self.exams_data["definitions"] = []
        if self.gen_cases:
            self.exams_data["industrial_cases"] = []

        def _reset_test_state(name: str):
            self.test_indices[name] = 0
            self.test_finished[name] = False
            for k in list(self.user_mcq_answers.keys()):
                if k[0] == name:
                    del self.user_mcq_answers[k]
            if name == "Preguntas Cortas":
                self.user_short_answers.clear()
                self.short_evaluations.clear()
            if name == "Definiciones":
                self.user_def_answers.clear()
                self.def_evaluations.clear()
            if name == "Casos Prácticos":
                self.user_case_answers.clear()
                self.case_evaluations.clear()

        if self.gen_medium:
            _reset_test_state("Test Medio")
        if self.gen_hard:
            _reset_test_state("Test Difícil")
        if self.gen_short:
            _reset_test_state("Preguntas Cortas")
        if self.gen_defs:
            _reset_test_state("Definiciones")
        if self.gen_cases:
            _reset_test_state("Casos Prácticos")

        self.global_history = []
        for key, kind in [("medium_mcq", "Medio"), ("hard_mcq", "Difícil"),
                          ("short_answers", "Corta"), ("definitions", "Definición"),
                          ("industrial_cases", "Caso Práctico")]:
            if self.exams_data.get(key):
                self._register_topics(self.exams_data[key], kind)

        self.is_generating = True
        self.progress_val = 0.03
        self.progress_target_ceiling = 0.20
        self.status_message = f"⏳ Extrayendo PDF y generando selección ({self._summarize_gen_selection()}) con {selected_model}..."

        self.render_center_content()
        self.page.update()

        self.page.run_task(self._async_progress_ticker)

        threading.Thread(
            target=self.generate_step_by_step,
            args=(api_key, selected_model),
            daemon=True,
        ).start()

    def generate_step_by_step(self, api_key, model_name):
        try:
            self._extract_pdf_text()

            client = genai.Client(api_key=api_key)

            tasks = []
            if self.gen_medium: tasks.append("medium")
            if self.gen_hard:   tasks.append("hard")
            if self.gen_short:  tasks.append("short")
            if self.gen_defs:   tasks.append("defs")
            if self.gen_cases:  tasks.append("cases")

            total_steps = len(tasks)
            step_done = 0

            def _step_done():
                nonlocal step_done
                step_done += 1
                self.progress_val = step_done / total_steps
                self.progress_target_ceiling = self.progress_val

            if "medium" in tasks:
                self.status_message = "⏳ Generando Test de Nivel Medio (10 preguntas)..."
                excluded = self._build_exclusion_prompt()
                prompt_med = f"""
Analiza el temario y genera exactamente 10 preguntas tipo test de nivel MEDIO variadas, enfocadas en la comprensión general de conceptos clave.
Usa redacciones claras y directas.
{self._common_rules(excluded)}
TEMARIO:
{self.pdf_text}
"""
                res_med = self.call_gemini_with_retry(client, model_name, prompt_med, MediumPackage)
                clean_med = self._postprocess_mcq(res_med.get("medium_mcq", []))
                self.exams_data["medium_mcq"] = clean_med
                self._register_topics(clean_med, "Medio")
                _step_done()

            if "hard" in tasks:
                self.status_message = "🔥 Generando Test Difícil (opciones de alta precisión)..."
                excluded = self._build_exclusion_prompt()
                prompt_hard = f"""
Genera exactamente 10 preguntas tipo test de nivel DIFÍCIL (nivel oposición/experto).
{self._hard_difficulty_instructions()}
{self._common_rules(excluded)}
TEMARIO:
{self.pdf_text}
"""
                res_hard = self.call_gemini_with_retry(client, model_name, prompt_hard, HardPackage)
                clean_hard = self._postprocess_mcq(res_hard.get("hard_mcq", []))
                self.exams_data["hard_mcq"] = clean_hard
                self._register_topics(clean_hard, "Difícil")
                _step_done()

            if "short" in tasks:
                self.status_message = "✍️ Generando 10 Preguntas Cortas..."
                excluded = self._build_exclusion_prompt()
                prompt_short = f"""
Genera exactamente 10 preguntas cortas de desarrollo que abarquen partes del documento que no se hayan tocado antes.
{self._common_rules(excluded)}
TEMARIO:
{self.pdf_text}
"""
                res_short = self.call_gemini_with_retry(client, model_name, prompt_short, ShortPackage)
                seen = self._get_all_topics()
                clean_short = []
                for q in res_short.get("short_answers", []):
                    t = q.get("topic", "").strip().lower()
                    if not t or t in seen:
                        continue
                    seen.add(t)
                    clean_short.append(q)
                self.exams_data["short_answers"] = clean_short
                self._register_topics(clean_short, "Corta")
                _step_done()

            if "defs" in tasks:
                self.status_message = "📖 Seleccionando 10 Conceptos Clave completos del temario..."
                excluded = self._build_exclusion_prompt()
                prompt_def = f"""
Selecciona exactamente 10 TÉRMINOS O CONCEPTOS COMPUESTOS ÍNTEGROS Y RELEVANTES del temario para que el alumno los defina.
{self._definitions_prompt_rules()}
Evita repetir conceptos ya usados:
{excluded}
TEMARIO:
{self.pdf_text}
"""
                res_def = self.call_gemini_with_retry(client, model_name, prompt_def, DefinitionPackage)
                seen = self._get_all_topics()
                clean_defs = []
                for d in res_def.get("definitions", []):
                    term_formatted = _format_capitalized_term(d.get("term", ""))
                    term_key = term_formatted.strip().lower()
                    if not term_key or term_key in seen:
                        continue
                    seen.add(term_key)
                    d["term"] = term_formatted
                    clean_defs.append(d)
                self.exams_data["definitions"] = clean_defs
                self._register_topics(clean_defs, "Definición")
                _step_done()

            if "cases" in tasks:
                self.status_message = "🏢 Generando Casos Prácticos..."
                excluded = self._build_exclusion_prompt()
                prompt_case = f"""
Genera exactamente 3 CASOS PRÁCTICOS basados ESTRICTAMENTE en el temario.
{self._cases_prompt_rules()}
Evita repetir estos temas:
{excluded}
TEMARIO:
{self.pdf_text}
"""
                res_cases = self.call_gemini_with_retry(client, model_name, prompt_case, CasePackage)
                seen = self._get_all_topics()
                clean_cases = []
                for c in res_cases.get("cases", []):
                    t = c.get("topic", "").strip().lower()
                    if not t or t in seen:
                        continue
                    seen.add(t)
                    clean_cases.append(c)
                self.exams_data["industrial_cases"] = clean_cases
                self._register_topics(clean_cases, "Caso Práctico")
                _step_done()

            self.progress_val = 1.0
            self.progress_target_ceiling = 1.0
            self.status_message = f"✨ ¡Generación completada ({self._summarize_gen_selection()})!"
            self.is_generating = False

        except Exception as ex:
            traceback.print_exc()
            self.progress_val = 0.0
            self.progress_target_ceiling = 0.0
            self.status_message = f"❌ Error: {ex}"
            self.is_generating = False
            try:
                self.notify(f"Error en la generación: {ex}", ACCENT_CORAL)
            except Exception:
                pass

    def regenerate_more_questions(self, test_type: str, btn_control: ft.Control):
        api_key = self._get_api_key()
        selected_model = (self.selected_model or "").strip()

        if not api_key or not self.pdf_text:
            self.notify("Faltan datos del PDF o API Key.", ACCENT_CORAL)
            return

        btn_control.disabled = True
        btn_control.content = "⏳..."
        self.page.update()

        async def _async_regen():
            try:
                def _sync_work():
                    client = genai.Client(api_key=api_key)
                    excluded = self._build_exclusion_prompt()

                    if test_type == "Test Medio":
                        prompt = f"""
Genera exactamente 10 NUEVAS preguntas tipo test de nivel MEDIO con conceptos totalmente distintos a los anteriores.
{self._common_rules(excluded)}
TEMARIO:
{self.pdf_text}
"""
                        data = self.call_gemini_with_retry(client, selected_model, prompt, MediumPackage)
                        new_qs = self._postprocess_mcq(data.get("medium_mcq", []))
                        self.exams_data["medium_mcq"].extend(new_qs)
                        self._register_topics(new_qs, "Medio")

                    elif test_type == "Test Difícil":
                        prompt = f"""
Genera exactamente 10 NUEVAS preguntas tipo test de nivel DIFÍCIL con opciones muy sutiles.
{self._hard_difficulty_instructions()}
{self._common_rules(excluded)}
TEMARIO:
{self.pdf_text}
"""
                        data = self.call_gemini_with_retry(client, selected_model, prompt, HardPackage)
                        new_qs = self._postprocess_mcq(data.get("hard_mcq", []))
                        self.exams_data["hard_mcq"].extend(new_qs)
                        self._register_topics(new_qs, "Difícil")

                    elif test_type == "Preguntas Cortas":
                        prompt = f"""
Genera exactamente 10 NUEVAS preguntas cortas de desarrollo con conceptos no repetidos.
{self._common_rules(excluded)}
TEMARIO:
{self.pdf_text}
"""
                        data = self.call_gemini_with_retry(client, selected_model, prompt, ShortPackage)
                        seen = self._get_all_topics()
                        new_qs = []
                        for q in data.get("short_answers", []):
                            t = q.get("topic", "").strip().lower()
                            if not t or t in seen:
                                continue
                            seen.add(t)
                            new_qs.append(q)
                        self.exams_data["short_answers"].extend(new_qs)
                        self._register_topics(new_qs, "Corta")

                    elif test_type == "Definiciones":
                        prompt = f"""
Selecciona exactamente 10 NUEVOS TÉRMINOS O CONCEPTOS COMPUESTOS ÍNTEGROS Y RELEVANTES del temario para definir.
{self._definitions_prompt_rules()}
Evita repetir estos conceptos:
{excluded}
TEMARIO:
{self.pdf_text}
"""
                        data = self.call_gemini_with_retry(client, selected_model, prompt, DefinitionPackage)
                        seen = self._get_all_topics()
                        new_defs = []
                        for d in data.get("definitions", []):
                            term_formatted = _format_capitalized_term(d.get("term", ""))
                            t_key = term_formatted.strip().lower()
                            if not t_key or t_key in seen:
                                continue
                            seen.add(t_key)
                            d["term"] = term_formatted
                            new_defs.append(d)
                        self.exams_data["definitions"].extend(new_defs)
                        self._register_topics(new_defs, "Definición")

                    elif test_type == "Casos Prácticos":
                        prompt = f"""
Genera exactamente 3 NUEVOS CASOS PRÁCTICOS basados ESTRICTAMENTE en el temario.
{self._cases_prompt_rules()}
Evita repetir estos temas:
{excluded}
TEMARIO:
{self.pdf_text}
"""
                        data = self.call_gemini_with_retry(client, selected_model, prompt, CasePackage)
                        seen = self._get_all_topics()
                        new_cases = []
                        for c in data.get("cases", []):
                            t = c.get("topic", "").strip().lower()
                            if not t or t in seen:
                                continue
                            seen.add(t)
                            new_cases.append(c)
                        self.exams_data["industrial_cases"].extend(new_cases)
                        self._register_topics(new_cases, "Caso Práctico")

                await asyncio.to_thread(_sync_work)
                self.test_finished[test_type] = False
                self.notify(f"¡Se han añadido nuevos elementos a {test_type}!", ACCENT_TEAL)
            except Exception as ex:
                traceback.print_exc()
                self.notify(f"No se pudieron generar más elementos: {ex}", ACCENT_CORAL)

            self.render_center_content()
            self.page.update()

        self.page.run_task(_async_regen)


def main(page: ft.Page):
    NexusExamFletApp(page)


if __name__ == "__main__":
    # Render inyecta el puerto en la variable PORT y exige escuchar en 0.0.0.0
    _port = int(os.environ.get("PORT", "8550"))
    _kwargs = dict(
        view=ft.AppView.WEB_BROWSER,
        host="0.0.0.0",
        port=_port,
        upload_dir=UPLOAD_DIR,
    )
    if hasattr(ft, "run"):
        ft.run(main, **_kwargs)
    else:
        ft.app(target=main, **_kwargs)
