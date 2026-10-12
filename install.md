# Installation von Qwen3-TTS-12Hz-1.7B-CustomVoice in einem venv

Diese Anleitung beschreibt, wie du unter Windows mit `C:\Python312\python.exe` auf dem Laufwerk `G:` ein virtuelles Environment (`venv`) einrichtest und Qwen TTS installierst.

## 1. Verzeichnis auf G: anlegen

1. Ã–ffne PowerShell oder die Eingabeaufforderung.
2. Wechsle auf das Laufwerk G:
   ```bat
   G:
   ```
3. Lege den Projektordner `QwenTTS` an und wechsle hinein:
   ```bat
   mkdir QwenTTS
   cd QwenTTS
   ```

## 2. Virtuelle Umgebung mit Python 3.12 erstellen

Stelle sicher, dass `C:\Python312\python.exe` existiert.

```bat
"C:\Python312\python.exe" -m venv .venv
```

Damit wird im aktuellen Ordner ein Unterordner `.venv` erstellt.

## 3. Virtuelle Umgebung aktivieren

In PowerShell:
```powershell
G:\QwenTTS\.venv\Scripts\Activate.ps1
```

In der Eingabeaufforderung (cmd):
```bat
G:\QwenTTS\.venv\Scripts\activate.bat
```

Wenn die Aktivierung geklappt hat, steht vorne in der Zeile etwas wie `(.venv)`.

## 4. pip aktualisieren

```bat
python -m pip install --upgrade pip
```

## 5. Installationsvarianten fÃ¼r Qwen TTS

Es gibt aktuell zwei typische Wege:

- Installation Ã¼ber `pip` (wenn das Paket verÃ¶ffentlicht ist)
- Installation Ã¼ber `git` + `pip` direkt aus dem Repository

### 5.1. Installation Ã¼ber pip (falls verfÃ¼gbar)

Probiere zunÃ¤chst:

```bat
pip install qwen-tts
```

Wenn dieses Paket nicht gefunden wird, nutze die Git-Variante unten.

### 5.2. Installation aus dem Git-Repository (Beispiel)

1. `git` installieren, falls noch nicht vorhanden (https://git-scm.com/download/win).
2. Repository klonen (Beispiel-URL, ggf. mit der echten Qwen-TTS-URL ersetzen):

   ```bat
   git clone https://github.com/QwenLM/Qwen3-TTS.git
   cd Qwen3-TTS
   ```

3. AbhÃ¤ngigkeiten installieren (typische Varianten):

   ```bat
   pip install -e .
   ```

   oder wenn es eine `requirements.txt` gibt:

   ```bat
   pip install -r requirements.txt
   ```

## 6. Hugging Face und Modell nutzen

1. Installiere die Hugging-Face-Bibliotheken:

   ```bat
   pip install torch --index-url https://download.pytorch.org/whl/cu121
   pip install transformers accelerate datasets huggingface-hub
   ```

2. Melde dich optional bei Hugging Face an (fÃ¼r private Modelle oder hÃ¶here Limits):

   ```bat
   pip install huggingface_hub[cli]
   huggingface-cli login
   ```

3. Grundlegendes Python-Beispiel, um das Modell `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` zu laden (Beispiel â€“ an die konkrete API der Implementation anpassen):

   ```python
   from transformers import AutoModelForSpeechSeq2Seq, AutoTokenizer

   model_id = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"

   model = AutoModelForSpeechSeq2Seq.from_pretrained(model_id)
   tokenizer = AutoTokenizer.from_pretrained(model_id)

   text = "Hallo, das ist ein Test mit Qwen TTS."
   inputs = tokenizer(text, return_tensors="pt")
   with torch.no_grad():
       audio = model.generate(**inputs)

   # Speichern oder weiterverarbeiten des Audios je nach API der Bibliothek
   ```

   Beachte: Die tatsÃ¤chliche API kann sich je nach Bibliothek Ã¤ndern; prÃ¼fe dafÃ¼r die Dokumentation des Modells auf Hugging Face.

## 7. Environment spÃ¤ter wieder aktivieren

Wenn du spÃ¤ter weiterarbeiten mÃ¶chtest:

1. In `G:\QwenTTS` wechseln:
   ```bat
   G:
   cd G:\QwenTTS
   ```

2. venv wieder aktivieren (Beispiel PowerShell):
   ```powershell
   G:\QwenTTS\.venv\Scripts\Activate.ps1
   ```

Danach kannst du wieder `python` und `pip` innerhalb der virtuellen Umgebung verwenden.


Установлено Qwen3-TTS на Windows с Python 3.12:

ПУТИ:
- venv: G:\QwenTTS\.venv
- Репозиторий: G:\QwenTTS\Qwen3-TTS
- Модели Qwen (по 4GB каждая):
  * D:\ComfyUi\models\tts_models\qwen3-tts\CustomVoice\
  * D:\ComfyUi\models\tts_models\qwen3-tts\VoiceDesign\
  * D:\ComfyUi\models\tts_models\qwen3-tts\Base\
- Модель Silero (38MB):
  * D:\ComfyUi\models\tts_models\silero_ru\v4_ru.pt

УСТАНОВЛЕНО:
- PyTorch 2.5.1+cu121
- transformers 4.57.3
- qwen-tts 0.0.4
- gradio 6.4.0
- omegaconf 2.3.0

ЗАПУСК ОТДЕЛЬНЫХ МОДЕЛЕЙ (работает):
cd G:\QwenTTS
.\.venv\Scripts\Activate.ps1
qwen-tts-demo Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice --port 7860 --no-flash-attn

ЗАДАЧА:
Создать единый Gradio интерфейс с выбором из 4 моделей (3 Qwen + Silero).
Проблема: QwenTTSInference не найден. Нужно узнать правильный import и API для qwen-tts.

Посмотри в G:\QwenTTS\Qwen3-TTS\qwen_tts\inference\ и examples\ чтобы понять правильный способ использования моделей из локальных папок.