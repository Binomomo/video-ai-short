"""Cerita Bersambung: HOROR, MISTERI, KOMEDI (animasi kartun, tanpa GPU).

Alur: topics.csv (seri & part) -> naskah per adegan (Gemini, nyambung dengan part sebelumnya lewat state.json)
      -> suara (edge-tts, kata demi kata) -> animasi (anim.py) -> video (FFmpeg) -> upload YouTube.

Uji tanpa API key / tanpa internet:  python main.py --uji
"""
import asyncio
import csv
import json
import os
import re
import zlib
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import anim
from anim import EFEK, EMOSI, LATAR, POSE, SHOT, render_kartu_akhir, render_segmen

UJI = "--uji" in sys.argv
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_MODEL", "gemini-3.5-flash,gemini-3.1-flash-lite,gemini-2.5-flash-lite").split(",") if m.strip()]
VOICE = os.environ.get("TTS_VOICE", "id-ID-ArdiNeural")           # suara narator (pria)
VOICE_WANITA = os.environ.get("TTS_VOICE_WANITA", "id-ID-GadisNeural")  # dipakai bila tokoh wanita yang bicara
MENANG = os.environ.get("MENANG", "").strip()                      # hasil voting penonton: "1" atau "2" (opsional)
PRIVACY = os.environ.get("YT_PRIVACY", "private")                 # private / unlisted / public
UPLOAD = os.environ.get("UPLOAD", "1") == "1" and not UJI
SIMPAN = os.environ.get("SIMPAN_KEMAJUAN", "0") == "1" and not UJI   # maju ke part berikutnya walau tanpa upload
WORK = Path("work")
WORK.mkdir(exist_ok=True)
TOPIK_CSV, STATE_JSON = Path("topics.csv"), Path("state.json")
KOLOM = ["seri", "genre", "part", "total", "premis", "arah", "status"]
USIA = ("anak", "dewasa", "tua")
GENDER, KULIT, BAJU = ("pria", "wanita"), ("sawo", "terang", "gelap"), tuple(anim.BAJU)


# ================================================================ genre
GENRE = {
    "horor": dict(
        gaya="cerita horor bersambung. Gaya bercerita pelan, dingin, jujur, seperti pengakuan",
        rate=-6, pitch=-6, hashtag="#horor #ceritahoror #bersambung #shorts #animasi",
        tag=["horor", "cerita horor", "horor indonesia"], kunci="seram lewat suasana dan implikasi",
        akhir="Ikuti untuk seri horor berikutnya"),
    "misteri": dict(
        gaya="cerita misteri bersambung (teka-teki, petunjuk, kejanggalan yang bisa dipecahkan). Gaya bercerita tegang, rasional, "
             "seperti detektif amatir yang menyusun petunjuk",
        rate=-3, pitch=-3, hashtag="#misteri #teka_teki #bersambung #shorts #animasi",
        tag=["misteri", "cerita misteri", "teka teki"], kunci="penasaran lewat petunjuk yang saling bertentangan",
        akhir="Ikuti untuk misteri berikutnya"),
    "komedi": dict(
        gaya="cerita komedi bersambung (situasi absurd yang makin kacau). Gaya bercerita cepat, jenaka, banyak dialog antar tokoh, "
             "ada punchline di akhir tiap adegan penting",
        rate=7, pitch=3, hashtag="#komedi #lucu #bersambung #shorts #animasi",
        tag=["komedi", "cerita lucu", "animasi lucu"], kunci="lucu lewat situasi dan dialog, bukan ejekan fisik atau SARA",
        akhir="Ikuti biar nggak ketinggalan part lucunya"),
    "drama": dict(
        gaya="cerita drama keluarga/kehidupan bersambung. Gaya bercerita pelan, hangat, emosional, jujur seperti curhat kepada teman",
        rate=-4, pitch=-2, hashtag="#drama #ceritakeluarga #bersambung #shorts #animasi",
        tag=["drama", "cerita keluarga", "kisah nyata rasa"], kunci="emosi lewat konflik dan rahasia keluarga, tanpa kekerasan",
        akhir="Ikuti biar tahu kelanjutan kisahnya"),
    "thriller": dict(
        gaya="cerita thriller bertahan hidup bersambung (waktu terbatas, tekanan tinggi, tanpa hantu). Gaya bercerita cepat, tegang, kalimat pendek",
        rate=3, pitch=-2, hashtag="#thriller #tegang #bersambung #shorts #animasi",
        tag=["thriller", "cerita tegang", "bertahan hidup"], kunci="tegang lewat waktu yang menipis dan kecurigaan antar tokoh, tanpa gore",
        akhir="Ikuti sebelum waktunya habis"),
    "legenda": dict(
        gaya="cerita legenda/mitos Nusantara versi ringan dengan twist modern. Gaya bercerita tenang seperti dongeng malam hari, sedikit misterius",
        rate=-5, pitch=-4, hashtag="#legenda #mitos #nusantara #bersambung #shorts #animasi",
        tag=["legenda", "mitos nusantara", "cerita rakyat"], kunci="hormat pada adat dan budaya, tanpa menjelekkan agama atau suku",
        akhir="Ikuti untuk legenda berikutnya"),
    "fiksi": dict(
        gaya="cerita fiksi ilmiah ringan bersambung (putaran waktu, dunia yang berubah, teknologi aneh). Gaya bercerita cerdas, penasaran, tenang tapi menggigit",
        rate=0, pitch=-2, hashtag="#fiksiilmiah #sciencefiction #bersambung #shorts #animasi",
        tag=["fiksi ilmiah", "sci-fi", "putaran waktu"], kunci="penasaran lewat aturan dunia yang aneh dan perlahan terungkap",
        akhir="Ikuti untuk bagian berikutnya"),
}

TANPA_SERAM = ("komedi", "drama", "thriller", "fiksi")   # emosi 'seram' (mata menyala) hanya untuk horor/misteri/legenda
TANPA_CCTV = ("komedi", "drama", "legenda")


def genre_row(row):
    g = (row.get("genre") or "horor").strip().lower()
    return g if g in GENRE else "horor"


def parse_hz(x):
    return int(re.sub(r"[^0-9+-]", "", str(x)) or 0)


PROFIL = {  # (voice-gender, usia): (selisih nada Hz dari nada narator, selisih kecepatan %)
    ("pria", "anak"): (24, 8), ("pria", "dewasa"): (9, 3), ("pria", "tua"): (-8, -14),
    ("wanita", "anak"): (22, 8), ("wanita", "dewasa"): (4, 2), ("wanita", "tua"): (-12, -16),
}


def atur_suara(row, nama, narator, gender, usia=None):
    """(voice, rate, pitch) per pembicara.
    Narator = suara bercerita (nada genre). Tokoh lain = suara sendiri sesuai gender dan usia (nenek, kakek, anak, dst)."""
    g = GENRE[genre_row(row)]
    usia = usia or {}
    rate0 = parse_hz(os.environ.get("TTS_RATE") or g["rate"])
    base = parse_hz(os.environ.get("TTS_PITCH") or g["pitch"])
    voice_pria = lambda gd: VOICE_WANITA if gd == "wanita" else VOICE
    if not nama or nama == narator:                        # penceritaan: suara narator
        return voice_pria(gender.get(narator)), f"{rate0:+d}%", f"{base:+d}Hz"
    gd, u = gender.get(nama, "pria"), usia.get(nama, "dewasa")
    dp, dr = PROFIL.get((gd, u), (6, 0))
    var = (zlib.crc32(nama.encode()) % 5 - 2) * 2          # -4..+4 Hz: tokoh sebaya tetap terdengar beda
    # dialog dibacakan dengan nada dasar netral (tidak ikut nada seram narator) supaya beda dari narasi
    pitch = (base if gd == gender.get(narator) else 0) + dp + var
    return voice_pria(gd), f"{rate0 + dr:+d}%", f"{pitch:+d}Hz"


