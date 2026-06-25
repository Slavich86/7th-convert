# 7th VFX convertor

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

```bash
python3 -m pip install PySide6 PyOpenColorIO OpenEXR
```

Або встановити залежності зі списку проєкту:

```bash
python3 -m pip install -r requirements.txt
```

## Запуск

Рекомендований запуск з кореня репозиторію:

```bash
./7th-vfx-convertor.sh
```

Launcher перевіряє потрібні системні інструменти та Python-модулі перед запуском UI.

Прямий запуск через Python теж доступний:

```bash
python3 -m seventh_convert.ui
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

```text
mov, mp4, mkv, ts, mxf, m4v, avi, webm
```

Зображення та image sequence:

```text
exr, dpx, png, jpg, jpeg, tga, targa, tif, tiff, gif
```

Аудіо:

```text
wav, mp3, aac, m4a, flac, ogg
```

## Підтримувані output формати

Image sequence / images:

```text
EXR
DPX
PNG
JPG
TARGA
GIF
```

Video:

```text
MOV
MP4
```

Заплановані video output контейнери:

```text
MKV
WEBM
MXF
AVI
```

Для цих контейнерів ще треба окремо налаштувати codec, audio, metadata та validation rules перед тим, як вмикати їх у UI.

Audio:

```text
WAV
MP3
AAC
```

## Кодеки та налаштування

MP4:

```text
H.264
H.265
H.264 NVENC
H.265 NVENC
```

MOV:

```text
ProRes
```

EXR:

```text
16-bit half float за замовчуванням
Compression: none, zip1, zip16, rle
```

PNG:

```text
RGB 8-bit
RGBA 8-bit
RGB 16-bit
RGBA 16-bit
```

JPG:

```text
Quality slider 0-100
```

GIF:

```text
Optimized palette
Sierra dithering
Bayer dithering
No dithering
```

Audio:

```text
WAV 16-bit: 48 kHz, 44.1 kHz, 24 kHz, 14 kHz, 8 kHz
MP3: до 256 kb/s
AAC: до 256 kb/s
```

## Color Management

Програма підтримує color transform для input і output окремо.

Базові трансформи:

```text
None
sRGB
Linear
Rec.709
```

Також є Color Management через OCIO:

```text
Nuke Default OCIO
Custom OCIO config
ACES через custom OCIO config
```

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
