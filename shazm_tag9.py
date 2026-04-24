# MP3 Shazam Tagger PRO (Windows GUI)
# Requirements:
# pip install shazamio mutagen pillow requests aiohttp-retry

import os
import asyncio
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from shazamio import Shazam, SearchParams, HTTPClient
from aiohttp_retry import ExponentialRetry
from mutagen.id3 import ID3, APIC
from mutagen.mp3 import MP3
from mutagen import MutagenError
import requests
from PIL import Image, ImageTk
from io import BytesIO
import subprocess
from aiohttp.client_exceptions import ClientConnectorDNSError

STOP_FLAG = False

async def recognize_song(file_path, attempts=1, max_timeout=5, segment_duration_seconds=12):
    shazam = Shazam(
        http_client=HTTPClient(
            retry_options=ExponentialRetry(
                attempts=12,
                max_timeout=220,
                statuses={500, 502, 503, 504, 429}
            ),
        ),
        segment_duration_seconds=segment_duration_seconds,
    )

    out = await shazam.recognize(
        file_path,
        options=SearchParams(segment_duration_seconds=segment_duration_seconds),
    )

    try:
        track = out['track']
        title = track['title']
        artist = track['subtitle']

        album_data = track.get('sections', [{}])[0].get('metadata', [])
        album_name = None
        year = None

        for item in album_data:
            if item.get('title') == 'Album':
                album_name = item.get('text')
            if item.get('title') == 'Released':
                year = item.get('text')

        genre = track.get('genres', {}).get('primary')

        cover = track['images']['coverart']
        cover = cover.replace("-t500x500", "-t3000x3000")

        return title, artist, album_name, genre, year, cover
    except:
        return None, None, None, None, None, None