# ================================================================ topik & state
def baca_topik():
    with open(TOPIK_CSV, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    premis, genre = {}, {}
    for r in rows:  # premis cukup ditulis di part 1; part berikutnya mewarisinya
        r["seri"] = (r.get("seri") or "").strip()
        if (r.get("premis") or "").strip():
            premis[r["seri"]] = r["premis"].strip()
        r["premis"] = premis.get(r["seri"], "")
        if (r.get("genre") or "").strip():
            genre[r["seri"]] = r["genre"].strip().lower()
        r["genre"] = genre.get(r["seri"], "horor")
    return rows


def pilih_topik(rows):
    for r in rows:
        if r["seri"] and (r.get("status") or "").strip() != "selesai":
            return r
    sys.exit("Semua part sudah selesai. Tambahkan seri baru di topics.csv")


def simpan_topik(rows):
    with open(TOPIK_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=KOLOM)
        w.writeheader()
        asli = {}
        for r in rows:  # tulis premis hanya di part pertama tiap seri
            pertama = r["seri"] not in asli
            asli[r["seri"]] = True
            w.writerow({k: ((r.get(k) or "") if (pertama or k != "premis") else "") for k in KOLOM})


def muat_state():
    if STATE_JSON.exists():
        return json.loads(STATE_JSON.read_text(encoding="utf-8"))
    return {}


def simpan_state(st):
    STATE_JSON.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


# ================================================================ naskah
FORMAT_JSON = """{
  "judul": "judul pendek mencekam tanpa kata 'Part' (maks 60 karakter)",
  "deskripsi": "2 kalimat teaser tanpa spoiler",
  "tag": ["horor", "ceritahoror", "..."],
  "narator": "nama tokoh 'aku' (isi di part 1, sama di part berikutnya)",
  "tokoh": [{"nama": "Bima", "gender": "pria", "usia": "dewasa", "rambut": "pendek", "warna_rambut": "hitam", "kulit": "sawo", "baju": "abu", "kacamata": false, "aksesori": "jam_tangan"}],
  "bagian": [
    {"narasi": "satu kalimat pendek", "latar": "kamar_malam", "shot": "lebar",
     "tokoh": [{"nama": "Bima", "pose": "berdiri", "emosi": "takut", "aksi": "menunjuk"}],
     "properti": [{"nama": "kamera_cctv", "posisi": "kanan"}], "aksi": "Bima mengangkat kamera dan memasangnya",
     "bicara": "Bima", "cara_bicara": "berbisik", "suasana": "dengung", "efek": ["getar"]},
    {"narasi": "...", "latar": "lorong", "shot": "cctv", "tokoh": [], "cctv": {"jam": "03:00:41", "kecepatan": 1},
     "efek": ["glitch"]}
  ],
  "ringkasan": "3-4 kalimat: apa yang terjadi di part ini (untuk kesinambungan part berikutnya)",
  "cliffhanger": "1 kalimat: kejadian terakhir yang menggantung",
  "kartu_akhir": {"waktu": "03:00:00", "teks": "JANGAN BUKA PINTU JAM 03.00"},
  "pilihan": ["arah A untuk part berikutnya (maks 4 kata)", "arah B untuk part berikutnya (maks 4 kata)"]
}"""

CONTOH = [  # contoh irama kalimat (bukan untuk disalin isinya)
    "Jangan pernah buka pintu kalau ada yang mengetuk jam tiga pagi.", "Aku Bima. Ini istriku, Laras.",
    "Kami pindah ke rumah kontrakan yang murah.", "Terlalu murah, sebenarnya.",
    "Malam pertama, pintu depan diketuk. Satu kali.", "Malam kedua, dua kali.", "Malam ketujuh, tujuh kali.",
    "Jadi kupasang kamera kecil di ruang tamu.", "Tidak ada siapa pun di depan pintu.",
    "Tapi pintunya bergetar dari dalam.", "Rambut panjang. Gaun putih. Itu Laras.",
    "Padahal Laras sedang tidur di sampingku."]


def alur_genre(genre, part, total):
    hook1 = {
        "horor": 'satu larangan atau fakta janggal yang spesifik, mengandung jam atau angka. Contoh bentuk: "Jangan pernah memutar rekaman CCTV jam dua lewat tiga belas."',
        "misteri": 'satu fakta aneh yang bisa diperiksa, mengandung jam, angka, atau tempat. Contoh bentuk: "Setiap hari jam dua lewat tujuh, semua jam di kota ini berhenti."',
        "komedi": 'satu situasi absurd yang langsung bikin penasaran. Contoh bentuk: "Hari pertama kerja, bosku ternyata mengunci pintu kantor dari luar."',
        "drama": 'satu rahasia atau kalimat yang mengguncang. Contoh bentuk: "Surat wasiat kedua ayahku disembunyikan di dalam radio tua."',
        "thriller": 'satu situasi terjebak dengan hitungan waktu. Contoh bentuk: "Oksigen di lift ini tinggal enam puluh menit."',
        "legenda": 'satu pantangan adat yang spesifik dengan jam atau tempat. Contoh bentuk: "Jangan menimba air sumur itu setelah magrib."',
        "fiksi": 'satu fakta mustahil yang spesifik dengan jam atau angka. Contoh bentuk: "Setiap pagi jam enam lewat tiga belas, hari Senin ini mulai lagi."',
    }[genre]
    hookn = "kalimat yang langsung menyambung cliffhanger part sebelumnya (ingatkan apa yang baru terjadi), tetap mengandung jam, angka, atau hal spesifik."
    ujung = {
        "horor": ("TWIST + CLIFFHANGER: kalimat terakhir diawali 'Padahal...' atau pertanyaan yang mengubah arti semua tadi. Kalimat terakhir maks 9 kata.",
                  "JAWABAN + TWIST AKHIR: ungkap penyebabnya, beri satu putaran terakhir yang membuat bulu kuduk berdiri, tutup dengan kalimat pendek yang menggantung."),
        "misteri": ("TWIST + CLIFFHANGER: temuan terakhir membalik dugaan tadi, tutup dengan pertanyaan 'Kalau begitu, siapa...?' atau sejenisnya. Maks 10 kata.",
                    "PENGUNGKAPAN: semua petunjuk tersambung, ungkap pelaku/penyebabnya, beri satu putaran terakhir yang tidak terduga tapi masuk akal."),
        "komedi": ("PUNCHLINE + CLIFFHANGER: kekacauan memuncak lalu muncul kejutan konyol yang membuat part berikutnya wajib ditonton. Maks 10 kata.",
                   "PUNCHLINE AKHIR: semua kekacauan terselesaikan dengan cara yang tak terduga dan lucu, tutup dengan satu kalimat jenaka yang diingat orang."),
        "drama": ("PENGUNGKAPAN SETENGAH + CLIFFHANGER: satu rahasia terbuka tapi memunculkan rahasia yang lebih besar. Tutup dengan kalimat emosional maks 10 kata.",
                  "PENGUNGKAPAN + TWIST EMOSIONAL: kebenaran terungkap, alasan tokoh yang tampak jahat ternyata masuk akal, tutup dengan kalimat hangat yang menyentuh."),
        "thriller": ("TWIST + CLIFFHANGER: waktu hampir habis dan ada temuan yang mengubah siapa yang dicurigai. Kalimat terakhir maks 9 kata.",
                     "PENGUNGKAPAN + TWIST AKHIR: dalang di balik jebakan terungkap, satu kejutan terakhir yang masuk akal, tutup dengan kalimat pendek yang menggantung."),
        "legenda": ("CLIFFHANGER ADAT: pantangan dilanggar dan terjadi tanda aneh pertama yang menakutkan. Maks 10 kata.",
                    "ASAL-USUL + TWIST: ungkap asal-usul pantangan itu, beri satu putaran yang menghubungkan masa lalu dengan tokoh masa kini, tutup dengan kalimat tenang yang membekas."),
        "fiksi": ("TWIST ATURAN DUNIA + CLIFFHANGER: satu aturan dunia terungkap tapi membuat segalanya makin janggal. Maks 10 kata.",
                  "JAWABAN + TWIST AKHIR: ungkap penyebab fenomena itu, beri satu putaran yang mengubah siapa tokoh sebenarnya, tutup dengan kalimat singkat yang menggantung."),
    }[genre][0 if part < total else 1]
    tengah = {
        "horor": ("NORMAL TAPI JANGGAL: 2-3 adegan keseharian dengan satu detail kecil yang terasa salah.",
                  "POLA BERULANG: kejanggalan terjadi lagi, makin jelas (jam yang sama, hitungan yang bertambah).",
                  "BUKTI: tokoh memeriksa (rekaman, pesan, foto). Kalimat jadi sangat pendek (3-6 kata)."),
        "misteri": ("PETUNJUK PERTAMA: 2-3 adegan tokoh menyelidiki dan menemukan satu kejanggalan yang jelas.",
                    "PETUNJUK KEDUA YANG BERTENTANGAN: saksi atau benda yang membuat dugaan pertama diragukan.",
                    "BUKTI: temuan kunci (rekaman, catatan, foto). Kalimat jadi sangat pendek (3-6 kata)."),
        "komedi": ("KEKACAUAN MULAI: 2-3 adegan situasi normal yang mulai salah, dengan dialog cepat antar tokoh.",
                   "KEKACAUAN MEMBESAR: aturan tiga (kejadian ke-1 lucu, ke-2 lebih lucu, ke-3 paling absurd), tokoh saling menyahut.",
                   "REAKSI: tokoh utama menyadari betapa parahnya; kalimat pendek, gaya deadpan."),
        "drama": ("SITUASI NORMAL TAPI GANJIL: 2-3 adegan keseharian keluarga dengan satu detail yang mengusik.",
                  "KONFLIK MEMUNCAK: tokoh berdebat atau menyimpan rahasia; dialog saling menyela, nada naik.",
                  "BUKTI/PENGAKUAN: benda, surat, atau saksi yang mengubah pandangan tokoh. Kalimat jadi pendek dan berat."),
        "thriller": ("SITUASI TERJEBAK: 2-3 adegan tokoh sadar mereka terkurung dan menghitung waktu/sumber daya.",
                     "KECURIGAAN: tokoh saling menuduh, ada detail yang tidak cocok dari cerita masing-masing.",
                     "BUKTI: temuan kunci yang mempersempit tersangka. Kalimat sangat pendek (3-6 kata), sebut sisa waktu."),
        "legenda": ("PANTANGAN DIKENALKAN: 2-3 adegan tokoh dari luar mendengar adat dan menertawakannya.",
                    "PANTANGAN DILANGGAR: tokoh melanggar dan tanda aneh muncul, makin jelas tiap adegan.",
                    "SESEPUH BERCERITA: orang tua desa menjelaskan sebagian asal-usulnya. Kalimat tenang, penuh jeda."),
        "fiksi": ("KEJANGGALAN DUNIA: 2-3 adegan keseharian dengan satu hal yang tidak masuk akal secara fisik.",
                  "POLA & ATURAN: tokoh mencatat pola, menemukan aturan tersembunyi, bertemu orang lain yang sadar.",
                  "BUKTI: catatan, rekaman, atau benda yang memastikan dugaan. Kalimat jadi sangat pendek (3-6 kata)."),
    }[genre]
    return (f"1. HOOK (adegan 1, maks 6 detik): {hook1 if part == 1 else hookn} Jangan mulai dengan \"Tahukah kamu\".\n"
            f"2. PERKENALAN: {"singkat saja, 'Aku [Nama].' lalu tokoh lain." if part == 1 else 'tidak perlu memperkenalkan lagi; sambungkan langsung dari cliffhanger part sebelumnya dalam 1 kalimat.'}\n"
            f"3. {tengah[0]}\n4. {tengah[1]}\n5. {tengah[2]}\n6. {ujung}")


def buat_prompt(row, st):
    part, total = int(row["part"]), int(row["total"])
    genre = genre_row(row)
    g = GENRE[genre]
    s = st.get(row["seri"], {})
    arah = (row.get("arah") or "").strip() or "bebas; lanjutkan secara logis dari cliffhanger sebelumnya dan naikkan taruhannya"
    p = f"""Kamu penulis naskah {g['gaya']}, berbahasa Indonesia, untuk video pendek animasi kartun
(YouTube Shorts / Reels / TikTok, vertikal 9:16, durasi 60-80 detik). Sudut pandang orang pertama ("aku").
Seri: "{row['seri']}" (genre {genre}). Ini PART {part} dari {total}.

PREMIS SERI: {row['premis']}
ARAH CERITA PART INI: {arah}
"""
    if s.get("ringkasan"):
        p += "\nRINGKASAN PART SEBELUMNYA:\n" + "\n".join(s["ringkasan"]) + "\n"
        p += f"CLIFFHANGER TERAKHIR: {s.get('cliffhanger', '')}\n"
        p += f"NARATOR: {s.get('narator', '')}. TOKOH YANG SUDAH ADA (pakai persis, jangan diubah): " \
             f"{json.dumps(s.get('tokoh', []), ensure_ascii=False)}\n"
    dialog = {"horor": "2-3 adegan boleh berupa ucapan tokoh lain (dalam tanda kutip, isi \"bicara\" dengan nama pembicara).",
              "misteri": "4-6 adegan berupa dialog singkat antar tokoh (saksi, rekan, tokoh utama); isi \"bicara\" dengan nama pembicara.",
              "komedi": "6-9 adegan berupa dialog cepat antar tokoh dengan punchline; ganti pembicara tiap 1-2 adegan, isi \"bicara\" dengan nama pembicara.",
              "drama": "5-8 adegan berupa dialog emosional antar tokoh (bergantian pembicara); isi \"bicara\" dengan nama pembicara.",
              "thriller": "5-7 adegan berupa dialog pendek dan tegang antar tokoh yang saling curiga; isi \"bicara\" dengan nama pembicara.",
              "legenda": "3-5 adegan berupa ucapan sesepuh atau tokoh desa (tokoh tua usia \"tua\"); isi \"bicara\" dengan nama pembicara.",
              "fiksi": "4-6 adegan berupa dialog singkat dan cerdas antar tokoh; isi \"bicara\" dengan nama pembicara."}[genre]
    p += f"""
ALUR KHAS (wajib diikuti urutannya):
{alur_genre(genre, part, total)}

ATURAN NARASI:
- Total narasi 170-210 kata, 18-22 adegan. Satu adegan = satu kalimat utuh (maks 13 kata), bukan potongan frasa. Setiap adegan harus punya sebab-akibat dan mengubah keadaan cerita; jangan mengulang informasi. Pastikan ada pembuka, pengenalan tokoh/lokasi, pemicu masalah, usaha pertama, kegagalan, petunjuk/bukti, keputusan, klimaks, akibat, dan cliffhanger. Irama seperti ini: {' / '.join(CONTOH[:6])}
- DIALOG: {dialog} Suara tiap tokoh dibedakan otomatis berdasarkan gender dan usia (nenek, kakek, anak, pria, wanita), jadi pembicara harus jelas.
- PEMBAGIAN SUARA: adegan penceritaan = kalimat narator (isi "bicara" dengan narator bila ia tampil di layar, kosongkan bila tidak). Adegan dialog = HANYA kata-kata tokoh itu sendiri, tanpa "katanya"/"ujar", tanpa tanda kutip, dan "bicara" wajib diisi nama pembicara. Tokoh tua ("nenek", "kakek", "Bu", "Pak" sepuh) wajib usia "tua"; anak kecil usia "anak".
- Campur penceritaan dan dialog secara bergantian, jangan semua dialog atau semua penceritaan.
- Tulis jam dan angka dengan huruf agar dibacakan benar ("jam dua lewat tiga belas", bukan "02.13").
- Tanpa gore, tanpa darah, tanpa kekerasan eksplisit, tanpa bunuh diri, tanpa tokoh/merek nyata, tanpa SARA. {g['kunci'].capitalize()}.
- Konsisten: nama, jam kunci, aturan misterinya tidak berubah antar part.
- "pilihan": {'dua arah berbeda untuk part berikutnya yang akan dipilih penonton lewat komentar (maks 4 kata, tanpa spoiler berat)' if part < total else 'kosongkan ([])'}.

ATURAN VISUAL (tiap adegan pilih dari daftar ini saja):
- latar: {', '.join(LATAR)}. Pilih latar paling spesifik dari lokasi yang benar-benar ada di cerita; jangan gunakan latar generik bila tersedia latar khusus (misalnya rumah_susun/lift, studio_foto/pesta, warung, gerbong, menara_air, bukit_kabut/gunung_api, perpustakaan, kota_futuristik, toko_jahit).
- shot: lebar (tokoh kecil, lihat ruangan), sedang (tokoh agak dekat), closeup (wajah penuh; untuk reaksi/ketakutan/punchline),
  ranjang (zoom ke tempat tidur; hanya di kamar_malam/kamar_pagi), cctv (tampilan kamera pengawas hijau, wajib ada
  "cctv": {{"jam": "HH:MM:SS", "kecepatan": 1 atau 16}}; jam harus naik masuk akal antar adegan cctv), cctv_zoom (wajah penuh di CCTV; pakai tokoh emosi seram).
- pose tokoh: berdiri, duduk (di meja dapur / sofa ruang_tamu / meja kantor / di kasur), tidur (hanya di kamar; maks 2 orang)
- emosi: {', '.join(EMOSI)} ({'seram = mata menyala, hanya untuk sosok hantu/kerasukan' if genre not in TANPA_SERAM else 'pakai kaget/marah/senang/sedih/takut sesuai cerita; jangan pakai seram'})
- efek (opsional, hemat): getar (gemetar), kedip (lampu berkedip), glitch (gangguan gambar, cocok di cctv), kejut (kilatan + dentuman, pakai maks 2 kali, untuk twist/punchline)
- KONTINUITAS WAJIB: setiap adegan dimulai dari akibat adegan sebelumnya. Urutan wajib: pembuka -> pengenalan -> pemicu -> reaksi -> keputusan -> tindakan -> bukti -> hambatan -> klimaks -> konsekuensi -> cliffhanger. Jangan melompat dari pengenalan langsung ke twist. Benda penting harus diperkenalkan sebelum dipakai dan hasil tindakan harus terlihat di adegan berikutnya. Jangan pindah lokasi tanpa alasan yang disebutkan. Jam, posisi benda, pakaian, dan siapa yang ada di ruangan harus konsisten. Setiap adegan harus menunjukkan tindakan konkret, bukan hanya tokoh berdiri diam.
- PROPERTI: isi "properti" dengan 0-3 benda yang benar-benar disentuh/dilihat/digunakan pada adegan itu. Nama benda pilih dari: kamera_cctv, ponsel, jam_digital, boneka_teddy, boneka_kelinci, kotak_paket, kunci, senter, foto, gelas, buku_catatan, pintu, payung_merah, kamera_foto, surat, amplop, sisir_kayu, tiket, radio, interkom, peta, denah, koper, mesin_jahit, gamelan, gong, lampu, lentera. Posisi pilih kiri/kanan/tengah. Benda yang diperkenalkan harus tetap konsisten di adegan berikutnya bila masih dipakai. Jangan menampilkan properti yang tidak disebut atau tidak masuk akal untuk adegan.
- AKSI TOKOH: setiap objek tokoh wajib punya "aksi" singkat yang bisa digambar: diam, berjalan, menunjuk, memegang_hp, memegang_benda, menutup_mulut, meraih, melambai, gemetar, menoleh, tidur. Pilih sesuai narasi; jangan semua berdiri menghadap kamera.
- DETAIL ADEGAN: jelaskan "aksi" tingkat adegan dalam 5-14 kata. Setiap adegan wajib memperagakan kata kerja yang benar-benar terjadi, bukan hanya mengulang narasi. Contoh: "Reno menarik gagang pintu, lalu mundur ketika pintu terkunci sendiri". Tokoh yang melakukan tindakan wajib tampak di layar, kecuali shot CCTV/bukti tanpa tokoh memang disengaja. Jangan biarkan tokoh utama hilang dari sebagian besar adegan. Setiap adegan harus memvisualkan kata kerja yang benar-benar terjadi: membuka, mengangkat, menjatuhkan, menyembunyikan, berlari, mengunci, menoleh, dll. Jangan hanya mengganti ekspresi.
- LATAR KONTEKSTUAL: pilih lokasi paling spesifik dan cocok dengan tindakan di adegan itu. Jika cerita menyebut paket datang, tampilkan teras/ruang tamu saat paket diterima; saat dibuka pindah ke meja/ruang yang sama; saat malam gunakan kamar_malam/lorong. Jangan memakai kamar_malam untuk semua adegan. Pertahankan lokasi sampai ada alasan tokoh berpindah.
- SUARA PER ADEGAN: isi "cara_bicara" dengan salah satu: natural, berbisik, panik, teriak, menangis, tenang. Gunakan natural sebagai default. Berbisik untuk rahasia/ancaman dekat, panik untuk napas terburu-buru, teriak hanya pada momen kaget/ancaman, menangis saat sedih, tenang untuk pengakuan. Jangan membuat semua adegan horor berbisik atau berteriak.
- SUASANA AUDIO: isi "suasana" dengan salah satu: sunyi, angin, hujan, dengung, ramai, kereta, mesin, api. Pilih sesuai lokasi dan kejadian: kereta untuk gerbong, mesin untuk alat/teknologi, api untuk gunung atau lentera, ramai untuk pesta/warung, angin untuk bukit/menara, dengung untuk lift/listrik, hujan saat hujan, sunyi untuk momen tegang. Hindari suasana yang tidak sesuai visual.
- Variasikan shot: jangan sama lebih dari 3 adegan berturut-turut. {'Pakai cctv/cctv_zoom untuk adegan bukti. ' if genre not in TANPA_CCTV else 'CCTV hanya bila benar-benar cocok dengan cerita. '}Pakai closeup untuk puncak emosi.
- "bicara" = nama tokoh yang mulutnya bergerak (narator bila ia tampil; tokoh lain bila narasi adalah ucapannya). Kosongkan jika tidak ada.
- Konsistensi desain tokoh: tiap tokoh punya warna baju, rambut, warna kulit, dan aksesori yang tetap sama di semua adegan dan part. Jangan mengubah penampilan saat emosi berubah.
- Tokoh: gender {'/'.join(GENDER)}, usia {'/'.join(USIA)}, rambut {'/'.join(anim.RAMBUT)}, warna_rambut hitam/cokelat/abu/pirang, aksesori kosong/kalung/anting/topi/jam_tangan, kulit {'/'.join(KULIT)}, baju {'/'.join(BAJU)}. Maks 4 tokoh per seri; tokoh hantu cukup ditulis dengan emosi seram pada adegannya.
- kartu_akhir.waktu = jam kunci cerita (HH:MM:SS); kartu_akhir.teks = peringatan/slogan khas seri, huruf besar, maks 5 kata.

Balas HANYA JSON valid dengan bentuk ini (jumlah adegan sesuai aturan, bukan hanya 2):
{FORMAT_JSON}"""
    return p


def _nama_cocok(nama, cast):
    for k in cast:
        if str(nama).strip().lower() == k.lower():
            return k
    return None


def _rapikan_tokoh(t):
    nama = str(t.get("nama", "")).strip()[:14] or "Tokoh"
    rambut = t.get("rambut") if t.get("rambut") in anim.RAMBUT else "pendek"
    gender = t.get("gender") if t.get("gender") in GENDER else "pria"
    return dict(nama=nama, gender=gender, usia=t.get("usia") if t.get("usia") in USIA else "dewasa", rambut=rambut, kulit=t.get("kulit") if t.get("kulit") in KULIT else "sawo",
                baju=t.get("baju") if t.get("baju") in BAJU else "abu", kacamata=bool(t.get("kacamata")),
                warna_rambut=t.get("warna_rambut") if t.get("warna_rambut") in ("hitam", "cokelat", "abu", "pirang") else "hitam",
                aksesori=t.get("aksesori") if t.get("aksesori") in ("kalung", "anting", "topi", "jam_tangan") else "")


def _petakan_visual(narasi, aksi, properti):
    """Lengkapi petunjuk visual yang terlewat tanpa menambah benda yang tidak disebut cerita."""
    teks = (str(narasi or "") + " " + str(aksi or "")).lower()
    aksi_tk = "diam"
    aturan_aksi = [
        (("berlari", "lari", "bergegas", "kabur", "mengejar", "berjalan", "melangkah", "mendekat", "pergi", "masuk", "keluar"), "berjalan"),
        (("menunjuk", "mengarah"), "menunjuk"),
        (("menutup mulut", "membekap mulut"), "menutup_mulut"),
        (("menoleh", "berbalik", "melihat ke belakang"), "menoleh"),
        (("gemetar", "menggigil", "ketakutan"), "gemetar"),
        (("mengambil", "meraih", "mengetuk", "menarik", "membuka", "membuka", "mengunci"), "meraih"),
        (("ponsel", "telepon", "hp", "handphone"), "memegang_hp"),
        (("memegang", "mengangkat", "membawa", "menarik", "mendorong", "membuka", "buka pintu", "menutup", "mengunci", "mengetuk"), "memegang_benda"),
    ]
    for keys, result in aturan_aksi:
        if any(k in teks for k in keys):
            aksi_tk = result
            break
    peta = [
        (("pintu", "gagang pintu"), "pintu"), (("ponsel", "telepon", "hp", "handphone", "pesan", "chat"), "ponsel"),
        (("jam", "pukul", "03.17", "02.13"), "jam_digital"), (("kamera cctv", "cctv", "rekaman"), "kamera_cctv"),
        (("boneka teddy", "boneka beruang", "boneka"), "boneka_teddy"), (("kotak paket", "paket", "kardus", "kiriman"), "kotak_paket"),
        (("kunci", "anak kunci"), "kunci"), (("senter", "sorot lampu"), "senter"),
        (("foto", "fotografi", "kartu memori"), "foto"), (("kamera",), "kamera_foto"),
        (("surat",), "surat"), (("amplop",), "amplop"), (("sisir",), "sisir_kayu"), (("tiket",), "tiket"),
        (("radio",), "radio"), (("interkom",), "interkom"), (("peta", "denah"), "denah"), (("koper",), "koper"),
        (("mesin jahit",), "mesin_jahit"), (("gamelan",), "gamelan"), (("gong",), "gong"), (("lentera",), "lentera"),
        (("payung merah", "payung"), "payung_merah"), (("gelas", "cangkir"), "gelas"), (("buku catatan", "catatan"), "buku_catatan"),
    ]
    names = [x.get("nama") for x in properti if isinstance(x, dict)]
    for keys, name in peta:
        if any(k in teks for k in keys) and name not in names:
            names.append(name)
        if len(names) >= 3:
            break
    return aksi_tk, [{"nama": n, "posisi": ("kiri", "tengah", "kanan")[i % 3]} for i, n in enumerate(names[:3])]


def validasi(data, st_seri, part, cek_kata=True, genre="horor"):
    """Rapikan keluaran model supaya tidak pernah membuat render error."""
    cast = {t["nama"]: t for t in st_seri.get("tokoh", [])}
    for t in data.get("tokoh") or []:
        if isinstance(t, dict):
            r = _rapikan_tokoh(t)
            if _nama_cocok(r["nama"], cast) is None:
                cast[r["nama"]] = r
    assert cast, "tokoh kosong"
    narator = _nama_cocok(data.get("narator") or st_seri.get("narator") or "", cast) or next(iter(cast))
    bagian = data.get("bagian") or []
    assert len(bagian) >= 16, f"adegan terlalu sedikit ({len(bagian)}); minimal 16 adegan agar alur lengkap"
    out = []
    for b in bagian[:24]:
        narasi = re.sub(r"\s+", " ", str(b.get("narasi", "")).replace('"', "").replace("\u201c", "").replace("\u201d", "")).strip()
        assert narasi, "narasi kosong"
        shot = b.get("shot") if b.get("shot") in SHOT else "sedang"
        latar = b.get("latar") if b.get("latar") in LATAR else "kamar_malam"
        if shot == "ranjang" and latar not in ("kamar_malam", "kamar_pagi"):
            shot = "sedang"
        tokoh, dipakai = [], set()
        for t in b.get("tokoh") or []:
            t = {"nama": t} if isinstance(t, str) else t
            if not isinstance(t, dict):
                continue
            nm = _nama_cocok(t.get("nama", ""), cast)
            if nm and nm not in dipakai and len(tokoh) < 3:
                dipakai.add(nm)
                emo = t.get("emosi") if t.get("emosi") in EMOSI else "netral"
                if genre in TANPA_SERAM and emo == "seram":
                    emo = "marah"
                aksi_tk = str(t.get("aksi") or "diam").strip().lower()
                aksi_valid = {"diam", "berjalan", "menunjuk", "memegang_hp", "memegang_benda", "menutup_mulut", "meraih", "melambai", "gemetar", "menoleh", "tidur"}
                if aksi_tk not in aksi_valid:
                    aksi_tk = "diam"
                tokoh.append(dict(nama=nm, pose=t.get("pose") if t.get("pose") in POSE else "berdiri", emosi=emo, aksi=aksi_tk))
        if shot in ("closeup", "cctv_zoom") and not tokoh:
            tokoh = [dict(nama=narator, pose="berdiri", emosi="takut")]
        if sum(t["pose"] == "tidur" for t in tokoh) > 2:
            for t in tokoh:
                t["pose"] = "berdiri"
        if "bicara" in b:
            bicara = _nama_cocok(b.get("bicara") or "", cast)
        else:
            bicara = narator if narator in dipakai else None
        if bicara and bicara not in dipakai and len(tokoh) < 3:   # pembicara harus tampil di layar
            tk_b = dict(nama=bicara, pose="berdiri", emosi="netral")
            tokoh.insert(0, tk_b) if shot in ("closeup", "cctv_zoom") else tokoh.append(tk_b)
            dipakai.add(bicara)
        if bicara not in dipakai:
            bicara = None
        properti = []
        daftar_properti = {"kamera_cctv", "ponsel", "jam_digital", "boneka_teddy", "boneka_kelinci", "kotak_paket",
                           "kunci", "senter", "foto", "gelas", "buku_catatan", "pintu", "payung_merah", "kamera_foto",
                           "surat", "amplop", "sisir_kayu", "tiket", "radio", "interkom", "peta", "denah", "koper",
                           "mesin_jahit", "alat_jahit", "gamelan", "gong", "lampu", "lentera", "kamera", "sisir", "tas", "kartu"}
        for pr in (b.get("properti") or [])[:3]:
            if isinstance(pr, str):
                pr = {"nama": pr}
            if isinstance(pr, dict) and pr.get("nama") in daftar_properti:
                properti.append({"nama": pr["nama"], "posisi": pr.get("posisi") if pr.get("posisi") in ("kiri", "kanan", "tengah") else "kanan"})
        aksi_adegan = re.sub(r"\s+", " ", str(b.get("aksi") or "")).strip()[:160]
        aksi_infer, properti_infer = _petakan_visual(narasi, aksi_adegan, properti)
        properti = properti_infer
        # Jangan biarkan frame kosong saat adegan menceritakan tindakan tokoh.
        if not tokoh and shot not in ("cctv", "cctv_zoom"):
            teks_visual = (narasi + " " + aksi_adegan).lower()
            emosi_default = "takut" if any(k in teks_visual for k in ("takut", "panik", "aneh", "gelap", "suara", "hantu")) else "netral"
            tokoh = [dict(nama=narator, pose="berdiri", emosi=emosi_default, aksi=aksi_infer)]
        elif tokoh and all(t.get("aksi") == "diam" for t in tokoh):
            tokoh[0]["aksi"] = aksi_infer
        cara = b.get("cara_bicara") if b.get("cara_bicara") in ("natural", "berbisik", "panik", "teriak", "menangis", "tenang") else "natural"
        suasana = b.get("suasana") if b.get("suasana") in ("sunyi", "angin", "hujan", "dengung", "ramai", "kereta", "mesin", "api") else ("dengung" if shot in ("cctv", "cctv_zoom") else "sunyi")
        item = dict(narasi=narasi, latar=latar, shot=shot, tokoh=tokoh, bicara=bicara,
                    properti=properti, aksi=aksi_adegan, cara_bicara=cara, suasana=suasana,
                    efek=[e for e in (b.get("efek") or []) if e in EFEK][:3])
        if shot in ("cctv", "cctv_zoom"):
            c = b.get("cctv") if isinstance(b.get("cctv"), dict) else {}
            jam = str(c.get("jam", ""))
            jam = jam if re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", jam) else "02:13:00"
            kec = c.get("kecepatan") if c.get("kecepatan") in (1, 2, 4, 8, 16, 32) else 1
            item["cctv"] = dict(jam=jam, kecepatan=kec)
        out.append(item)
    kata = sum(len(b["narasi"].split()) for b in out)
    assert not cek_kata or 150 <= kata <= 240, f"jumlah kata tidak cukup untuk alur lengkap: {kata}"
    ka = data.get("kartu_akhir") if isinstance(data.get("kartu_akhir"), dict) else {}
    waktu = str(ka.get("waktu", ""))
    waktu = waktu if re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", waktu) else "02:13:00"
    pilihan = [re.sub(r"\s+", " ", str(x)).strip()[:28] for x in (data.get("pilihan") or []) if str(x).strip()][:2]
    return dict(judul=str(data.get("judul") or "Cerita Bersambung")[:70], pilihan=pilihan, deskripsi=str(data.get("deskripsi") or ""),
                tag=[str(x) for x in (data.get("tag") or [])][:12], narator=narator, tokoh=list(cast.values()),
                bagian=out, ringkasan=str(data.get("ringkasan") or ""), cliffhanger=str(data.get("cliffhanger") or ""),
                kartu_akhir=dict(waktu=waktu, teks=str(ka.get("teks") or "")[:60]))


def tulis_naskah(row, st):
    import requests
    part = int(row["part"])
    prompt = buat_prompt(row, st)
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9}}
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for percobaan in range(3):
            try:
                r = requests.post(url, params={"key": GEMINI_KEY}, json=body, timeout=180)
                if r.status_code in (404, 429):
                    print(f"Model {model} ditolak (HTTP {r.status_code}), coba model berikutnya")
                    break
                r.raise_for_status()
                bag = r.json()["candidates"][0]["content"]["parts"]
                teks = "".join(p.get("text", "") for p in bag if not p.get("thought"))
                data = validasi(json.loads(teks), st.get(row["seri"], {}), part, genre=genre_row(row))
                print("Naskah dibuat dengan model:", model)
                return data
            except Exception as e:  # noqa: BLE001
                print(f"Naskah gagal ({model}, percobaan {percobaan + 1}): {e}")
                time.sleep(5)
    sys.exit("Gagal membuat naskah.")


