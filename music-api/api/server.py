# server.py
from fastapi import FastAPI, BackgroundTasks, HTTPException, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
import yt_dlp
import os
import uuid
import asyncio 
import tempfile
import logging
from typing import Literal

# Mejora: Configuración de Logging profesional
# Usar el logger de Python en lugar de print() para un mejor control en producción.
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = FastAPI()
TEMP_DIR = tempfile.gettempdir()

# Mejora: Limpieza de Archivos más segura
# Se elimina el retardo de 60 segundos. La tarea se ejecuta después de que la descarga del cliente finaliza.
async def cleanup_file(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
            logging.info(f"🧹 Archivo temporal eliminado: {path}")
    except OSError as e:
        logging.error(f"❌ Error al eliminar el archivo {path}: {e}")

@app.get("/")
async def root():
    return {"status": "Music Backend Running OK!"}

# Mejora: Validación de Entradas con Pydantic
# FastAPI validará automáticamente que la URL es válida y la calidad es una de las esperadas.
class DownloadRequest(BaseModel):
    url: HttpUrl
    quality: Literal['mp3_320', 'mp3_192', 'm4a', 'flac']

# --- CORRECCIÓN: Cambiar de Body a Query Parameters ---
# Usamos Depends() para que FastAPI construya el modelo desde los parámetros de la URL, no desde el body.
@app.get("/download")
async def download_music(background_tasks: BackgroundTasks, request: DownloadRequest = Depends()):
    logging.info(f"📥 Solicitud de descarga recibida: {request.url} | Calidad: {request.quality}")
    temp_id = str(uuid.uuid4())

    # Mejora: Refactorización de la lógica de calidad
    # Usar un diccionario hace el código más limpio y fácil de extender.
    QUALITY_SETTINGS = {
        'mp3_320': {'ext': 'mp3', 'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}]},
        'mp3_192': {'ext': 'mp3', 'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]},
        'm4a': {'ext': 'm4a', 'format': 'bestaudio[ext=m4a]/best'},
        'flac': {'ext': 'flac', 'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'flac'}]}
    }

    setting = QUALITY_SETTINGS.get(request.quality)
    ext = setting['ext']

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'outtmpl': os.path.join(TEMP_DIR, f"{temp_id}.%(ext)s"),
        'nocheckcertificate': True,
        'format': setting.get('format', 'bestaudio/best'),
        'postprocessors': setting.get('postprocessors', []),
        # Mejora: Añadir argumento para evitar warnings de JS Runtime en el servidor.
        'extractor_args': {
            'youtube': {'player_client': 'default'}
        }
    }

    # Mejora: Ejecución no bloqueante de la descarga
    # Definimos una función síncrona para la descarga que se ejecutará en un hilo separado.
    def do_download():
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(str(request.url), download=True)
                title = info.get('title', 'audio').replace('/', '_')
                
                # Busca el archivo final por su ID único
                for f in os.listdir(TEMP_DIR):
                    if f.startswith(temp_id):
                        final_path = os.path.join(TEMP_DIR, f)
                        return final_path, title
                
                raise FileNotFoundError("No se encontró el archivo procesado en el directorio temporal.")
        except yt_dlp.utils.DownloadError as e:
            # Error específico si la URL no es válida o el video no está disponible
            logging.warning(f"Error de descarga de yt-dlp: {e}")
            raise ValueError(f"No se pudo procesar la URL. Puede ser inválida o el video no está disponible.")
        except Exception as e:
            # Captura otros errores inesperados durante la descarga
            logging.error(f"Error inesperado en el hilo de descarga: {e}", exc_info=True)
            raise

    try:
        # Ejecutamos la función bloqueante en el pool de hilos para no congelar el servidor.
        final_path, title = await asyncio.to_thread(do_download)

        # Agregamos la tarea de limpieza que se ejecutará después de que la respuesta se complete.
        background_tasks.add_task(cleanup_file, final_path)

        logging.info(f"✅ Enviando archivo: {title}.{ext}")
        return FileResponse(final_path, filename=f"{title}.{ext}", media_type='application/octet-stream')

    except ValueError as e:
        # Error controlado desde do_download (ej. URL inválida) -> Error 400
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Captura cualquier otro error inesperado -> Error 500
        logging.error(f"❌ Error crítico en el endpoint /download: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ocurrió un error interno en el servidor.")
