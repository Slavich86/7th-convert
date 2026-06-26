<p align="center">
  <img src="docs/images/social-preview.jpg" alt="7th VFX convertor" width="900">
</p>

<p align="center">
  <strong>Десктопний VFX media converter для Linux.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11%2B-blue" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/UI-Qt%206-green" alt="Qt 6">
  <img src="https://img.shields.io/badge/Platform-Linux-lightgrey" alt="Linux">
  <img src="https://img.shields.io/github/v/release/Slavich86/7th-convert?include_prereleases" alt="Release">
</p>

<p align="center">
  <a href="#встановлення-системних-залежностей">Встановлення</a> &bull;
  <a href="#запуск">Запуск</a> &bull;
  <a href="#підтримувані-input-файли">Input формати</a> &bull;
  <a href="#підтримувані-output-формати">Output формати</a> &bull;
  <a href="#хоткеї">Хоткеї</a> &bull;
  <a href="README.md">English</a>
</p>

---

**7th VFX convertor** - десктопний конвертор медіа для VFX-пайплайнів.

Програма створена для конвертації відео, image sequence, окремих зображень та аудіо з контролем кольору, розміру, pixel aspect, In/Out range і пресетів.

Поточна версія написана на Python з Qt 6 Widgets UI і використовує `ffmpeg` / `ffprobe` для аналізу та конвертації медіа.

![Головне вікно 7th VFX convertor](docs/images/main-window.png)

## Встановлення системних залежностей

`ffmpeg` і `ffprobe` мають бути встановлені в системі та доступні через `PATH`.

Назва Linux-дистрибутива | Команда
--- | ---
Fedora, офіційний free-build | `sudo dnf install ffmpeg-free`
Fedora, RPM Fusion / full codecs | `sudo dnf install ffmpeg --allowerasing`
Ubuntu | `sudo apt update && sudo apt install ffmpeg`
Debian | `sudo apt update && sudo apt install ffmpeg`
Linux Mint | `sudo apt update && sudo apt install ffmpeg`
Pop!_OS | `sudo apt update && sudo apt install ffmpeg`
Arch Linux | `sudo pacman -S ffmpeg`
Manjaro | `sudo pacman -S ffmpeg`
EndeavourOS | `sudo pacman -S ffmpeg`
openSUSE Tumbleweed / Leap | `sudo zypper install ffmpeg`
Alpine Linux | `sudo apk add ffmpeg`

## Встановлення Python-залежностей

Потрібен Python 3.11+.