def naskah_contoh():
    """Contoh lengkap (dipakai --uji) sekaligus gambaran hasil yang dituju."""
    def T(n, p="berdiri", e="netral"):
        return dict(nama=n, pose=p, emosi=e)
    B, L = "Bima", "Laras"
    s = [
        ("Jangan pernah buka pintu kalau ada yang mengetuk jam tiga pagi.", "luar_malam", "lebar", [T(B, e="takut")], B, ["getar"]),
        ("Aku Bima. Ini istriku, Laras.", "ruang_tamu", "sedang", [T(B, "duduk", "senang"), T(L, "duduk", "senang")], B, []),
        ("Kami pindah ke rumah kontrakan yang murah.", "ruang_tamu", "lebar", [T(B, e="senang"), T(L, e="senang")], B, []),
        ("Terlalu murah, sebenarnya.", "dapur", "closeup", [T(B)], B, []),
        ("Nak, rumah itu jangan ditinggali lama-lama.", "ruang_tamu", "sedang", [T(B, "duduk", "kaget"), T("Bu Rukmi", "duduk", "sedih")], "Bu Rukmi", []),
        ("Kenapa, Bu?", "ruang_tamu", "sedang", [T(B, "duduk", "kaget"), T(L, "duduk", "netral")], L, []),
        ("Malam pertama, pintu depan diketuk. Satu kali.", "kamar_malam", "ranjang", [T(B, "tidur"), T(L, "tidur")], None, []),
        ("Jam tiga tepat.", "kamar_malam", "lebar", [T(B, "tidur"), T(L, "tidur")], None, ["kedip"]),
        ("Malam kedua, dua kali.", "kamar_malam", "ranjang", [T(B, "tidur", "takut"), T(L, "tidur")], None, []),
        ("Malam ketujuh, tujuh kali.", "lorong", "sedang", [T(B, e="takut")], B, ["getar"]),
        ("Jadi kupasang kamera kecil di ruang tamu.", "rak_buku", "lebar", [T(B)], B, []),
        ("Jam tiga. Aku memutar rekamannya.", "ruang_tamu", "sedang", [T(B, "duduk", "takut")], B, []),
        ("Tidak ada siapa pun di depan pintu.", "lorong", "cctv", [], None, []),
        ("Tapi pintunya bergetar dari dalam.", "lorong", "cctv", [], None, ["glitch"]),
        ("Lalu seseorang berjalan ke arah pintu.", "lorong", "cctv", [T(L, e="seram")], None, []),
        ("Rambut panjang. Gaun putih. Itu Laras.", "lorong", "cctv_zoom", [T(L, e="seram")], None, ["glitch", "getar"]),
        ("Padahal Laras sedang tidur di sampingku.", "kamar_malam", "ranjang", [T(B, "tidur", "kaget"), T(L, "tidur")], None, ["kejut"]),
    ]
    jam = iter(["03:00:00", "03:00:41", "03:01:02", "03:01:10"])
    bagian = []
    for n, la, sh, tk, bc, ef in s:
        d = dict(narasi=n, latar=la, shot=sh, tokoh=tk, efek=ef)
        if bc:
            d["bicara"] = bc
        if sh.startswith("cctv"):
            d["cctv"] = dict(jam=next(jam), kecepatan=1)
        bagian.append(d)
    return dict(judul="Rumah Nomor 13", deskripsi="Setiap jam tiga pagi, pintu depan diketuk.", tag=["horor"], narator=B,
                tokoh=[dict(nama=B, gender="pria", rambut="pendek", kulit="sawo", baju="abu"),
                       dict(nama=L, gender="wanita", rambut="panjang", kulit="terang", baju="putih"),
                       dict(nama="Bu Rukmi", gender="wanita", usia="tua", rambut="kuncir", kulit="sawo", baju="merah")],
                bagian=bagian + [], ringkasan="Bima dan Laras pindah ke rumah nomor 13; pintu diketuk tiap jam tiga.",
                cliffhanger="Rekaman memperlihatkan Laras di depan pintu, padahal Laras tertidur di sampingnya.",
                kartu_akhir=dict(waktu="03:00:00", teks="Jangan buka pintu jam 03.00"))