def cut_audio_segment(input_path, start_minute, start_second):
    start_sec = start_minute * 60 + start_second

    base, ext = os.path.splitext(input_path)
    temp_path = f"{base}_cut_{start_sec}s.mp3"

    counter = 1
    while os.path.exists(temp_path):
        temp_path = f"{base}_cut_{start_sec}s_{counter}.mp3"
        counter += 1

    try:
        subprocess.run([
            "ffmpeg",
            "-y",
            "-ss", str(start_sec),
            "-t", "30",
            "-i", input_path,
            "-acodec", "libmp3lame",
            "-ab", "128k",
            temp_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        return temp_path
    except:
        return None


def download_cover(url, original_png):
    data = requests.get(url).content

    if original_png:
        return data

    img = Image.open(BytesIO(data))
    img = img.resize((1000, 1000))

    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def tag_file(file_path, title, artist, album, genre, year, cover_data):
    try:
        audio = MP3(file_path, ID3=ID3)

        try:
            audio.add_tags()
        except:
            pass

        tags = audio.tags

        from mutagen.id3 import TIT2, TPE1, TALB, TCON, TDRC

        tags.delall('TIT2')
        tags.delall('TPE1')
        tags.delall('TALB')
        tags.delall('TCON')
        tags.delall('TDRC')
        tags.delall('APIC')

        tags.add(TIT2(encoding=3, text=title))
        tags.add(TPE1(encoding=3, text=artist))

        if album:
            tags.add(TALB(encoding=3, text=album))
        if genre:
            tags.add(TCON(encoding=3, text=genre))
        if year:
            tags.add(TDRC(encoding=3, text=year))

        tags.add(APIC(
            encoding=3,
            mime='image/png' if cover_data[:8].startswith(b'\x89PNG') else 'image/jpeg',
            type=3,
            desc='Cover',
            data=cover_data
        ))

        audio.save(v2_version=3)
        return True

    except MutagenError as e:
        if 'Permission denied' in str(e):
            return "LOCKED"
        return False


def rename_file(file_path, artist, title, fmt):
    directory = os.path.dirname(file_path)
    name = fmt.replace("{artist}", artist).replace("{title}", title)
    name = name.replace("/", "-").replace("\\", "-")
    new_path = os.path.join(directory, name + ".mp3")

    if not os.path.exists(new_path):
        os.rename(file_path, new_path)
        return new_path
    return file_path


async def process_files(files, app):
    global STOP_FLAG

    for i, file_path in enumerate(files):
        if STOP_FLAG:
            app.log("⏹ Остановлено")
            break

        app.log(f"Обработка: {os.path.basename(file_path)}")

        try:
            title, artist, album, genre, year, cover_url = await recognize_song(
                file_path,
                app.attempts_var.get(),
                app.timeout_var.get(),
                app.segment_var.get()
            )
        except ClientConnectorDNSError:
            app.log("❌ Нет соединения")
            continue
        except Exception:
            app.log("❌ Ошибка соединения")
            continue

        if not title and app.cut_var.get():
            app.log("✂️ Пробуем с другого момента")
            cut_file = cut_audio_segment(file_path, app.minute_var.get(), app.second_var.get())

            if cut_file:
                try:
                    title, artist, album, genre, year, cover_url = await recognize_song(
                        cut_file,
                        12,
                        220,
                        12
                    )
                except ClientConnectorDNSError:
                    app.log("❌ Нет соединения")
                    continue

        if not title:
            app.log("❌ Не распознано (пропуск)")
            continue

        cover_data = download_cover(cover_url, app.png_var.get())

        result = tag_file(file_path, title, artist, album, genre, year, cover_data)

        if result == "LOCKED":
            app.log("🔒 Файл занят другим процессом")
            continue
        elif not result:
            app.log("❌ Ошибка записи")
            continue

        if app.rename_var.get():
            file_path = rename_file(file_path, artist, title, app.format_var.get())

        app.show_cover(cover_data)
        app.log(f"✅ {artist} - {title}")
        app.update_progress((i + 1) / len(files) * 100)


class App:
    def __init__(self, root):
        self.root = root
        self.root.iconbitmap("C:\\tag.ico")
        self.root.title("MP3 Shazam Tagger PRO")
        self.root.geometry("900x600")

        self.files = []

        self.rename_var = tk.BooleanVar(value=True)
        self.format_var = tk.StringVar(value="{artist}-{title}")
        self.png_var = tk.BooleanVar(value=False)
        self.cut_var = tk.BooleanVar(value=True)

        self.minute_var = tk.IntVar(value=1)
        self.second_var = tk.IntVar(value=0)

        self.attempts_var = tk.IntVar(value=1)
        self.timeout_var = tk.IntVar(value=5)
        self.segment_var = tk.IntVar(value=12)

        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=5)

        tk.Button(btn_frame, text="Добавить файлы", command=self.add_files_dialog).pack(side='left', padx=5)
        tk.Button(btn_frame, text="Добавить папку", command=self.add_folder).pack(side='left', padx=5)
        tk.Button(btn_frame, text="Снять выделение", command=self.clear_selection).pack(side='left', padx=5)
        tk.Button(btn_frame, text="Очистить список", command=self.clear_list).pack(side='left', padx=5)
        tk.Button(btn_frame, text="Старт", command=self.start).pack(side='left', padx=5)
        tk.Button(btn_frame, text="Стоп", command=self.stop).pack(side='left', padx=5)

        self.counter_label = tk.Label(root, text="Выбрано: 0")
        self.counter_label.pack()

        rename_frame = tk.Frame(root)
        rename_frame.pack(fill='x', padx=10)

        tk.Checkbutton(rename_frame, text="Переименовывать", variable=self.rename_var).pack(side='left', anchor='w')
        tk.Entry(rename_frame, textvariable=self.format_var).pack(side='left', fill='x', expand=True, padx=5)

        cut_frame1 = tk.Frame(root)
        cut_frame1.pack(fill='x', padx=10)
        
        tk.Checkbutton(cut_frame1, text="Сохранить обложку в Оригинальном PNG (3000x3000)", variable=self.png_var).pack(side='left', anchor='w')

        cut_frame = tk.Frame(root)
        cut_frame.pack(fill='x', padx=10)

        tk.Checkbutton(cut_frame, text="При неудачной попатке, Распознать с другой минуты", variable=self.cut_var).pack(side='left', anchor='w')

        tk.Label(cut_frame, text="Мин:").pack(side='left')
        tk.Entry(cut_frame, width=5, textvariable=self.minute_var).pack(side='left')

        tk.Label(cut_frame, text="Сек:").pack(side='left')
        tk.Entry(cut_frame, width=5, textvariable=self.second_var).pack(side='left')

        list_frame = tk.Frame(root)
        list_frame.pack(fill='both', expand=True, padx=10, pady=5)

        list_scroll = tk.Scrollbar(list_frame)
        list_scroll.pack(side='right', fill='y')

        self.listbox = tk.Listbox(list_frame, selectmode=tk.MULTIPLE, yscrollcommand=list_scroll.set)
        self.listbox.pack(side='left', fill='both', expand=True)
        list_scroll.config(command=self.listbox.yview)

        self.listbox.bind('<<ListboxSelect>>', lambda e: self.update_counter())

        log_cover_frame = tk.Frame(root)
        log_cover_frame.pack(fill='both', expand=True, padx=10, pady=5)

        log_frame = tk.Frame(log_cover_frame)
        log_frame.pack(side='left', fill='both', expand=True)

        cover_frame = tk.Frame(log_cover_frame)
        cover_frame.pack(side='right', fill='y')

        log_scroll = tk.Scrollbar(log_frame)
        log_scroll.pack(side='right', fill='y')

        self.log_box = tk.Text(log_frame, height=8, yscrollcommand=log_scroll.set)
        self.log_box.pack(side='left', fill='both', expand=True)
        log_scroll.config(command=self.log_box.yview)

        self.cover_label = tk.Label(cover_frame)
        self.cover_label.pack(pady=5)

        self.progress = ttk.Progressbar(root, length=400)
        self.progress.pack(side='bottom', fill='x', padx=10, pady=5)

    def update_counter(self):
        selected = len(self.listbox.curselection())
        self.counter_label.config(text=f"Выбрано: {selected}")

    def add_files_dialog(self):
        files = filedialog.askopenfilenames(filetypes=[("MP3 files", "*.mp3")])
        for f in files:
            self.files.append(f)
            self.listbox.insert(tk.END, f)
        self.select_all()

    def add_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            for root, _, files in os.walk(folder):
                for f in files:
                    if f.lower().endswith('.mp3'):
                        path = os.path.join(root, f)
                        self.files.append(path)
                        self.listbox.insert(tk.END, path)
        self.select_all()

    def select_all(self):
        self.listbox.select_set(0, tk.END)
        self.update_counter()

    def clear_selection(self):
        self.listbox.selection_clear(0, tk.END)
        self.update_counter()

    def clear_list(self):
        self.listbox.delete(0, tk.END)
        self.files.clear()
        self.update_counter()

    def log(self, text):
        self.log_box.insert(tk.END, text + "\n")
        self.log_box.see(tk.END)

    def update_progress(self, val):
        self.progress['value'] = val

    def show_cover(self, data):
        img = Image.open(BytesIO(data))
        img = img.resize((150, 150))
        self.tk_img = ImageTk.PhotoImage(img)
        self.cover_label.config(image=self.tk_img)

    def start(self):
        global STOP_FLAG
        STOP_FLAG = False

        selected = self.listbox.curselection()
        files_to_process = [self.files[i] for i in selected]

        if not files_to_process:
            messagebox.showerror("Ошибка", "Нет выбранных файлов")
            return

        threading.Thread(target=self.run_async, args=(files_to_process,), daemon=True).start()

    def stop(self):
        global STOP_FLAG
        STOP_FLAG = True

    def run_async(self, files):
        asyncio.run(process_files(files, self))


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
