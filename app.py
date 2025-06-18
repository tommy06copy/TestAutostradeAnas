from flask import Flask, render_template, request, jsonify, redirect, url_for
import psycopg2
import logging
import os

app = Flask(__name__)

# Configura logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# === CONFIG DB ===
DB_CONFIG = {
    "dbname": "puntiautostrade",
    "user": "postgres",
    "password": "root",
    "host": "localhost",
    "port": 5432
}

def get_connection():
    return psycopg2.connect(**DB_CONFIG)

def get_table_from_punto(punto):
    if 'A90' in punto:
        return 'datia90'
    elif 'SS51' in punto:
        return 'datiss51'
    elif 'SS675' in punto:
        return 'datiss675'
    else:
        return None

# === ENDPOINT STATICI ===
@app.route("/")
@app.route("/punto")
def index():
    # redirect a /previsionale per mostrare subito il previsionale
    return redirect(url_for('previsionale2'))

@app.route("/previsionale")
def previsionale2():
    return render_template("previsionale2.html")

@app.route("/storico")
def storico():
    return render_template("storico.html")


# === FILES DISPONIBILI PER TRATTO ===
@app.route('/api/files/<strada>/<tratto>')
def lista_file_per_tratto(strada, tratto):
    directory = os.path.join("static", "jsons", "history", strada)
    if not os.path.exists(directory):
        return jsonify([])
    pattern = f"{strada}_{strada}_{tratto}_"
    files = [f for f in os.listdir(directory) if f.startswith(pattern) and f.endswith(".jsons")]
    files.sort(reverse=True)
    return jsonify(files)


# === DATI PREVISIONALI OTTIMIZZATO ===
@app.route("/api/previsionale_dato")
def previsionale_dato():
    variabile = request.args.get('variabile')
    strada    = request.args.get('strada')
    if not variabile or not strada:
        return jsonify({"errore": "Parametri 'variabile' e 'strada' obbligatori"}), 400

    tabella = get_table_from_punto(strada)
    if not tabella:
        return jsonify({"errore": f"Strada '{strada}' non supportata"}), 400

    conn   = get_connection()
    cursor = conn.cursor()
    try:
        # 1. Ottieni tutti i tratti per la strada selezionata
        cursor.execute(
            f"SELECT DISTINCT tratto FROM {tabella} WHERE tratto LIKE %s",
            (f"{strada}%",)
        )
        tratti = [row[0] for row in cursor.fetchall()]

        risultati = {}
        orari_set  = set()

        # 2. Per ciascun tratto, prendi l'ultimo downloaded_at e i valori ordinati per time
        for tratto in tratti:
            cursor.execute(
                f"SELECT MAX(downloaded_at) FROM {tabella} WHERE tratto = %s",
                (tratto,)
            )
            ultimo = cursor.fetchone()[0]
            if not ultimo:
                continue

            cursor.execute(
                f"""
                SELECT time, {variabile}
                FROM {tabella}
                WHERE tratto = %s AND downloaded_at = %s
                ORDER BY time
                """,
                (tratto, ultimo)
            )
            rows = cursor.fetchall()
            if not rows:
                continue

            dati_tratto = []
            for time_val, misura in rows:
                iso = time_val.isoformat()
                dati_tratto.append({"time": iso, "valore": misura})
                orari_set.add(iso)
            risultati[tratto] = dati_tratto

        orari = sorted(orari_set)
        return jsonify({"times": orari, "data": risultati})

    except Exception as e:
        logging.error(f"Errore in previsionale_dato: {e}", exc_info=True)
        return jsonify({"errore": "Errore interno"}), 500
    finally:
        cursor.close()
        conn.close()


# === DATI STORICI (giornaliero) ===
@app.route("/api/storico_dato")
def storico_dato():
    variabile  = request.args.get('variabile')
    start_date = request.args.get('start_date')
    end_date   = request.args.get('end_date')
    if not variabile or not start_date or not end_date:
        return jsonify({"errore": "Parametri 'variabile', 'start_date' e 'end_date' obbligatori"}), 400

    conn   = get_connection()
    cursor = conn.cursor()
    risultati = {}
    try:
        for tabella in ['datia90', 'datiss51', 'datiss675']:
            cursor.execute(f"SELECT DISTINCT tratto, punto FROM {tabella}")
            tratti = cursor.fetchall()
            for tratto, punto in tratti:
                cursor.execute(
                    f"""
                    SELECT DATE(time) AS giorno, AVG({variabile})
                    FROM {tabella}
                    WHERE tratto = %s AND punto = %s
                      AND downloaded_at BETWEEN %s AND %s
                    GROUP BY giorno
                    ORDER BY giorno
                    """,
                    (tratto, punto, start_date, end_date)
                )
                rows = cursor.fetchall()
                if rows:
                    risultati.setdefault(tratto, []).extend(
                        {"giorno": r[0].isoformat(), "valore": r[1]} for r in rows
                    )
        return jsonify(risultati)

    except Exception as e:
        logging.error(f"Errore in storico_dato: {e}", exc_info=True)
        return jsonify({"errore": "Errore interno"}), 500
    finally:
        cursor.close()
        conn.close()


# === DATI STORICO ORARIO ===
@app.route("/api/historico_orario")
def storico_orario():
    variabile  = request.args.get('variabile')
    strada     = request.args.get('strada')
    start_date = request.args.get('start_date')
    end_date   = request.args.get('end_date')
    if not variabile or not strada or not start_date or not end_date:
        return jsonify({"errore": "Parametri mancanti"}), 400

    tabella = get_table_from_punto(strada)
    if not tabella:
        return jsonify({"errore": f"Strada '{strada}' non supportata"}), 400

    conn   = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"""
            SELECT tratto, time, {variabile}
            FROM {tabella}
            WHERE tratto LIKE %s
              AND time >= %s
              AND time <= %s
            ORDER BY time
        """, (f"{strada}%", start_date, end_date + " 23:59:59"))
        rows = cursor.fetchall()

        data  = {}
        times = set()
        for tratto, t, val in rows:
            iso = t.isoformat()
            times.add(iso)
            data.setdefault(tratto, []).append({"time": iso, "valore": val})

        return jsonify({"times": sorted(times), "data": data})

    except Exception as e:
        logging.error(f"Errore in storico_orario: {e}", exc_info=True)
        return jsonify({"errore": "Errore interno"}), 500
    finally:
        cursor.close()
        conn.close()

@app.route("/grafico.html")
def grafico_page():
    return render_template("grafico.html")



if __name__ == "__main__":
    app.run(debug=True)