# ================================================================ suara
def _komunikasi(teks, voice, rate, pitch):
    import edge_tts
    try:
        return edge_tts.Communicate(teks, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    except TypeError:  # edge-tts versi lama tanpa parameter boundary
        return edge_tts.Communicate(teks, voice, rate=rate, pitch=pitch)


async def _tts(teks, path, voice, rate, pitch):
    # Teks satu adegan diberi tanda baca yang wajar agar jeda napas terdengar alami.
    teks = re.sub(r"\s+", " ", str(teks)).strip()
    if teks and teks[-1] not in ".!?…":
        teks += "."
    com = _komunikasi(teks, voice, rate, pitch)
    kata = []
    with open(path, "wb") as f:
        async for ch in com.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                mulai = ch["offset"] / 1e7
                kata.append((mulai, mulai + ch["duration"] / 1e7, ch["text"]))
    return kata



def suara_elevenlabs(teks, path, voice):
    """Suara lebih ekspresif melalui ElevenLabs; memerlukan API key dan voice ID."""
    import base64
    import requests

    wanita = voice == VOICE_WANITA
    voice_id = (os.environ.get("ELEVENLABS_VOICE_ID_FEMALE") if wanita
                else os.environ.get("ELEVENLABS_VOICE_ID_MALE")) or os.environ.get("ELEVENLABS_VOICE_ID", "")
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key or not voice_id:
        raise RuntimeError("TTS_PROVIDER=elevenlabs memerlukan ELEVENLABS_API_KEY dan ELEVENLABS_VOICE_ID")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
    payload = {
        "text": re.sub(r"\s+", " ", str(teks)).strip(),
        "model_id": os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2"),
        "voice_settings": {
            "stability": float(os.environ.get("ELEVENLABS_STABILITY", "0.42")),
            "similarity_boost": float(os.environ.get("ELEVENLABS_SIMILARITY", "0.78")),
            "style": float(os.environ.get("ELEVENLABS_STYLE", "0.28")),
            "use_speaker_boost": True
        }
    }
    response = requests.post(url, headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                             json=payload, timeout=180)
    response.raise_for_status()
    data = response.json()
    Path(path).write_bytes(base64.b64decode(data["audio_base64"]))
    align = data.get("normalized_alignment") or data.get("alignment") or {}
    chars = align.get("characters", [])
    starts = align.get("character_start_times_seconds", [])
    ends = align.get("character_end_times_seconds", [])
    hasil, word, t0, t1 = [], "", None, None
    for ch, st, en in zip(chars, starts, ends):
        if str(ch).isspace():
            if word:
                hasil.append((float(t0), float(t1), word))
                word, t0, t1 = "", None, None
        else:
            if not word:
                t0 = st
            word += str(ch)
            t1 = en
    if word:
        hasil.append((float(t0), float(t1), word))
    return hasil


def mastering_audio(path, cara_bicara="natural"):
    tmp = str(path) + ".master.mp3"
    # Performa suara diolah ringan per adegan; bukan sekadar mengubah pitch seluruh cerita.
    extra = {
        "berbisik": "highpass=f=180,lowpass=f=6500,acompressor=threshold=-25dB:ratio=2:attack=8:release=140,volume=0.82",
        "panik": "highpass=f=90,equalizer=f=3200:t=q:w=1:g=2.0,acompressor=threshold=-20dB:ratio=2.5:attack=5:release=100,volume=1.08",
        "teriak": "highpass=f=100,equalizer=f=1800:t=q:w=1:g=2.5,acompressor=threshold=-22dB:ratio=3:attack=3:release=120,volume=1.10",
        "menangis": "highpass=f=100,equalizer=f=900:t=q:w=1:g=1.3,acompressor=threshold=-22dB:ratio=2:attack=12:release=180,volume=0.92",
        "tenang": "highpass=f=75,lowpass=f=10500,acompressor=threshold=-20dB:ratio=1.7:attack=15:release=220,volume=0.96",
        "natural": "highpass=f=75,lowpass=f=11500,equalizer=f=2600:t=q:w=1:g=1.2,acompressor=threshold=-19dB:ratio=2:attack=12:release=180"
    }.get(cara_bicara, "highpass=f=75,lowpass=f=11500")
    anim.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
              "-af", extra + ",loudnorm=I=-16:TP=-1.5:LRA=10,afade=t=in:st=0:d=0.025",
              "-c:a", "libmp3lame", "-q:a", "3", tmp])
    os.replace(tmp, str(path))