Рекомендовано: встановити `uv`, після цього launcher сам створить `.venv` і поставить Python-залежності туди:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
./7th-vfx-convertor.sh
```

Launcher створює:

```text
.venv/
```

і встановлює залежності командою:

```bash
uv pip install --python .venv/bin/python --link-mode=copy -r requirements.txt
```

Fallback без `uv`:

```bash
python3 -m pip install -r requirements.txt
```

## Запуск

Рекомендований запуск з кореня репозиторію:

```bash
./7th-vfx-convertor.sh
```

Launcher перевіряє потрібні системні інструменти та Python-модулі перед запуском UI.
Якщо доступний `uv`, launcher автоматично готує локальну `.venv` і запускає UI через `.venv/bin/python`.

Прямий запуск через Python теж доступний:

```bash
.venv/bin/python -m seventh_convert.ui
```

## Desktop Launcher

У репозиторії є:

```text
7th-vfx-convertor.desktop
7th-vfx-convertor.sh
```

`.desktop` файл очікує, що `7th-vfx-convertor.sh` лежить у тій самій теці. Якщо файловий менеджер блокує запуск desktop-файлів, зроби обидва файли executable або запускай shell launcher напряму.

## Що вміє програма

- Відкривати відеофайли, зображення, image sequence та аудіо.
- Автоматично знаходити image sequence.
- Розбивати sequence на окремі частини, якщо є пропущені кадри.
- Показувати preview у плеєрі.
- Показувати поточний кадр.
- Ставити In / Out markers.
- Конвертувати весь файл або тільки вибраний range.
- Конвертувати через чергу jobs.
- Зберігати і завантажувати user presets.
- Запам'ятовувати останні папки для відео/image input, audio input та presets.
- Показувати Media Info і Metadata.
- Перетягувати файли через Drag and Drop.
- Відкривати output folder після конвертації.

## Підтримувані input файли

Відео:

`mov, mp4, mkv, ts, mxf, m4v, avi, webm`

Зображення та image sequence:

`exr, dpx, png, jpg, jpeg, tga, targa, tif, tiff, gif`

Аудіо:

`wav, mp3, aac, m4a, flac, ogg`

## Підтримувані output формати

Image sequence / images:

`EXR, DPX, PNG, JPG, TARGA, GIF`

Video:

`MOV, MP4`

Заплановані video output контейнери:

`MKV, WEBM, MXF, AVI`

Для цих контейнерів ще треба окремо налаштувати codec, audio, metadata та validation rules перед тим, як вмикати їх у UI.

Audio:

`WAV, MP3, AAC`

## Кодеки та налаштування

MP4:

`H.264, H.265, H.264 NVENC, H.265 NVENC`

MOV:

`ProRes`

EXR:

`16-bit half float за замовчуванням, compression: none, zip1, zip16, rle`

PNG:

`RGB 8-bit, RGBA 8-bit, RGB 16-bit, RGBA 16-bit`

JPG:

`Quality slider 0-100`

GIF:

`Optimized palette, Sierra dithering, Bayer dithering, No dithering`

Audio:

`WAV 16-bit: 48 kHz, 44.1 kHz, 24 kHz, 14 kHz, 8 kHz; MP3: до 256 kb/s; AAC: до 256 kb/s`

## Image Geometry та Pixel Aspect

Блок Image керує scale, output size, pixel aspect та тим, як записувати non-square pixels.

- `Scale` пропорційно змінює output size.
- `Output Size` можна редагувати напряму; width і height зберігають співвідношення сторін source.
- `Pixel Aspect: Auto` читає pixel aspect з metadata, якщо він є, включно з EXR `pixelAspectRatio`.
- `Pixel Aspect: Manual` дозволяє ввести PAR вручну, якщо metadata немає або вона неправильна.
- `Manual PAR` у режимі Auto disabled, але показує знайдене значення.
- `Pixel Aspect Output: Keep Original Pixels` зберігає raster size і non-square pixel intent.
- `Pixel Aspect Output: Resize to Square Pixels` розтягує зображення до square pixels, що потрібно для анаморфних source.

Приклад: EXR sequence `2880 x 2160` з PAR `2.0` у preview виглядає як `5760 x 2160` display aspect.

## Робота зі звуком

Конвертор може працювати із source audio, external audio або audio-only output.

- `Audio Input` дозволяє додати окремий audio file.
- Drag and Drop одного audio file заповнює `Audio Input`.
- Підтримуваний audio input: `wav, mp3, aac, m4a, flac, ogg`.
- Підтримуваний audio output: `WAV, MP3, AAC`.
- Video files з embedded audio можуть використовувати `Copy Source Audio`, `AAC`, `MP3` або `WAV`.
- Для MP4 з external audio режим `Add Audio Without Re-encoding Video` копіює video stream і тільки кодує/додає audio.
- У цьому режимі resize, pixel aspect, FPS і color-transform controls disabled, бо video не перекодовується.

## Color Management

Програма підтримує color transform для input і output окремо.

Базові трансформи:

```text
None
sRGB
Linear
Rec.709
```

Режими Color Management:

```text
None
Nuke Default OCIO
Custom OCIO config
ACES через custom OCIO config
```

У режимі `None` керування кольором вимкнене. Поля Input Transform та Output Transform приховані, а конвертор не застосовує OCIO LUT, FFmpeg color transform або примусові output color metadata.

У режимі Nuke використовується вбудований Nuke-style OCIO config.

У режимі OCIO можна вибрати свій `config.ocio`, наприклад ACES config, якщо він встановлений на машині.

## Presets

Програма підтримує збереження і завантаження user presets.

Preset зберігає налаштування конвертора:

```text
Input
Audio Input
Output
File Type
Codec
Codec Profile / Compression
FPS
In / Out
Scale
Output Size
Pixel Aspect
Color Management
Input Transform
Output Transform
Audio settings
Overwrite mode
Add Audio Without Re-encoding Video mode
```

User presets зберігаються локально:

```text
~/.config/7th_VFX_convertor/presets/
```

## Hotkeys

Хоткеї працюють у зоні плеєра, preview placeholder, Play button і timeline slider.

Вони не перехоплюються, коли користувач вводить текст у поля In / Out.

```text
Left Arrow  - назад на 1 кадр
Right Arrow - вперед на 1 кадр
Up Arrow    - вперед на 10 кадрів
Down Arrow  - назад на 10 кадрів
I           - поставити In marker
O           - поставити Out marker
```

## Donate

Підтримати розвиток конвертора:

```text
PayPal: sl.oxuta@gmail.com
```

## Статус

Це робочий прототип у активній розробці. UI, presets, backend contract і частина поведінки ще можуть змінюватися.