def suara(teks, path, voice, rate="+0%", pitch="+0Hz", cara_bicara="natural"):
    """Buat suara dengan jeda prosodi dan mastering ringan; timing kata tetap berasal dari Edge TTS."""
    if os.environ.get("TTS_PROVIDER", "edge").strip().lower() == "elevenlabs" and not UJI:
        for percobaan in range(3):
            try:
                kata = suara_elevenlabs(teks, str(path), voice)
                mastering_audio(str(path), cara_bicara)
                return kata
            except Exception as e:  # noqa: BLE001
                print(f"ElevenLabs gagal (percobaan {percobaan + 1}): {e}")
                time.sleep(3)
        sys.exit("Gagal membuat suara ElevenLabs. Periksa API key, voice ID, dan kuota.")
    if UJI:  # suara tiruan: nada bergetar sepanjang kalimat (hanya untuk uji tampilan)
        dur = max(1.2, 0.36 * len(teks.split()))
        frek = max(90, 170 + 5 * parse_hz(pitch))            # nada tiruan ikut berbeda per tokoh
        anim.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=f={frek}:d={dur:.2f}",
                  "-af", "tremolo=f=4.5:d=0.95,volume=0.8", str(path)])
        return []
    for percobaan in range(3):
        try:
            kata = asyncio.run(_tts(teks, str(path), voice, rate, pitch))
            # Rapikan frekuensi, dinamika, dan level tanpa mengubah karakter suara secara berlebihan.
            mastering_audio(str(path), cara_bicara)
            return kata
        except Exception as e:  # noqa: BLE001
            print(f"Suara gagal (percobaan {percobaan + 1}): {e}")
            time.sleep(3)
    sys.exit("Gagal membuat suara.")


# ================================================================ render paralel
_KAR = {}


def _worker(args):
    b, specs, audio, kata, out, pengantar = args
    cast = {}
    for s in specs:
        k = json.dumps(s, sort_keys=True)
        if k not in _KAR:
            _KAR[k] = anim.Karakter(s)
        cast[s["nama"]] = _KAR[k]
    render_segmen(b, cast, audio, kata, out, set(pengantar))
    return out


def gabung(segmen, out_path):
    daftar = WORK / "list.txt"
    daftar.write_text("".join(f"file '{Path(s).name}'\n" for s in segmen), encoding="utf-8")
    anim.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(daftar),
              "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out_path)])


# ================================================================ upload
def upload_youtube(video, naskah, row):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"], token_uri="https://oauth2.googleapis.com/token",
                        client_id=os.environ["YT_CLIENT_ID"], client_secret=os.environ["YT_CLIENT_SECRET"],
                        scopes=["https://www.googleapis.com/auth/youtube.upload"])
    creds.refresh(Request())
    yt = build("youtube", "v3", credentials=creds)
    part, total = int(row["part"]), int(row["total"])
    g = GENRE[genre_row(row)]
    tag_hash = g["hashtag"]
    ajak = ""
    if part < total and naskah.get("pilihan"):
        ajak = "\nPart berikutnya KAMU yang pilih! Komen 1 atau 2:\n" + "\n".join(
            f"{i} = {p}" for i, p in enumerate(naskah["pilihan"], 1)) + "\n"
    deskripsi = (f"{naskah['deskripsi']}\n\nPart {part} dari {total} - {row['seri']}\n"
                 + ajak + ("Nonton part berikutnya, ikuti channel ini!\n" if part < total else "TAMAT.\n")
                 + f"\n{tag_hash}\nCerita fiksi. Animasi dan narasi dibuat dengan bantuan AI.")
    body = {"snippet": {"title": f"Part {part} | {naskah['judul']}"[:95], "description": deskripsi[:4900],
                        "tags": (naskah["tag"] + g["tag"] + ["shorts"])[:15], "categoryId": "24"},
            "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False}}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(video), mimetype="video/mp4", resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    print("Terunggah: https://youtube.com/shorts/" + resp["id"])


# ================================================================ main
def main():
    if UJI:
        row = dict(seri="Rumah Nomor 13 (uji)", genre=os.environ.get("GENRE_UJI", "horor"), part="1", total="3", premis="", arah="", status="")
        rows, st = [row], {}
        naskah = validasi(naskah_contoh(), {}, 1, cek_kata=False)
        naskah["pilihan"] = ["Cari tetangga tua", "Pasang kamera lagi"]
    else:
        rows = baca_topik()
        row = pilih_topik(rows)
        st = muat_state()
        pil = st.get(row["seri"], {}).get("pilihan") or []
        if MENANG in ("1", "2") and len(pil) >= int(MENANG):  # voting penonton menimpa kolom 'arah'
            row["arah"] = f"PILIHAN PENONTON MENANG: {pil[int(MENANG) - 1]}. Jadikan ini arah utama part ini."
            print("Voting penonton dipakai:", pil[int(MENANG) - 1])
        print(f"Seri: {row['seri']} ({genre_row(row)}) - Part {row['part']}/{row['total']}")
        naskah = tulis_naskah(row, st)
    part, total = int(row["part"]), int(row["total"])
    print("Judul:", naskah["judul"], "| adegan:", len(naskah["bagian"]), "| narator:", naskah["narator"])

    kenal = set(t["nama"] for t in st.get(row["seri"], {}).get("tokoh", [])) if part > 1 else set()
    gender = {t["nama"]: t["gender"] for t in naskah["tokoh"]}
    usia = {t["nama"]: t.get("usia", "dewasa") for t in naskah["tokoh"]}
    kerja = []
    for i, b in enumerate(naskah["bagian"]):
        audio, seg = WORK / f"a{i}.mp3", WORK / f"seg{i}.mp4"
        voice, rate, pitch = atur_suara(row, b["bicara"], naskah["narator"], gender, usia)
        # Variasi sangat kecil per adegan; tidak mengubah identitas suara karakter.
        seed_adegan = zlib.crc32((row["seri"] + str(part) + str(i)).encode())
        delta_rate = (-1, 0, 1)[seed_adegan % 3]
        delta_pitch = (-1, 0, 1)[(seed_adegan // 3) % 3]
        rate = f"{max(-12, min(12, parse_hz(rate) + delta_rate)):+d}%"
        pitch = f"{max(-10, min(10, parse_hz(pitch) + delta_pitch)):+d}Hz"
        # Prosodi mengikuti emosi adegan. Efek ekstrem dibatasi agar tidak melelahkan.
        cara = b.get("cara_bicara", "natural")
        if cara == "berbisik":
            rate, pitch = f"{max(-18, parse_hz(rate)-5):+d}%", f"{max(-14, parse_hz(pitch)-2):+d}Hz"
        elif cara == "panik":
            rate, pitch = f"{min(16, parse_hz(rate)+9):+d}%", f"{min(12, parse_hz(pitch)+2):+d}Hz"
        elif cara == "teriak":
            rate, pitch = f"{min(18, parse_hz(rate)+12):+d}%", f"{min(14, parse_hz(pitch)+3):+d}Hz"
        elif cara == "menangis":
            rate, pitch = f"{max(-15, parse_hz(rate)-3):+d}%", f"{min(12, parse_hz(pitch)+2):+d}Hz"
        kata = suara(b["narasi"], audio, voice, rate, pitch, cara)
        baru = set()
        if b["shot"] in ("lebar", "sedang", "ranjang"):
            baru = {t["nama"] for t in b["tokoh"]} - kenal
            kenal |= baru
        kerja.append((b, naskah["tokoh"], str(audio), kata, str(seg), sorted(baru)))
    print("Suara selesai, mulai render animasi...")

    n = max(1, min(4, os.cpu_count() or 1))
    with ProcessPoolExecutor(max_workers=n) as ex:
        for k, hasil in enumerate(ex.map(_worker, kerja), 1):
            print(f"Bagian {k}/{len(kerja)} selesai")
    segmen = [k[4] for k in kerja]

    ka = naskah["kartu_akhir"]
    kartu = WORK / "kartu.mp4"
    pil = naskah.get("pilihan") or []
    ajak = (["Komen pilihanmu:"] + [f"{i}. {p}" for i, p in enumerate(pil, 1)]
            if part < total and len(pil) == 2 else ["Komen: seri apa lagi?"])
    render_kartu_akhir(dict(judul="BERSAMBUNG…" if part < total else "", besar=f"PART {part + 1}" if part < total else "TAMAT",
                            kecil=ka["teks"] if part < total else GENRE[genre_row(row)]["akhir"],
                            rec=ka["waktu"], efek=["glitch"], ajak=ajak), kartu)
    segmen.append(kartu)

    final = WORK / ("final_uji.mp4" if UJI else "final.mp4")
    gabung(segmen, final)
    print("Video jadi:", final, f"({anim.durasi(final):.1f} detik)")
    (WORK / "info.json").write_text(json.dumps(dict(
        seri=row["seri"], genre=genre_row(row), part=part, total=total, judul=f"Part {part} | {naskah['judul']}",
        deskripsi=naskah["deskripsi"], hashtag=GENRE[genre_row(row)]["hashtag"], pilihan=naskah.get("pilihan", []),
        cliffhanger=naskah["cliffhanger"]), ensure_ascii=False, indent=2), encoding="utf-8")

    if UPLOAD:
        upload_youtube(final, naskah, row)
    if UPLOAD or SIMPAN:
        s = st.setdefault(row["seri"], {})
        s["narator"] = naskah["narator"]
        s["tokoh"] = naskah["tokoh"]
        s.setdefault("ringkasan", []).append(f"Part {part}: {naskah['ringkasan']}")
        s["cliffhanger"] = naskah["cliffhanger"]
        s["pilihan"] = naskah.get("pilihan", [])
        simpan_state(st)
        row["status"] = "selesai"
        simpan_topik(rows)


if __name__ == "__main__":
    main()
